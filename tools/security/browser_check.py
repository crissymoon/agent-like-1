"""Read the published surfaces the way a browser reads them.

    python3 tools/security/browser_check.py --surface all
    python3 tools/security/browser_check.py --surface pages --json results/security/surfaces.json
    CHROME=/path/to/chrome python3 tools/security/browser_check.py --surface viewer

The source scanner reads a page as a file: whether it runs script of its own,
whether it declares a policy, whether the markup it assigns is a literal. Three
of those are questions about text. The question this answers is the one the text
cannot: does the page still do what it did, in a browser, under its own policy.

That matters because the two changes that made these pages safe are the two that
can silently break them. A policy can refuse the very script the page depends
on, and a sanitiser turned on can strip the labels out of the drawing it was
asked to render. A page that fails either way is a clean file on disk. So the
page is served, opened in a browser that is asked to log, and read back:

    pages     the article list renders the first entry of its own manifest,
              which is a fetch and a draw the policy had to allow, and each
              page's own script changed the document it was loaded into;
    viewer    a diagram is drawn as an svg carrying text from the diagram
              source, and the browser logged no refusal and no removal.

Nothing here names a page, a class or a title. The pages are whatever the served
directory holds, the script each page declares is discovered from the markup,
the manifest is discovered as a json file holding titled entries, and the
diagram is the one the viewer's own manifest opens first. A page added to either
directory is read without this file being edited.

Exit status: zero when every reading passed, one when a reading failed, and two
when a reading could not be taken at all, which is what a missing browser is.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]

#: Each surface is a name and the directory its pages are served from, relative
#: to the repository root. A surface is a directory rather than a page, so what
#: is inside it is discovered rather than listed.
SURFACES: dict[str, str] = {
    "pages": "plans_and_writeups",
    "viewer": "mermaid-viewer",
}

#: A script the page declares. Only a same-origin one is fetched over the test
#: server, so an address with a scheme is read as a note rather than followed.
SCRIPT_SRC = re.compile(r"""<script[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

#: The class tokens a document carries, which is how a rendered page is told
#: from the file on disk: a script that ran added a token the file never had.
CLASS_ATTRIBUTE = re.compile(r"""\bclass\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

#: A word from a diagram source that a rendered drawing would have to carry.
#: Short words are dropped because they survive a wrong render as fragments.
DIAGRAM_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{5,}")

#: A browser log line that is a refusal rather than a warning: the policy
#: declined, or the sanitiser took something out of the drawing.
REFUSAL_LINE = re.compile(
    r"refused to (load|execute|apply|connect)|content security policy|"
    r"dompurify|removed (an|the) (element|node)",
    re.IGNORECASE,
)

#: How the browser is asked to read a page. The budget is the wall clock the
#: page is given to finish its fetch and its draw before the document is read.
CHROME_FLAGS = (
    "--headless",
    "--disable-gpu",
    "--no-sandbox",
    "--no-first-run",
    "--disable-dev-shm-usage",
    "--enable-logging=stderr",
    "--v=0",
)

#: The names a headless browser is installed under, in the order one is looked
#: for on PATH. The playwright cache is searched first, because that is where a
#: test runner puts one without touching the system.
PATH_CHROME = ("chrome-headless-shell", "chromium", "google-chrome", "chromium-browser")

#: The caches a browser is installed into on this platform and on Linux.
CACHE_ROOTS = (
    ("PLAYWRIGHT_BROWSERS_PATH",),
    ("Library", "Caches", "ms-playwright"),
    (".cache", "ms-playwright"),
    (".cache", "chromium"),
)


@dataclass
class Reading:
    """One thing read from a page, and what it settles."""

    surface: str
    subject: str
    code: str
    ok: bool
    message: str
    detail: str = ""

    def render(self) -> str:
        head = "ok  " if self.ok else "FAIL"
        text = f"{head} {self.surface}/{self.subject}  [{self.code}]  {self.message}"
        if self.detail:
            text += f"\n     {self.detail}"
        return text


@dataclass
class Surface:
    """A surface, its directory, and the readings taken from it."""

    name: str
    directory: Path
    readings: list[Reading] = field(default_factory=list)

    def add(self, subject: str, code: str, ok: bool, message: str, detail: str = "") -> None:
        self.readings.append(Reading(self.name, subject, code, ok, message, detail))


class ReadingUnavailable(RuntimeError):
    """A reading could not be taken, which is not the same as one that failed."""


def resolve_surface(root: Path, name: str) -> Surface:
    """The directory a surface is served from, refused when it is not there."""
    relative = SURFACES[name]
    directory = root / relative
    if not directory.is_dir():
        raise ReadingUnavailable(f"{name}: {relative} is not a directory in this checkout")

    return Surface(name, directory)


def find_chrome(explicit: str | None) -> Path:
    """The browser to open the pages with, or a refusal naming where it looked.

    The order is the caller's argument, then the environment, then the caches a
    test runner installs into, then PATH. A checkout is not the right place to
    record the path to a browser: it differs by machine, and a recorded one goes
    stale silently, so the same search runs everywhere and reports what it read.
    """
    looked: list[str] = []
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        looked.append(f"the argument {candidate}")

    from_environment = os.environ.get("CHROME")
    if from_environment:
        candidate = Path(from_environment).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        looked.append(f"CHROME={candidate}")

    for parts in CACHE_ROOTS:
        if parts[0] == "PLAYWRIGHT_BROWSERS_PATH":
            value = os.environ.get(parts[0])
            if not value:
                continue
            roots = [Path(value).expanduser()]
        else:
            roots = [Path.home().joinpath(*parts)]
        for cache in roots:
            if not cache.is_dir():
                continue
            for pattern in ("**/chrome-headless-shell", "**/Chromium", "**/chrome"):
                for found in sorted(cache.glob(pattern)):
                    if found.is_file() and os.access(found, os.X_OK):
                        return found
            looked.append(str(cache))

    for name in PATH_CHROME:
        found = shutil.which(name)
        if found:
            return Path(found)
    looked.extend(PATH_CHROME)

    raise ReadingUnavailable(
        "no headless browser was found; pass --chrome, set CHROME, or install one. "
        "Looked at: " + ", ".join(looked)
    )


class StaticServer:
    """A static server over one directory, on a port the kernel chooses.

    The directory is recorded rather than a port. A fixed port is a fact about
    the machine the reading was first taken on, and two readings at once would
    fight over it, so the socket is bound to port zero and the port it was given
    is read back. Every response is recorded with its status, because whether a
    page's own script was served is a question the page cannot answer.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.requests: list[tuple[str, int]] = []
        handler = functools.partial(_QuietHandler, directory=str(directory))
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.requests = self.requests  # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def url(self, relative: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/{relative.lstrip('/')}"

    def status_for(self, path: str) -> int | None:
        """The status one path was answered with, or None when it was not asked for."""
        wanted = "/" + path.lstrip("/")
        for asked, status in reversed(self.requests):
            if asked.split("?")[0] == wanted:
                return status
        return None

    def __enter__(self) -> "StaticServer":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """A handler that records what it answered and prints nothing."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def log_error(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def send_response(self, code: int, message: str | None = None) -> None:
        requests = getattr(self.server, "requests", None)
        if requests is not None:
            requests.append((self.path, code))
        super().send_response(code, message)


class Browser:
    """A headless browser, asked for the document it ended up with and its log."""

    def __init__(self, binary: Path, scratch: Path, budget: int) -> None:
        self.binary = binary
        self.scratch = scratch
        self.budget = budget
        self._opened = 0

    def open(self, url: str, tag: str) -> tuple[str, str]:
        """The rendered document and the browser's own log for one address."""
        self._opened += 1
        profile = self.scratch / f"profile-{self._opened:02d}-{tag}"
        command = [
            str(self.binary),
            *CHROME_FLAGS,
            f"--user-data-dir={profile}",
            f"--virtual-time-budget={self.budget}",
            "--dump-dom",
            url,
        ]
        finished = subprocess.run(command, capture_output=True, text=True, check=False)
        if finished.returncode != 0 and not finished.stdout.strip():
            raise ReadingUnavailable(
                f"the browser exited {finished.returncode} on {url}: "
                + (finished.stderr.strip().splitlines() or ["no output"])[-1]
            )

        return finished.stdout, finished.stderr


def resolved(page: str, target: str) -> str:
    """The path a script is asked for at, taken from the page that names it.

    A page in a subdirectory asks for `assets/x.js` and the request that arrives
    is `pages/assets/x.js`, so the question is settled against the request rather
    than against the string in the markup.
    """
    base = posixpath.dirname(page)

    return posixpath.normpath(posixpath.join(base, target))


def declared_scripts(page: Path) -> list[str]:
    """The same-origin scripts a page declares, in the order it declares them."""
    found: list[str] = []
    for match in SCRIPT_SRC.finditer(page.read_text(encoding="utf-8", errors="replace")):
        target = match.group(1).strip()
        if not target or "//" in target or target.startswith(("data:", "mailto:")):
            continue
        found.append(target)

    return found


def class_tokens(text: str) -> set[str]:
    """Every class token a document carries."""
    tokens: set[str] = set()
    for match in CLASS_ATTRIBUTE.finditer(text):
        tokens.update(match.group(1).split())

    return tokens


def manifests(directory: Path) -> list[tuple[Path, dict]]:
    """The json files that are an index of titled entries, with their first entry.

    A listing is discovered by its shape rather than by its name, so a manifest
    that is renamed or a second one that is added is still read.
    """
    found: list[tuple[Path, dict]] = []
    for path in sorted(directory.rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(document, list) and document and isinstance(document[0], dict):
            if "title" in document[0]:
                found.append((path, document[0]))

    return found


def read_pages(surface: Surface, server: StaticServer, browser: Browser) -> None:
    """Every page in the directory, as the browser renders it under its policy."""
    pages = sorted(surface.directory.rglob("*.html"))
    if not pages:
        raise ReadingUnavailable(f"{surface.name}: no page was found under {surface.directory}")

    served_manifests = manifests(surface.directory)
    for page in pages:
        relative = page.relative_to(surface.directory).as_posix()
        dom, log = browser.open(server.url(relative), page.stem[:24])
        source = page.read_text(encoding="utf-8", errors="replace")
        scripts = declared_scripts(page)

        answered = [(name, server.status_for(resolved(relative, name))) for name in scripts]
        missing = [name for name, status in answered if status != 200]
        if not scripts:
            told = "no script is declared, so no fetch was needed"
        elif missing:
            told = f"{len(missing)} of {len(scripts)} declared script(s) were not answered"
        else:
            told = f"{len(scripts)} script(s) declared, every one answered"
        surface.add(
            relative,
            "page-script-served",
            not missing,
            told,
            (
                f"not answered: {', '.join(missing)}"
                if missing
                else ", ".join(f"{name} {status}" for name, status in answered)
            ),
        )

        refusals = [line for line in log.splitlines() if REFUSAL_LINE.search(line)]
        surface.add(
            relative,
            "page-policy-refusal",
            not refusals,
            "the browser logged no refusal and removed nothing" if not refusals else "the page was refused",
            refusals[0].strip() if refusals else "",
        )

        mutating = [
            name
            for name in scripts
            if (page.parent / name).is_file()
            and re.search(
                r"classList\s*\.\s*(add|remove|toggle)|className\s*=",
                (page.parent / name).read_text(encoding="utf-8", errors="replace"),
            )
        ]
        if not mutating:
            surface.add(
                relative,
                "page-script-ran",
                True,
                "no declared script claims to change a class, so a class is not evidence here",
                "skipped rather than passed",
            )
        else:
            added = class_tokens(dom) - class_tokens(source)
            surface.add(
                relative,
                "page-script-ran",
                bool(added),
                (
                    f"the page is not the file: {', '.join(sorted(added))}"
                    if added
                    else "the rendered page carries no class the file did not, so no script ran"
                ),
                ", ".join(mutating),
            )

        for manifest_path, first in served_manifests:
            named_by = [
                name
                for name in scripts
                if (page.parent / name).is_file()
                and manifest_path.name
                in (page.parent / name).read_text(encoding="utf-8", errors="replace")
            ]
            if not named_by:
                continue
            title = str(first["title"])
            surface.add(
                relative,
                "page-data-rendered",
                title in dom,
                (
                    f"the page drew the first entry of {manifest_path.name} ({title!r})"
                    if title in dom
                    else f"the page did not draw {title!r} from {manifest_path.name}"
                ),
                ", ".join(named_by),
            )


def read_viewer(surface: Surface, server: StaticServer, browser: Browser) -> None:
    """The viewer, opened the way it opens itself, and read back."""
    pages = sorted(surface.directory.glob("*.html"))
    if not pages:
        raise ReadingUnavailable(f"{surface.name}: no page was found under {surface.directory}")

    page = pages[0]
    dom, log = browser.open(server.url(page.name), page.stem[:24])

    scripts = declared_scripts(page)
    missing = [name for name in scripts if server.status_for(resolved(page.name, name)) != 200]
    if not scripts:
        told = "no script is declared"
    elif missing:
        told = f"{len(missing)} of {len(scripts)} declared script(s) were not answered"
    else:
        told = f"{len(scripts)} script(s) declared, every one answered"
    surface.add(
        page.name,
        "viewer-script-served",
        not missing,
        told,
        f"not answered: {', '.join(missing)}" if missing else ", ".join(scripts),
    )

    drawn = dom.count("<svg")
    surface.add(
        page.name,
        "viewer-drawn",
        drawn > 0,
        f"{drawn} svg drawing(s) on the page" if drawn else "nothing was drawn",
        "",
    )

    listing = surface.directory / "sources.json"
    first_diagram = ""
    words: list[str] = []
    if listing.is_file():
        try:
            entries = json.loads(listing.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            entries = []
        if isinstance(entries, list) and entries:
            first_diagram = str(entries[0])
    diagram = (surface.directory / first_diagram) if first_diagram else None
    if diagram is not None and diagram.is_file():
        words = sorted(set(DIAGRAM_WORD.findall(diagram.read_text(encoding="utf-8", errors="replace"))))
    carried = [word for word in words if word in dom]
    if not words:
        surface.add(
            page.name,
            "viewer-content",
            True,
            "no diagram listing and no readable diagram, so the drawing was not read for text",
            "skipped rather than passed",
        )
    else:
        surface.add(
            page.name,
            "viewer-content",
            bool(carried),
            (
                f"the drawing carries text from {first_diagram} ({', '.join(carried[:3])})"
                if carried
                else f"no text from {first_diagram} reached the drawing"
            ),
            f"{len(words)} word(s) looked for",
        )

    refusals = [line for line in log.splitlines() if REFUSAL_LINE.search(line)]
    surface.add(
        page.name,
        "viewer-refusal",
        not refusals,
        "the browser logged no refusal and stripped no element" if not refusals else "the drawing was refused",
        refusals[0].strip() if refusals else "",
    )

    if first_diagram:
        surface.add(
            page.name,
            "viewer-source-read",
            True,
            f"the viewer's listing opens {first_diagram} first, which is what was read",
            "",
        )


def read_surface(name: str, root: Path, chrome: Path, scratch: Path, budget: int) -> Surface:
    """Serve one surface and read everything it holds."""
    surface = resolve_surface(root, name)
    browser = Browser(chrome, scratch, budget)
    with StaticServer(surface.directory) as server:
        if name == "viewer":
            read_viewer(surface, server, browser)
        else:
            read_pages(surface, server, browser)

    return surface


def readings_of(root: Path, chrome: Path, scratch: Path, budget: int, only: str | None = None) -> list[Reading]:
    """Every reading of every surface, or of one of them."""
    collected: list[Reading] = []
    for name in sorted(SURFACES):
        if only and name != only:
            continue
        try:
            collected.extend(read_surface(name, root, chrome, scratch, budget).readings)
        except ReadingUnavailable as error:
            collected.append(Reading(name, "surface", "surface-present", False, str(error)))

    return collected


def self_test(root: Path, chrome: Path, scratch: Path, budget: int) -> list[Reading]:
    """The check's own controls, run rather than asserted in prose.

    A check that reports nothing and a check that reads nothing print the same
    thing, so before the repository is read with this, a copy of the published
    surfaces is read three ways. It has to pass clean, and each of the three
    defects has to be found: a policy that refuses the script its own page
    depends on, a script the page declares that is not there, and a listing that
    is absent, which is a reading that could not be taken rather than a pass.
    """
    fixture = scratch / "selftest"
    readings: list[Reading] = []

    def fresh() -> Path:
        if fixture.exists():
            shutil.rmtree(fixture)
        fixture.mkdir(parents=True)
        for relative in SURFACES.values():
            source = root / relative
            if source.is_dir():
                shutil.copytree(source, fixture / relative)

        return fixture

    def verdict(case: str, ok: bool, message: str, detail: str = "") -> None:
        readings.append(Reading("selftest", case, "selftest-case", ok, message, detail))

    def failed(readings_taken: list[Reading], code: str) -> list[Reading]:
        return [item for item in readings_taken if item.code == code and not item.ok]

    # The control. A fixture that fails before it is doctored makes every other
    # line here say nothing.
    clean = readings_of(fresh(), chrome, scratch, budget)
    broken = [item for item in clean if not item.ok]
    verdict(
        "clean",
        not broken,
        "the surfaces pass before they are doctored" if not broken else "the fixture does not pass",
        "; ".join(f"{item.subject} {item.code}" for item in broken),
    )

    # A policy that refuses the very script the page loads. The page still parses
    # and its source still reads clean, which is why the file reading cannot see
    # this one.
    for page in sorted(fresh().joinpath(SURFACES["pages"]).rglob("*.html")):
        text = page.read_text(encoding="utf-8", errors="replace")
        page.write_text(text.replace("script-src 'self'", "script-src 'none'"), encoding="utf-8")
    refused = readings_of(fixture, chrome, scratch, budget, only="pages")
    found = failed(refused, "page-policy-refusal")
    verdict(
        "policy-refuses",
        bool(found),
        (
            f"a policy that refuses its own script is reported ({len(found)} page(s))"
            if found
            else "a policy that refuses its own script was read as clean"
        ),
        found[0].detail if found else "",
    )

    # A script the page declares that is not served. The page renders as a
    # skeleton and the browser logs nothing, so the absence is the only reading.
    absent = ""
    for page in sorted(fresh().joinpath(SURFACES["pages"]).rglob("*.html")):
        for name in declared_scripts(page):
            target = page.parent / name
            if target.is_file():
                target.rename(target.with_suffix(target.suffix + ".moved"))
                absent = name
                break
        if absent:
            break
    missing = failed(readings_of(fixture, chrome, scratch, budget, only="pages"), "page-script-served")
    verdict(
        "script-absent",
        bool(missing),
        (
            f"a declared script that is not served is reported ({absent})"
            if missing
            else "a declared script that is not served was read as clean"
        ),
        missing[0].detail if missing else "",
    )

    # A listing that is absent. The drawing cannot be read for text, so the
    # reading is skipped and says so rather than passing.
    listing = fresh() / SURFACES["viewer"] / "sources.json"
    if listing.is_file():
        listing.unlink()
    viewer = readings_of(fixture, chrome, scratch, budget, only="viewer")
    skipped = [
        item for item in viewer if item.code == "viewer-content" and "skipped" in item.detail
    ]
    verdict(
        "listing-absent",
        bool(skipped),
        (
            "a listing that is absent is skipped, not passed"
            if skipped
            else "a listing that is absent was read as a pass"
        ),
        skipped[0].message if skipped else "; ".join(item.code for item in viewer),
    )

    return readings


def render(readings: list[Reading]) -> list[str]:
    """One line per reading, in the order they were taken."""
    return [reading.render() for reading in readings]


def document(surfaces: list[Surface]) -> dict:
    """The readings as a json document, in the shape the other checks write."""
    checks = [asdict(reading) for surface in surfaces for reading in surface.readings]

    return {
        "schema_version": "1",
        "document": "browser-check",
        "clean": all(check["ok"] for check in checks),
        "surfaces": [surface.name for surface in surfaces],
        "counts": {
            "checks": len(checks),
            "failed": sum(1 for check in checks if not check["ok"]),
            "skipped": sum(1 for check in checks if "skipped rather than passed" in check["detail"]),
        },
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="browser_check",
        description="Serve the published surfaces and read them as a browser reads them.",
    )
    parser.add_argument(
        "--surface",
        default="all",
        choices=("all", *sorted(SURFACES)),
        help="which published surface to read, all of them by default",
    )
    parser.add_argument("--root", default=str(PROJECT), help="the checkout to read, this one by default")
    parser.add_argument("--chrome", default=None, help="the browser to open the pages with")
    parser.add_argument("--json", default="", help="write the readings to this file as json")
    parser.add_argument("--budget", type=int, default=25000, help="milliseconds a page is given to finish")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="doctor a copy of the surfaces and check that each defect is found",
    )
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory and print its path")
    parser.add_argument("--quiet", action="store_true", help="print the verdict only")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    try:
        chrome = find_chrome(args.chrome)
    except ReadingUnavailable as error:
        print(f"browser-check: {error}", file=sys.stderr)
        return 2

    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / f"browser-check-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)

    wanted = sorted(SURFACES) if args.surface == "all" else [args.surface]
    if args.selftest:
        readings = self_test(root, chrome, scratch, args.budget)
        if not args.quiet:
            print(f"browser     {chrome}")
            for line in render(readings):
                print(line)
            print()
        failed = [reading for reading in readings if not reading.ok]
        if args.keep:
            print(f"     scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
        print(f"\nbrowser-check: {len(readings)} control(s), {len(failed)} not found")
        return 1 if failed else 0

    surfaces: list[Surface] = []
    try:
        for name in wanted:
            surfaces.append(read_surface(name, root, chrome, scratch, args.budget))
    except ReadingUnavailable as error:
        print(f"browser-check: {error}", file=sys.stderr)
        return 2

    readings = [reading for surface in surfaces for reading in surface.readings]
    if not args.quiet:
        print(f"browser     {chrome}")
        for line in render(readings):
            print(line)
        print()

    failed = [reading for reading in readings if not reading.ok]
    if not args.quiet:
        for surface in surfaces:
            names = len(surface.readings)
            bad = sum(1 for reading in surface.readings if not reading.ok)
            print(f"     {surface.name}: {names} reading(s), {bad} failed, in {SURFACES[surface.name]}")

    if args.json:
        report = Path(args.json)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(document(surfaces), indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"     report: {report}")

    if args.keep:
        print(f"     scratch kept: {scratch}")
    else:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\nbrowser-check: {len(readings)} reading(s), {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
