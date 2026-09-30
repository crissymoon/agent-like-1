"""The image models this repository can drive, described as data.

A profile is the answer to one question: given a single GGUF weight file, what
does a diffusion library need in order to run it? That is the architecture
configuration the file itself does not carry, the diffusers classes to build,
the parts of the base repository that the quantised file does *not* replace, and
the argument names the pipeline's `__call__` expects. None of those differ in
kind between models, so they are columns in a table.

The repository is read rather than declared. A profile names the weight file it
wants and nothing resolves a path until asked, so `python3 img_mdl_tester.py
--list` is instant and needs no diffusion library installed. A GGUF file in the
model directory that no profile claims is reported as unclaimed rather than
silently ignored, which is how a user finds out that a vision projector is not
an image model.

Adding a model is an entry in `PROFILES`. Nothing else changes: every module
downstream of this one reads the profile and never a model name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: The directory holding the weight files, and an explicit file that overrides
#: the profile's own choice. Both are read from the environment so a checkout
#: can be pointed at weights on another disk without editing this table.
MODEL_DIR_VAR = "AGENT_LIKE_MODEL_DIR"
WEIGHTS_VAR = "IMG_WEIGHTS"

#: The default image output directory: `images/`, one folder per model. A
#: generated image is the evidence that a model ran on this machine, so it sits
#: beside the other fixtures rather than in scratch. The `images/*` rules in
#: .gitignore are anchored to the top level, so a file inside a model's folder is
#: kept while a loose photograph at the top is not, which is the behaviour this
#: default relies on.
OUTPUT_DIR_VAR = "IMG_OUTPUT_DIR"


class CatalogError(RuntimeError):
    """A model was asked for by a name that matches nothing, or is not present."""


@dataclass(frozen=True)
class Signature:
    """What a weight file must contain for a profile to be the right one.

    `blocks` is a name prefix and how many distinct numbered blocks must sit
    under it, so a five layer model cannot be mistaken for a nine layer one.
    `anchors` is an exact tensor name and the exact shape it must have, in the
    file's own dimension order, which for a linear layer is the reverse of the
    torch shape: a GGUF `(in, out)` pair is a torch weight of `(out, in)`.

    The point of both is to refuse a wrong file before gigabytes of a base
    repository are downloaded, because the download happens first and the
    mismatch is discovered last.
    """

    blocks: tuple[tuple[str, int], ...] = ()
    anchors: tuple[tuple[str, tuple[int, ...]], ...] = ()


@dataclass(frozen=True)
class ModelProfile:
    """Everything a loader needs to know about one quantised image model."""

    key: str
    label: str
    weights: str
    repo: str
    pipeline: str
    transformer: str
    steps: int
    components: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    config_subfolder: str = "transformer"
    guidance: str | None = None
    cfg_default: float = 4.0
    negative: str | None = None
    distilled: bool = False
    edits: bool = False
    note: str = ""
    signature: Signature = field(default_factory=Signature)

    def weight_path(self, directory: Path) -> Path:
        """Where this profile's weight file is expected, honoured from the env."""
        override = os.environ.get(WEIGHTS_VAR)
        if override:
            return Path(override).expanduser()
        return directory / self.weights

    def present(self, directory: Path) -> bool:
        return self.weight_path(directory).is_file()

    def _names(self) -> tuple[str, ...]:
        stem = Path(self.weights).stem
        return (self.key, self.label, self.weights, stem, *self.aliases)

    def matches(self, text: str) -> bool:
        """An exact, case-insensitive match on any name this profile answers to."""
        wanted = text.strip().casefold()
        if wanted == "":
            return False
        return any(name.casefold() == wanted for name in self._names())

    def appears_in(self, text: str) -> bool:
        """A substring match, used only when it narrows the table to one row."""
        wanted = text.strip().casefold()
        if wanted == "":
            return False
        return any(wanted in name.casefold() for name in self._names())

    def modes(self) -> str:
        return "text and image" if self.edits else "text only"


#: The models this repository drives, in the order they are offered. Two entries
#: so far, and they were chosen because they differ in every column that
#: matters: one carries its architecture in its metadata and one carries none,
#: one is guidance distilled and one is not, one wants a causal language model
#: as its text encoder and one wants a vision language model, and their
#: pipelines name the guidance argument differently. A table with two rows that
#: exercised nothing would not be worth having.
PROFILES: tuple[ModelProfile, ...] = (
    ModelProfile(
        key="qwen-image-2.1",
        label="Qwen-Image 2.1",
        weights="qwen-image-2.1-Q4_K_M.gguf",
        repo="Qwen/Qwen-Image-2.1",
        pipeline="QwenImage21Pipeline",
        transformer="QwenImage21Transformer2DModel",
        steps=40,
        components=("text_encoder", "vae", "processor", "scheduler"),
        aliases=("qwen-image", "qwen image"),
        guidance="true_cfg_scale",
        cfg_default=4.0,
        negative="negative_prompt",
        edits=True,
        note=(
            "classifier free guidance is always on and `true_cfg_scale` sets it, "
            "so a negative prompt changes the result"
        ),
        signature=Signature(
            blocks=(("model.diffusion_model.transformer_blocks", 32),),
            anchors=(
                ("model.diffusion_model.img_in.weight", (64, 4096)),
                ("model.diffusion_model.proj_out.weight", (4096, 64)),
                ("model.diffusion_model.transformer_blocks.0.attn.to_q.weight", (4096, 4096)),
            ),
        ),
    ),
    ModelProfile(
        key="flux-2-klein-4b",
        label="FLUX.2 klein 4B",
        weights="flux-2-klein-4b-Q4_K_M.gguf",
        repo="black-forest-labs/FLUX.2-klein-4B",
        pipeline="Flux2KleinPipeline",
        transformer="Flux2Transformer2DModel",
        steps=8,
        components=("text_encoder", "tokenizer", "vae", "scheduler"),
        aliases=("flux-2-klein", "flux klein", "klein"),
        guidance="guidance_scale",
        cfg_default=4.0,
        distilled=True,
        edits=True,
        note=(
            "step distilled, so the pipeline reports the guidance scale as "
            "ignored and runs without classifier free guidance; eight steps is "
            "the intended operating point rather than the signature's fifty"
        ),
        signature=Signature(
            blocks=(("double_blocks", 5), ("single_blocks", 20)),
            anchors=(
                ("img_in.weight", (128, 3072)),
                ("txt_in.weight", (7680, 3072)),
                ("final_layer.linear.weight", (3072, 128)),
            ),
        ),
    ),
)


def repository_root() -> Path:
    """The checkout this package sits in: `.../gemma/tools/imgmodels` up three."""
    return Path(__file__).resolve().parents[2]


def model_directory() -> Path:
    override = os.environ.get(MODEL_DIR_VAR)
    return Path(override).expanduser() if override else repository_root() / "models"


def output_directory() -> Path:
    override = os.environ.get(OUTPUT_DIR_VAR)
    return Path(override).expanduser() if override else repository_root() / "images"


def profiles() -> tuple[ModelProfile, ...]:
    return PROFILES


def present(directory: Path) -> list[ModelProfile]:
    """The profiles whose weight file this machine actually holds, in order."""
    return [profile for profile in PROFILES if profile.present(directory)]


def weight_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.gguf") if path.is_file())


def unclaimed(directory: Path) -> list[Path]:
    """Weight files in the directory that no profile knows how to drive.

    A projector, a language model, or a download that no longer matches its
    profile. Reported so that a file sitting in the models directory is either
    runnable or explained, never simply invisible.
    """
    claimed = {profile.weights for profile in PROFILES}
    return [path for path in weight_files(directory) if path.name not in claimed]


def resolve(selection: str | None, directory: Path) -> ModelProfile:
    """The profile a user named, or the first one this machine can run.

    A name may be a key, an alias, a file name, or a file name without its
    extension. A partial name is accepted only when it narrows the table to one
    row, so a typo is an error rather than a surprise.
    """
    if selection:
        wanted = selection.strip()
        exact = [profile for profile in PROFILES if profile.matches(wanted)]
        if len(exact) == 1:
            return exact[0]
        partial = [profile for profile in PROFILES if profile.appears_in(wanted)]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(profile.key for profile in partial)
            raise CatalogError(f"'{wanted}' matches more than one model: {names}")
        known = ", ".join(profile.key for profile in PROFILES)
        held = ", ".join(path.name for path in weight_files(directory)) or "none"
        raise CatalogError(
            f"no model matches '{wanted}'\n"
            f"  known models: {known}\n"
            f"  weight files in {directory}: {held}"
        )

    available = present(directory)
    if available:
        return available[0]

    wanted = ", ".join(profile.weights for profile in PROFILES)
    raise CatalogError(
        f"no weight file for any known model in {directory}\n"
        f"  looked for: {wanted}\n"
        f"  set {WEIGHTS_VAR} to a file, or {MODEL_DIR_VAR} to another directory"
    )
