"""Where a generated image goes.

One rule, in one place, because two callers need it: the naming of a file from a
prompt, and the directory a model's images live in. The non interactive image
command and the interactive tool both generate images, and they must not disagree
about what an image is called or where it is put.

The default directory is `images/<model>/`. A generated image is evidence that a
model ran on this machine, so it sits beside the other fixtures rather than in
scratch. The `images/*` rules in .gitignore are anchored to the top level, so a
file inside a model's own folder is kept while a loose photograph at the top is
not, which is the behaviour this default relies on.
"""

from __future__ import annotations

from pathlib import Path

#: A prompt becomes a file name: lowercase, punctuation collapsed to hyphens,
#: bounded so a long prompt cannot produce a name the filesystem refuses.
SLUG_LIMIT = 40


def slugify(text: str, limit: int = SLUG_LIMIT) -> str:
    """A file name from a prompt."""
    kept = [character.lower() if character.isalnum() else "-" for character in text.strip()]
    slug = "".join(kept).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:limit].strip("-") or "image"


def model_directory(output_root: Path, key: str) -> Path:
    """The folder one model's images live in."""
    return output_root / key


def target(output_root: Path, key: str, prompt: str, explicit=None) -> Path:
    """Where an image goes: an explicit path, or this model's own folder."""
    if explicit:
        return Path(explicit).expanduser()
    return model_directory(output_root, key) / f"{slugify(prompt)}.png"
