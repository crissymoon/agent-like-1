"""The constructs a security reading looks for, and why each one is one.

A rule here is a claim about a line of source: this shape is a place where a
defect is written, or a place where a mistake becomes a breach. Every rule
carries the reason it exists, because a rule whose reason is lost is a rule
somebody deletes, and it carries the way out - either what to use instead, or the
marker that records a reviewed exception.

Two severities do the work. `high` is a defect that should stop a push: either it
is exploitable as read, or it is a property the code claims and does not have.
`medium` is a place that is safe only because of something nearby, which is worth
a look because it is where the next defect will be written.

Every rule is a reading of text and not a proof about behaviour. A pattern that
cannot see reachability will always mark some correct code, which is why an
exception is a first class part of the format rather than a change to the rule:

    a line carrying `security-allow: <reason>` is not reported
    a file carrying `security-allow-file: <reason>` is read but not reported

Both are counted in the report. An exception that is not counted is an exception
nobody reviews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from findings import Severity

#: The marker that records a reviewed exception, and the one that covers a file.
ALLOW_MARKER = "security-allow:"
ALLOW_FILE_MARKER = "security-allow-file:"

#: Path fragments that mean somebody else's build output. It is parsed nowhere
#: here, for the same reason the syntax preflight does not parse it: a rule
#: written for this repository has no meaning for a vendored bundle.
VENDOR_MARKERS = ("/vendor/", "/node_modules/", "/site-packages/", "/.venv/", "/venv_")

#: Suffix to language. A file of a language that is not here is not read.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".mjs", ".cjs"),
    "php": (".php",),
    "shell": (".sh",),
}


@dataclass(frozen=True)
class Rule:
    """One shape, what it means, and when it does not apply."""

    code: str
    severity: Severity
    language: str
    pattern: re.Pattern[str]
    message: str
    hint: str = ""
    #: Substrings that must also appear on the same line for the rule to fire.
    also: tuple[str, ...] = ()
    #: Substrings whose presence on the line means the rule does not fire.
    unless: tuple[str, ...] = ()
    #: Extra file names this rule is limited to, when the pattern is not enough.
    only_paths: tuple[str, ...] = ()


def _rule(
    code: str,
    severity: Severity,
    language: str,
    pattern: str,
    message: str,
    hint: str = "",
    also: tuple[str, ...] = (),
    unless: tuple[str, ...] = (),
    only_paths: tuple[str, ...] = (),
) -> Rule:
    return Rule(
        code=code,
        severity=severity,
        language=language,
        pattern=re.compile(pattern),
        message=message,
        hint=hint,
        also=also,
        unless=unless,
        only_paths=only_paths,
    )


#: A request superglobal, which is the only thing in PHP that is a user input.
PHP_REQUEST = r"\$_(?:GET|POST|REQUEST|COOKIE|FILES|SERVER|ENV)"

RULES: tuple[Rule, ...] = (
    # ------------------------------------------------------------------ php
    _rule(
        "php-request-to-process",
        Severity.HIGH,
        "php",
        r"(?<![\w:>\\-])(?<!function\s)(exec|shell_exec|system|passthru|proc_open|popen|pcntl_exec)\s*\(",
        "a request value reaches a process start",
        "the command line is built from a request superglobal, so it is the caller's command",
        also=(PHP_REQUEST,),
    ),
    _rule(
        "php-process-start",
        Severity.MEDIUM,
        "php",
        r"(?<![\w:>\\-])(?<!function\s)(exec|shell_exec|system|passthru|proc_open|popen)\s*\(",
        "a process is started here",
        "check the argument is built from an allowlist rather than from anything a caller sent",
    ),
    _rule(
        "php-eval",
        Severity.HIGH,
        "php",
        r"\beval\s*\(",
        "eval evaluates whatever it is given as code",
    ),
    _rule(
        "php-unserialize",
        Severity.HIGH,
        "php",
        r"\bunserialize\s*\(",
        "unserialize runs a class's magic methods on data it was handed",
        "use json_decode for data that crosses a boundary",
    ),
    _rule(
        "php-variable-include",
        Severity.HIGH,
        "php",
        r"\b(include|include_once|require|require_once)\s*\(?\s*\$",
        "a file is included from a variable",
        "a variable include path is a local file inclusion the moment the variable is reachable",
    ),
    _rule(
        "php-dynamic-call",
        Severity.MEDIUM,
        "php",
        r"\bcall_user_func(?:_array)?\s*\(",
        "a function is called from a value",
    ),
    _rule(
        "php-request-to-file-write",
        Severity.MEDIUM,
        "php",
        r"\bfile_put_contents\s*\(",
        "a file is written here",
        "check the path is fixed and the size of what is written is bounded",
        also=(PHP_REQUEST,),
    ),
    _rule(
        "php-input-unbounded",
        Severity.MEDIUM,
        "php",
        r"file_get_contents\s*\(\s*['\"]php://input",
        "the whole request body is read into memory",
        "pass a length and offset so a caller cannot decide how much memory the endpoint spends",
    ),
    _rule(
        "php-header-from-request",
        Severity.HIGH,
        "php",
        r"\bheader\s*\(\s*(?:\"[^\"]*\"|'[^']*'|\$)",
        "a response header is built from a value",
        "a header built from input is a response splitting defect",
        also=(PHP_REQUEST,),
    ),
    # --------------------------------------------------------------- python
    _rule(
        "py-shell-string",
        Severity.HIGH,
        "python",
        r"shell\s*=\s*True",
        "a command is handed to a shell",
        "pass a list and shell=False; a string through a shell is a command injection",
    ),
    _rule(
        "py-eval-exec",
        Severity.MEDIUM,
        "python",
        r"(?<![\w.])(eval|exec)\s*\(",
        "code is evaluated from a value at runtime",
        "a literal argument is harmless; a value that arrived from anywhere else is not",
    ),
    _rule(
        "py-os-system",
        Severity.MEDIUM,
        "python",
        r"\bos\.system\s*\(",
        "a command is run through the shell",
        "use subprocess.run with a list",
    ),
    _rule(
        "py-pickle",
        Severity.HIGH,
        "python",
        r"\bpickle\.loads?\s*\(",
        "unpickling runs whatever the stream says to run",
        "use json for data that crosses a boundary",
    ),
    _rule(
        "py-yaml-unsafe",
        Severity.HIGH,
        "python",
        r"\byaml\.load\s*\(",
        "yaml.load without a safe loader constructs arbitrary objects",
        unless=("SafeLoader", "safe_load"),
    ),
    _rule(
        "py-tls-verify-off",
        Severity.HIGH,
        "python",
        r"\bverify\s*=\s*False",
        "certificate verification is switched off",
    ),
    _rule(
        "py-tempfile-mktemp",
        Severity.MEDIUM,
        "python",
        r"\btempfile\.mktemp\s*\(",
        "mktemp names a file without creating it",
        "use mkstemp or TemporaryDirectory",
    ),
    _rule(
        "py-http-url",
        Severity.LOW,
        "python",
        r"http://[A-Za-z0-9.-]+",
        "an address is written in the clear",
        "loopback and the compose service are the ones that are meant to be plain",
        unless=("127.0.0.1", "localhost", "0.0.0.0", "gemma:", "w3.org", "example.com", "schemas."),
    ),
    # ----------------------------------------------------------- javascript
    _rule(
        "js-eval",
        Severity.HIGH,
        "javascript",
        r"(?<![\w.])eval\s*\(",
        "eval evaluates whatever it is given as code",
    ),
    _rule(
        "js-new-function",
        Severity.MEDIUM,
        "javascript",
        r"\bnew\s+Function\s*\(",
        "a function is built from text",
    ),
    _rule(
        "js-inner-html",
        Severity.MEDIUM,
        "javascript",
        r"\.(innerHTML|outerHTML)\s*=",
        "markup is assigned as markup",
        "a value that is not markup this code produced is a cross site scripting defect; a cleared element is fine and is not a value at all",
        unless=('""', "''", "''", "empty", "clear", "` `"),
    ),
    _rule(
        "js-insert-adjacent-html",
        Severity.MEDIUM,
        "javascript",
        r"\binsertAdjacentHTML\s*\(",
        "markup is inserted as markup",
    ),
    _rule(
        "js-child-process",
        Severity.MEDIUM,
        "javascript",
        r"\b(exec|execSync|spawnSync|spawn)\s*\(",
        "a process is started here",
        "check the argument is a list of arguments and not a command line",
        also=("child_process",),
    ),
    _rule(
        "js-open-external",
        Severity.MEDIUM,
        "javascript",
        r"\bopenExternal\s*\(",
        "a shell hands a url to the operating system",
        "an address from a rendered document reaches the default handler, so it has to be a fixed one",
    ),
    _rule(
        "js-http-url",
        Severity.LOW,
        "javascript",
        r"http://[A-Za-z0-9.-]+",
        "an address is written in the clear",
        unless=("127.0.0.1", "localhost", "0.0.0.0", "w3.org", "gemma:", "schemas."),
    ),
    # ---------------------------------------------------------------- shell
    _rule(
        "shell-eval",
        Severity.HIGH,
        "shell",
        r"(?<![\w-])eval\s",
        "eval evaluates whatever it is given as code",
    ),
    _rule(
        "shell-pipe-to-shell",
        Severity.HIGH,
        "shell",
        r"\b(curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b",
        "a download is piped into a shell",
    ),
    _rule(
        "shell-rm-variable",
        Severity.MEDIUM,
        "shell",
        r"\brm\s+-[A-Za-z]*[rf][A-Za-z]*\s+(?![\"'])",
        "a recursive delete takes an unquoted word",
        "an unquoted variable expands to nothing or to several words and the delete lands somewhere else",
        also=("$",),
    ),
    _rule(
        "any-url-credential",
        Severity.HIGH,
        "any",
        r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@",
        "a credential is written into an address",
    ),
)

#: Rules that apply to every language, kept apart so a file is read once.
ANY_RULES: tuple[Rule, ...] = tuple(rule for rule in RULES if rule.language == "any")

#: Language to the rules that decide what is read for it.
BY_LANGUAGE: dict[str, tuple[Rule, ...]] = {
    language: tuple(rule for rule in RULES if rule.language == language)
    for language in (*LANGUAGES, "any")
}


def rules_for(language: str) -> tuple[Rule, ...]:
    """The rules that apply to one language, its own first and then the shared ones."""
    return BY_LANGUAGE.get(language, ()) + ANY_RULES


#: File suffixes the Electron assertions read. The hardening questions are about
#: one main process and one document, so they are named rather than discovered.
ELECTRON_MAIN = "desktop/main.js"
ELECTRON_PRELOAD = "desktop/preload.js"
ELECTRON_DOCUMENT = "desktop/renderer/index.html"

#: Every bridge handler that reaches a file, a process or a location, and the
#: guard call that has to stand in front of it. This is a tripwire rather than a
#: proof: it says the call is still there, so a refactor that removes it is
#: refused instead of shipped. The message names what to restore.
BRIDGE_GUARDS: tuple[tuple[str, str, str], ...] = (
    ("settings:set", "guard.sanitizeSettings", "the settings patch is held to the guard before it is saved"),
    ("runs:replay", "guard.rootOf", "a replay is confined to the results directory"),
    ("project:stage", "guard.checkProject", "a staged path is held against where the harness lives"),
    ("shell:reveal", "guard.rootOf", "a reveal is confined to the run directories"),
)


@dataclass(frozen=True)
class Assertion:
    """A property the Electron application is required to have, and where it lives."""

    code: str
    severity: Severity
    path: str
    message: str
    pattern: re.Pattern[str]
    hint: str = ""
    #: True when the property is the presence of the pattern rather than its absence.
    required: bool = True

    def satisfied(self, text: str) -> bool:
        found = self.pattern.search(text) is not None

        return found if self.required else not found


def _assert(
    code: str,
    severity: Severity,
    path: str,
    message: str,
    pattern: str,
    hint: str = "",
    required: bool = True,
) -> Assertion:
    return Assertion(code, severity, path, message, re.compile(pattern), hint, required)


#: What the window must be, as properties rather than as a review of the code
#: that sets them up. Each one is a thing a reader cannot see on screen and which
#: decides whether the page in front of them is the page that was served.
ELECTRON_ASSERTIONS: tuple[Assertion, ...] = (
    _assert(
        "electron-context-isolation",
        Severity.HIGH,
        ELECTRON_MAIN,
        "the renderer runs with context isolation",
        r"contextIsolation:\s*true",
        "without it a script in the page shares a context with the preload bridge",
    ),
    _assert(
        "electron-node-integration",
        Severity.HIGH,
        ELECTRON_MAIN,
        "the renderer has no node integration",
        r"nodeIntegration:\s*false",
    ),
    _assert(
        "electron-sandbox",
        Severity.HIGH,
        ELECTRON_MAIN,
        "the renderer runs in a sandbox",
        r"sandbox:\s*true",
    ),
    _assert(
        "electron-no-web-security-off",
        Severity.HIGH,
        ELECTRON_MAIN,
        "web security is not switched off",
        r"webSecurity\s*:\s*false",
        "turning it off removes the same origin policy for the whole window",
        required=False,
    ),
    _assert(
        "electron-no-insecure-content",
        Severity.HIGH,
        ELECTRON_MAIN,
        "mixed content is not allowed",
        r"allowRunningInsecureContent\s*:\s*true",
        required=False,
    ),
    _assert(
        "electron-window-open-denied",
        Severity.HIGH,
        ELECTRON_MAIN,
        "a request to open a window is refused",
        r"setWindowOpenHandler\s*\([\s\S]{0,400}?action:\s*'deny'",
        "an unhandled request opens a window with the default privileges",
    ),
    _assert(
        "electron-navigation-guarded",
        Severity.HIGH,
        ELECTRON_MAIN,
        "navigation away from the one document is refused",
        r"on\(\s*'will-navigate'",
        "a page that can be navigated is a page that can be sent somewhere else",
    ),
    _assert(
        "electron-webview-refused",
        Severity.MEDIUM,
        ELECTRON_MAIN,
        "attaching a webview is refused",
        r"on\(\s*'will-attach-webview'",
    ),
    _assert(
        "electron-csp-declared",
        Severity.HIGH,
        ELECTRON_DOCUMENT,
        "the document declares a content security policy",
        r"Content-Security-Policy",
    ),
    _assert(
        "electron-csp-default-src-none",
        Severity.HIGH,
        ELECTRON_DOCUMENT,
        "the policy denies everything it does not name",
        r"default-src\s+'none'",
    ),
    _assert(
        "electron-csp-no-unsafe-script",
        Severity.HIGH,
        ELECTRON_DOCUMENT,
        "the policy does not allow inline or evaluated script",
        r"script-src[^;]*unsafe-(inline|eval)",
        required=False,
    ),
    _assert(
        "electron-csp-no-network",
        Severity.MEDIUM,
        ELECTRON_DOCUMENT,
        "the policy does not let the document reach the network",
        r"connect-src\s+'none'",
    ),
    _assert(
        "electron-preload-surface",
        Severity.MEDIUM,
        ELECTRON_PRELOAD,
        "the bridge is exposed with contextBridge rather than assigned to the page",
        r"contextBridge\.exposeInMainWorld",
    ),
    _assert(
        "electron-no-direct-ipc-in-page",
        Severity.HIGH,
        ELECTRON_PRELOAD,
        "the preload does not hand the page a raw ipcRenderer",
        r"exposeInMainWorld[\s\S]{0,3000}?:\s*ipcRenderer\s*[,}\n]",
        required=False,
        hint="a page holding the raw object can send any channel to the main process",
    ),
)

#: The bridge tripwires, expressed as assertions over the main process.
BRIDGE_ASSERTIONS: tuple[Assertion, ...] = tuple(
    _assert(
        f"bridge-guard-{name.replace(':', '-')}",
        Severity.HIGH,
        ELECTRON_MAIN,
        f"{description} ({name})",
        re.escape(call),
        "the handler reaches a file, a process or a location, so it is held to the guard in lib/guard.js",
    )
    for name, call, description in BRIDGE_GUARDS
)

#: A script that runs with `set -e` stops at the first failure rather than
#: continuing with what is left. Reported as a reading rather than a finding.
SHELL_STRICTNESS: str = "set -e"

#: Suffix of the documents the document checks read: a page that runs script has
#: a policy question, and that question is about the whole file rather than about
#: one line, so it is asked where the whole file is in hand.
DOCUMENT_SUFFIXES: tuple[str, ...] = (".html",)

#: The tag that decides whether a document runs script of its own.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
POLICY_META = re.compile(r"http-equiv=[\"']?Content-Security-Policy", re.IGNORECASE)
POLICY_UNSAFE_INLINE = re.compile(r"script-src[^;]*unsafe-inline", re.IGNORECASE)
