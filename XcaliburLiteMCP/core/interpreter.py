"""
Interpretation helpers.

Turns a loose human reference ("login page", "auth/login.php", "login") into the
one real file it most likely means, and reports whether that file actually
exists. The matching is deliberately local and cheap: an exact path wins, then a
basename, then a stem, then a closest-neighbour score over the file index. The
score gate means a typo is reported as unmatched rather than silently mapped
onto the wrong file, which is what lets the caller green-light a real file.

Nothing here reads file bodies; it only resolves names, so it can run before the
model is ever loaded.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from config import FUZZY_MIN_SCORE, TARGET_ROOT, TEXT_SUFFIXES
from core import adapter

_TOKEN = re.compile(r"[A-Za-z0-9_@./*\-\[\]]+")
_GLOB_CHARS = set("*?[")
# Words that look like a path only because of a trailing dot, not a reference.
_STOPWORDS = frozenset({
    "it", "the", "and", "for", "this", "that", "e.g.", "i.e.", "etc.",
    "file", "files", "dir", "folder", "please", "make", "add", "create",
})


@dataclass
class Resolution:
    """The outcome of resolving one reference against the file index."""

    query: str
    path: str = ""                      # repository-relative, "" when unresolved
    exists: bool = False
    confidence: float = 0.0
    kind: str = "missing"               # exact | case | basename | stem | fuzzy | glob | missing
    candidates: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        """True only when the reference maps onto a file that is really there."""
        return self.exists and bool(self.path)

    def describe(self) -> str:
        if self.green:
            return f"{self.path} ({self.kind}, {self.confidence:.2f})"
        if self.candidates:
            return "unmatched; closest: " + ", ".join(self.candidates[:3])
        return "unmatched; no candidate"


def _normalize(fragment: str) -> str:
    return fragment.strip().strip("'\"").replace("\\", "/").lstrip("@").strip()


def _score(query: str, candidate: str) -> float:
    """Closest-neighbour score between a query and one index entry."""
    q = query.lower()
    c = candidate.lower()
    base = c.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    qs = q.rsplit("/", 1)[-1]
    if q == c:
        return 1.0
    if qs == base:
        return 0.95
    if qs == stem:
        return 0.9
    best = max(
        difflib.SequenceMatcher(None, q, c).ratio(),
        difflib.SequenceMatcher(None, qs, base).ratio(),
        difflib.SequenceMatcher(None, qs, stem).ratio(),
    )
    if q in c:
        best = max(best, 0.85)
    if stem.startswith(qs):
        best = max(best, 0.8)
    return best


class FileIndex:
    """A cached list of the text files under a root, with a resolver over it."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or TARGET_ROOT).resolve()
        self._files: list[str] | None = None

    def refresh(self) -> None:
        self._files = None

    def files(self) -> list[str]:
        if self._files is None:
            found: list[str] = []
            for path in self.root.rglob("*"):
                if adapter.is_ignored(path, self.root):
                    continue
                if not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                found.append(adapter.relative(path, self.root))
            self._files = sorted(found)
        return self._files

    def _glob(self, query: str) -> Resolution:
        matches = [entry for entry in self.files() if _glob_match(query, entry)]
        if not matches:
            return Resolution(query, kind="missing")
        matches.sort(key=lambda rel: (rel.count("/"), len(rel)))
        return Resolution(query, matches[0], True, 1.0, "glob", matches[:8])

    def resolve(self, fragment: str, cwd: str = "") -> Resolution:
        """
        Resolve one reference, preferring a path relative to ``cwd``.

        ``cwd`` is repository-relative ("" means the root). A trailing slash is
        ignored; a bare name is matched against every basename in the index.
        """
        query = _normalize(fragment)
        if not query:
            return Resolution(fragment, kind="missing")
        files = self.files()
        file_set = set(files)

        if any(char in query for char in _GLOB_CHARS):
            return self._glob(query)

        cwd = _normalize(cwd).strip("/")
        attempts: list[str] = []
        if cwd:
            attempts.append(f"{cwd}/{query}")
        attempts.append(query)

        for attempt in attempts:
            if attempt in file_set:
                return Resolution(fragment, attempt, True, 1.0, "exact")

        lowered = {rel.lower(): rel for rel in files}
        for attempt in attempts:
            hit = lowered.get(attempt.lower())
            if hit:
                return Resolution(fragment, hit, True, 0.97, "case")

        leaf = query.rsplit("/", 1)[-1]
        stem = leaf.rsplit(".", 1)[0]
        by_base = [rel for rel in files if rel.rsplit("/", 1)[-1].lower() == leaf.lower()]
        if len(by_base) == 1:
            return Resolution(fragment, by_base[0], True, 0.95, "basename", by_base)
        if by_base:
            return _from_candidates(fragment, "basename", by_base, 0.9)

        by_stem = [rel for rel in files if rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == stem.lower()]
        if len(by_stem) == 1:
            return Resolution(fragment, by_stem[0], True, 0.9, "stem", by_stem)
        if by_stem:
            return _from_candidates(fragment, "stem", by_stem, 0.85)

        ranked = sorted(files, key=lambda rel: _score(query, rel), reverse=True)
        if ranked:
            top = _score(query, ranked[0])
            close = [rel for rel in ranked if _score(query, rel) >= top - 0.05][:8]
            if top >= FUZZY_MIN_SCORE:
                return Resolution(fragment, ranked[0], True, round(top, 3), "fuzzy", close)
            return Resolution(fragment, kind="missing", confidence=round(top, 3), candidates=close[:5])
        return Resolution(fragment, kind="missing")

    def mentions(self, message: str, cwd: str = "", limit: int = 8,
                 min_confidence: float = 0.7) -> list[Resolution]:
        """
        Pull the file references out of a sentence and resolve each one.

        Only confident, existing resolutions survive, so ordinary English words
        that merely look path-like do not drag unrelated files into the prompt.
        """
        found: list[Resolution] = []
        seen: set[str] = set()
        for raw in _TOKEN.findall(message):
            token = _normalize(raw).strip(".,:;()")
            if not token or token.lower() in _STOPWORDS:
                continue
            if any(char in token for char in _GLOB_CHARS):
                resolved = self._glob(token)
            else:
                looks_like_path = (
                    token.startswith("./")
                    or "/" in token
                    or Path(token).suffix.lower() in TEXT_SUFFIXES
                    or len(token) > 4
                )
                if not looks_like_path:
                    continue
                resolved = self.resolve(token, cwd)
            if resolved.green and resolved.confidence >= min_confidence and resolved.path not in seen:
                found.append(resolved)
                seen.add(resolved.path)
            if len(found) >= limit:
                break
        return found


def _from_candidates(query: str, kind: str, candidates: list[str], confidence: float) -> Resolution:
    ordered = sorted(candidates, key=lambda rel: (rel.count("/"), len(rel)))
    return Resolution(query, ordered[0], True, confidence, kind, ordered[:8])


def _glob_match(pattern: str, rel: str) -> bool:
    from fnmatch import fnmatch

    return fnmatch(rel, pattern) or fnmatch(rel.rsplit("/", 1)[-1], pattern)
