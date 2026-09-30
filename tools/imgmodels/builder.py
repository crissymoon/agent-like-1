"""Turning a profile and a weight file into a runnable pipeline.

The one thing this module exists to get right is that a quantised file is not a
pipeline directory. `from_pretrained` wants a directory holding every component
of a pipeline; a single GGUF holds one component, the diffusion transformer, and
the rest of the pipeline lives in the base repository. So the transformer is
loaded from the file on its own and handed to the pipeline as a finished object,
which is what stops the pipeline from fetching the full precision transformer it
would otherwise download.

Holding the weights packed is a property of the quantisation config: it names a
compute dtype for the arithmetic while the storage stays as the file wrote it.
That is why a two and a half gigabyte file does not become a seven gigabyte
model in memory, and `describe` reports both numbers so the claim is checkable
rather than asserted.

Class names come from the profile, so adding a model that this library already
supports is a row in the catalog and not a branch here. A name the installed
library does not have is an error that says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import ModelProfile
from .plan import Plan, Weights
from .sizes import human_bytes

#: The compute dtype to name on the command line, mapped to a torch dtype only
#: inside a function so that `--list` runs without torch importable.
DTYPES = ("bfloat16", "float16", "float32")

#: bfloat16 has the wider exponent and is the safe default for a quantisation
#: that widens its arithmetic; float16 is faster on some accelerators and can
#: overflow on others, so it is offered rather than assumed.
DEFAULT_DTYPE = "bfloat16"


class BuildError(RuntimeError):
    """The installed library cannot build what the profile asks for."""


def torch_dtype(name: str):
    """A dtype name from the command line, resolved to a torch dtype."""
    import torch

    table = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    if name not in table:
        raise BuildError(f"unknown dtype '{name}'; choose one of {', '.join(DTYPES)}")
    return table[name]


def _library_class(name: str):
    """A diffusers class by name, with a message that names the missing piece."""
    import diffusers

    found = getattr(diffusers, name, None)
    if found is None:
        version = getattr(diffusers, "__version__", "unknown")
        raise BuildError(
            f"this diffusers build ({version}) has no {name}\n"
            "  GGUF loading and the newest pipelines are on main:\n"
            "    pip install git+https://github.com/huggingface/diffusers"
        )
    return found


def _quantisation_config(dtype):
    """The config that keeps the weights packed and expands them per block."""
    try:
        from diffusers import GGUFQuantizationConfig
    except ImportError as error:
        raise BuildError(
            "this diffusers build has no GGUF support; install it from source:\n"
            "  pip install git+https://github.com/huggingface/diffusers"
        ) from error
    return GGUFQuantizationConfig(compute_dtype=dtype)


def load_transformer(profile: ModelProfile, path: Path, dtype_name: str, device: str | None = None):
    """Load the diffusion transformer from the GGUF file, weights left packed.

    The architecture config is fetched from the profile's repository rather than
    the library's default, which is a different model and, for one of these, a
    gated one that cannot be read at all.

    `device` places each tensor as it is read instead of assembling the model in
    host memory first and moving it afterwards. On a machine where the host is the
    tighter of the two memories that ordering is the difference between loading
    and paging.
    """
    dtype = torch_dtype(dtype_name)
    transformer_class = _library_class(profile.transformer)
    return transformer_class.from_single_file(
        str(path),
        config=profile.repo,
        subfolder=profile.config_subfolder,
        quantization_config=_quantisation_config(dtype),
        dtype=dtype,
        low_cpu_mem_usage=True,
        device=device,
    )


def describe_loaded(model, exact_bytes: int | None = None) -> str:
    """What the loaded model actually occupies, measured rather than assumed.

    The obvious test for "are the weights still packed" is whether a weight
    tensor has a `uint8` dtype, and it is wrong. The loader gives every weight a
    byte buffer, the ones it stores exactly as well as the quantised ones, and
    expands them a block at a time during the forward pass. Under that scheme
    every layer looks packed and the question is not answered.

    What cannot be faked is how many bytes are held. `numel` times `element_size`
    over the parameters is the storage the process is carrying, and comparing it
    with what the same weights would need held exactly is the measurement that
    says whether the file's quantisation survived the load.
    """
    import torch

    parameters = list(model.parameters())
    storage = sum(parameter.numel() * parameter.element_size() for parameter in parameters)
    byte_backed = sum(1 for parameter in parameters if parameter.dtype == torch.uint8)
    linears = sum(1 for module in model.modules() if isinstance(module, torch.nn.Linear))
    compute = next(
        (parameter.dtype for parameter in parameters if parameter.dtype != torch.uint8),
        None,
    )

    lines = [
        f"  held             {human_bytes(storage)} across {len(parameters)} tensors",
        f"  byte buffers     {byte_backed} of {len(parameters)}, expanded per block in the "
        f"forward pass",
        f"  linear layers    {linears}",
        f"  compute dtype    {compute}",
    ]
    if exact_bytes:
        lines.append(
            f"  if held exactly  {human_bytes(exact_bytes)}, which is "
            f"{storage / exact_bytes:.1%} of that in storage"
        )
    return "\n".join(lines)


@dataclass
class Built:
    """A pipeline and the pieces a caller needs to call it."""

    pipeline: object
    profile: ModelProfile
    plan: Plan
    dtype_name: str


def build(profile: ModelProfile, weights: Weights, plan: Plan, dtype_name: str, encoder=None) -> Built:
    """Assemble the pipeline: the file for one component, the repo for the rest.

    `encoder` is a text encoder already built from its own weight file. Passing it
    as a finished component is what stops the pipeline from fetching the full
    precision encoder the repository holds, exactly as the transformer is passed
    rather than downloaded.
    """
    transformer = load_transformer(
        profile,
        weights.transformer,
        dtype_name,
        device=plan.device if plan.offload == "none" else None,
    )
    pipeline_class = _library_class(profile.pipeline)
    pipeline = pipeline_class.from_pretrained(
        profile.repo,
        transformer=transformer,
        text_encoder=encoder,
        dtype=torch_dtype(dtype_name),
        low_cpu_mem_usage=True,
    )
    enable_memory_savers(pipeline)
    place(pipeline, plan)
    return Built(pipeline=pipeline, profile=profile, plan=plan, dtype_name=dtype_name)


def enable_memory_savers(pipeline) -> None:
    """Turn on the VAE options that trade a little speed for a lot of memory.

    Both are attributes of the VAE rather than of the pipeline, and both are
    optional, so a pipeline whose VAE does not offer them is left alone.
    """
    vae = getattr(pipeline, "vae", None)
    if vae is None:
        return
    for name in ("enable_slicing", "enable_tiling"):
        method = getattr(vae, name, None)
        if callable(method):
            method()


def place(pipeline, plan: Plan) -> None:
    """Put the pipeline where the plan decided it goes."""
    if plan.offload == "none":
        pipeline.to(plan.device)
    elif plan.offload == "model":
        pipeline.enable_model_cpu_offload(device=plan.device)
    else:
        pipeline.enable_sequential_cpu_offload(device=plan.device)
