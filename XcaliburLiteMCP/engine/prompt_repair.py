"""
Prompt and tool-call repair.

A small model drifts from the exact tool schema: it wraps JSON in a code fence,
renames ``old_str`` to ``old_string``, sends ``content`` where ``new_str``
belongs, emits edits as a single object, or spills prose around the payload.
Rather than fail the turn, this module reshapes what came back into the form the
patcher expects. It also measures a proposed file against the minimal-code
budget and, when it overshoots, produces the corrective line that replaces the
call with a smaller one. Keeping this in one place is what lets the prompt stay
short while the harness absorbs the model's variation.
"""
from __future__ import annotations

import json
import re

from config import MINIMAL_CODE_LINES

_FENCE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# A field that swallowed the tail of the JSON envelope: it holds a stray key
# followed by a collection, e.g. the model wrote `""}],files:[` as old_str.
_LEAK_RE = re.compile(r"[}\]]\s*\]?\s*,?\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[\[\{]")
# A path that cannot be real: whitespace or JSON punctuation inside it.
_BAD_PATH_RE = re.compile(r"[\s{}\[\]\"]")

# Keys the model uses for the same three fields.
_FILE_KEYS = ("filepath", "file_path", "path", "file", "filename", "target")
_OLD_KEYS = ("old_str", "old_string", "old", "search", "find", "target_str")
_NEW_KEYS = ("new_str", "new_string", "new", "replace", "replacement", "content", "text", "code")


def extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of a blob that may have prose."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\" and in_string:
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:index + 1]
                    for attempt in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
                        try:
                            return json.loads(attempt)
                        except json.JSONDecodeError:
                            continue
                    break
        start = text.find("{", start + 1)
    return None


def strip_fence(text: str) -> str:
    """Remove one enclosing Markdown code fence from a value, if present."""
    if not isinstance(text, str):
        return text
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _first(mapping: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _looks_like_json_leak(value: str) -> bool:
    """
    True when a field holds a fragment of the JSON envelope rather than content.

    A small model sometimes drops a closing brace in the wrong place, so a
    later key and its array or object end up inside an earlier string. That is
    still valid JSON, which is why it parses cleanly and then fails at patch
    time; the shape is detectable even though the syntax is not.
    """
    return bool(_LEAK_RE.search(value or ""))


def coerce_edit(item: object, default_path: str = "") -> dict | None:
    """
    Reshape one edit-like object into {filepath, old_str, new_str, regex}.

    An edit whose filepath cannot be a real path is dropped, and an ``old_str``
    that carries a leaked JSON fragment is reduced to an empty string so the
    edit still stages as a creation rather than failing against garbage.
    """
    if isinstance(item, str):
        if not default_path:
            return None
        return {"filepath": default_path, "old_str": "", "new_str": strip_fence(item), "regex": False}
    if not isinstance(item, dict):
        return None

    filepath = _first(item, _FILE_KEYS) or default_path
    new_str = _first(item, _NEW_KEYS)
    old_str = _first(item, _OLD_KEYS)
    if not str(filepath).strip() or new_str is None:
        return None
    cleaned_path = _clean_path(str(filepath))
    if not cleaned_path or _BAD_PATH_RE.search(cleaned_path):
        return None
    if not isinstance(new_str, str):
        new_str = json.dumps(new_str, indent=2)
    if old_str is None:
        old_str = ""
    if not isinstance(old_str, str):
        old_str = str(old_str)
    repaired = False
    if old_str and _looks_like_json_leak(old_str):
        old_str = ""
        repaired = True
    new_str = strip_fence(new_str)
    if _looks_like_json_leak(new_str.rstrip()[-24:]):
        new_str = _LEAK_RE.split(new_str)[0].rstrip()
        repaired = True
    regex = bool(_first(item, ("regex", "is_regex", "use_regex")) or False)
    return {
        "filepath": cleaned_path,
        "old_str": old_str,
        "new_str": new_str,
        "regex": regex,
        "repaired": repaired,
    }


def _clean_path(value: str) -> str:
    """
    Normalise a filepath without hiding a traversal.

    Only a leading ``./`` is removed. Using ``lstrip("./")`` would strip every
    leading dot and slash, which turns ``../../etc/passwd`` into ``etc/passwd``
    and defeats the resolver that is meant to refuse it. A bare leading ``..``
    or ``/`` is left intact so the path guard sees the real escape.
    """
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def coerce_edits(value: object, default_path: str = "") -> list[dict]:
    """Reshape any plausible edits payload into a clean list of edits, without repeats."""
    raw: list[dict] = []
    if value is None:
        return []
    elif isinstance(value, str):
        parsed = extract_json(value)
        if parsed is not None:
            return coerce_edits(parsed, default_path)
        edit = coerce_edit(value, default_path)
        raw = [edit] if edit else []
    elif isinstance(value, dict):
        nested = next((value[key] for key in ("edits", "changes", "patches", "replacements") if key in value), None)
        if nested is not None:
            return coerce_edits(nested, default_path)
        filepath = _first(value, _FILE_KEYS) or default_path
        if "new_str" in value or _first(value, _NEW_KEYS) is not None:
            edit = coerce_edit(value, default_path)
            raw = [edit] if edit else []
        else:
            # A mapping of path -> content is a multi-file create.
            for key, content in value.items():
                if isinstance(content, str) and "." in key:
                    edit = coerce_edit({"filepath": key, "old_str": "", "new_str": content})
                    if edit:
                        raw.append(edit)
    elif isinstance(value, list):
        raw = [edit for edit in (coerce_edit(item, default_path) for item in value) if edit]

    deduped: list[dict] = []
    seen: set[tuple] = set()
    for edit in raw:
        key = (edit["filepath"], edit["old_str"], edit["new_str"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edit)
    return deduped


# Scalar envelope fields worth recovering from a blob cut off inside its JSON.
_HEAD_KEYS = ("instruction", "task", "summary", "files", "paths", "run", "command")
_HEAD_RE = {
    key: re.compile(rf'"{key}"\s*:\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\])') for key in _HEAD_KEYS
}


def _envelope_head(raw: str) -> dict:
    """
    The envelope fields still readable in a blob cut off inside its JSON.

    A cut-off call keeps the fields it wrote before the edits, so the
    instruction and the file list survive beside the recovering edit. A
    truncation inside one of these values simply does not match and is skipped.
    """
    head: dict = {}
    for key, pattern in _HEAD_RE.items():
        match = pattern.search(raw)
        if not match:
            continue
        try:
            head[key] = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return head


def repair_arguments(raw: object) -> dict:
    """
    Best-effort parse of a tool call's arguments.

    Accepts a dict, a JSON string, or a JSON string with surrounding prose or a
    code fence. A call cut off inside its JSON envelope is repaired rather than
    discarded: the first edit that closed is kept, together with the instruction
    and file list that came before it. Returns {} when nothing usable is present.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        return {}
    if "edits" in parsed or "changes" in parsed:
        return parsed
    salvaged = _envelope_head(raw)
    salvaged["edits"] = [parsed]
    return salvaged


def failure_reason(finish: str, reason: str = "") -> str:
    """
    Why a pass produced no usable tool call, in one phrase.

    A generation that stopped at the token ceiling and a call whose JSON never
    closed are one event seen from two sides: the call was cut off. Saying so is
    what makes the retry ask for a smaller file instead of repeating the same
    oversized one.
    """
    if finish == "length":
        return "the call was cut off before its JSON closed" if reason else "the response was cut off"
    return reason or "the model did not call the tool"


def normalize_tool_arguments(arguments: dict, default_path: str = "") -> dict:
    """Normalise one run_sh_runner argument object into the request shape."""
    edits = coerce_edits(arguments.get("edits", arguments.get("changes")), default_path)
    if not edits:
        # A call cut off inside its JSON envelope can still hold completed
        # edits: each one is a balanced object on its own, so the parser hands
        # back the first that closed. Coercing the object itself keeps the file
        # the model finished instead of dropping the whole call. An envelope
        # with no edits coerces to nothing, so the queued path is unaffected.
        edits = coerce_edits(arguments, default_path)
    files = arguments.get("files") or arguments.get("paths") or []
    if isinstance(files, str):
        files = [files]
    files = [str(item).strip() for item in files if str(item).strip()]
    if not files and edits:
        files = [edit["filepath"] for edit in edits]
    instruction = str(
        arguments.get("instruction") or arguments.get("task") or arguments.get("summary") or ""
    ).strip()
    return {
        "instruction": instruction,
        "files": files,
        "edits": edits,
        "run": str(arguments.get("run") or arguments.get("command") or "").strip(),
        "execute": bool(arguments.get("execute", False)),
        "origin": str(arguments.get("origin", "agent")),
        "mode": str(arguments.get("mode", "direct" if edits else "auto")),
        "reviewer_note": str(arguments.get("reviewer_note", "")).strip(),
        "parent_id": str(arguments.get("parent_id", "")).strip(),
    }


def oversized_edit(edit: dict, limit: int = MINIMAL_CODE_LINES) -> bool:
    """True when a proposed body exceeds the minimal-code budget."""
    body = edit.get("new_str") or ""
    return body.count("\n") + 1 > limit


# Markers that only appear in a document the minimal-code rule forbids.
_BOILERPLATE = ("<!doctype", "<html", "<head", "<body", "<style", "<script")
_DOCUMENT_RE = re.compile(
    r"<!doctype[^>]*>|<html[^>]*>|</html>|<head[^>]*>.*?</head>|<body[^>]*>|</body>",
    re.IGNORECASE | re.DOTALL,
)


def is_write_edit(edit: dict) -> bool:
    """
    True when an edit is writing a file rather than amending one, by shape alone.

    An empty ``old_str`` is a write. A tiny ``old_str`` beside a large
    ``new_str`` is also a write. This is only the fallback: a caller that knows
    the filesystem should pass its own predicate, because a model that pastes
    the whole intended body into ``old_str`` looks surgical by shape while the
    target does not exist.
    """
    old = edit.get("old_str") or ""
    new = edit.get("new_str") or ""
    if not old:
        return True
    return len(new) >= 120 and len(old) < len(new) * 0.25


def has_boilerplate(edit: dict) -> bool:
    body = (edit.get("new_str") or "").lower()
    return any(token in body for token in _BOILERPLATE)


def _write_predicate(edits: list[dict], is_write):
    if is_write is None:
        return is_write_edit
    return is_write


def boilerplate_hint(edits: list[dict], is_write=None) -> str:
    """
    A corrective line when a written file carries document boilerplate.

    This is the automatic form of the minimal-code rule, catching the common
    case where a six-line form is wrapped in a full HTML document. ``is_write``
    overrides the shape heuristic with the caller's filesystem knowledge.
    """
    predicate = _write_predicate(edits, is_write)
    for edit in edits:
        if not predicate(edit) or not has_boilerplate(edit):
            continue
        body = (edit.get("new_str") or "").lower()
        hits = [token for token in _BOILERPLATE if token in body]
        return (
            f"`{edit['filepath']}` includes {', '.join(hits)}, which the "
            "minimal-code rule forbids. Call `run_sh_runner` again with only "
            "the working lines; a PHP login form is about 6 lines: the "
            "`<?php` block and the `<form>`, nothing around them."
        )
    return ""


def strip_document(text: str) -> str:
    """Remove a surrounding HTML document wrapper, keeping the inner markup."""
    stripped = _DOCUMENT_RE.sub("", text)
    return "\n".join(line for line in stripped.splitlines() if line.strip()).strip() + "\n"


def enforce_minimal(edits: list[dict], is_write=None) -> int:
    """
    Apply the minimal-code policy to any write that still carries a wrapper.

    Used after the corrective nudge has been spent and the model has not
    complied: the rule is a hard policy of this harness, so the wrapper is
    removed deterministically rather than left for the reviewer to strip.
    Returns the number of edits changed.
    """
    predicate = _write_predicate(edits, is_write)
    changed = 0
    for edit in edits:
        if not predicate(edit) or not has_boilerplate(edit):
            continue
        minimized = strip_document(edit.get("new_str") or "")
        if minimized and minimized != edit.get("new_str"):
            edit["new_str"] = minimized
            changed += 1
    return changed


def minimal_lines_hint(edits: list[dict], limit: int = MINIMAL_CODE_LINES) -> str:
    """
    A corrective line when a proposed file is larger than the minimal budget.

    Phrased as a single tight instruction because the model complies with a hard
    limit and ignores a lecture.
    """
    offenders = [edit for edit in edits if oversized_edit(edit, limit)]
    if not offenders:
        return ""
    largest = max(offenders, key=lambda edit: (edit.get("new_str") or "").count("\n"))
    current = (largest.get("new_str") or "").count("\n") + 1
    return (
        f"`{largest['filepath']}` came back as {current} lines; the limit is "
        f"{limit}. Call `run_sh_runner` again with the smallest version that "
        "fully works: no boilerplate, no styling, no comments, no helper "
        "functions that are used once. Shorter is required."
    )


def shape_hint(reason: str) -> str:
    """A corrective line for a call that could not be parsed at all."""
    return (
        f"The previous call could not be used: {reason}. Call `run_sh_runner` "
        "again with valid JSON: `instruction`, `files`, and `edits` (a list of "
        "objects with `filepath`, `old_str`, `new_str`). For a new file set "
        "`old_str` to an empty string. No prose, no code fences."
    )
