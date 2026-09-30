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

Two exclusions run the other way, because a report that is wrong is a report
that stops being read. A value carrying the shape of a regular expression is a
rule rather than a secret, which is what lets a file state the check a scanner
enforces without being reported by the scanner it describes. And a path is
allowed to be a written placeholder, which is what lets a document name the
shape it refuses without the explanation being refused itself.

The second list below is a different question with the same answer. A machine
path is not a credential, and it is still not repository content: an absolute
home path in a recorded run names the account, the checkout location and
whatever sits beside it, and a record is the thing that gets handed to somebody
else. It is kept apart from the credential list because the two are reported
differently and a reader should be able to tell at a glance which of the two
they are looking at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DisclosurePattern:
    """A string that describes the machine rather than the work, and how to spot it."""

    name: str
    regex: re.Pattern[str]
    note: str
    group: int = 0


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


#: Strings that describe the machine that produced a file rather than the work
#: in it. Each of these is a layout an operator can read: an account name, the
#: directory a checkout sits in, the drive a project lives on.
#:
#: Nothing here matches a relative path, and nothing here matches a loopback
#: address, because those are what a record is supposed to carry. The rule is
#: about who the record describes, not about how specific it is.
DISCLOSURE_PATTERNS: tuple[DisclosurePattern, ...] = (
    DisclosurePattern(
        name="home-directory-path",
        regex=_compile(r"(?:/Users|/home|/root)/[A-Za-z0-9._-]+(?:/[^\"'\s\\]*)"),
        note="An absolute home path. Shorten it to a repository path, a home marker or a placeholder.",
    ),
    DisclosurePattern(
        name="windows-home-path",
        regex=_compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\"'\s]+"),
        note="A drive letter, an account name and the checkout location in one string.",
    ),
    DisclosurePattern(
        name="macos-temp-path",
        regex=_compile(r"/(?:private/)?var/folders/[A-Za-z0-9_]{2,}/[A-Za-z0-9_]{2,}/[A-Za-z0-9_]+"),
        note="The per-user temporary directory, which names the account that ran the work.",
    ),
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

#: Shapes that make a value a rule rather than a secret.
#:
#: A file that states the rule a scanner enforces has to write that rule down,
#: and the rule is a string: a name that says it holds a credential, followed by
#: a regular expression, is the definition of the check rather than a breach of
#: it. A value carrying a backslash escape, a character class or a non-capturing
#: group is describing what a credential looks like, and none of the provider
#: shapes contain any of those.
PATTERN_SHAPE: re.Pattern[str] = re.compile(r"\\[bwsdWSD]|\[[^\]]{0,40}\]|\(\?[:!<]")


def is_pattern_value(value: str) -> bool:
    """Whether a matched value is a regular expression rather than a credential.

    This is applied to the generic assignment rule alone. A provider prefix is
    evidence on its own, and a value standing behind one is a credential however
    it happens to be punctuated, so widening this into that rule would trade a
    real finding for a tidy report.
    """
    return PATTERN_SHAPE.search(value) is not None


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


#: Words that only appear in a value somebody wrote for a reader to replace.
#: A person writing documentation about a key has to write something in the
#: examples, and "your key goes here" is what they write.
INSTRUCTION_WORDS: frozenset[str] = frozenset(
    {
        "add",
        "changeme",
        "dummy",
        "example",
        "fake",
        "here",
        "insert",
        "my",
        "our",
        "paste",
        "placeholder",
        "put",
        "redacted",
        "replace",
        "sample",
        "todo",
        "you",
        "your",
    }
)

#: The nouns a value has to contain before it is talking about a credential at
#: all. This check needs both halves: an instruction word alone is ordinary
#: English, and a credential noun alone is ordinary English too, so a value has
#: to be doing both before it is recognised as a written stand-in.
CREDENTIAL_WORDS: frozenset[str] = frozenset(
    {
        "api",
        "apikey",
        "auth",
        "credential",
        "credentials",
        "key",
        "keys",
        "passwd",
        "password",
        "secret",
        "token",
        "tokens",
    }
)

_WORD_SPLIT: re.Pattern[str] = re.compile(r"[^A-Za-z0-9]+")

#: A run of characters a person would write: letters, or a short number. Every
#: segment of a value has to look like this before the value can be called
#: written rather than generated, which is what keeps a key out. `AbCdEf12` and
#: `a8f5f167` are not words, and no amount of instruction words beside them would
#: make them one.
_WRITTEN_SEGMENT: re.Pattern[str] = re.compile(r"[A-Za-z]+|\d{1,4}\Z")


def is_written_credential_placeholder(value: str) -> bool:
    """Whether a value is a stand-in a person wrote out in words.

    `MY_API_KEY = "YOUR_SECRET_Vast_API_KEY"` is a document showing a reader
    where a credential goes. It matches the assignment rule honestly: the name
    says it holds a key and the value is a literal. It is not a credential, and
    reporting it teaches the reader to skim the scanner's output, which costs
    more than the finding bought.

    Three things have to hold, and each excludes a different mistake. Every
    segment must be a word or a short number, so a generated key is out however
    it is punctuated. At least one segment must be about credentials, so an
    ordinary hyphenated phrase is out. At least one must be an instruction to the
    reader, so a bare noun like `secret` is out. Provider names pass through
    untouched: `Vast` and `OpenAI` are words, and a person writing documentation
    has to be able to name the service the key belongs to.
    """
    segments = [segment for segment in _WORD_SPLIT.split(value.strip()) if segment]
    if not segments:
        return False
    if not all(_WRITTEN_SEGMENT.fullmatch(segment) for segment in segments):
        return False

    lowered = [segment.lower() for segment in segments]
    if not any(word in CREDENTIAL_WORDS for word in lowered):
        return False
    return any(word in INSTRUCTION_WORDS for word in lowered)


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
    if is_written_credential_placeholder(candidate):
        return True
    # A value that repeats one character is a mask, not a key.
    stripped = set(lowered)
    if len(stripped) <= 2 and len(candidate) >= 8:
        return True

    return False


def is_written_placeholder(value: str) -> bool:
    """Whether a matched path is a stand-in the project writes on purpose.

    A document that explains this rule has to be able to name the shape it
    refuses without the explanation being refused itself, so a value carrying
    an angle bracket or an environment reference is a written placeholder
    rather than a machine path.
    """
    return any(hint in value for hint in STRUCTURAL_HINTS)
