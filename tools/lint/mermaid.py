"""What can be said about a mermaid diagram, before and after drawing it.

Mermaid answers two questions badly. It fails to parse loudly, which is the one
case that needs no help, and it accepts everything else in silence:

    classDef a fill:#fff,font-wieght:bold     parsed, drawn, and bold is dropped
    X["x"]:::nosuch                           parsed, drawn, and no style applied
    A["a"] --> Misspelled["b"]                parsed, drawn, and both are right

So there are two passes here and the second is the one that catches the quiet
failures. The static pass reads the source, which needs no browser. Where the
source is a flowchart it can go further, and it does: it counts subgraph against
end, resolves every `:::` reference against the classes that were declared, and
notes a node whose first mention is outside the subgraph it is later used in,
because mermaid keeps such a node in the outer group however the drawing ends up
looking. The groups and the nodes are asked about only for that family, because
every other family writes `end` for blocks of its own and names its members in
notation of its own. The render pass draws the diagram in the mermaid
build already vendored in this repository and compares what was declared against
what came out, which is the only way to see a style property that never reached
the stylesheet.

The render pass is skipped, not failed, when there is no browser or no vendored
build to run. It says so in the findings.
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .findings import Finding, Severity

#: Where the vendored build lives, relative to the checkout this file is in.
BUNDLE_RELATIVE = Path("mermaid-viewer") / "vendor" / "mermaid.min.js"

#: How long a render is allowed to take before it is called a failure.
RENDER_TIMEOUT_SECONDS = 90

COMMENT = re.compile(r"%%[^\n]*")
QUOTED = re.compile(r'"[^"\n]*"')
EDGE_LABEL = re.compile(r"\|[^|\n]*\|")
CLASS_REFERENCE = re.compile(r":::([A-Za-z_][A-Za-z0-9_-]*)")
WORDS = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
CLASSDEF = re.compile(r"^classDef\s+([A-Za-z_][A-Za-z0-9_-]*)\s+(.*)$")
SUBGRAPH = re.compile(r"^subgraph\s+([^\s\[]+)?\s*\[?\s*\"?([^\"\]]*)\"?\]?\s*$")
PARSE_LINE = re.compile(r"on line (\d+)")
#: A classDef reaches the stylesheet as `#id .name>*{...}`, and the body is what
#: the declared property names are checked against.
SELECTOR = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)\s*>\s*\*?\s*\{([^}]*)\}")

#: Words that are mermaid's rather than a node's. The direction letters are not
#: here on purpose: `TB` is a direction after `graph`, and it is also the name of
#: a node in this repository's diagram, and a node wins that argument.
KEYWORDS = frozenset(
    {
        "graph", "flowchart", "subgraph", "end", "classDef", "class", "style",
        "linkStyle", "click", "direction", "and", "default", "title",
        "accTitle", "accDescr",
    }
)

#: A graph header is a type and a direction, and it names no node.
HEADER = re.compile(r"^(graph|flowchart)\b.*$")

#: The family whose source is `subgraph` blocks closed by `end`, and whose node
#: ids can therefore be counted from the source. Every other family reuses the
#: word end for blocks of its own - a sequence diagram closes each `alt` that way
#: - so counting `end` against `subgraph` in one of those reports a diagram that
#: draws correctly as one that cannot parse.
FLOWCHART_TYPES = frozenset({"graph", "flowchart"})

#: Statements whose line holds no node, only styling or a link.
NON_NODE_STATEMENTS = ("classDef", "class ", "style ", "linkStyle", "click ", "direction")

HARNESS = """<!doctype html>
<html><head><meta charset="utf-8"><title>lint</title></head><body>
<div id="host"></div>
<script src="__BUNDLE__"></script>
<script>
var SRC = __SOURCE__;
(function () {
  var out = {ok: false, error: null, style: "", nodes: [], clusters: [], edges: 0};
  function finish() { document.documentElement.setAttribute('data-mmd', JSON.stringify(out)); }
  function shortId(node) {
    return (node.id || '').replace(/^.*?-flowchart-/, '').replace(/-\\d+$/, '');
  }
  try {
    mermaid.initialize({startOnLoad: false, theme: 'default', securityLevel: 'strict'});
    mermaid.render('lint', SRC).then(function (res) {
      var host = document.getElementById('host');
      host.innerHTML = res.svg;
      var svg = host.querySelector('svg');
      var style = svg.querySelector('style');
      out.style = style ? style.textContent : '';
      svg.querySelectorAll('g.cluster').forEach(function (c) {
        out.clusters.push((c.textContent || '').trim().slice(0, 80));
      });
      svg.querySelectorAll('g.node').forEach(function (n) {
        var label = n.querySelector('.nodeLabel, .label, foreignObject, text');
        out.nodes.push({
          id: shortId(n),
          label: label ? (label.textContent || '').replace(/\\s+/g, ' ').trim() : '',
          classes: n.getAttribute('class') || ''
        });
      });
      out.edges = svg.querySelectorAll('g.flowchart-link').length;
      out.ok = true;
      finish();
    }).catch(function (err) {
      out.error = String((err && err.message) || err);
      finish();
    });
  } catch (e) {
    out.error = String((e && e.message) || e);
    finish();
  }
})();
</script>
</body></html>
"""


def bundle_path(explicit: str | None = None) -> Path | None:
    """The vendored mermaid build, from the argument, the environment, or here."""
    candidate = explicit or os.environ.get("MMD_BUNDLE")
    if candidate:
        path = Path(candidate).expanduser()
        return path if path.is_file() else None
    # tools/lint/mermaid.py -> tools/lint -> tools -> the checkout
    path = Path(__file__).resolve().parents[2] / BUNDLE_RELATIVE
    return path if path.is_file() else None


def find_chrome(explicit: str | None = None) -> str | None:
    """A chrome that can dump a DOM, wherever this machine keeps one.

    Nothing is hardcoded to one installation. The argument wins, then an
    environment override, then the browser playwright has already downloaded,
    then whatever is on the path. A machine with none of those gets the static
    pass and a note saying the render did not happen.
    """
    if explicit:
        return explicit if Path(explicit).is_file() else None
    for variable in ("MMD_CHROME", "CHROME_BIN"):
        value = os.environ.get(variable)
        if value and Path(value).is_file():
            return value
    roots = (
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    )
    for root in roots:
        if not root.is_dir():
            continue
        candidates = sorted(root.glob("chromium_headless_shell-*/*/chrome-headless-shell"))
        if candidates:
            return str(candidates[-1])
    for name in ("chrome-headless-shell", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def diagram_type(text: str) -> str:
    """The word a diagram's source opens with, past comments and front matter."""
    in_front_matter = False
    for raw in text.splitlines():
        line = COMMENT.sub("", raw).strip()
        if not line:
            continue
        if line == "---":
            in_front_matter = not in_front_matter
            continue
        if in_front_matter:
            continue
        words = line.split()
        return words[0].lower() if words else ""
    return ""


def is_flowchart(text: str) -> bool:
    """True when the source is the family the group and node rules were read off."""
    return diagram_type(text) in FLOWCHART_TYPES


def strip_line(line: str) -> str:
    """A source line with everything that is not an id taken out of it.

    A quoted label, an edge label and a class reference all hold words that look
    exactly like an id, so all three are removed before anything is counted.
    """
    line = COMMENT.sub("", line)
    line = EDGE_LABEL.sub(" ", line)
    line = QUOTED.sub(" ", line)
    return CLASS_REFERENCE.sub(" ", line)


def declared_classes(text: str) -> dict[str, list[str]]:
    """Every classDef name, and the style property names it declares."""
    classes: dict[str, list[str]] = {}
    for raw in text.splitlines():
        match = CLASSDEF.match(strip_line(raw).strip())
        if not match:
            continue
        properties = []
        for chunk in match.group(2).rstrip(";").split(","):
            if ":" in chunk:
                properties.append(chunk.split(":", 1)[0].strip())
        classes[match.group(1)] = properties
    return classes


def referenced_classes(text: str) -> set[str]:
    """Every class name an edge or node refers to with `:::`."""
    return set(CLASS_REFERENCE.findall(COMMENT.sub("", text)))


def declared_nodes(
    text: str,
) -> tuple[dict[str, tuple[str, ...]], list[tuple[str, tuple[str, ...], tuple[str, ...]]]]:
    """Every node id with the subgraph scope it was first seen in, plus scopes.

    The second value is one entry per node that is first named at the root level
    and then used inside a subgraph, which is the case mermaid resolves by
    keeping the node where it was first named. It is the direction that matters:
    a node declared inside a subgraph and used outside it afterwards is an edge
    leaving the group, which is the ordinary way to join one group to the next,
    and it is not reported.
    """
    scopes: dict[str, tuple[str, ...]] = {}
    indirect: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    clusters: set[str] = set()
    seen: dict[str, tuple[str, ...]] = {}
    stack: list[str] = []

    for raw in text.splitlines():
        line = strip_line(raw).strip()
        if not line:
            continue
        if HEADER.match(line):
            continue
        if line.startswith("subgraph"):
            match = SUBGRAPH.match(line)
            name = (match.group(1) or match.group(2) or "").strip() if match else "subgraph"
            stack.append(name)
            clusters.add(name)
            continue
        if line == "end":
            if stack:
                stack.pop()
            continue
        if line.startswith(NON_NODE_STATEMENTS):
            continue
        for token in WORDS.findall(line):
            if token in KEYWORDS or token in clusters:
                continue
            here = tuple(stack)
            if token not in seen:
                seen[token] = here
                scopes[token] = here
            elif seen[token] != here:
                if not seen[token] and here:
                    indirect.append((token, seen[token], here))
                seen[token] = here
    return scopes, indirect


def static_findings(text: str, path: str) -> list[Finding]:
    """Everything the source alone can settle.

    The group and node rules are read off the flowchart family, so they are
    asked only of a source in that family. A sequence, class, state or gantt
    diagram is answered for by the render pass, which is the pass that knows
    what its blocks mean.
    """
    findings: list[Finding] = []

    if is_flowchart(text):
        opens = len(re.findall(r"^\s*subgraph\b", text, flags=re.MULTILINE))
        closes = len(re.findall(r"^\s*end\s*$", text, flags=re.MULTILINE))
        if opens != closes:
            findings.append(
                Finding(
                    path,
                    Severity.ERROR,
                    "mmd-subgraph-unbalanced",
                    f"{opens} subgraph(s) but {closes} end(s), so the diagram cannot parse",
                    hint="every subgraph line needs a matching line that is only the word end",
                )
            )

    classes = declared_classes(text)
    referenced = referenced_classes(text)
    for name in sorted(referenced - classes.keys()):
        findings.append(
            Finding(
                path,
                Severity.WARNING,
                "mmd-class-undeclared",
                f"a node refers to class {name!r} and no classDef declares it",
                hint="mermaid draws the node unstyled and reports nothing",
            )
        )
    for name in sorted(classes.keys() - referenced):
        findings.append(
            Finding(
                path,
                Severity.NOTE,
                "mmd-class-unused",
                f"classDef {name!r} is declared and no node refers to it",
            )
        )

    scopes, indirect = declared_nodes(text) if is_flowchart(text) else ({}, [])
    for token, first, later in indirect:
        findings.append(
            Finding(
                path,
                Severity.NOTE,
                "mmd-scope-indirect",
                f"node {token!r} is first named outside {later[0]} and used inside it later, "
                f"so mermaid keeps it in {'/'.join(first) or 'the root group'}",
                hint="name the node inside the subgraph when the group it is drawn in is the intent",
            )
        )
    return findings


def render_findings(text: str, path: str, chrome: str, bundle: Path) -> list[Finding]:
    """Draw the diagram, then compare what was declared against what came out."""
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="lint-mmd-") as workdir:
        work = Path(workdir)
        page = work / "harness.html"
        page.write_text(
            HARNESS.replace("__BUNDLE__", bundle.resolve().as_uri()).replace(
                "__SOURCE__", json.dumps(text)
            ),
            encoding="utf-8",
        )
        command = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--allow-file-access-from-files",
            f"--user-data-dir={work / 'profile'}",
            f"--virtual-time-budget={RENDER_TIMEOUT_SECONDS * 100}",
            "--dump-dom",
            page.resolve().as_uri(),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=RENDER_TIMEOUT_SECONDS, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            findings.append(
                Finding(
                    path,
                    Severity.NOTE,
                    "mmd-render-unavailable",
                    f"the diagram was not drawn: {error}",
                    hint=f"the browser used was {chrome}",
                )
            )
            return findings

        stdout = result.stdout.decode("utf-8", "replace")
        match = re.search(r'data-mmd="([^"]*)"', stdout)
        if not match:
            findings.append(
                Finding(
                    path,
                    Severity.NOTE,
                    "mmd-render-unavailable",
                    "the browser returned a page with no result on it",
                    hint="run the same chrome by hand against the generated harness to see why",
                )
            )
            return findings

        payload = json.loads(html_module.unescape(match.group(1)))

    if not payload.get("ok"):
        error = str(payload.get("error") or "").strip()
        line_match = PARSE_LINE.search(error)
        expecting = ""
        if "Expecting" in error:
            expecting = " ".join(error.split("Expecting", 1)[1].split())[:120]
        findings.append(
            Finding(
                path,
                Severity.ERROR,
                "mmd-parse-failed",
                "mermaid could not parse the diagram: " + " ".join(error.split())[:200],
                line=int(line_match.group(1)) if line_match else None,
                hint=f"expecting {expecting}" if expecting else None,
            )
        )
        return findings

    style = payload.get("style", "")
    bodies = {name: body for name, body in SELECTOR.findall(style)}
    for name, properties in declared_classes(text).items():
        body = bodies.get(name)
        if body is None:
            continue
        missing = [prop for prop in properties if not re.search(rf"\b{re.escape(prop)}\s*:", body)]
        if missing:
            findings.append(
                Finding(
                    path,
                    Severity.WARNING,
                    "mmd-style-dropped",
                    f"classDef {name!r} declares {', '.join(missing)} and the stylesheet "
                    "does not carry it",
                    hint="mermaid ignores a style property it does not know, and says nothing",
                )
            )

    # Only a flowchart source names its nodes in a way this scan can follow, and
    # the harness only counts flowchart nodes, so the comparison is asked of a
    # flowchart and of nothing else.
    declared = set(declared_nodes(text)[0]) if is_flowchart(text) else set()
    drawn = {node["id"] for node in payload.get("nodes", [])} if is_flowchart(text) else set()
    for name in sorted(drawn - declared):
        findings.append(
            Finding(
                path,
                Severity.WARNING,
                "mmd-node-undeclared",
                f"the drawing has a node {name!r} that the source never names",
                hint="an id that differs by a character from the intended one draws a second node",
            )
        )
    for name in sorted(declared - drawn):
        findings.append(
            Finding(
                path,
                Severity.NOTE,
                "mmd-node-undrawn",
                f"the source names node {name!r} and the drawing has no node for it",
            )
        )
    return findings


def lint_source(text: str, path: str) -> list[Finding]:
    """The static pass on its own, which is what runs with no browser."""
    return static_findings(text, path)


def lint_render(text: str, path: str, chrome: str | None, bundle: Path | None) -> list[Finding]:
    """The render pass, or the note that explains why it did not happen."""
    if bundle is None:
        return [
            Finding(
                path,
                Severity.NOTE,
                "mmd-render-skipped",
                "the diagram was not drawn: no vendored mermaid build was found",
                hint="pass --bundle, or set MMD_BUNDLE, to the mermaid build to draw with",
            )
        ]
    if chrome is None:
        return [
            Finding(
                path,
                Severity.NOTE,
                "mmd-render-skipped",
                "the diagram was not drawn: no headless browser was found",
                hint="install one with `python3 -m playwright install chromium`, or set MMD_CHROME",
            )
        ]
    return render_findings(text, path, chrome, bundle)
