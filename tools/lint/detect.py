"""What a file is, decided before anything tries to read it as code.

The order matters, because a suffix is a claim a file makes about itself and the
first bytes are not. A `.json` that starts with `GGUF` is a weight file with a
wrong name, and running a json parser over it wastes a model call to learn what
four bytes would have said. So the bytes are read first, then the suffix, then
the interpreter line, and each answer carries the evidence that produced it:

    GGUF at byte 0            the file is a weight file, whatever it is called
    suffix .mmd               a mermaid diagram, which is checked by rendering it
    no suffix, #!/bin/sh      a shell script, which is checked with sh -n
    suffix .log               text, and nothing here has a checker for it

A file with a NUL byte in its first block is binary and is never read as text.
That single test is what keeps the checkers from being handed a weight file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: How many bytes are enough to answer the questions above.
HEAD_BYTES = 8192

#: Suffix to language. A suffix is a claim, so this is only consulted after the
#: bytes have been read and have not contradicted it.
SUFFIX_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".ipynb": "json",
    ".mmd": "mermaid",
    ".mermaid": "mermaid",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".sql": "sql",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".log": "text",
    ".csv": "text",
    ".tsv": "text",
    ".xml": "text",
    ".svg": "text",
}

#: Bytes at offset zero that name a type the suffix can only get wrong. The
#: list is short on purpose: it carries the types this repository actually
#: holds, not every type that exists.
MAGIC_LEADING: tuple[tuple[bytes, str], ...] = (
    (b"GGUF", "gguf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF8", "gif"),
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\x1f\x8b", "gzip"),
    (b"\x7fELF", "elf"),
    (b"SQLite format 3\x00", "sqlite"),
    (b"RIFF", "riff"),
)

#: Interpreters that name their own language on the first line.
SHEBANG_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("python", "python"),
    ("node", "javascript"),
    ("php", "php"),
    ("bash", "shell"),
    ("zsh", "shell"),
    ("sh", "shell"),
)

#: Files whose whole name is the type, or whose name begins with the type and
#: then a variant, which is how `Dockerfile.server` is named.
NAMED_LANGUAGES: dict[str, str] = {
    "Makefile": "makefile",
    "CMakeLists.txt": "cmake",
}

#: A name that starts with one of these is that type whatever follows it.
NAMED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Dockerfile", "dockerfile"),
    ("Makefile.", "makefile"),
)


@dataclass(frozen=True)
class FileKind:
    """A file's type, how confident the answer is, and what decided it."""

    path: Path
    language: str
    evidence: str
    is_binary: bool

    @property
    def checkable(self) -> bool:
        return not self.is_binary


def _head(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(HEAD_BYTES)
    except OSError:
        return b""


def _shebang_language(head: bytes) -> tuple[str, str] | None:
    if not head.startswith(b"#!"):
        return None
    first = head.split(b"\n", 1)[0].decode("utf-8", "replace")
    for needle, language in SHEBANG_LANGUAGES:
        if needle in first:
            return language, f"shebang line {first.strip()[:40]}"
    return None


def detect_file(path: Path) -> FileKind:
    """Name the type of one file, with the evidence that decided it."""
    head = _head(path)
    if b"\x00" in head:
        for signature, name in MAGIC_LEADING:
            if head.startswith(signature):
                return FileKind(path, name, f"{signature!r} at byte 0", True)
        return FileKind(path, "binary", "NUL byte in the first block", True)

    for signature, name in MAGIC_LEADING:
        if head.startswith(signature):
            return FileKind(path, name, f"{signature!r} at byte 0", False)

    if path.name in NAMED_LANGUAGES:
        return FileKind(path, NAMED_LANGUAGES[path.name], f"named {path.name}", False)

    for prefix, language in NAMED_PREFIXES:
        if path.name.startswith(prefix):
            return FileKind(path, language, f"named {path.name}", False)

    shebang = _shebang_language(head)
    if shebang is not None:
        language, evidence = shebang
        return FileKind(path, language, evidence, False)

    suffix = path.suffix.lower()
    if suffix in SUFFIX_LANGUAGES:
        return FileKind(path, SUFFIX_LANGUAGES[suffix], f"suffix {suffix}", False)
    if not suffix and head.strip():
        return FileKind(path, "text", "no suffix, no shebang, printable bytes", False)
    return FileKind(path, "unknown", f"suffix {suffix or '(none)'} is not in the table", False)


def read_text(path: Path) -> str | None:
    """The file as text, or None when its bytes are not text after all."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
