"""What models exist, read from `models.toml` rather than compiled into a list.

The question this module answers is narrow and it is the one a user has: of the
weight files on this disk, which are language models, which are image models, and
which are neither. That answer is data, so it is a file, and adding a model is a
block in the file and no code change at all.

Three deliberate restraints.

It does not describe architectures. What a diffusion library needs in order to
run a GGUF is a profile, and the profiles live in `tools/imgmodels/catalog.py`
beside the loader that uses them. An image entry here names the profile rather
than repeating it, so there is one description of FLUX.2 klein and not two that
can disagree.

It does not hide anything. A `.gguf` in the weight directory that no entry
mentions is reported as unregistered, so a download that was never wired up, or
a file whose name drifted, is visible rather than simply absent from the menu.

It reads the file as data and checks it as data. A missing `file`, an unknown
role, a profile that does not exist: each is collected and reported with the
entry that caused it, because one bad block should not cost the others their
place in the list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import paths

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on a Python before 3.11
    tomllib = None  # type: ignore[assignment]

REGISTRY_NAME = "models.toml"

#: The parts a weight file can play. `ignore` is not a silence: it is a statement
#: that a file is present and deliberately not offered, which reads very
#: differently from a file nobody has accounted for.
ROLES: tuple[str, ...] = ("text", "image", "projector", "encoder", "ignore")

#: Which role a file plays when its entry does not say. A language model is the
#: common case and the one that is useful by default.
DEFAULT_ROLE = "text"


class RegistryError(RuntimeError):
    """The registry could not be read at all, as opposed to read with problems."""


@dataclass(frozen=True)
class Entry:
    """One weight file, and what it is for."""

    key: str
    label: str
    file: str
    roles: tuple[str, ...]
    profile: str | None
    context: int
    modes: tuple[str, ...]
    note: str

    def has(self, role: str) -> bool:
        return role in self.roles

    def path(self, directory: Path) -> Path:
        return directory / self.file


@dataclass
class Registry:
    """The entries, the directory they are read against, and what was wrong."""

    entries: tuple[Entry, ...]
    directory: Path
    source: str
    problems: list[str] = field(default_factory=list)
    unregistered_files: tuple[str, ...] = ()

    def by_role(self, role: str) -> list[Entry]:
        """Every entry that plays a part, in the order the file lists them."""
        return [entry for entry in self.entries if entry.has(role) and not entry.has("ignore")]

    def present(self, entry: Entry) -> bool:
        return entry.path(self.directory).is_file()

    def here(self, role: str) -> list[Entry]:
        """The entries that play a part and whose file is actually on this disk."""
        return [entry for entry in self.by_role(role) if self.present(entry)]

    def missing(self) -> list[Entry]:
        """Entries whose file is absent, so the menu can say so rather than fail."""
        return [entry for entry in self.entries if not entry.has("ignore") and not self.present(entry)]

    def get(self, name: str) -> Entry:
        """An entry by any name it answers to, refusing an ambiguous partial.

        A key, a label, a file name or a file name without its extension. A
        partial name is accepted only when it narrows the list to one, so a typo
        is an error and not whichever model happened to sort first.
        """
        wanted = name.strip().casefold()
        if not wanted:
            raise RegistryError("no model was named")

        for entry in self.entries:
            if wanted in _names(entry):
                return entry
        partial = [entry for entry in self.entries if any(wanted in n for n in _names(entry))]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            listed = ", ".join(entry.key for entry in partial)
            raise RegistryError(f"'{name}' matches more than one model: {listed}")
        known = ", ".join(entry.key for entry in self.entries) or "none"
        raise RegistryError(f"no model named '{name}'\n  registered: {known}")

    def default_for(self, role: str) -> Entry:
        """The first entry of a kind whose file is here, so nothing to choose."""
        available = self.here(role)
        if available:
            return available[0]
        if self.by_role(role):
            names = ", ".join(entry.file for entry in self.by_role(role))
            raise RegistryError(
                f"no {role} model is on this disk\n  registered: {names}\n"
                f"  looked in {paths.record(self.directory)}"
            )
        raise RegistryError(f"no {role} model is registered in {self.source}")


def _names(entry: Entry) -> tuple[str, ...]:
    stem = Path(entry.file).stem.casefold()
    return (
        entry.key.casefold(),
        entry.label.casefold(),
        entry.file.casefold(),
        stem,
        stem.replace("_", "-"),
    )


def _need_toml() -> None:
    if tomllib is None:
        raise RegistryError(
            "reading models.toml needs tomllib, which is in the standard library "
            "from Python 3.11; this interpreter is older"
        )


def read(folder: Path, models_dir: Path, name: str = REGISTRY_NAME) -> Registry:
    """Read the registry beside the tool and measure it against the directory."""
    path = folder / name
    _need_toml()
    if not path.is_file():
        raise RegistryError(
            f"no registry at {paths.record(path)}\n"
            "  it is the file that says which weight file is which kind of model"
        )

    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RegistryError(f"{path.name} could not be read: {error}") from error

    blocks = document.get("models")
    if not isinstance(blocks, dict) or not blocks:
        raise RegistryError(f"{path.name} has no [models.<name>] blocks")

    problems: list[str] = []
    entries: list[Entry] = []
    for key in sorted(blocks):
        block = blocks[key]
        split = _split_name(block)
        if split is not None:
            # TOML reads a dot inside a bare key as a path: `[models.llama-3.2-3b]`
            # becomes `models`, then `llama-3`, then `2-3b`. The model is not
            # broken, the header is, and the remedy is one pair of quotes. Said
            # here because the symptom otherwise is a model that is present in
            # the file and absent from the list, with no reason given.
            problems.append(
                f"[[models.{key}]] is not a model: a name containing a dot has to be "
                f'quoted, as [models."{key}.{split}"]'
            )
            continue
        entry, trouble = _entry(key, block)
        problems.extend(trouble)
        if entry is not None:
            entries.append(entry)

    registry = Registry(
        entries=tuple(entries),
        directory=models_dir,
        source=path.name,
        problems=problems,
    )
    registry.unregistered_files = tuple(
        sorted(
            candidate.name
            for candidate in _weight_files(models_dir)
            if candidate.name not in {entry.file for entry in entries}
        )
    )
    return registry


def _split_name(block) -> str | None:
    """The rest of a name that TOML split on a dot, or nothing when it did not.

    A block that is really the tail of a longer dotted header has no `file` and
    holds a table of its own, which nothing else in this file does.
    """
    if not isinstance(block, dict) or "file" in block:
        return None
    nested = [name for name, value in block.items() if isinstance(value, dict)]
    if len(nested) != 1:
        return None
    return nested[0]


def _entry(key: str, block) -> tuple[Entry | None, list[str]]:
    """One block as an entry, with every fault named rather than raised."""
    problems: list[str] = []
    if not isinstance(block, dict):
        return None, [f"[models.{key}] is not a table"]

    file_name = str(block.get("file", "")).strip()
    if not file_name:
        problems.append(f"[models.{key}] names no file")

    roles = block.get("roles", [DEFAULT_ROLE])
    if isinstance(roles, str):
        roles = [roles]
    if not isinstance(roles, list):
        problems.append(f"[models.{key}] roles must be a list")
        roles = [DEFAULT_ROLE]
    cleaned: list[str] = []
    for role in roles:
        text = str(role).strip().casefold()
        if text not in ROLES:
            problems.append(
                f"[models.{key}] role '{role}' is not one of {', '.join(ROLES)}"
            )
            continue
        cleaned.append(text)
    if not cleaned:
        cleaned = [DEFAULT_ROLE]

    profile = block.get("profile")
    profile = str(profile).strip() if profile is not None else None
    if "image" in cleaned:
        known = _profile_keys()
        if profile is None:
            problems.append(
                f"[models.{key}] is an image model and names no profile; "
                f"the profiles are {', '.join(known) or 'none available'}"
            )
        elif known and profile not in known:
            problems.append(
                f"[models.{key}] names profile '{profile}', which is not in the "
                f"catalog; the profiles are {', '.join(known)}"
            )

    modes = block.get("modes", [])
    if isinstance(modes, str):
        modes = [modes]
    if not isinstance(modes, list):
        problems.append(f"[models.{key}] modes must be a list")
        modes = []

    context = block.get("context", 4096)
    if not isinstance(context, int) or isinstance(context, bool):
        problems.append(f"[models.{key}] context must be a whole number")
        context = 4096

    entry = Entry(
        key=key,
        label=str(block.get("label", key)).strip() or key,
        file=file_name or f"{key}.gguf",
        roles=tuple(dict.fromkeys(cleaned)),
        profile=profile,
        context=context,
        modes=tuple(str(mode).strip() for mode in modes),
        note=str(block.get("note", "")).strip(),
    )
    # An entry with a fault is still returned, so the list stays complete and the
    # problem is reported beside the model rather than in place of it.
    return entry, problems


def _profile_keys() -> list[str]:
    """The image profiles this checkout knows, or nothing if it cannot be read."""
    try:
        from tools.imgmodels import catalog
    except ImportError:
        return []
    return [profile.key for profile in catalog.profiles()]


def _weight_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.gguf") if path.is_file())
