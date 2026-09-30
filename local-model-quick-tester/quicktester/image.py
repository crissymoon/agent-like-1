"""Generating images, with the diffusion package this repository already has.

Nothing about loading a quantised image model is decided here. `tools/imgmodels`
owns the architecture table, the memory plan, the encoder substitution and the
pipeline assembly, and it is shared with the non interactive image command. This
module is the part that belongs to an interactive tool: asking before a run that
this machine cannot finish, showing a real progress bar driven by the denoising
loop rather than a guess, and writing the image where the other images go.

The order of operations is the one that saves the most time, and it is deliberate.
The weight file is read and held against the profile that claims it first, so a
wrong file is refused before anything is downloaded. Then the arrangement is
planned from the Hub's own metadata, so "will this fit" is answered before the
fetch rather than after it. Only then is anything loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.imgmodels import builder, catalog as catalog_module, encoder, output, plan as planner
from tools.imgmodels import probe, runner

from . import textui, theme
from .registry import Entry

#: Raised instead of a library's own error, so the caller can offer the install
#: menu rather than print a traceback about a missing import.
class ImageError(RuntimeError):
    """The model cannot be run here, with the reason rather than a stack trace."""


def capability(entry: Entry) -> tuple[bool, str]:
    """Whether this entry can be driven, and what is missing when it cannot.

    Three separate things have to be true, and they fail for different reasons
    with different remedies, so they are reported apart rather than as one
    unusable row.
    """
    if not entry.profile:
        return False, (
            f"{entry.label} names no architecture profile, so its weights have "
            "nothing to be loaded against; add a profile to "
            "tools/imgmodels/catalog.py and name it in models.toml"
        )
    try:
        profile = catalog_module.resolve(entry.profile, catalog_module.model_directory())
    except catalog_module.CatalogError as error:
        return False, str(error)

    present, reason = _library()
    if not present:
        return False, (
            f"the diffusion library is not installed in this interpreter "
            f"({reason}); the install menu builds the environment"
        )
    return True, profile.label


def _library() -> tuple[bool, str]:
    try:
        import diffusers  # noqa: F401
    except ImportError as error:
        return False, str(error)
    return True, ""


def profile_for(entry: Entry):
    """The architecture profile an entry names."""
    if not entry.profile:
        raise ImageError(f"{entry.label} names no architecture profile")
    try:
        return catalog_module.resolve(entry.profile, catalog_module.model_directory())
    except catalog_module.CatalogError as error:
        raise ImageError(str(error)) from error


@dataclass
class Ready:
    """A run that has been checked and planned, and not yet built."""

    entry: Entry
    profile: object
    path: Path
    weights: object
    plan: object
    encoder_path: Path | None = None


def prepare(entry: Entry, device: str = "auto") -> Ready:
    """Read the file, hold it against the profile, and price the run."""
    ok, detail = capability(entry)
    if not ok:
        raise ImageError(detail)

    profile = profile_for(entry)
    path = entry.path(catalog_module.model_directory())
    if not path.is_file():
        raise ImageError(
            f"{entry.label} weights are not on this disk\n"
            f"  expected {path.name} in {catalog_module.model_directory()}"
        )

    verdict = probe.match(profile, path)
    if not verdict.ok:
        raise ImageError(
            f"{path.name} is not a usable {profile.label} weight file\n"
            + "\n".join(f"  {problem}" for problem in verdict.problems)
        )

    encoder_path, encoder_verdict = find_encoder(profile)
    if encoder_verdict is not None and not encoder_verdict.ok:
        raise ImageError(
            f"{encoder_path.name} cannot serve as {profile.label}'s text encoder\n"
            + "\n".join(f"  {problem}" for problem in encoder_verdict.problems)
        )

    weights = planner.read_weights(profile, path, encoder_path)
    arrangement = planner.apply_override(
        planner.plan_run(profile, weights, None if device == "auto" else device), "auto"
    )
    return Ready(
        entry=entry,
        profile=profile,
        path=path,
        weights=weights,
        plan=arrangement,
        encoder_path=encoder_path,
    )


def find_encoder(profile) -> tuple[Path | None, object | None]:
    """A quantised text encoder file, when this model declares one.

    A model whose conditioning is a vision language model read through a
    processor has no single file that can stand in for it, and a model whose
    architecture file is simply absent from this disk has none either. Both
    return nothing, and the caller says which, rather than implying the full
    precision encoder is the only option left.
    """
    if profile.text is None:
        return None, None
    try:
        found = encoder.find(catalog_module.model_directory(), profile)
    except encoder.EncoderError:
        return None, None
    if found is None:
        return None, None
    try:
        config = encoder.load_config(profile)
        return found, encoder.verify(profile, found, config)
    except Exception:  # noqa: BLE001 - a config that will not fetch is not a verdict
        return found, None


def describe(ready: Ready) -> list[str]:
    """The plan, as lines a person reads before saying yes."""
    arrangement = ready.plan
    profile = ready.profile
    lines = [
        textui.row("model", f"{profile.label} ({profile.key})"),
        textui.row("weights", f"{ready.path.name} ({_size(ready.path)})"),
        textui.row("base", f"{profile.repo}, for every part the file does not carry"),
        textui.row("steps", str(profile.steps)),
        textui.row("device", f"{arrangement.device}, {arrangement.offload}"),
        textui.row("reason", arrangement.note),
    ]
    if ready.encoder_path is not None:
        lines.append(
            textui.row(
                "encoder",
                f"{ready.encoder_path.name}, held packed in place of the full "
                "precision encoder",
            )
        )
    for warning in arrangement.warnings:
        lines.append(f"  {theme.active().paint('warn', 'warn')}  {warning}")
    return lines


def too_big(ready: Ready) -> str | None:
    """Why this run should not be started, or nothing when it should.

    A plan that has already concluded the machine cannot hold the model is not a
    warning to read past. Starting anyway spends an hour moving pages rather than
    numbers and ends by being killed, so it is refused, and the two numbers that
    decided it are the explanation.
    """
    if ready.plan.verdict == planner.EXCEEDS_HOST:
        return (
            "this machine cannot hold the model, so nothing was started\n"
            f"  {ready.plan.note}"
        )
    return None


def check(entry: Entry, device: str = "auto") -> list[str]:
    """Report on the file and the fit, and load nothing heavy."""
    ready = prepare(entry, device)
    lines = describe(ready)
    verdict = probe.match(ready.profile, ready.path)
    lines.append(textui.row("tensors", f"{verdict.observation.tensor_count} ({verdict.observation.quantised_tensors} stored packed)"))
    if verdict.observation.file_type:
        lines.append(textui.row("file type", verdict.observation.file_type))
    lines.append(textui.row("packed size", f"{_bytes(verdict.observation.packed_bytes)} against {_bytes(verdict.observation.exact_bytes)} held exactly"))
    lines.append(textui.row("library", verdict.library if verdict.library != "checked" else "every quantisation in this file can be expanded"))
    return lines


def draw(
    entry: Entry,
    prompt: str,
    size: int = 0,
    steps: int = 0,
    seed: int = 42,
    condition: Path | None = None,
    negative: str | None = None,
    cfg: float | None = None,
    device: str = "auto",
    out: str | None = None,
    allow_paging: bool = False,
    ready: Ready | None = None,
) -> tuple[Path, float, list[str]]:
    """Build the pipeline and generate one image, reporting progress as it goes.

    `ready` lets a caller that has already read the file and planned the run hand
    that work in rather than doing it twice, which matters because reading a
    tensor table and asking the Hub for metadata is not free.
    """
    ready = ready or prepare(entry, device)
    refusal = too_big(ready)
    if refusal and not allow_paging:
        raise ImageError(refusal + "\n  run with --allow-paging to try it anyway")

    profile = ready.profile
    notes: list[str] = []

    text_encoder = None
    if ready.encoder_path is not None:
        with textui.Spinner("holding the text encoder packed on the device", "sweep") as turn:
            config = encoder.load_config(profile)
            loaded = encoder.load(
                profile, ready.encoder_path, config, ready.plan.device, builder.DEFAULT_DTYPE
            )
            text_encoder = loaded.model
            turn.set("text encoder is held packed")

    with textui.Spinner(f"building {profile.label} from its base repository", "sweep"):
        built = builder.build(
            profile, ready.weights, ready.plan, builder.DEFAULT_DTYPE, encoder=text_encoder
        )

    total = steps or profile.steps
    request = runner.Request(
        prompt=prompt,
        steps=steps or None,
        size=size or None,
        seed=seed,
        image=condition,
        negative=negative,
        cfg=cfg,
    )
    with textui.Progress(prompt, total) as bar:
        request.on_step = lambda index, count: bar.tick()
        image, elapsed = runner.generate(built.pipeline, request, profile, ready.plan)

    notes.extend(request.notes)
    target = output.target(catalog_module.output_directory(), profile.key, prompt, explicit=out)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target, elapsed, notes


def _size(path: Path) -> str:
    return _bytes(path.stat().st_size) if path.is_file() else "unknown"


def _bytes(value: int | None) -> str:
    from tools.imgmodels.sizes import human_bytes

    return human_bytes(value)
