"""Driving quantised image models that are kept as single GGUF weight files.

The pieces are separated by what each one needs to know and nothing else:

    catalog   what image models exist, as data
    probe     reading a weight file and holding it against a profile
    plan      what this machine can hold and where the offload boundary is
    builder   turning a profile and a file into a runnable pipeline
    runner    one generation, with the arguments that pipeline wants
    output    where a generated image goes, named from the prompt
    encoder   a quantised language model standing in as the text encoder

`catalog` is the only module that knows model names, and `builder` is the only
one that imports a diffusion library, so listing the models costs nothing and
adding a model is an entry in a table rather than a branch in a loader.
"""

from __future__ import annotations

__all__ = ["builder", "catalog", "encoder", "output", "plan", "probe", "runner"]
