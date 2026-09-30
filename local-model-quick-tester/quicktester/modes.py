"""Conversation modes, read from `modes.toml`.

A mode is a system prompt and a temperature, and that pair is the whole of what
makes one set of weights read as an editor in one session and a pair programmer
in the next. The wording is the part a person actually wants to change, so it is
a file rather than a string in a module.

A mode a model cannot do well is a judgement its entry in `models.toml` can make,
by naming the modes it should be offered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on a Python before 3.11
    tomllib = None  # type: ignore[assignment]

MODES_NAME = "modes.toml"

#: What a mode falls back to when its temperature is absent or unusable. A
#: language model at 0 repeats itself and at 1 wanders, so the middle is the
#: least surprising place to land.
DEFAULT_TEMPERATURE = 0.5


class ModeError(RuntimeError):
    """The modes file could not be read at all."""


@dataclass(frozen=True)
class Mode:
    """One way of conducting a conversation."""

    key: str
    label: str
    temperature: float
    system: str

    def summary(self) -> str:
        """The first clause of the instruction, for a menu line."""
        first = self.system.strip().split("\n", 1)[0]
        sentence = first.split(". ", 1)[0].strip().rstrip(".")
        return sentence if len(sentence) <= 88 else sentence[:85].rstrip() + "..."


@dataclass
class ModeSet:
    """The modes, which one is the default, and what was wrong with the file."""

    modes: tuple[Mode, ...]
    default: str
    source: str
    problems: list[str] = field(default_factory=list)

    def keys(self) -> tuple[str, ...]:
        return tuple(mode.key for mode in self.modes)

    def get(self, name: str) -> Mode:
        """A mode by key or label, refusing a name that matches nothing."""
        wanted = (name or "").strip().casefold()
        for mode in self.modes:
            if wanted in (mode.key.casefold(), mode.label.casefold()):
                return mode
        known = ", ".join(self.keys()) or "none"
        raise ModeError(f"no conversation mode named '{name}'\n  there are: {known}")

    def first_of(self, keys: tuple[str, ...], wanted: str | None = None) -> Mode:
        """The mode asked for, else the default, else the first the model allows.

        Narrowing a model's modes in `models.toml` must not leave a session with
        no mode at all, so the fallback walks what is actually offered rather
        than assuming the default is among them.
        """
        allowed = [key for key in keys] or list(self.keys())
        for candidate in (wanted, self.default):
            if candidate and candidate.casefold() in [key.casefold() for key in allowed]:
                return self.get(candidate)
        return self.get(allowed[0]) if allowed else self.get(self.default)


def read(folder: Path, name: str = MODES_NAME) -> ModeSet:
    """Read the modes beside the tool, reporting faults rather than raising."""
    path = folder / name
    if tomllib is None:
        raise ModeError("reading modes.toml needs tomllib, which is in the standard library from Python 3.11")
    if not path.is_file():
        raise ModeError(f"no modes file at {name}; it holds the system prompt for each way of conversing")

    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ModeError(f"{name} could not be read: {error}") from error

    blocks = document.get("modes")
    if not isinstance(blocks, dict) or not blocks:
        raise ModeError(f"{name} has no [modes.<name>] blocks")

    problems: list[str] = []
    modes: list[Mode] = []
    for key in sorted(blocks):
        block = blocks[key]
        if not isinstance(block, dict):
            problems.append(f"[modes.{key}] is not a table")
            continue
        system = str(block.get("system", "")).strip()
        if not system:
            problems.append(f"[modes.{key}] has no system prompt, so it was left out")
            continue
        temperature = block.get("temperature", DEFAULT_TEMPERATURE)
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            problems.append(f"[modes.{key}] temperature is not a number, so {DEFAULT_TEMPERATURE} was used")
            temperature = DEFAULT_TEMPERATURE
        modes.append(
            Mode(
                key=key,
                label=str(block.get("label", key)).strip() or key,
                temperature=float(temperature),
                system=system,
            )
        )

    if not modes:
        raise ModeError(f"{name} holds no usable modes")

    declared = str(document.get("default", "")).strip()
    keys = [mode.key for mode in modes]
    if declared and declared not in keys:
        problems.append(f"default = '{declared}' is not a mode, so '{keys[0]}' was used")
        declared = ""
    return ModeSet(
        modes=tuple(modes),
        default=declared or keys[0],
        source=name,
        problems=problems,
    )
