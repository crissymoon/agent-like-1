"""The shapes a leaked credential takes, kept as data rather than as logic.

A scanner is only as good as the list it carries, so the list is separated from
the code that walks the repository. Each entry says what it matches, which part
of the match is the credential, and what a reader should do about it. The
generic rule at the bottom catches the credentials that have no memorable
prefix, which is most of them: an environment variable named `..._API_KEY=`
followed by a literal is a leak whether or not the literal has a recognisable
shape.

Two things are deliberately not here. There is no entropy heuristic, because on
a repository of model runs and base64 fixtures it reports digest hashes as
credentials until nobody reads the output, and a report nobody reads is worse
than no report. And nothing here is allowed to be widened into a guess: every
pattern below is either a documented token prefix or a name that states what it
holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    """One thing that must not be committed, and how to recognise it."""

    name: str
    regex: re.Pattern[str]
    note: str
    #: Which capture group holds the credential. Zero means the whole match,
    #: which is the case for a pattern whose prefix is part of the secret.
    group: int = 0


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


#: Documented provider prefixes, longest-lived first. Ordering matters only in
#: that the anthropic rule is stated before the openai one, since an anthropic
#: key also begins with the openai prefix.
PROVIDER_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        name="anthropic-api-key",
        regex=_compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
        note="Anthropic key. Rotate it in the console and keep it in the environment.",
    ),
    SecretPattern(
        name="openai-api-key",
        regex=_compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}"),
        note="OpenAI key. Revoke it, then read it from the environment.",
    ),
    SecretPattern(
        name="aws-access-key-id",
        regex=_compile(r"(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}"),
        note="AWS access key id. Deactivate the key before it is used.",
    ),
    SecretPattern(
        name="google-api-key",
        regex=_compile(r"AIza[0-9A-Za-z_\-]{35}"),
        note="Google API key. Restrict or delete it in the cloud console.",
    ),
    SecretPattern(
        name="huggingface-token",
        regex=_compile(r"hf_[A-Za-z0-9]{30,}"),
        note="Hugging Face token. Revoke it in settings; it is not scoped by default.",
    ),
    SecretPattern(
        name="github-fine-grained-pat",
        regex=_compile(r"github_pat_[A-Za-z0-9_]{50,}"),
        note="GitHub fine-grained token. Revoke it in developer settings.",
    ),
    SecretPattern(
        name="github-token",
        regex=_compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
        note="GitHub token. Revoke it, then re-authenticate with the CLI.",
    ),
    SecretPattern(
        name="slack-token",
        regex=_compile(r"xox[abpros]-[A-Za-z0-9\-]{10,}"),
        note="Slack token. Revoke the app installation that issued it.",
    ),
    SecretPattern(
        name="stripe-secret-key",
        regex=_compile(r"(?:sk|rk)_live_[A-Za-z0-9]{20,}"),
        note="Live Stripe key. Roll it immediately; live keys move money.",
    ),
    SecretPattern(
        name="sendgrid-api-key",
        regex=_compile(r"SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
        note="SendGrid key. Delete it; mail sending is not recoverable once abused.",
    ),
    SecretPattern(
        name="npm-token",
        regex=_compile(r"npm_[A-Za-z0-9]{36}"),
        note="npm publish token. Revoke it; it can publish under the account.",
    ),
    SecretPattern(
        name="pypi-token",
        regex=_compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{40,}"),
        note="PyPI token. Revoke it in account settings.",
    ),
    SecretPattern(
        name="private-key-block",
        regex=_compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        note="An embedded private key. Remove the file from history, not just the commit.",
    ),
    SecretPattern(
        name="json-web-token",
        regex=_compile(
            r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
        ),
        note="A signed token. Treat the signature as the password it is.",
    ),
    SecretPattern(
        name="authorization-header",
        regex=_compile(r"(?i)\bauthorization\b\s*[:=]\s*['\"]?(?:bearer|basic)\s+[A-Za-z0-9._=+\-]{16,}"),
        note="A literal credential in an Authorization header.",
    ),
)


#: A name that says it holds a secret, followed by a literal.
#:
#: The value must be at least twelve characters, because a short one is a flag
#: or an empty string, and a quoted literal is required or the value is a
#: reference to the environment rather than a secret.
GENERIC_ASSIGNMENT: SecretPattern = SecretPattern(
    name="hardcoded-secret-assignment",
    regex=_compile(
        r"(?i)\b([A-Za-z0-9_\-.]*"
        r"(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret"
        r"|secret|token|password|passwd|credential|private[_-]?key)"
        r"[A-Za-z0-9_\-.]*)"
        r"\s*[:=]\s*"
        r"(['\"])([^'\"\s]{12,})\2"
    ),
    note="A credential written into a file rather than read from the environment.",
    group=3,
)


#: Values that are not credentials. A structural marker is checked as a
#: substring; a word is compared whole, because a real key can contain a word
#: like `test` by chance and dropping it would be a false negative.
STRUCTURAL_HINTS: tuple[str, ...] = (
    "${",
    "$(",
    "{{",
    "<",
    "%(",
    "getenv",
    "environ",
    "process.env",
    "os.getenv",
)

#: Whole values that mean nobody meant to commit a secret.
PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "change_me",
        "dummy",
        "example",
        "fake",
        "none",
        "null",
        "placeholder",
        "redacted",
        "replace-me",
        "replace_me",
        "secret",
        "todo",
        "undefined",
        "unset",
        "your-key-here",
        "your_key_here",
        "xxxxxxxx",
        "xxxxxxxxxxxxxxxx",
        "****",
        "***",
    }
)

#: Values a scanner must not report even when they match a provider prefix.
#: These are the strings the project itself documents as examples.
ALLOWED_VALUES: frozenset[str] = frozenset(
    {
        "sk-example",
        "sk-ant-example",
        "sk-your-key-here",
    }
)


def is_placeholder(value: str) -> bool:
    """Whether a matched value is a stand-in rather than a credential."""
    candidate = value.strip()
    if candidate == "":
        return True
    lowered = candidate.lower()
    if lowered in PLACEHOLDER_VALUES or candidate in ALLOWED_VALUES:
        return True
    if any(hint in lowered for hint in STRUCTURAL_HINTS):
        return True
    # A value that repeats one character is a mask, not a key.
    stripped = set(lowered)
    if len(stripped) <= 2 and len(candidate) >= 8:
        return True

    return False
