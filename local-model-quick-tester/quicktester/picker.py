"""Asking which model to use, when more than one would do.

The registry already knows which files are on this disk and which kind of model
each one is. What it cannot decide is which of several someone wants this time,
and that is the question asked here rather than answered by position in the file.

A picker answers to three situations and treats them differently on purpose.

No model of that kind is on the disk. That is not a question, it is a reason, so
the registry's own explanation is raised and the caller reports it.

Exactly one model would do, or nobody is at the terminal. Asking would be
theatre, so the model is returned. A run from a script must not be stopped by a
prompt nobody is there to answer.

Several models, and a person to choose between them. The list is printed with a
number against each, a name answers as well as a number, and a bad answer is
repeated rather than fatal, because a typo is not a decision to stop.
"""

from __future__ import annotations

from . import textui, theme
from .registry import Entry, RegistryError


def select(
    context,
    role: str,
    prompt: str | None = None,
    default_key: str | None = None,
) -> Entry | None:
    """The model of a kind to use, asking only when there is a choice to make.

    `None` means the person was asked and backed out. Everything else returns an
    entry whose file is on this disk.
    """
    registry = context.registry
    available = registry.here(role)
    if not available:
        # Raises, with the registry's own words for what is registered and where
        # it looked, which is more use than "there is nothing to choose".
        return registry.default_for(role)
    if len(available) == 1 or not textui.interactive():
        return available[0]

    fallback = _index_of(available, default_key or available[0].key)
    _show(context, role, available, fallback)
    question = prompt or "which one (a number or a name)"

    while True:
        answer = textui.ask(question, default=str(fallback))
        if answer is None:
            return None
        chosen, problem = _match(context, role, available, answer)
        if chosen is not None:
            return chosen
        textui.warn(problem)


def _show(context, role: str, available: list[Entry], fallback: int) -> None:
    """The numbered list, with the default marked and each model's note under it."""
    palette = theme.active()
    key_width = max(len(entry.key) for entry in available) + 2
    textui.say()
    textui.heading(f"{role} models on this disk")
    for index, entry in enumerate(available, 1):
        mark = palette.paint("*", "accent") if index == fallback else " "
        textui.say(
            f"    {mark} {textui.pad(str(index), 4)}"
            f"{textui.pad(entry.key, key_width)}{entry.label}"
        )
        if entry.note:
            textui.note(entry.note, indent="         ")
    absent = [entry for entry in context.registry.by_role(role) if not context.registry.present(entry)]
    if absent:
        textui.note(
            f"{len(absent)} registered {role} model(s) are not on this disk "
            "and cannot be chosen"
        )
    textui.say()


def _match(
    context, role: str, available: list[Entry], answer: str
) -> tuple[Entry | None, str]:
    """The entry an answer names, or why it names nothing offerable here."""
    text = answer.strip()
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(available):
            return available[index - 1], ""
        return None, f"'{answer}' is not one of the {len(available)} on the list"

    try:
        entry = context.registry.get(text)
    except RegistryError as error:
        return None, str(error)

    if entry in available:
        return entry, ""
    if not entry.has(role):
        return None, (
            f"{entry.label} is registered as {', '.join(entry.roles)}, "
            f"not as a {role} model"
        )
    return None, f"{entry.label} is registered but its file is not on this disk"


def _index_of(available: list[Entry], key: str | None) -> int:
    """Where a key sits in the printed list, or the first position when absent."""
    if key is not None:
        for index, entry in enumerate(available, 1):
            if entry.key == key:
                return index
    return 1
