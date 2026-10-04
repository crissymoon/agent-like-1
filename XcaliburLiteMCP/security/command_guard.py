"""
Command guard.

The scanner that stands between the agent and the shell. Every command the
agent asks to run passes through ``scan`` first. The policy is a deny list of
destructive patterns plus an allow list of developer verbs. There is no shell:
the runner execs the program directly, so any metacharacter that would only mean
something to a shell is refused rather than interpreted.
"""
from __future__ import annotations

import os
import re

# Patterns that are never allowed, regardless of context.
DENY_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-[a-z]*[rf][a-z]*\s+(/|~|\$HOME|/\*)(\s|$)",
    r"\bsudo\b",
    r"(^|\s)doas(\s|$)",
    r"\bsu\s",
    r"\bmkfs",
    r"\bdd\s+if=",
    r"\bdiskutil\b",
    r"\bnewfs\b",
    r":\s*\(\s*\)\s*\{",                       # fork bomb
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\bkill(all)?\s+-9\s+-1\b",
    r"\bpkill\s+-9\b",
    r"\blaunchctl\b",
    r"\bdefaults\s+write\b",
    r"\bcurl\b[^|]*\|\s*(ba|z)?sh\b",
    r"\bwget\b[^|]*\|\s*(ba|z)?sh\b",
    r"(^|\s)eval(\s|$)",  # security-allow: this names a verb the policy refuses; it is a rule, not a call
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bchown\s+-R\b",
    r">\s*/dev/(sd|disk|rdisk)",
    r"\bnc\s+[^;|&]*-e\b",
    r"\bpip3?\s+install\b",
    r"\bnpm\s+install\b[^;|&]*(--global|-g)\b",
    r"\bbrew\s+(install|uninstall|upgrade|reinstall)\b",
    r"\bcrontab\b",
    r"\bssh\b",
    r"\bscp\b",
)

# Command substitution and backticks hide intent; refuse them outright.
SUBSTITUTION_PATTERNS: tuple[str, ...] = (r"\$\(", r"`")

# Operators that only a shell would interpret. The runner execs the program
# directly, so a command carrying one is refused instead of being handed to a
# shell, which is what keeps a string from becoming an injection. Redirection is
# in the list for the same reason: without a shell there is nothing to perform
# it, and allowing it would promise a write the runner does not make.
SHELL_OPERATORS: tuple[str, ...] = ("||", "&&", "|", ";", ">", "<", "\n")

ALLOWED_VERBS: frozenset[str] = frozenset({
    "python", "python3", "pytest", "pip", "node", "npm", "npx", "pnpm", "yarn",
    "go", "cargo", "rustc", "make", "cmake",
    "ls", "cat", "head", "tail", "grep", "rg", "find", "sed", "awk", "wc",
    "sort", "uniq", "diff", "echo", "printf", "test", "true", "false", "cd",
    "pwd", "which", "env", "sleep", "jq", "yq",
    "git", "ruff", "flake8", "mypy", "black", "isort", "pylint", "bandit",
    "tsc", "eslint", "prettier",
    "php", "composer", "bash", "sh", "zsh", "shellcheck",
    "dotnet", "javac", "java", "gcc", "clang", "g++",
    "curl", "wget", "tar", "unzip", "zip", "cp", "mv", "mkdir", "touch",
    "chmod", "stat", "file", "realpath", "dirname", "basename", "date",
    "hostname", "uname", "ps", "tree", "od", "xxd", "shasum", "sha256sum",
    "tr", "cut", "xargs", "tee", "column", "columns", "pgrep",
})

_FIRST_TOKEN = re.compile(r"^\s*([A-Za-z0-9_./-]+)")


def _verb_of(command: str) -> str:
    """Return the basename of the command's first token, skipping env assignments."""
    token = ""
    for part in command.split():
        if "=" in part and not part.startswith("-") and "/" not in part.split("=", 1)[0]:
            continue  # leading VAR=value assignment
        token = part
        break
    match = _FIRST_TOKEN.match(token)
    if not match:
        return ""
    return os.path.basename(match.group(1))


def scan(command: str) -> tuple[bool, str]:
    """
    Inspect a command line. Returns (allowed, reason).

    ``reason`` is empty when allowed, otherwise a human-readable refusal.
    """
    if not command or not command.strip():
        return False, "empty command"

    for pattern in SUBSTITUTION_PATTERNS:
        if re.search(pattern, command):
            return False, f"command substitution is not allowed ({pattern!r})"

    for pattern in DENY_PATTERNS:
        if re.search(pattern, command):
            return False, f"blocked pattern: {pattern}"

    for operator in SHELL_OPERATORS:
        if operator in command:
            return False, (
                f"shell operator {operator!r} is not allowed: the runner executes "
                "the program directly, without a shell"
            )

    verb = _verb_of(command)
    if not verb:
        return False, "no runnable command found"
    if verb not in ALLOWED_VERBS:
        return False, f"command not on the allow list: {verb}"

    return True, ""


def scan_many(commands: list[str]) -> tuple[bool, str]:
    for command in commands:
        ok, reason = scan(command)
        if not ok:
            return False, reason
    return True, ""
