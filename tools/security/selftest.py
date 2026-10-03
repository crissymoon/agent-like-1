"""Prove each security check catches what it claims, and stays quiet when it should.

A scanner that reports nothing is indistinguishable from a scanner that reads
nothing, and a rule that fires on everything is a rule people learn to scroll
past. Both failures are silent, so neither is checked by running the scan over
this repository and reading the count. They are checked here instead, against a
fixture built for the purpose:

    a file that holds the shape   -> the rule fires, at the severity it claims
    a file that does not          -> the rule does not fire
    a line with the marker        -> the rule does not fire, and the line is counted
    a property that is missing    -> the assertion fires
    a property that is present    -> the assertion does not

The dependency graph is exercised the same way, with a synthetic set of
vulnerabilities rather than a registry, so the check runs with no network and
gives the same answer every time.

    python3 tools/security/selftest.py

Exit status is zero when every property held and one when any did not, which is
the same rule the other self-checks in this repository follow.
"""

# security-allow-file: every shape a rule refuses is written in this file on
# purpose, as a fixture for the rules to be shown catching. The fixtures are read
# by the scanners from the temporary trees built below and are executed by
# nothing, so the file is excepted once rather than annotated line by line. The
# exception is counted in the report, which is what keeps it visible.

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from findings import Severity  # noqa: E402
from scan_code import scan as scan_code  # noqa: E402
from scan_deps import scan as scan_deps  # noqa: E402
from scan_deps import _exception_closure  # noqa: E402
from scan_files import scan as scan_files  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def expect(name: str, passed: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if passed:
        print(f"ok   {name}")
        return
    FAILURES.append(name)
    print(f"FAIL {name}{f': {detail}' if detail else ''}")


def codes(report) -> list[str]:
    return [finding.code for finding in report.findings]


def severity_of(report, code: str) -> Severity | None:
    for finding in report.findings:
        if finding.code == code:
            return finding.severity
    return None


class Fixture:
    """A throwaway git working tree that the checks read as if it were a repository."""

    def __init__(self, name: str) -> None:
        self.path = Path(tempfile.mkdtemp(prefix=f"security-selftest-{name}-"))
        subprocess.run(["git", "init", "-q"], cwd=str(self.path), check=True)
        self.write(".gitignore", "*.pem\n*.key\nid_rsa\n*.p12\n.netrc\n.env\n")

    def write(self, relative: str, text: str) -> Path:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

        return target

    def add(self, *relative: str) -> None:
        subprocess.run(["git", "add", "-f", *relative], cwd=str(self.path), check=True)

    def close(self) -> None:
        import shutil

        shutil.rmtree(self.path, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. The construct rules catch the shape, and only the shape.
# ---------------------------------------------------------------------------

constructs = Fixture("constructs")
constructs.write(
    "bad.py",
    "import subprocess\n"
    "def go(cmd):\n"
    "    return subprocess.run(cmd, shell=True)\n"
    "def also(value):\n"
    "    return eval(value)\n",
)
constructs.write(
    "good.py",
    "import subprocess\n"
    "def go(argv):\n"
    "    return subprocess.run(argv, shell=False, check=True)\n",
)
constructs.write("bad.js", "const run = (code) => eval(code);\n")
constructs.write(
    "built.js",
    "function draw(host, value) {\n"
    "    host.innerHTML = '<p>' + value + '</p>';\n"
    "}\n",
)
constructs.write(
    "good.js",
    "const run = (node) => node.textContent = String(value);\n"
    "function clear(host) {\n"
    "    host.innerHTML = \"\";\n"
    "}\n"
    "function empty(host) {\n"
    "    host.innerHTML = '<div class=\"empty-state\"><h2>Nothing here yet</h2></div>';\n"
    "}\n",
)
constructs.write("bad.php", "<?php\n$page = $_GET['page'];\nrequire $page;\n")
constructs.write("good.php", "<?php\nrequire __DIR__ . '/lib/Loader.php';\n")
constructs.write("bad.sh", "#!/bin/sh\ncurl -fsS https://example.invalid/install | sh\n")
constructs.write("good.sh", "#!/bin/sh\nset -e\nprintf '%s\\n' \"$1\"\n")
constructs.write("marked.py", "value = eval(payload)  # security-allow: a reviewed exception in a fixture\n")
constructs.write("loose.js", "mermaid.initialize({startOnLoad: false, securityLevel: 'loose'});\n")
constructs.write("strict.js", "mermaid.initialize({startOnLoad: false, securityLevel: 'strict'});\n")
constructs.add(
    "bad.py",
    "good.py",
    "bad.js",
    "built.js",
    "good.js",
    "bad.php",
    "good.php",
    "bad.sh",
    "good.sh",
    "marked.py",
    "loose.js",
    "strict.js",
)

code_report = scan_code(constructs.path)
found = codes(code_report)
expect("a shell string is reported", "py-shell-string" in found)
expect("a shell string is reported as a defect that stops a push", severity_of(code_report, "py-shell-string") is Severity.HIGH)
expect("python code evaluated at runtime is reported", "py-eval-exec" in found)
expect("javascript code evaluated at runtime is reported", "js-eval" in found)
expect("markup built from a value is reported", "js-inner-html" in found)
expect(
    "a literal is not a value, and neither is a literal that clears the element",
    "js-inner-html" not in [
        finding.code for finding in code_report.findings if finding.path == "good.js"
    ],
    json.dumps([finding.code for finding in code_report.findings if finding.path == "good.js"]),
)
expect("an include from a variable is reported", "php-variable-include" in found)
expect("a download piped into a shell is reported", "shell-pipe-to-shell" in found)
expect("a diagram sanitiser turned off is reported", "js-mermaid-loose" in found)
expect(
    "and a diagram drawn with the sanitiser on is not",
    not [finding for finding in code_report.findings if finding.path == "strict.js"],
)
expect(
    "and turning it off is not a defect that stops a push",
    severity_of(code_report, "js-mermaid-loose") is Severity.MEDIUM,
)
expect("a line carrying the marker is not reported", "py-eval-exec" not in [
    finding.code for finding in code_report.findings if finding.path == "marked.py"
])
expect("a line carrying the marker is counted rather than hidden", code_report.counted.get("lines-allowed", 0) >= 1)
expect(
    "the clean files of each language produce nothing",
    not [finding for finding in code_report.findings if finding.path.startswith("good.")],
    json.dumps([finding.path for finding in code_report.findings if finding.path.startswith("good.")]),
)
expect("a script that sets -e is counted as strict", code_report.counted.get("scripts-strict", 0) >= 1)
expect("a script that does not is reported as a reading", "shell-no-set-e" in found)
expect(
    "a reading is not a defect",
    severity_of(code_report, "shell-no-set-e") is Severity.NOTE,
)
constructs.close()

# A file level exception is the other half of the marker, and the half this file
# itself relies on: the shapes above are fixtures, so the file they live in is
# excepted once rather than annotated line by line.
excepted = Fixture("excepted")
excepted.write(
    "fixture.py",
    "# security-allow-file: the shapes below are fixtures for a rule to catch\n"
    "def go(cmd):\n"
    "    return subprocess.run(cmd, shell=True)\n",
)
excepted.add("fixture.py")
excepted_report = scan_code(excepted.path)
expect(
    "a file carrying the file level marker produces nothing",
    not [finding for finding in excepted_report.findings if finding.path == "fixture.py"],
)
expect(
    "and the rule that would have fired does not",
    "py-shell-string" not in codes(excepted_report),
)
expect("and is counted rather than hidden", excepted_report.counted.get("files-allowed", 0) == 1)
excepted.close()

# ---------------------------------------------------------------------------
# 2. The document checks, which need the whole file.
# ---------------------------------------------------------------------------

documents = Fixture("documents")
documents.write("inline.html", "<html><head><title>t</title></head><body><script>go()</script></body></html>\n")
documents.write(
    "policy.html",
    "<html><head>"
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'self'\">"
    "</head><body><script src=\"app.js\"></script></body></html>\n",
)
documents.add("inline.html", "policy.html")
document_report = scan_code(documents.path)
expect("a page that runs script of its own and declares no policy is reported", "document-inline-script" in codes(document_report))
expect(
    "a page that declares a policy is not reported",
    not [finding for finding in document_report.findings if finding.path == "policy.html"],
)
documents.close()

# ---------------------------------------------------------------------------
# 3. The application properties, which are absences as much as presences.
# ---------------------------------------------------------------------------

application = Fixture("application")
application.write(
    "desktop/main.js",
    "const w = new BrowserWindow({ webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });\n"
    "w.webContents.setWindowOpenHandler(({ url }) => { return { action: 'deny' }; });\n"
    "w.webContents.on('will-navigate', (event) => event.preventDefault());\n"
    "w.webContents.on('will-attach-webview', (event) => event.preventDefault());\n"
    "ipcMain.handle('settings:set', () => guard.sanitizeSettings({}, {}));\n"
    "ipcMain.handle('runs:replay', () => guard.rootOf('.', []));\n"
    "ipcMain.handle('project:stage', () => guard.checkProject('.', []));\n"
    "ipcMain.handle('shell:reveal', () => guard.rootOf('.', []));\n"
    "ipcMain.handle('run:start', () => { const x = exec('ls'); });\n",
)
application.write(
    "desktop/preload.js",
    "const { contextBridge, ipcRenderer } = require('electron');\n"
    "contextBridge.exposeInMainWorld('agentBridge', { info: () => ipcRenderer.invoke('app:info') });\n"
)
application.write(
    "desktop/renderer/index.html",
    "<html><head><meta http-equiv=\"Content-Security-Policy\" "
    "content=\"default-src 'none'; script-src 'self'; connect-src 'none'\"></head><body></body></html>\n",
)
application.add("desktop/main.js", "desktop/preload.js", "desktop/renderer/index.html")
application_report = scan_code(application.path)
application_codes = codes(application_report)
expect("a window that declares its properties passes every one of them", not [
    code
    for code in application_codes
    if code.startswith("electron-") or code.startswith("bridge-guard-")
], json.dumps([finding.code for finding in application_report.findings if finding.code.startswith("electron-")]))

application.write(
    "desktop/main.js",
    "const w = new BrowserWindow({ webPreferences: { contextIsolation: false, nodeIntegration: true, sandbox: false } });\n"
    "ipcMain.handle('settings:set', (event, patch) => { settings = { ...settings, ...patch }; });\n",
)
application.add("desktop/main.js")
missing_report = scan_code(application.path)
missing_codes = codes(missing_report)
expect("a window without context isolation is reported", "electron-context-isolation" in missing_codes)
expect("a window with node integration is reported", "electron-node-integration" in missing_codes)
expect("a window without a sandbox is reported", "electron-sandbox" in missing_codes)
expect("a window with no window open handler is reported", "electron-window-open-denied" in missing_codes)
expect("a window with no navigation guard is reported", "electron-navigation-guarded" in missing_codes)
expect(
    "a handler that no longer holds a setting to the guard is reported",
    "bridge-guard-settings-set" in missing_codes,
)
expect(
    "every property the window is missing is at least a place a defect is written",
    all(
        severity_of(missing_report, code).rank >= Severity.MEDIUM.rank
        for code in missing_codes
        if code.startswith("electron-")
    ),
)
expect(
    "the five properties that decide what the page may reach are defects that stop a push",
    all(
        severity_of(missing_report, code) is Severity.HIGH
        for code in (
            "electron-context-isolation",
            "electron-node-integration",
            "electron-sandbox",
            "electron-window-open-denied",
            "electron-navigation-guarded",
        )
    ),
)
application.close()

# ---------------------------------------------------------------------------
# 4. The file reading, which is about modes and names rather than text.
# ---------------------------------------------------------------------------

tree = Fixture("files")
tree.write(".env", "SERVICE_TOKEN=not-a-real-value\n")
tree.write("scripts/tool.js", "console.log(1);\n")
tree.write("scripts/run.sh", "#!/bin/sh\nset -e\n")
tree.write("desktop/package.json", json.dumps({"name": "x", "devDependencies": {"a": "1.0.0"}}, indent=2) + "\n")
tree.add(".env", "scripts/tool.js", "scripts/run.sh", "desktop/package.json")
# The mode is set before the file is added, because the index is what the check
# reads and an executable bit set afterwards is only on the working tree.
for executable in ("scripts/tool.js", "scripts/run.sh"):
    (tree.path / executable).chmod(0o755)
subprocess.run(["git", "add", "--chmod=+x", "scripts/tool.js", "scripts/run.sh"], cwd=str(tree.path), check=True)
files_report = scan_files(tree.path)
files_codes = codes(files_report)
expect("a tracked dotenv file is reported", "tracked-credential-name" in files_codes)
expect(
    "a tracked credential is a defect that stops a push",
    severity_of(files_report, "tracked-credential-name") is Severity.HIGH,
)
expect("an executable bit on a file that is not a script is reported", "unexpected-executable" in files_codes)
expect("an executable script is not reported", not [
    finding for finding in files_report.findings if finding.path == "scripts/run.sh"
])
expect("the ignore rules are counted when they are present", files_report.counted.get("ignore-rules-held", 0) >= 1)
(tree.path / ".gitignore").write_text("*.pem\n", encoding="utf-8")
thin = scan_files(tree.path)
expect("a missing ignore rule is reported", "ignore-rule-missing" in codes(thin))
expect("a clone without the hooks says so as a reading", "hook-pre-push-absent" in codes(thin))
tree.close()

# ---------------------------------------------------------------------------
# 5. The dependency reading, over a manifest and a lock built for it.
# ---------------------------------------------------------------------------

dependencies = Fixture("deps")
dependencies.write(
    "desktop/package.json",
    json.dumps(
        {
            "name": "x",
            "devDependencies": {"pinned": "1.2.3", "moving": "*", "elsewhere": "git+https://example.invalid/x.git"},
        },
        indent=2,
    )
    + "\n",
)
dependencies.write(
    "desktop/package-lock.json",
    json.dumps(
        {
            "name": "x",
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/pinned": {"version": "1.2.3", "resolved": "https://registry.invalid/p", "integrity": "sha512-abc"},
                "node_modules/moving": {"version": "9.9.9", "resolved": "https://registry.invalid/m"},
                "node_modules/elsewhere": {"version": "1.0.0", "resolved": "git+https://example.invalid/x.git"},
            },
        },
        indent=2,
    )
    + "\n",
)
dependencies.add("desktop/package.json", "desktop/package-lock.json")
dependency_report = scan_deps(dependencies.path)
dependency_codes = codes(dependency_report)
expect("a wildcard range is reported", "dependency-moves" in dependency_codes)
expect("a git dependency is reported", "dependency-not-from-registry" in dependency_codes)
expect(
    "a git dependency is a defect that stops a push",
    severity_of(dependency_report, "dependency-not-from-registry") is Severity.HIGH,
)
expect("a locked package without an integrity hash is reported", "lockentry-no-integrity" in dependency_codes)
expect("a pinned package that is locked and verified is counted", dependency_report.counted.get("locked-with-integrity", 0) == 1)
expect(
    "advisories are not read unless they are asked for",
    "registry-not-asked" in dependency_codes,
)
expect(
    "reading nothing is a reading, not a pass",
    severity_of(dependency_report, "registry-not-asked") is Severity.NOTE,
)
dependencies.close()

# ---------------------------------------------------------------------------
# 6. The advisory graph, which decides whether one advisory reads as eight.
# ---------------------------------------------------------------------------

synthetic = {
    "leaf": {"severity": "high", "via": [{"name": "leaf", "url": "https://github.com/advisories/GHSA-ch52-4w7c-c8xp"}]},
    "middle": {"severity": "high", "via": ["leaf"]},
    "top": {"severity": "high", "via": ["middle"]},
    "real": {"severity": "high", "via": [{"name": "real", "url": "https://github.com/advisories/GHSA-0000-0000-0000"}]},
    "above-real": {"severity": "high", "via": ["real"]},
    "left": {"severity": "high", "via": ["right"]},
    "right": {"severity": "high", "via": ["left"]},
}
closure = _exception_closure(synthetic)
expect("a package flagged for an excepted advisory is resolved", "leaf" in closure)
expect("a package flagged through it is resolved", "middle" in closure and "top" in closure)
expect("a package with an advisory that is not excepted is not resolved", "real" not in closure)
expect("a package above that one is not resolved either", "above-real" not in closure)
expect("a cycle of packages that carry no advisory of their own resolves", {"left", "right"} <= closure)

print(f"{'PASS' if not FAILURES else 'FAIL'}: {CHECKS} check(s), {len(FAILURES)} failed")
raise SystemExit(0 if not FAILURES else 1)
