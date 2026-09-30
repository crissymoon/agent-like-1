"""Reading a weight file and holding it against the profile that claims it.

Two questions are answered before anything is downloaded or loaded.

The first is whether this is the right file at all. A GGUF holds weights and
almost no description of them: one of the two models here records its
architecture in its metadata and the other records nothing whatsoever, so the
only thing both have in common is the tensors themselves. The block counts and
the shapes of three named tensors are enough to separate architectures, and they
are read from the file rather than trusted from its name.

The second is whether the file can be run as it stands. A quantised tensor is
stored packed and expanded a block at a time during the forward pass, so whether
a given quantisation is supported is a property of the installed library. A type
the library cannot expand fails at the first denoising step, which is after the
base repository has been downloaded, so it is worth knowing sooner. Only the
diffusion library's own list is consulted; nothing here guesses what it accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .catalog import ModelProfile
from .sizes import human_bytes, saved


def _gguf():
    """This repository's GGUF reader, imported by its package path.

    Imported as `tools.gguf` rather than as the top level `gguf`, because the
    diffusion library imports a package of that name to read the same files. A
    top level module here would shadow it, and the shadow is quiet: the reader
    loads, the quantisation support does not, and the symptom is a model that
    will not run rather than a module that was not found.

    The shape check is for the reverse mistake, where something else's `gguf`
    answers first. Better to refuse than to call `read_index` on a module that
    does not have one.
    """
    from tools import gguf

    if not hasattr(gguf, "read_index"):
        raise RuntimeError(
            f"the wrong 'gguf' module answered: {getattr(gguf, '__file__', 'unknown')}\n"
            "  this reader is tools/gguf.py; do not put tools/ itself on sys.path"
        )
    return gguf


@dataclass(frozen=True)
class Observation:
    """What the weight file says about itself, with no library loaded."""

    path: Path
    bytes_on_disk: int
    tensor_count: int
    quantised_tensors: int
    block_counts: dict[str, int]
    type_histogram: dict[str, int]
    packed_bytes: int | None
    exact_bytes: int
    architecture: str
    file_type: str

    def shrink(self) -> str:
        """How much smaller the packed weights are than holding them exactly."""
        return saved(self.packed_bytes, self.exact_bytes)


def _observe(index, path: Path, prefixes: tuple[str, ...]) -> Observation:
    """Everything the tensor table says, in the terms a reader would ask it in."""
    return Observation(
        path=path,
        bytes_on_disk=path.stat().st_size,
        tensor_count=len(index.tensors),
        quantised_tensors=index.quantised_count(),
        block_counts={prefix: len(index.block_indices(prefix)) for prefix in prefixes},
        type_histogram=index.type_histogram(),
        packed_bytes=index.packed_bytes(),
        exact_bytes=index.exact_bytes(),
        architecture=str(index.metadata.get("general.architecture", "")),
        file_type=_gguf().file_type_name(index.metadata.get("general.file_type")),
    )


@dataclass
class Verdict:
    """A file held against a profile: what agreed, and what did not."""

    profile: ModelProfile
    observation: Observation
    agreed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unsupported: tuple[str, ...] = ()
    library: str = "absent"

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self, indent: str = "  ") -> str:
        observed = self.observation
        lines = [
            f"weights           {observed.path.name} ({human_bytes(observed.bytes_on_disk)})",
            f"tensors           {observed.tensor_count} "
            f"({observed.quantised_tensors} stored packed)",
        ]
        if observed.architecture:
            lines.append(f"architecture      {observed.architecture}")
        if observed.file_type:
            lines.append(f"file type         {observed.file_type}")
        lines.append(f"quantisation      {observed.type_histogram}")

        packed = observed.packed_bytes
        if packed is None:
            lines.append("packed size       unknown, a tensor type has no size here")
        else:
            lines.append(
                f"packed size       {human_bytes(packed)} against "
                f"{human_bytes(observed.exact_bytes)} held exactly "
                f"(saving {observed.shrink()})"
            )
        if observed.block_counts:
            counts = ", ".join(
                f"{name} {count}" for name, count in sorted(observed.block_counts.items())
            )
            lines.append(f"blocks            {counts}")

        if self.library == "checked":
            if self.unsupported:
                lines.append(f"library support   cannot expand {', '.join(self.unsupported)}")
            else:
                lines.append("library support   every type in this file can be expanded")
        elif self.library == "absent":
            lines.append("library support   not checked, no diffusion library on this machine")
        else:
            # A check that could not be made is reported as such. Reporting it as
            # absent would hide a broken install behind a missing one.
            lines.append(f"library support   could not be asked: {self.library}")

        for note in self.agreed:
            lines.append(f"{indent}ok    {note}")
        for note in self.problems:
            lines.append(f"{indent}FAIL  {note}")
        return "\n".join(lines)


def match(profile: ModelProfile, path: Path, check_library: bool = True) -> Verdict:
    """Hold one weight file against one profile and report every disagreement.

    Every check runs. A file that is the wrong architecture and also holds an
    unsupported quantisation reports both, because fixing one and rediscovering
    the other costs another download.
    """
    index = _gguf().read_index(path)
    verdict = Verdict(
        profile=profile,
        observation=_observe(index, path, tuple(name for name, _ in profile.signature.blocks)),
    )

    for prefix, expected in profile.signature.blocks:
        found = len(index.block_indices(prefix))
        if found == expected:
            verdict.agreed.append(f"{prefix} holds {expected} blocks")
        else:
            verdict.problems.append(
                f"{prefix} holds {found} blocks where {profile.label} has {expected}"
            )

    for name, expected in profile.signature.anchors:
        found = index.dims(name)
        if found is None:
            verdict.problems.append(f"tensor {name} is absent, so this is not {profile.label}")
        elif tuple(found) == tuple(expected):
            verdict.agreed.append(f"{name} is {tuple(found)}")
        else:
            verdict.problems.append(
                f"tensor {name} is {tuple(found)} where {profile.label} has {tuple(expected)}"
            )

    unknown = index.unknown_types()
    if unknown:
        verdict.problems.append(
            f"tensor types {unknown} have no size here, so the weights cannot be measured"
        )

    if check_library:
        blocked, status = expandable(index)
        verdict.library = status
        if status == "checked":
            verdict.unsupported = blocked

    return verdict


def _import_one(target: str) -> tuple[object | None, str]:
    """Import a dotted name, telling a missing package from a broken one.

    A module that is not installed raises `No module named`. Anything else,
    including a name that cannot be found inside a module that did load, means
    something is installed and wrong, and that is reported rather than rounded
    off to "not installed".
    """
    import importlib

    module_name, _, attribute = target.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        if str(error).startswith("No module named"):
            return None, "absent"
        return None, f"{type(error).__name__}: {error}"
    except Exception as error:  # noqa: BLE001 - the reason is the report
        return None, f"{type(error).__name__}: {error}"
    return getattr(module, attribute, None), "ok"


def expandable(index) -> tuple[tuple[str, ...], str]:
    """Quantisations in the file that the diffusion library cannot expand.

    Returns the offending type names and how the question went: `checked`, or
    `absent` when there is nothing installed to ask, or the failure itself when
    something is installed and will not answer. A library that is present but
    broken must not be reported as a library that is missing, which is what a
    bare except around the import would do.
    """
    supported, first = _import_one(
        "diffusers.quantizers.gguf.utils.SUPPORTED_GGUF_QUANT_TYPES"
    )
    enum, second = _import_one("gguf.GGMLQuantizationType")
    for status in (first, second):
        if status != "ok":
            return (), status

    known = {int(member) for member in supported}
    offenders: set[str] = set()
    for tensor in index.tensors:
        if not tensor.quantised or tensor.type_id in known:
            continue
        try:
            offenders.add(enum(tensor.type_id).name)
        except ValueError:
            offenders.add(f"type-{tensor.type_id}")
    return tuple(sorted(offenders)), "checked"
