"""One generation, in the argument names the pipeline in question actually wants.

The pipelines differ in more than their weights. The guidance argument is named
`true_cfg_scale` on one and `guidance_scale` on the other; a negative prompt is
wired to real classifier free guidance in one and is meaningless in the other,
because a step distilled model runs with guidance switched off no matter what it
is passed. Those differences are columns in the catalog, so this module reads
them instead of knowing them, and passing `--negative` to a distilled model is
reported as having no effect rather than silently dropped.

A generation is described by a request rather than by twenty keyword arguments,
so the caller can log exactly what was asked for and the same request can be
replayed against a different model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .catalog import ModelProfile
from .plan import Plan


@dataclass
class Request:
    """What to generate, before it is translated into a pipeline's own terms."""

    prompt: str
    steps: int | None = None
    size: int | None = None
    seed: int = 42
    image: Path | None = None
    negative: str | None = None
    cfg: float | None = None
    notes: list[str] = field(default_factory=list)

    def resolved_steps(self, profile: ModelProfile) -> int:
        return self.steps if self.steps is not None else profile.steps


def generator(device: str, seed: int):
    """A generator on the device that will do the sampling.

    A generator is bound to a device, so one made for the CPU and used on an
    accelerator either raises or silently seeds differently; making it for the
    device in use is what makes a seed reproducible.
    """
    import torch

    return torch.Generator(device=device).manual_seed(seed)


def kwargs_for(request: Request, profile: ModelProfile, plan: Plan) -> dict:
    """The request translated into the pipeline's argument names.

    Three cases, and they are not the same case. A model that wires a negative
    prompt to real guidance is passed both. A model that has no negative prompt
    argument at all is an error, because the caller asked for something that
    cannot be delivered. A model that has one but runs with guidance switched off
    is neither: the request is understood, it simply cannot change the result, and
    saying so is more use than a crash or a silence.
    """
    arguments: dict = {
        "prompt": request.prompt,
        "num_inference_steps": request.resolved_steps(profile),
        "generator": generator(plan.device, request.seed),
    }
    if request.size:
        arguments["height"] = request.size
        arguments["width"] = request.size

    if request.image is not None:
        from PIL import Image

        with Image.open(request.image) as opened:
            arguments["image"] = opened.convert("RGB").copy()

    if request.negative:
        if profile.negative is not None:
            arguments[profile.negative] = request.negative
            arguments[profile.guidance] = (
                request.cfg if request.cfg is not None else profile.cfg_default
            )
        elif profile.distilled:
            request.notes.append(
                f"{profile.label} is step distilled and runs with guidance switched off, "
                "so a negative prompt cannot change the result and none was passed"
            )
        else:
            raise ValueError(f"{profile.label} takes no negative prompt")

    return arguments


def generate(pipeline, request: Request, profile: ModelProfile, plan: Plan):
    """Run one generation and return the first image."""
    import time

    arguments = kwargs_for(request, profile, plan)
    started = time.time()
    result = pipeline(**arguments)
    elapsed = time.time() - started
    return result.images[0], elapsed
