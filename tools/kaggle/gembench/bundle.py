"""Everything one benchmark run produced, in one file, with a link to take it away.

A hosted run ends when its session does and the files in it are gone. A run whose
result cannot be carried off the machine it ran on is not evidence, so the last
step of the notebook writes every file it produced into one zip and prints a
link to it: the runs, the comparison, the charts, the dataset, and a manifest
that names each member with its size and a digest, so a reader can tell what
they hold without unpacking it.

The link is produced by the notebook's own machinery when there is any, and the
path is printed when there is not, because a link that silently was not rendered
would read as a run that produced nothing. Nothing here reaches the network: the
zip is written beside the run and the link points at it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCHEMA_VERSION = "1"

DEFAULT_NAME = "benchmark-bundle.zip"

#: The manifest's own name. It is written fresh on every run and is carried in
#: the zip, but it is not one of the files it describes: a digest of the file
#: that holds the digests is a circle, so the manifest names every member but
#: itself and says so.
MANIFEST_NAME = "bundle.json"

#: Directories under the run that are not the run: a build byproduct and a
#: cache are never part of what a reader is being given.
SKIP_NAMES = {".DS_Store", "__pycache__"}


def digest(path: Path) -> str:
    """A short content digest, which is what tells two runs' charts apart."""
    body = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            body.update(block)
    return body.hexdigest()[:16]


def collect(out_dir: Path, target: Path) -> list[Path]:
    """Every file under *out_dir* that belongs in the bundle, the target zip and the manifest aside."""
    out_dir = Path(out_dir)
    target = Path(target)
    found: list[Path] = []
    for item in sorted(out_dir.rglob("*")):
        if not item.is_file():
            continue
        if item == target or item.suffix == ".zip":
            continue
        if item.name == MANIFEST_NAME:
            continue
        if any(part in SKIP_NAMES for part in item.parts):
            continue
        found.append(item)
    return found


def manifest(out_dir: Path, members: list[Path]) -> dict:
    """What the bundle holds, by name, by size and by digest."""
    out_dir = Path(out_dir)
    entries = []
    for member in members:
        entries.append(
            {
                "path": member.relative_to(out_dir).as_posix(),
                "bytes": member.stat().st_size,
                "sha256": digest(member),
            }
        )
    kinds: dict[str, int] = {}
    for entry in entries:
        top = entry["path"].split("/")[0]
        kinds[top] = kinds.get(top, 0) + 1
    return {
        "document": "benchmark-bundle",
        "schema_version": SCHEMA_VERSION,
        "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "manifest": MANIFEST_NAME,
        "members": entries,
        "totals": {
            "members": len(entries),
            "bytes": sum(entry["bytes"] for entry in entries),
        },
        "containing": {name: count for name, count in sorted(kinds.items())},
    }


def build(out_dir: Path, target: Path | None = None) -> dict:
    """Write one zip of everything under *out_dir*, and return what it holds.

    The manifest is written into the directory before the zip is made and is
    carried in the zip as a member, so the same account travels with the files
    and is readable without unpacking them. Its own size and digest are not in
    it, for the reason stated on :data:`MANIFEST_NAME`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = Path(target) if target is not None else out_dir / DEFAULT_NAME

    members = collect(out_dir, target)
    body = manifest(out_dir, members)
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            archive.write(member, member.relative_to(out_dir).as_posix())
        archive.write(manifest_path, MANIFEST_NAME)

    return {
        "path": str(target),
        "size": target.stat().st_size,
        "members": [entry["path"] for entry in body["members"]] + [MANIFEST_NAME],
        "counts": body["totals"],
        "containing": body["containing"],
        "manifest": str(manifest_path),
    }


def link(path: Path, label: str = "") -> str:
    """The HTML a notebook renders to offer the file, on a Kaggle page or anywhere else."""
    target = Path(path)
    name = label or target.name
    return (
        f'<a href="{target.as_uri()}" download="{target.name}" '
        f'style="border-radius:0;text-decoration:none;'
        f'font-family:Helvetica,Arial,sans-serif;font-size:14px;'
        f'padding:8px 12px;display:inline-block;background:#2f5d8a;color:#ffffff">'
        f"download {name}</a>"
    )


def show(path: Path, label: str = "") -> str:
    """Render a download link where there is a notebook, and report the file where there is not.

    The link is the notebook's own ``FileLink`` and not an anchor this module
    writes, because on a hosted page the file is served through the notebook's
    own route and a ``file://`` href would point at a path the reader's browser
    cannot reach. :func:`link` is kept for a page that holds the file beside it.
    """
    target = Path(path)
    size = target.stat().st_size if target.is_file() else 0
    text = f"{target} ({size / 1024:.1f} KiB)"
    try:
        from IPython.display import FileLink, display  # type: ignore
    except ImportError:
        return text
    display(FileLink(str(target), result_html_prefix=f"{label or target.name}: "))
    return text


def render(built: dict) -> str:
    lines = [
        f"Benchmark bundle: {built['path']}",
        f"  members   {built['counts']['members']}",
        f"  bytes     {built['counts']['bytes']}",
        "",
    ]
    for name, count in built["containing"].items():
        lines.append(f"    {name:<16} {count} file(s)")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gembench.bundle",
        description="Write one zip of everything a benchmark run produced.",
    )
    parser.add_argument("--dir", required=True, help="directory holding the run's files")
    parser.add_argument("--name", default=DEFAULT_NAME, help="name of the zip inside that directory")
    parser.add_argument("--json", action="store_true", help="print the manifest instead of the table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.dir).expanduser()
    if not out_dir.is_dir():
        print(f"{out_dir} is not a directory", file=sys.stderr)
        return 3
    built = build(out_dir, out_dir / args.name)
    if args.json:
        print(json.dumps(built, indent=2))
    else:
        print(render(built), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
