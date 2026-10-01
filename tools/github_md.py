"""Make the markdown a plan exports readable where GitHub renders it.

An export writes two files, and each one has its own reader:

    pages/<name>.html    the local viewer, served from the page folder
    pages/<name>.md      GitHub, which renders the markdown beside its images

The markdown is copied out of the editor exactly as it was typed, and what is
typed is addressed for the local server: an image is named from the site root
(`/imgs/agent-like-icon.webp`) and a link to another plan points at the page
that server hands over (`2026-10-01_1036-planning-ui-ux.html`). GitHub reads
both from the repository instead, where `/imgs/...` is `https://github.com/imgs/...`
and an `.html` link opens a source listing rather than a page.

This rewrites the references in the markdown so that the page is left alone:

    /imgs/a.webp                         ->  imgs/a.webp
    imgs/a.webp                          ->  unchanged, already repository relative
    https://example.com/a.png            ->  unchanged
    a page.html                          ->  a page.md
    /pages/a page.md                     ->  a page.md
    /images/agent-like.webp              ->  ../../../images/agent-like.webp

Every target is resolved against the tree the page sits in, so a reference that
answers to something in the checkout becomes the path that finds it, and one
that is already right is not touched. That is what makes this safe to run after
every export and safe to run twice.

What happens to a reference that answers to nothing depends on what it is, and
the difference is the reader it was written for. An image is addressed to the
page's own image folder anyway and reported as missing, because that folder is
where a synced copy lands: the export converges once the sync has run, and the
report is what says to run it. A link carrying the site prefix keeps the
remainder without that prefix and is reported as unresolved. A relative link
that answers to nothing is left exactly as it is, because there is no second
reading of it to prefer over the one already written.

    python3 tools/github_md.py --check                  # report, change nothing
    python3 tools/github_md.py --write                  # rewrite in place
    python3 tools/github_md.py --write pages/a.md       # one file
    python3 tools/github_md.py --write --json           # for a caller that shells out

The default subject is every `.md` under `plans_and_writeups/pages`. `--check`
exits non-zero when a file would change, so it works as a gate; `--strict` also
fails on a reference that resolves to nothing.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

#: The pages an export writes into, holding the markdown and, one level below,
#: the images it points at. Both are the same folder on disk and in a checkout,
#: which is what lets one relative path serve both readers.
PAGES_DIR = "plans_and_writeups/pages"

#: The folder a destination keeps its images in, relative to its pages.
IMAGE_DIR_NAME = "imgs"

#: How a page is addressed when the local server is the reader. A root relative
#: path describes the site, not the repository, so the prefix is taken off
#: before the rest is resolved against the tree. Longest first.
LOCAL_PREFIXES: tuple[str, ...] = (
    "/docs/pages/",
    "docs/pages/",
    "/pages/",
    "pages/",
    "/",
)

#: A target that is not the repository's to resolve: an absolute URL, a
#: protocol relative one, or a `data:`/`mailto:` URI.
EXTERNAL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:|//)")

#: A markdown image or link: `[text](target)` and `![alt](target)`, where the
#: target may be wrapped in angle brackets and followed by a title.
#: Resolved by hand rather than by one expression, because a target can contain
#: balanced parentheses and an optional title can contain a space.
_HTML_REF = re.compile(r"<(img|a)\b[^>]*?\b(?:src|href)\s*=\s*([\"'])(.*?)\2", re.I | re.S)

#: Characters that end a markdown target early or are unsafe inside a URL path.
#: A percent sign is deliberately absent: an escaped name stays escaped.
UNSAFE_PATH_CHARS: dict[str, str] = {
    " ": "%20",
    "(": "%28",
    ")": "%29",
    "<": "%3C",
    ">": "%3E",
    '"': "%22",
    "'": "%27",
    "#": "%23",
    "?": "%3F",
}


@dataclass(frozen=True)
class Reference:
    """One image or link found in the markdown, and where its target is written."""

    kind: str  # "image" or "link"
    target: str
    start: int  # span of the target, so only the path is replaced
    end: int


@dataclass(frozen=True)
class Finding:
    """What became of one reference."""

    page: str
    line: int
    kind: str
    status: str  # "rewritten", "missing" or "unresolved"
    before: str
    after: str

    def render(self) -> str:
        if self.status == "rewritten":
            return f"{self.page}:{self.line}  {self.kind}  {self.before}  ->  {self.after}"
        return (f"{self.page}:{self.line}  {self.kind}  {self.status}  "
                f"{self.before}  ->  {self.after}")


@dataclass(frozen=True)
class Context:
    """Where a page sits, and therefore what a relative path means to it."""

    page: Path
    page_dir: Path
    root: Path
    image_dir: Path | None
    index: dict[str, str]

    @property
    def name(self) -> str:
        """The page as a reader names it: relative to the tree when it is inside it."""
        try:
            return str(self.page.resolve().relative_to(self.root))
        except ValueError:
            return str(self.page)

    @property
    def image_rel(self) -> str | None:
        """The image folder addressed from the page, or None when there is none."""
        if self.image_dir is None:
            return None
        return posixpath.relpath(str(self.image_dir), str(self.page_dir))


def repository_root() -> Path:
    """The working tree this tool is running inside, as git resolves it."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit("github_md: not inside a git working tree")
    return Path(result.stdout.decode().strip())


def image_directory(page_dir: Path, root: Path) -> Path | None:
    """The folder this page's images live in, found rather than assumed.

    A destination keeps its images one level below its pages, but how far below
    depends on where the page is, so the folder is looked for instead of being
    computed from a count of `..`.
    """
    current = page_dir
    for _ in range(6):
        if (current / IMAGE_DIR_NAME).is_dir():
            return current / IMAGE_DIR_NAME
        if current == root or current.parent == current:
            break
        current = current.parent
    return None


def page_index(page_dir: Path) -> dict[str, str]:
    """Which markdown produced each exported page, from the destination's index.

    An exported page is named after the moment it was exported and the markdown
    after the moment it was written, so the two names do not match and a link to
    a page cannot be turned into a link to its source by string work alone. The
    index beside the pages is the record that pairs them.
    """
    index: dict[str, str] = {}
    source = page_dir.parent / "exports.json"
    if not source.is_file():
        return index
    try:
        entries = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return index
    if not isinstance(entries, list):
        return index

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        markdown = entry.get("source_md")
        if not isinstance(markdown, str) or not markdown:
            continue
        for key in (entry.get("id"), entry.get("filename")):
            if isinstance(key, str) and key:
                index.setdefault(posixpath.basename(key), posixpath.basename(markdown))
    return index


def context_for(page: Path, root: Path) -> Context:
    return Context(
        page=page,
        page_dir=page.parent,
        root=root,
        image_dir=image_directory(page.parent, root),
        index=page_index(page.parent),
    )


def _blank(mask: bytearray, start: int, end: int) -> None:
    for position in range(max(start, 0), min(end, len(mask))):
        mask[position] = 0


def _editable_mask(text: str) -> bytearray:
    """One byte per character: 1 where a reference may be rewritten.

    A fenced block and an inline code span both show a path without meaning it as
    one, so neither is touched. A fence is found line by line, an inline span by
    pairing backtick runs of the same width, which is what markdown does.
    """
    mask = bytearray(b"\x01" * len(text))
    offset = 0
    fence: str | None = None

    for line in text.splitlines(keepends=True):
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if fence is not None:
            _blank(mask, offset, offset + len(line))
            if marker and marker.group(1)[0] * 3 == fence:
                fence = None
        elif marker:
            fence = marker.group(1)[0] * 3
            _blank(mask, offset, offset + len(line))
        else:
            _blank_inline_code(mask, text, offset, offset + len(line))
        offset += len(line)

    return mask


def _blank_inline_code(mask: bytearray, text: str, start: int, end: int) -> None:
    runs = list(re.finditer(r"`+", text[start:end]))
    index = 0
    while index < len(runs):
        width = len(runs[index].group(0))
        closing = next(
            (i for i in range(index + 1, len(runs)) if len(runs[i].group(0)) == width),
            None,
        )
        if closing is None:
            return
        _blank(mask, start + runs[index].start(), start + runs[closing].end())
        index = closing + 1


def _closing_paren(text: str, start: int) -> int:
    """The index of the parenthesis that ends a target, or -1."""
    depth = 0
    for position in range(start, len(text)):
        character = text[position]
        if character == "\\":
            continue
        if character == "\n":
            return -1
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return position
            depth -= 1
    return -1


def _read_markdown_target(text: str, cursor: int) -> tuple[str, int, int] | None:
    """The target of a link whose `(` is at `cursor`. Returns it with its span."""
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    if cursor >= len(text):
        return None

    if text[cursor] == "<":
        closing = text.find(">", cursor)
        if closing == -1:
            return None
        target = text[cursor + 1 : closing]
        end_of_link = _closing_paren(text, closing)
        return (target, cursor + 1, closing) if end_of_link != -1 else None

    end = _closing_paren(text, cursor)
    if end == -1:
        return None
    raw = text[cursor:end].strip()
    if not raw:
        return None
    # A title follows the target after a space. Everything after the first run
    # of whitespace is the title, which is also how GitHub reads it.
    target = re.split(r"\s+[\"'(]", raw, maxsplit=1)[0].strip() or raw.split()[0]
    return target, cursor, cursor + len(target)


def _iter_references(text: str, mask: bytearray) -> list[Reference]:
    """Every image and link in the markdown outside code."""
    found: list[Reference] = []
    position = 0
    while position < len(text):
        if not mask[position]:
            position += 1
            continue

        character = text[position]
        if character == "[" and not (position and text[position - 1] == "\\"):
            close = text.find("]", position, len(text))
            if close != -1 and close + 1 < len(text) and text[close + 1] == "(":
                parsed = _read_markdown_target(text, close + 2)
                if parsed:
                    target, start, end = parsed
                    kind = "image" if position and text[position - 1] == "!" else "link"
                    found.append(Reference(kind, target, start, end))
                    position = end
                    continue
        elif character == "<":
            match = _HTML_REF.match(text, position)
            if match:
                found.append(Reference("image" if match.group(1).lower() == "img" else "link",
                                       match.group(3), match.start(3), match.end(3)))
                position = match.end()
                continue

        position += 1

    return found


def _path_and_suffix(target: str) -> tuple[str, str]:
    """A target split into the path that names a file and the `#`/`?` tail."""
    for index, character in enumerate(target):
        if character in "?#":
            return target[:index], target[index:]
    return target, ""


def _strip_local_prefix(path: str) -> str:
    """Take the site's own idea of where the page lives off the front."""
    while path.startswith("./"):
        path = path[2:]
    for prefix in LOCAL_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _encode(path: str) -> str:
    return "".join(UNSAFE_PATH_CHARS.get(character, character) for character in path)


def _decode(path: str) -> str:
    """A target as the file system names it. `%20` is a space to a browser."""
    return unquote(path)


def _relative(path: Path, context: Context) -> str:
    return _encode(posixpath.relpath(str(path), str(context.page_dir)))


def _candidates(path: str, context: Context) -> list[Path]:
    """Every place a reference could point, in the order to try them.

    A path that still begins with a slash was rooted at the site rather than at
    the page, so the repository root is the first reading and the page folder
    the second. Anything else is read from the page first.
    """
    if path.startswith("/"):
        return [context.root / path.lstrip("/"), context.page_dir / path.lstrip("/")]

    cleaned = posixpath.normpath(path)
    if cleaned in ("", "."):
        return []
    return [context.page_dir / cleaned, context.root / cleaned]


def _resolve_image(target: str, context: Context) -> tuple[str | None, str]:
    path, suffix = _path_and_suffix(target)
    local = _strip_local_prefix(_decode(path.replace("\\", "/")))
    name = posixpath.basename(local)
    if not name or name == ".":
        return None, ""

    for candidate in _candidates(local, context):
        if candidate.is_file():
            return _relative(candidate, context) + suffix, ""

    # Nothing in the tree answers to it. The page folder is the one place a
    # copied image can live, so that is where the reference is pointed and the
    # caller is told it found nothing.
    if context.image_rel:
        return _encode(posixpath.join(context.image_rel, name)) + suffix, "missing"
    return None, ""


def _resolve_link(target: str, context: Context) -> tuple[str | None, str]:
    path, suffix = _path_and_suffix(target)
    path = path.replace("\\", "/")
    if not path:
        return None, ""
    local = _strip_local_prefix(_decode(path))

    # A page link: GitHub renders the markdown, so the source beside the page is
    # what a reader wants, not the exported page it was generated from. The
    # source is the sibling of the same name when there is one, and otherwise
    # the one the index records for that page.
    if local.lower().endswith(".html"):
        stem = posixpath.basename(local)[: -len(".html")]
        for name in (stem + ".md", context.index.get(stem, "")):
            if not name:
                continue
            sibling = context.page_dir / name
            if sibling.is_file():
                return _relative(sibling, context) + suffix, ""

    for candidate in _candidates(local, context):
        if candidate.is_file() or candidate.is_dir():
            return _relative(candidate, context) + suffix, ""

    if path.startswith("/") or local != path:
        # The site prefix has been taken off and the remainder matches nothing
        # in the tree, so the relative form of it is the honest thing to leave.
        return _encode(posixpath.normpath(local)) + suffix, "unresolved"
    return None, ""


def _resolve(reference: Reference, context: Context) -> tuple[str | None, str]:
    if reference.target.startswith("#") or EXTERNAL.match(reference.target):
        return None, ""
    if reference.kind == "image":
        return _resolve_image(reference.target, context)
    return _resolve_link(reference.target, context)


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def convert_text(text: str, context: Context) -> tuple[str, list[Finding]]:
    """The markdown with its references addressed for GitHub, and what changed."""
    mask = _editable_mask(text)
    findings: list[Finding] = []
    edits: list[tuple[Reference, str]] = []

    for reference in _iter_references(text, mask):
        replacement, note = _resolve(reference, context)
        line = _line_of(text, reference.start)
        if replacement is None or replacement == reference.target:
            if note:
                findings.append(
                    Finding(context.name, line, reference.kind, note,
                            reference.target, reference.target)
                )
            continue
        edits.append((reference, replacement))
        findings.append(
            Finding(context.name, line, reference.kind,
                    "rewritten" if note == "" else note,
                    reference.target, replacement)
        )

    for reference, replacement in reversed(edits):
        text = text[: reference.start] + replacement + text[reference.end :]

    return text, findings


def read_markdown(path: Path) -> str:
    """A file's text with its line endings kept as they are."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def convert_file(page: Path, root: Path, write: bool) -> tuple[bool, list[Finding]]:
    """Convert one file. Returns whether it changed, and what was found."""
    context = context_for(page, root)
    original = read_markdown(page)
    updated, findings = convert_text(original, context)
    changed = updated != original
    if changed and write:
        page.write_text(updated, encoding="utf-8", newline="")
    return changed, findings


def _subject_files(paths: list[str], root: Path) -> list[Path]:
    if paths:
        return [Path(raw) for raw in paths]
    pages = root / PAGES_DIR
    if not pages.is_dir():
        return []
    return sorted(pages.rglob("*.md"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="github_md",
        description="Address the references in an exported page's markdown for GitHub.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report, write nothing (default)")
    mode.add_argument("--write", action="store_true", help="rewrite the files in place")
    parser.add_argument(
        "--strict", action="store_true",
        help="also exit non-zero when a reference resolves to nothing",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="one JSON object on stdout, for a caller that shells out",
    )
    parser.add_argument("paths", nargs="*", help=f"files to convert instead of every .md under {PAGES_DIR}")
    args = parser.parse_args(argv)

    root = repository_root()
    targets = _subject_files(args.paths, root)
    if not targets:
        if args.as_json:
            print(json.dumps({"status": "empty", "files": [], "changed": 0}))
        else:
            print(f"github_md: no markdown found under {PAGES_DIR}")
        return 0

    records: list[dict[str, object]] = []
    rewritten = missing = unresolved = 0
    changed_files = 0

    for page in targets:
        if not page.is_file():
            continue
        changed, findings = convert_file(page, root, write=bool(args.write))
        changed_files += 1 if changed else 0
        for finding in findings:
            if finding.status == "rewritten":
                rewritten += 1
            elif finding.status == "missing":
                missing += 1
            else:
                unresolved += 1
            if not args.as_json:
                print(f"github_md: {finding.render()}")

        try:
            name = str(page.resolve().relative_to(root))
        except ValueError:
            name = str(page)
        records.append({"page": name, "changed": changed,
                        "references": [f.render() for f in findings]})

    summary = {
        "status": "written" if args.write else "checked",
        "files": records,
        "files_changed": changed_files,
        "rewritten": rewritten,
        "missing": missing,
        "unresolved": unresolved,
        "changed": bool(changed_files),
    }

    if args.as_json:
        print(json.dumps(summary))
    else:
        verb = "rewrote" if args.write else "would rewrite"
        if changed_files:
            print(f"github_md: {verb} {changed_files} file(s), "
                  f"{rewritten} reference(s) rewritten")
        else:
            print("github_md: nothing to change, every reference is already relative")
        if missing:
            print(f"github_md: {missing} reference(s) resolve to nothing")
        if unresolved:
            print(f"github_md: {unresolved} reference(s) point outside the tree")

    if args.write:
        return 1 if args.strict and (missing or unresolved) else 0
    if changed_files:
        return 1
    return 1 if args.strict and (missing or unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main())
