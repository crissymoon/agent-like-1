"""Reading a weight file to find out what it is, without running it.

The whole point of this module is that it is cheap. Nothing here loads a model:
a GGUF's header and its tensor table are at the front of the file, and reading
them costs a few hundred kilobytes however large the weights are. So the answer
to "how big is this, how is it quantised, and will it run here" arrives in under
a second and before anything is downloaded.

The two kinds of model are described differently because they differ in what
they can say about themselves. A language model declares its own shape in its
metadata: architecture, layer count, embedding width, context, parameter count.
A diffusion model declares almost nothing, and one of the two here declares
nothing at all, so its shape has to be counted from the tensors. Both readings
come from the file.
"""

from __future__ import annotations

from pathlib import Path

from tools.imgmodels import catalog as catalog_module
from tools.imgmodels import plan as planner, probe
from tools.imgmodels.sizes import human_bytes, saved

from . import textui, theme
from .registry import Entry


def reader():
    """This repository's GGUF reader, refusing a same named module from elsewhere."""
    from tools import gguf

    if not hasattr(gguf, "read_header"):
        raise RuntimeError(
            f"the wrong 'gguf' module answered: {getattr(gguf, '__file__', 'unknown')}\n"
            "  this reader is tools/gguf.py; do not put tools/ itself on sys.path"
        )
    return gguf


def text_lines(entry: Entry, path: Path) -> list[str]:
    """What a language model's own header says about it."""
    lines: list[str] = []
    try:
        header = reader().read_header(path)
    except Exception as error:  # noqa: BLE001 - the reading is the report
        return [f"  the header could not be read: {type(error).__name__}: {error}"]

    facts = header.summary()
    lines.append(textui.row("architecture", facts["architecture"] or "not declared"))
    if facts["name"]:
        lines.append(textui.row("name", facts["name"]))
    if facts["size_label"]:
        lines.append(textui.row("size label", facts["size_label"]))
    if facts["parameter_count"]:
        lines.append(textui.row("parameters", f"{facts['parameter_count']:,}"))
    lines.append(textui.row("context", f"{facts['context_length']:,}" if facts["context_length"] else "not declared"))
    if facts["embedding_length"]:
        lines.append(textui.row("embedding", f"{facts['embedding_length']:,}"))
    if facts["block_count"]:
        lines.append(textui.row("blocks", str(facts["block_count"])))
    lines.append(
        textui.row(
            "quantisation",
            facts["quantisation"] or f"file type {facts['file_type']}",
        )
    )
    lines.append(textui.row("tensors", f"{facts['tensor_count']:,}"))
    lines.append(textui.row("metadata", f"{facts['metadata_entries']:,} entries, GGUF v{facts['gguf_version']}"))

    if entry.context and facts["context_length"] and entry.context > facts["context_length"]:
        lines.append(
            f"  {theme.active().paint('note', 'info')}  this entry asks for "
            f"{entry.context:,} of context and the file declares "
            f"{facts['context_length']:,}, so the declared window is what a session opens"
        )
    return lines


def image_lines(entry: Entry) -> list[str]:
    """What an image model is, counted from its tensors and its profile."""
    if not entry.profile:
        return [
            f"  {theme.active().paint('note', 'info')}  {entry.label} names no architecture "
            "profile, so nothing here knows how its weights are arranged"
        ]

    directory = catalog_module.model_directory()
    try:
        profile = catalog_module.resolve(entry.profile, directory)
    except catalog_module.CatalogError as error:
        return [f"  {error}"]

    path = entry.path(directory)
    if not path.is_file():
        return [f"  the weight file is not on this disk: {path.name}"]

    verdict = probe.match(profile, path)
    observed = verdict.observation
    lines = [
        textui.row("pipeline", profile.pipeline),
        textui.row("transformer", profile.transformer),
        textui.row("base", profile.repo),
        textui.row("steps", str(profile.steps)),
        textui.row("modes", profile.modes()),
        textui.row("tensors", f"{observed.tensor_count} ({observed.quantised_tensors} stored packed)"),
    ]
    if observed.file_type:
        lines.append(textui.row("file type", observed.file_type))
    lines.append(textui.row("quantisation", str(observed.type_histogram)))
    if observed.packed_bytes is not None:
        lines.append(
            textui.row(
                "packed size",
                f"{human_bytes(observed.packed_bytes)} against "
                f"{human_bytes(observed.exact_bytes)} held exactly "
                f"(saving {saved(observed.packed_bytes, observed.exact_bytes)})",
            )
        )
    if observed.block_counts:
        counted = ", ".join(f"{name} {count}" for name, count in sorted(observed.block_counts.items()))
        lines.append(textui.row("blocks", counted))
    lines.append(
        textui.row(
            "library",
            "every quantisation in this file can be expanded"
            if verdict.library == "checked"
            else f"not checked ({verdict.library})",
        )
    )
    return lines


def fit_lines(entry: Entry, device: str = "auto") -> list[str]:
    """Whether the run fits, priced from the Hub's own metadata.

    The diffusion library is asked for the device's memory, so this cannot be
    answered without it, and a machine that has not installed the image stack yet
    should still be able to read a weight file. So the answer is "not priced
    here" with the reason, rather than a traceback about a missing module.
    """
    if not entry.profile:
        return []
    directory = catalog_module.model_directory()
    try:
        profile = catalog_module.resolve(entry.profile, directory)
    except catalog_module.CatalogError as error:
        return [f"  {error}"]
    path = entry.path(directory)
    if not path.is_file():
        return []

    from .image import find_encoder

    try:
        encoder_path, _ = find_encoder(profile)
        weights = planner.read_weights(profile, path, encoder_path)
        arrangement = planner.apply_override(
            planner.plan_run(profile, weights, None if device == "auto" else device), "auto"
        )
    except ImportError as error:
        return [
            textui.row(
                "fit",
                "not priced here, the memory plan needs the diffusion library",
            ),
            f"  {theme.active().paint('note', 'info')}  {error}",
            f"  {theme.active().paint('note', 'info')}  option 15 on the menu installs it",
        ]

    lines = [
        textui.row("device", arrangement.device),
        textui.row("device budget", human_bytes(arrangement.device_budget)),
        textui.row("host available", human_bytes(arrangement.host_available)),
        textui.row("total to hold", human_bytes(weights.total_bytes) if weights.components_known else "unknown"),
        textui.row("offload", arrangement.offload),
        textui.row("verdict", arrangement.verdict),
        textui.row("reason", arrangement.note),
    ]
    if arrangement.verdict == planner.EXCEEDS_HOST:
        lines.append(
            f"  {theme.active().paint('warn', 'warn')}  this machine cannot hold the model; "
            "a quantised text encoder is the next thing to try"
        )
    return lines


def report(entry: Entry, device: str = "auto") -> list[str]:
    """Everything known about one entry, in the order a reader would ask."""
    directory = catalog_module.model_directory()
    path = entry.path(directory)
    kind = "image" if entry.has("image") else ("text" if entry.has("text") else "other")

    lines = [
        textui.row("model", f"{entry.label} ({entry.key})"),
        textui.row("file", entry.file),
        textui.row("roles", ", ".join(entry.roles)),
        textui.row("size on disk", human_bytes(path.stat().st_size) if path.is_file() else "not present"),
        textui.row("present", "yes" if path.is_file() else f"no, expected in {directory.name}"),
    ]
    if entry.note:
        lines.append(textui.row("note", entry.note))
    lines.append("")

    if kind == "text" and path.is_file():
        lines.extend(text_lines(entry, path))
    elif kind == "image":
        lines.extend(image_lines(entry))
        lines.append("")
        lines.extend(fit_lines(entry, device))
    else:
        lines.append(
            textui.row(
                "reading",
                "a projector is read by a language model's host rather than on its own, "
                "so there is nothing here to measure it against",
            )
        )
    return lines
