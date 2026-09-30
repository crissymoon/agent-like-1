"""The text encoder as a quantised file of its own, held packed.

Why this module exists. A diffusion model is driven by a language model, and that
language model is usually the largest thing in the pipeline. There is a common
way to hand the diffusion library a quantised transformer, and no equivalent for
the encoder: the loader that reads a llama.cpp file into a transformers model
(`gguf_file=`) dequantises every tensor as it reads it, so a 1.66 GB file arrives
as 8.04 GB of weights. That is not a saving, it is the same encoder written
smaller, and on a machine that could not hold the full precision encoder it
changes nothing.

The way around it is the one the diffusion library already uses for its own
transformers. A quantised tensor is stored as bytes with a scale per block and is
expanded a block at a time during the forward pass, so the resident cost is the
file and the transient cost is one layer. This module builds a transformers model
whose linear layers and embedding are those packed tensors, using the diffusion
library's own packed linear, its own dequantiser and transformers' own tensor name
map, so the arithmetic is the library's and not a second implementation of the
same quantisation.

What is established before anything is loaded:

  * the file carries the architecture the profile's encoder names, read from the
    file's metadata rather than from its file name
  * every field transformers maps for that architecture agrees with the base
    repository's text encoder config, which is fetched rather than assumed
  * the block count, the contents of each block and the vocabulary dimension
    agree with the tensors in the file, so a truncated download is refused
  * the conditioning layers the pipeline reads exist, and stacking them equals
    the transformer's joint attention width, which ties the encoder to the
    diffusion model that conditions on it

Nothing here downloads weights. The configs are kilobytes, and they are needed to
build the model whether the file is accepted or not.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import ModelProfile
from .probe import expandable, reader
from .sizes import human_bytes, saved

#: An explicit encoder file, for a machine that keeps its weights outside the
#: model directory. Without it the file is found by reading what is present.
ENCODER_VAR = "IMG_TEXT_ENCODER"

#: The architectures a vision language model declares. Named so a file of the
#: wrong kind is refused with the reason rather than with a shape error.
VISION_ARCHITECTURES = ("qwen3_vl", "qwen2_5_vl", "qwen3vl")

#: Tensor types that are their own values rather than packed ones. Everything
#: else is stored as bytes and expanded, which is the distinction the loader
#: makes and therefore the one this module follows.
PLAIN_TYPES = ("F32", "F16")


class EncoderError(RuntimeError):
    """No encoder could be found, verified or built."""


_PACKED: tuple | None = None


def packed():
    """The diffusion library's packed-weight machinery, imported once.

    `GGUFLinear` holds a stored tensor and expands it to a block at a time;
    `dequantize_gguf_tensor` is the expansion for every type the library supports;
    `GGUFParameter` is the byte buffer that remembers which type it holds.

    They are reached inside the library's quantiser module rather than at its top
    level, which is a private path and a deliberate dependency: reimplementing
    quantisation here would be a second version of arithmetic that has to match
    byte for byte, and the failure would be silent. The import is guarded so a
    library that moves them says so instead of raising an attribute error deep in
    a forward pass.
    """
    global _PACKED
    if _PACKED is None:
        try:
            from diffusers.quantizers.gguf.utils import (
                GGUFLinear,
                GGUFParameter,
                dequantize_gguf_tensor,
            )
        except ImportError as error:
            raise EncoderError(
                "this diffusion library does not expose its packed weight layers, "
                f"so a quantised text encoder cannot be held packed: {error}"
            ) from error
        _PACKED = (GGUFParameter, GGUFLinear, dequantize_gguf_tensor)
    return _PACKED


_EMBEDDING: type | None = None


def embedding_class():
    """The packed embedding module, defined on first use.

    Defined inside a function because it is a `torch.nn.Module` and this module is
    imported by `--list`, which has to run on a machine with no torch installed.
    Everything heavy here is reached the same way: after the decision to load.
    """
    global _EMBEDDING
    if _EMBEDDING is None:
        import torch
        import torch.nn as nn

        class QuantisedEmbedding(nn.Module):
            """An embedding table that stays packed and expands a row at a time.

            A lookup touches a handful of rows. Expanding the whole table would
            cost several hundred megabytes to use a dozen of them, and on a
            machine that is counting megabytes that is the difference between
            running and not.

            It works because of how a block quantisation is laid out: a row of the
            table is a whole number of blocks and no block spans two rows, so the
            rows asked for are the stored bytes of those rows. The slice is over
            the first dimension of the byte array, which the reader presents in
            the order the model wants them in.
            """

            def __init__(self, num_embeddings: int, embedding_dim: int, compute_dtype) -> None:
                super().__init__()
                self.num_embeddings = num_embeddings
                self.embedding_dim = embedding_dim
                self.compute_dtype = compute_dtype
                # Registered as a parameter by the loader once the bytes are on
                # the device; None until then so the module exists first.
                self.weight = None

            def forward(self, inputs):
                """Read the table, by index or as the output projection.

                The same table is described twice by this model: once as the
                embedding a token id looks up, and once as the projection that
                turns a hidden state back into a distribution over the
                vocabulary. The file holds it once, so both are this module, and
                which one was asked for is the dtype of what arrived: integer ids
                are a lookup, and anything else is a projection over the whole
                table. Only the lookup avoids expanding the table, and that is
                the case that matters; a projection has to read every weight to
                answer, so it is expanded.
                """
                import torch
                import torch.nn.functional as functional

                GGUFParameter, _, dequantize_gguf_tensor = packed()
                stored = self.weight

                if inputs.dtype in (torch.long, torch.int32, torch.int64, torch.int16):
                    rows = stored.as_tensor()[inputs.reshape(-1)].contiguous()
                    expanded = dequantize_gguf_tensor(
                        GGUFParameter(rows, quant_type=stored.quant_type)
                    )
                    return expanded.to(self.compute_dtype).reshape(
                        *inputs.shape, self.embedding_dim
                    )

                # Expanded weights arrive in float16, because a GGUF scale is a
                # half. Left that way the multiply is asked to mix two dtypes,
                # which the accelerator refuses; the packed layer casts for the
                # same reason.
                table = dequantize_gguf_tensor(
                    GGUFParameter(stored.as_tensor(), quant_type=stored.quant_type)
                ).to(self.compute_dtype)
                return functional.linear(inputs.to(self.compute_dtype), table)

        _EMBEDDING = QuantisedEmbedding
    return _EMBEDDING


@dataclass(frozen=True)
class EncoderFacts:
    """What the file says about itself, read from the byte-level index."""

    path: Path
    bytes_on_disk: int
    tensor_count: int
    quantised_tensors: int
    architecture: str
    block_count: int
    per_block: dict[int, int]
    embedding_dims: tuple[int, ...] | None
    packed_bytes: int | None
    exact_bytes: int
    histogram: dict[str, int]

    def shrink(self) -> str:
        return saved(self.packed_bytes, self.exact_bytes)


@dataclass
class EncoderVerdict:
    """A candidate file held against the model that would condition on it."""

    profile: ModelProfile
    facts: EncoderFacts
    agreements: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    uncompared: list[str] = field(default_factory=list)
    unsupported: tuple[str, ...] = ()
    library: str = "absent"
    conditioning: int | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self, indent: str = "  ") -> str:
        spec = self.profile.text
        facts = self.facts
        lines = [
            f"encoder file      {facts.path.name} ({human_bytes(facts.bytes_on_disk)})",
            f"architecture      {facts.architecture or 'none declared'}",
            f"tensors           {facts.tensor_count} ({facts.quantised_tensors} stored packed)",
            f"blocks            {facts.block_count}",
            f"quantisation      {facts.histogram}",
        ]
        if facts.packed_bytes is None:
            lines.append("packed size       unknown, a tensor type has no size here")
        else:
            lines.append(
                f"packed size       {human_bytes(facts.packed_bytes)} against "
                f"{human_bytes(facts.exact_bytes)} held exactly "
                f"(saving {facts.shrink()})"
            )
        if facts.embedding_dims is not None:
            lines.append(f"embedding         {tuple(facts.embedding_dims)}")
        if self.conditioning is not None and spec is not None:
            layers = ", ".join(str(layer) for layer in spec.layers)
            lines.append(f"conditioning      layers {layers} stacking to {self.conditioning}")

        if self.library == "checked":
            if self.unsupported:
                lines.append(f"library support   cannot expand {', '.join(self.unsupported)}")
            else:
                lines.append("library support   every type in this file can be expanded")
        elif self.library == "absent":
            lines.append("library support   not checked, no diffusion library on this machine")
        else:
            lines.append(f"library support   could not be asked: {self.library}")

        for note in self.agreements:
            lines.append(f"{indent}ok    {note}")
        for note in self.uncompared:
            lines.append(f"{indent}note  {note}")
        for note in self.problems:
            lines.append(f"{indent}FAIL  {note}")
        return "\n".join(lines)


def _index(path: Path):
    """Read the file's index once and hand it around rather than around again."""
    return reader().read_index(path)


def facts_of(index, path: Path) -> EncoderFacts:
    """What the tensors say, in the terms a reader would ask it in."""
    per_block: dict[int, int] = {}
    for tensor in index.tensors:
        parts = tensor.name.split(".")
        if len(parts) > 2 and parts[0] == "blk" and parts[1].isdigit():
            per_block[int(parts[1])] = per_block.get(int(parts[1]), 0) + 1
    return EncoderFacts(
        path=path,
        bytes_on_disk=path.stat().st_size,
        tensor_count=len(index.tensors),
        quantised_tensors=index.quantised_count(),
        architecture=str(index.metadata.get("general.architecture", "")),
        block_count=len(index.block_indices("blk")),
        per_block=per_block,
        embedding_dims=index.dims("token_embd.weight"),
        packed_bytes=index.packed_bytes(),
        exact_bytes=index.exact_bytes(),
        histogram=index.type_histogram(),
    )


def candidates(directory: Path, profile: ModelProfile) -> list[Path]:
    """Weight files in the directory whose own metadata names this encoder.

    Read, not guessed: a file is a candidate because it declares the
    architecture, so a renamed download is still found and a file of the wrong
    kind is never offered. The diffusion transformers carry no architecture at
    all - one of the two here declares nothing - so they cannot answer, and a
    language model that is not this encoder answers with a different label.
    """
    spec = profile.text
    if spec is None or not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.gguf")):
        if not path.is_file():
            continue
        try:
            declared = str(_index(path).metadata.get("general.architecture", ""))
        except Exception:  # noqa: BLE001 - an unreadable file is simply not a candidate
            continue
        if declared == spec.architecture:
            found.append(path)
    return found


def find(directory: Path, profile: ModelProfile, explicit: str | Path | None = None) -> Path | None:
    """The file to use: the one named, or the environment's, or the single found.

    Several candidates are refused rather than picked between. Two files of the
    same architecture are two different encoders - a quantisation each, or a
    different fine-tune - and choosing one silently would be a guess with the
    output as the evidence.
    """
    spec = profile.text
    if spec is None:
        return None

    specified = explicit or os.environ.get(ENCODER_VAR)
    if specified:
        path = Path(specified).expanduser()
        if not path.is_file():
            raise EncoderError(f"text encoder file not found: {path}")
        return path

    found = candidates(directory, profile)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        names = ", ".join(path.name for path in found)
        raise EncoderError(
            f"{len(found)} files declare themselves as {profile.label}'s text encoder: {names}\n"
            f"  name the one to use with --text-encoder, or set {ENCODER_VAR}"
        )
    return None


def load_config(profile: ModelProfile):
    """The base repository's own text encoder config, fetched rather than assumed.

    A kilobyte, and needed either way. It is what the file is measured against
    and what the model is built from, so the encoder has one description of its
    shape and it is the repository's rather than a copy kept here.
    """
    spec = profile.text
    if spec is None:
        raise EncoderError(f"{profile.label} declares no quantised text encoder")
    import transformers

    model_class = getattr(transformers, spec.class_name, None)
    if model_class is None:
        raise EncoderError(
            f"this transformers build has no {spec.class_name}, which {profile.label} needs"
        )
    try:
        return model_class.config_class.from_pretrained(profile.repo, subfolder=spec.subfolder)
    except Exception as error:  # noqa: BLE001 - the reason is the report
        raise EncoderError(
            f"could not read {spec.subfolder}/config.json from {profile.repo}: {error}"
        ) from error


def mapped_fields(architecture: str) -> dict[str, str | None]:
    """How transformers itself maps a GGUF architecture onto a config.

    The library's own table. A field checked here is therefore a field the loader
    reads, and there is no second table to drift out of step with it.
    """
    from transformers.integrations import GGUF_CONFIG_MAPPING

    return dict(GGUF_CONFIG_MAPPING.get(architecture, {}))


def _comparable(stored, expected) -> bool:
    """Whether a value from the file and a value from a config are the same value.

    Integers exactly; floats to within the precision metadata is written in, so
    an epsilon stored as float32 does not read as a change from the double a
    config parses to.
    """
    if isinstance(stored, bool) or isinstance(expected, bool):
        return bool(stored) == bool(expected)
    if isinstance(stored, (int, float)) and isinstance(expected, (int, float)):
        if isinstance(stored, int) and isinstance(expected, int):
            return stored == expected
        return math.isclose(float(stored), float(expected), rel_tol=1e-6, abs_tol=1e-12)
    return str(stored) == str(expected)


def joint_width(profile: ModelProfile) -> int | None:
    """The width the diffusion transformer expects to be conditioned with.

    Read from the transformer's own config in the base repository, which is where
    the other half of the contract lives. It is what makes the stack of encoder
    layers checkable against the model they are handed to rather than only
    against each other.
    """
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(profile.repo, "transformer/config.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("joint_attention_dim")
    except Exception:  # noqa: BLE001 - an unavailable check is reported as unavailable
        return None


def verify(profile: ModelProfile, path: Path, config, check_library: bool = True) -> EncoderVerdict:
    """Hold a candidate file against the config of the encoder it claims to be.

    Every check runs, so a file that is the wrong shape and also holds a type the
    installed library cannot expand reports both: fixing one and rediscovering
    the other costs another download.
    """
    spec = profile.text
    if spec is None:
        raise EncoderError(f"{profile.label} declares no quantised text encoder")

    index = _index(path)
    facts = facts_of(index, path)
    verdict = EncoderVerdict(profile=profile, facts=facts)

    if facts.architecture != spec.architecture:
        if facts.architecture in VISION_ARCHITECTURES:
            verdict.problems.append(
                f"this file is a {facts.architecture} vision language model and "
                f"{profile.label} conditions on a {spec.architecture} causal one"
            )
        else:
            verdict.problems.append(
                f"the file declares {facts.architecture or 'no architecture'} where "
                f"{profile.label}'s encoder is {spec.architecture}"
            )
    else:
        verdict.agreements.append(f"the file declares the {spec.architecture} architecture")

    mapping = mapped_fields(spec.architecture)
    compared = 0
    absent = []
    for suffix, attribute in sorted(mapping.items()):
        if attribute is None:
            continue
        key = f"{spec.architecture}.{suffix}"
        if key not in index.metadata:
            absent.append(key)
            continue
        # An attribute the installed library no longer exposes is reported as
        # such rather than passed over. A field quietly not compared is how a
        # check stops checking anything without anybody noticing.
        if not hasattr(config, attribute):
            verdict.uncompared.append(
                f"{key} was not compared: {type(config).__name__} has no {attribute}"
            )
            continue
        stored = index.metadata[key]
        expected = getattr(config, attribute)
        compared += 1
        if _comparable(stored, expected):
            verdict.agreements.append(f"{key} is {stored}, and {attribute} is {expected}")
        else:
            verdict.problems.append(
                f"{key} is {stored} where {type(config).__name__}.{attribute} is {expected}"
            )
    if compared == 0:
        verdict.problems.append(
            f"the file carries none of the keys transformers reads for "
            f"{spec.architecture}, so it cannot be shown to be this encoder"
        )

    layers = getattr(config, "num_hidden_layers", None)
    if layers is not None and facts.block_count != layers:
        verdict.problems.append(
            f"the file holds {facts.block_count} blocks where the encoder has {layers} layers"
        )

    counts = list(facts.per_block.values())
    if counts and len(set(counts)) > 1:
        common = max(set(counts), key=counts.count)
        offenders = [index_ for index_, count in sorted(facts.per_block.items()) if count != common]
        verdict.problems.append(
            f"blocks {offenders[:6]} hold a different number of tensors from the rest, "
            "so the file is not complete"
        )

    vocabulary = getattr(config, "vocab_size", None)
    if facts.embedding_dims is None:
        verdict.problems.append("there is no token embedding in the file")
    elif vocabulary is None:
        pass
    elif vocabulary in facts.embedding_dims:
        verdict.agreements.append(f"the token embedding covers {vocabulary} tokens")
    else:
        verdict.problems.append(
            f"the token embedding is {tuple(facts.embedding_dims)} and does not cover "
            f"the encoder's {vocabulary} tokens"
        )

    hidden = getattr(config, "hidden_size", None)
    if layers is not None:
        beyond = [layer for layer in spec.layers if layer >= layers]
        if beyond:
            verdict.problems.append(
                f"conditioning is read at layers {beyond} and the encoder has {layers}"
            )
        elif hidden is not None:
            verdict.conditioning = hidden * len(spec.layers)
            expected = joint_width(profile)
            if expected is None:
                verdict.agreements.append(
                    f"conditioning is {verdict.conditioning} wide; the transformer's "
                    "own width could not be read to compare"
                )
            elif expected == verdict.conditioning:
                verdict.agreements.append(
                    f"conditioning is {verdict.conditioning} wide, the transformer's "
                    "joint attention width"
                )
            else:
                verdict.problems.append(
                    f"conditioning is {verdict.conditioning} wide where the transformer "
                    f"takes {expected}"
                )

    if absent:
        verdict.agreements.append(
            f"{len(absent)} of transformers' keys are not written in the file: "
            f"{', '.join(absent)}"
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


@dataclass
class Loaded:
    """A text encoder built from a file, with what it cost to build it."""

    model: object
    facts: EncoderFacts
    dtype_name: str
    device: str
    elapsed: float
    assigned: int
    packed: int
    tied: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  built            {self.assigned} tensors into the encoder in "
            f"{self.elapsed:.1f}s",
            f"  held packed      {self.packed} of them, expanded a block at a time",
            f"  stored           {human_bytes(self.facts.packed_bytes)} on {self.device} "
            f"at {self.dtype_name}",
            f"  if held exactly  {human_bytes(self.facts.exact_bytes)}, which is "
            f"{self.facts.shrink()} saved",
        ]
        if self.tied:
            lines.append(
                f"  tied             {', '.join(self.tied)} read the same table, "
                "which the file holds once"
            )
        return "\n".join(lines)


def _holder(model, name: str):
    """The module a dotted name is held under, and the name it is held as.

    A name with no dot is held by the model itself, which is how a tied output
    projection is described: `lm_head` and not `model.lm_head`.
    """
    holder_name, _, attribute = name.rpartition(".")
    if not holder_name:
        return model, attribute
    return model.get_submodule(holder_name), attribute


def _tie_output(model, config) -> list[str]:
    """Give the output projection the table the file does hold.

    A model with tied word embeddings writes that table once and describes it
    twice: as the embedding a token id looks up, and as the projection that turns
    a hidden state back into a distribution over the vocabulary. The file holds it
    once, so the second description is left without the shape the file contains.
    That is not a missing weight, it is the same table read the other way round,
    so the module that reads it that way is given the table itself.

    Only a holder with nothing of its own is replaced, so a model that keeps its
    two tables apart is left alone and the unplaced weight is reported instead.
    """
    if not getattr(config, "tie_word_embeddings", False):
        return []
    table = model.get_input_embeddings()
    if table is None:
        return []

    tied = []
    for name, parameter in list(model.named_parameters()):
        if parameter.device.type != "meta":
            continue
        parent, leaf = _holder(model, name.rpartition(".")[0])
        holder = getattr(parent, leaf, None)
        if holder is None or holder is table:
            continue
        if any(held.device.type != "meta" for held in holder.parameters()):
            continue
        setattr(parent, leaf, table)
        tied.append(name.rpartition(".")[0])
    return tied


def _swap_linears(model, mapping, quantised, dtype):
    """Replace every linear that will hold packed weights with the packed layer.

    The replacement is made with the diffusion library's own layer so the forward
    pass expands the stored bytes exactly as it does for the transformer, and it
    is made before the weights are placed so nothing is ever held twice.
    """
    import torch.nn as nn
    from accelerate import init_empty_weights

    GGUFParameter, GGUFLinear, _ = packed()
    swapped = 0
    for gguf_name in quantised:
        target = mapping.get(gguf_name)
        if target is None or not target.endswith(".weight"):
            continue
        owner, attribute = _holder(model, target.rsplit(".", 1)[0])
        current = getattr(owner, attribute, None)
        if not isinstance(current, nn.Linear):
            continue
        with init_empty_weights():
            replacement = GGUFLinear(
                current.in_features,
                current.out_features,
                current.bias is not None,
                compute_dtype=dtype,
            )
        replacement.source_cls = type(current)
        replacement.requires_grad_(False)
        setattr(owner, attribute, replacement)
        swapped += 1
    return swapped


def _swap_embedding(model, mapping, quantised, dtype):
    """The same for a packed embedding table, which needs a modal lookup.

    Handled apart because the library's packed layer is a linear and an embedding
    is a gather: the shape of the arithmetic differs, so the class differs.
    """
    import torch.nn as nn
    from accelerate import init_empty_weights

    for gguf_name in quantised:
        target = mapping.get(gguf_name)
        if target is None or not gguf_name.startswith("token_embd"):
            continue
        owner, attribute = _holder(model, target.rsplit(".", 1)[0])
        current = getattr(owner, attribute, None)
        if not isinstance(current, nn.Embedding):
            continue
        replacement = embedding_class()(current.num_embeddings, current.embedding_dim, dtype)
        setattr(owner, attribute, replacement)
        return True
    return False


def _value(tensor, dtype, device):
    """One stored tensor as what it should be in the model.

    A packed type stays bytes and is wrapped so the forward pass knows how to read
    it; a plain type is a number and is cast to the compute dtype. The copy is
    deliberate: the reader hands out a view of a mapped file, which is read-only
    and must not be written through, and a view would keep the whole file mapped
    for as long as the model lives.
    """
    import numpy as np
    import torch

    GGUFParameter = packed()[0]
    data = torch.from_numpy(np.asarray(tensor.data).copy()).to(device)
    if tensor.tensor_type.name in PLAIN_TYPES:
        return data.to(dtype)
    return GGUFParameter(data, quant_type=tensor.tensor_type)


def load(profile: ModelProfile, path: Path, config, device: str, dtype_name: str) -> Loaded:
    """Build the encoder with its weights packed, straight onto the device.

    Each tensor is read from the file, moved to where it will live and placed, one
    at a time, so the host holds one tensor rather than the model. That ordering
    is the point: the reason for loading a quantised encoder at all is memory, and
    a load that assembles eight gigabytes in host memory to save four on the
    device has spent the saving before the first step.
    """
    import torch
    import transformers
    from accelerate import init_empty_weights
    from gguf import GGUFReader
    from transformers.modeling_gguf_pytorch_utils import (
        TENSOR_PROCESSORS,
        TensorProcessor,
        get_gguf_hf_weights_map,
    )

    from .builder import torch_dtype

    spec = profile.text
    if spec is None:
        raise EncoderError(f"{profile.label} declares no quantised text encoder")

    started = time.time()
    dtype = torch_dtype(dtype_name)
    model_class = getattr(transformers, spec.class_name, None)
    if model_class is None:
        raise EncoderError(f"this transformers build has no {spec.class_name}")

    with init_empty_weights():
        model = model_class(config)

    processor = TENSOR_PROCESSORS.get(spec.architecture, TensorProcessor)(config=config.to_dict())
    mapping = get_gguf_hf_weights_map(
        model, processor, model_type=config.model_type, num_layers=config.num_hidden_layers
    )

    gguf_reader = GGUFReader(str(path))
    tensors = {tensor.name: tensor for tensor in gguf_reader.tensors}
    quantised = {
        name for name, tensor in tensors.items() if tensor.tensor_type.name not in PLAIN_TYPES
    }

    _swap_linears(model, mapping, quantised, dtype)
    embedded = _swap_embedding(model, mapping, quantised, dtype)

    assigned = 0
    packed_count = 0
    with torch.no_grad():
        for name, tensor in tensors.items():
            target = mapping.get(name)
            if target is None:
                continue
            owner, attribute = _holder(model, target)
            value = _value(tensor, dtype, device)
            if isinstance(value, torch.nn.Parameter):
                setattr(owner, attribute, value)
                packed_count += 1
            else:
                setattr(owner, attribute, torch.nn.Parameter(value, requires_grad=False))
            assigned += 1

    # The output projection is tied to the embedding table, and the file holds
    # that table once, so the projection is given the table rather than a copy of
    # it or a guess at what it would have been.
    tied = _tie_output(model, config)
    model.tie_weights()
    model.eval()

    meta = [name for name, parameter in model.named_parameters() if parameter.device.type == "meta"]
    if meta:
        raise EncoderError(
            f"{len(meta)} of the encoder's parameters were never placed, starting with "
            f"{', '.join(meta[:4])}\n"
            "  the file does not hold everything this encoder needs"
        )
    if not embedded:
        raise EncoderError("the token embedding was not replaced, so the file is not an encoder")

    index = _index(path)
    return Loaded(
        model=model,
        facts=facts_of(index, path),
        dtype_name=dtype_name,
        device=device,
        elapsed=time.time() - started,
        assigned=assigned,
        packed=packed_count,
        tied=tied,
    )


def smoke(model, hidden: int, layers: tuple[int, ...]) -> str:
    """Run a short prompt through the encoder and report what came out.

    Eight tokens and no padding, so the check costs seconds rather than minutes
    and still exercises every block, every packed expansion and the embedding
    lookup. What it proves is that the three conditioning layers exist and stack
    to the width the transformer takes; what it does not prove is anything about
    the quality of the quantisation, which no check here can.
    """
    import torch

    tokens = torch.arange(1000, 1000 + 8, dtype=torch.long, device=next(model.parameters()).device)
    started = time.time()
    with torch.no_grad():
        output = model(input_ids=tokens.unsqueeze(0), output_hidden_states=True, use_cache=False)
    states = output.hidden_states
    missing = [layer for layer in layers if layer >= len(states)]
    if missing:
        raise EncoderError(f"the encoder returned no hidden state for layers {missing}")
    stacked = torch.stack([states[layer] for layer in layers], dim=1)
    width = stacked.shape[-1] * stacked.shape[1]
    if width != hidden:
        raise EncoderError(
            f"the conditioning stack is {width} wide where this encoder is {hidden}"
        )
    return (
        f"  forward          {len(states)} hidden states in {time.time() - started:.1f}s, "
        f"layers {', '.join(str(layer) for layer in layers)} stack to {width}"
    )
