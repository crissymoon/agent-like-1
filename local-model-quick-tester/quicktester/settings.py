"""The settings that describe this machine, kept apart from what models exist.

`models.toml` says which files are models and travels with the repository. This
says which platform to build for, which interpreter to run in and where output
goes, and it describes the machine rather than the project, so it is written
beside the tool and left out of the repository.

Two rules are inherited from the window's own settings module, because they are
the right ones. A file that will not parse is not repaired silently: it is read,
the values that could not be used fall back to their defaults, and every fallback
is reported so the settings screen can say which values it took from the default
rather than from the file. And a value whose type has drifted is treated as
unusable rather than coerced, because a string where a number belongs is a
mistake to report and not a number to guess at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

SETTINGS_NAME = "settings.local.json"

#: Platform, device and colour accept `auto` and it is the default, so a machine
#: that is not the one this was written on still works without editing anything.
#:
#: `stream` is the answer to "should the output appear as it is produced". It is
#: on because a language model that prints nothing for forty seconds looks
#: broken, and off is offered because a transcript read back is not a terminal.
DEFAULTS: dict = {
    "platform": "auto",
    "device": "auto",
    "colour": "auto",
    "theme": "auto",
    "stream": True,
    "models_dir": "",
    "venv": "",
    "transcript_dir": "model-tests",
    "save_transcript": True,
    "default_text_mode": "general",
    "prompt_end": "END",
    "image_size": 0,
    "image_steps": 0,
    "text_context": 0,
    "threads": 0,
}

#: What each setting is for, shown by the settings screen so the list is
#: readable without opening this file.
HELP: dict[str, str] = {
    "platform": "which runtime to build and run for: auto, apple-silicon, apple-intel, windows-gpu, windows-cpu, linux-gpu, linux-cpu",
    "device": "where the image models run: auto, cpu, mps, cuda",
    "colour": "auto, always or never",
    "theme": "which palette the terminal is read as: auto, dark, light",
    "stream": "print text as the model produces it rather than all at once",
    "models_dir": "where the weight files are; empty means the repository's models/",
    "venv": "the virtual environment to run in; empty means the one beside this tool",
    "transcript_dir": "where a conversation is written after a text run",
    "save_transcript": "write a markdown copy of a text run",
    "default_text_mode": "the conversation mode a text run starts in",
    "prompt_end": "the line that ends a pasted paragraph, for a terminal that cannot report a paste",
    "image_size": "square side in pixels for image models; 0 means the model's own",
    "image_steps": "denoising steps for image models; 0 means the model's own",
    "text_context": "a ceiling on the context window for every text model; 0 opens each model at its own",
    "threads": "CPU threads for a text model; 0 lets llama.cpp decide",
}

#: A stored value and a default may be a number, a truth value or a string, and
#: each has to survive a round trip through JSON. Only booleans need care in
#: Python, because `bool` is a subclass of `int`.
def _same_kind(value, default) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


@dataclass
class Settings:
    """The values in force, and what was taken from the default instead."""

    platform: str = "auto"
    device: str = "auto"
    colour: str = "auto"
    theme: str = "auto"
    stream: bool = True
    models_dir: str = ""
    venv: str = ""
    transcript_dir: str = "model-tests"
    save_transcript: bool = True
    default_text_mode: str = "general"
    prompt_end: str = "END"
    image_size: int = 0
    image_steps: int = 0
    text_context: int = 0
    threads: int = 0

    #: Names read back from the default rather than from the file, and the path
    #: the file was read from. Neither is stored, so neither can go stale.
    taken: list[str] = field(default_factory=list, compare=False)
    source: str = field(default="defaults", compare=False)

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in DEFAULTS}

    def colour_depth(self) -> int | None:
        """`auto` leaves the decision to the environment, which is the correct one."""
        return {"auto": None, "always": 24, "never": 0}.get(self.colour, None)


def path_for(folder: Path) -> Path:
    return folder / SETTINGS_NAME


def load(folder: Path) -> Settings:
    """Read the settings beside the tool, reporting every fallback."""
    path = path_for(folder)
    if not path.is_file():
        return Settings(source="defaults, no settings file yet")

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        settings = Settings(source=f"defaults, {path.name} could not be read: {error}")
        settings.taken = ["the whole file"]
        return settings

    if not isinstance(parsed, dict):
        settings = Settings(source=f"defaults, {path.name} does not hold an object")
        settings.taken = ["the whole file"]
        return settings

    known = {definition.name for definition in fields(Settings)}
    settings = Settings(source=path.name)
    for name, default in DEFAULTS.items():
        if name not in parsed or name not in known:
            continue
        value = parsed[name]
        if _same_kind(value, default):
            setattr(settings, name, value)
        else:
            settings.taken.append(name)

    for name in parsed:
        if name not in DEFAULTS and name not in ("taken", "source"):
            settings.taken.append(f"{name} (not a setting)")
    return settings


def save(folder: Path, settings: Settings) -> Path:
    """Write the settings, keeping only the keys this version knows about."""
    path = path_for(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.as_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def coerce(name: str, raw: str, default) -> tuple[object, str]:
    """One `KEY=VALUE` pair from the command line, against its default's type.

    The type comes from the default rather than from the parser, so adding a
    setting is one entry in `DEFAULTS` and no change here.
    """
    if name not in DEFAULTS:
        known = ", ".join(sorted(DEFAULTS))
        return None, f"'{name}' is not a setting; there are {known}"

    text = raw.strip()
    if isinstance(default, bool):
        lowered = text.casefold()
        if lowered in ("true", "yes", "on", "1"):
            return True, ""
        if lowered in ("false", "no", "off", "0"):
            return False, ""
        return None, f"'{name}' takes true or false, not '{raw}'"

    if isinstance(default, int):
        try:
            return int(text), ""
        except ValueError:
            return None, f"'{name}' takes a whole number, not '{raw}'"

    return text, ""


def apply_pairs(settings: Settings, pairs: list[str]) -> list[str]:
    """Apply `KEY=VALUE` arguments, returning one problem line per bad pair."""
    problems: list[str] = []
    for pair in pairs:
        name, separator, raw = pair.partition("=")
        if not separator:
            problems.append(f"'{pair}' is not KEY=VALUE")
            continue
        value, problem = coerce(name.strip(), raw, DEFAULTS.get(name.strip()))
        if problem:
            problems.append(problem)
            continue
        setattr(settings, name.strip(), value)
    return problems


def catalogue() -> tuple[tuple[str, object, str], ...]:
    """Every setting as name, its default, and what it is for.

    Built from `DEFAULTS` and `Settings` together, so the settings screen cannot
    offer a name the file does not accept, or miss one it does.
    """
    return tuple((name, DEFAULTS[name], HELP.get(name, "")) for name in DEFAULTS)
