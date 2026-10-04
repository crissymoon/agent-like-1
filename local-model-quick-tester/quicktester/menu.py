"""The screen a person sees when they run the tool with no arguments.

The menu is data: a list of items, each with a key, a name, the reason you would
choose it, and the action it runs. Adding a screen is an item in the list, and
the numbering, the columns and the grouping all follow from it rather than being
laid out by hand.

Two decisions about the shape of it.

The install items are one per platform rather than a wizard that asks questions
first. A person who wants the Windows instructions is often reading on a Mac, and
the honest answer is to print the commands for the machine named rather than to
refuse or to silently build for the one you are on. So every platform is offered,
and a plan for another machine is printed and not run.

The default answer is the first item, which is a conversation. Starting the tool
and pressing return is the shortest path to the thing it is for, and everything
else is one key away.
"""

from __future__ import annotations

from . import actions, platforms, textui, theme
from .textui import Item, Menu

#: The items, in the order they are offered. `group` starts a new block when it
#: changes, and the ordering here is the numbering on screen.
ITEMS: tuple[Item, ...] = (
    Item(
        key="1",
        label="Chat with a text model",
        hint="grammar, code, prose, or chat",
        group="run",
        action="chat",
    ),
    Item(
        key="2",
        label="Generate an image",
        hint="text to image, or edit one",
        group="run",
        action="draw",
    ),
    Item(
        key="3",
        label="Compare models",
        hint="one question, every text model",
        group="run",
        action="compare",
    ),
    Item(
        key="4",
        label="List the models",
        hint="registered, present, unplaced",
        group="machine",
        action="list",
    ),
    Item(
        key="5",
        label="Inspect a model",
        hint="size, quantisation, and fit",
        group="machine",
        action="inspect",
    ),
    Item(
        key="6",
        label="Settings",
        hint="platform, device, colour, output",
        group="machine",
        action="settings",
    ),
    Item(
        key="7",
        label="Transcripts",
        hint="the markdown of past runs",
        group="machine",
        action="transcripts",
    ),
    Item(
        key="8",
        label="Install for this machine",
        hint="whichever platform this is",
        group="install",
        action="install",
    ),
    Item(
        key="9",
        label="Install for Apple silicon",
        hint="Metal build of llama.cpp",
        group="install",
        action="install:apple-silicon",
    ),
    Item(
        key="10",
        label="Install for Apple Intel",
        hint="CPU build; no Metal on Intel",
        group="install",
        action="install:apple-intel",
    ),
    Item(
        key="11",
        label="Install for Windows GPU",
        hint="prebuilt CUDA wheel",
        group="install",
        action="install:windows-gpu",
    ),
    Item(
        key="12",
        label="Install for Windows CPU",
        hint="prebuilt CPU wheel",
        group="install",
        action="install:windows-cpu",
    ),
    Item(
        key="13",
        label="Install for Linux GPU",
        hint="prebuilt CUDA wheel",
        group="install",
        action="install:linux-gpu",
    ),
    Item(
        key="14",
        label="Install for Linux CPU",
        hint="prebuilt CPU wheel",
        group="install",
        action="install:linux-cpu",
    ),
    Item(
        key="15",
        label="Install everything",
        hint="adds the image stack, several GB",
        group="install",
        action="install:all",
    ),
    Item(
        key="h",
        label="Help",
        hint="what this is and where its pieces are",
        group="other",
        action="help",
    ),
    Item(key="q", label="Quit", hint="", group="other", action="quit"),
)

#: Kept out of the menu because it is a one line aside rather than an action.
EXTRAS: str = "enter alone starts a conversation"


def banner(context: actions.Context, palette=None, samples: tuple[int, int] | None = None) -> list[str]:
    """The heading: what this is, and what it has to work with.

    The second line is measured rather than asserted, so it is the truth about
    this machine: which platform it will build for, how many models are
    registered, and how many of each kind are actually on disk.
    """
    palette = palette or theme.active()
    text_entries = len(context.registry.here("text"))
    image_entries = len(context.registry.here("image"))
    subtitle = (
        f"{context.runtime()} on {context.device()} · {len(context.registry.entries)} registered · "
        f"{text_entries} text · {image_entries} image"
    )
    lines = [palette.paint(subtitle, "muted")]
    if samples is not None:
        used, total = samples
        lines.append(palette.paint(f"history {used} of {total} turns kept", "muted"))
    return textui.box(
        palette.paint("LOCAL MODEL QUICK TESTER", "accent", bold=True),
        lines,
        indent="  ",
    )


def build(context: actions.Context) -> Menu:
    """The menu for this machine, with the wording that only applies here."""
    items: list[Item] = []
    for item in ITEMS:
        items.append(_tailored(item, context))
    return Menu(items=items, title="what next", subtitle=EXTRAS)


def _tailored(item: Item, context: actions.Context) -> Item:
    """One item, with a hint that knows what this machine can do."""
    if item.action == "chat":
        available = context.registry.here("text")
        if not available:
            return _replace(item, hint="no text model is on this disk yet")
        if len(available) > 1:
            return _replace(item, hint=f"pick one of {_plural(len(available), 'model')}")
        return _replace(item, hint=available[0].label)
    if item.action == "draw":
        available = context.registry.here("image")
        if not available:
            return _replace(item, hint="no image model is on this disk yet")
        return _replace(item, hint=f"{available[0].label} first")
    if item.action == "compare":
        available = context.registry.here("text")
        return _replace(item, hint=f"the same prompt to {_plural(len(available), 'model')}")
    if item.action == "install":
        chosen = platforms.resolve(context.settings.platform)
        return _replace(item, hint=f"this machine shows as {chosen.key}")
    if item.action.startswith("install:"):
        key = item.action.split(":", 1)[1]
        if key == "all":
            return item
        # A plan for another machine is still offered, because preparing
        # instructions for a second machine is a real thing to want. The hint
        # says which of the two this one is, since that decides whether it runs.
        here = platforms.matches(platforms.target(key))
        return _replace(item, hint="this machine" if here else "printed, not run here")
    return item


def _replace(item: Item, hint: str) -> Item:
    return Item(key=item.key, label=item.label, hint=hint, group=item.group, action=item.action)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def loop(context: actions.Context) -> int:
    """Show the menu, run what was chosen, and come back.

    Nothing is re-read between choices on purpose. A model that appeared on disk
    while the menu was open would be a surprise; a menu that always shows what
    was true when the tool started is a menu that says the same thing twice.

    The banner is printed once, before the menu, and not again after every
    action: a heading repeated after each step stops being a heading and becomes
    noise between a result and the next choice.
    """
    for line in banner(context):
        textui.say(line)
    if context.registry.problems:
        for problem in context.registry.problems:
            textui.warn(problem)
    if context.registry.unregistered_files:
        textui.note(
            f"{len(context.registry.unregistered_files)} weight file(s) in the directory are "
            "not in models.toml; option 4 says which"
        )
    textui.say()

    menu = build(context)
    first = menu.items[0].key if menu.items else ""
    while True:
        chosen = textui.choose(menu, "what next", default_key=first)
        if chosen is None:
            return actions.EXIT_OK
        if chosen.action == "quit":
            return actions.EXIT_OK
        textui.say()
        try:
            _dispatch(context, chosen.action)
        except KeyboardInterrupt:
            textui.say()
            textui.note("stopped")
        except actions.RegistryError as error:
            textui.fail(str(error))
        textui.say()
        textui.rule("")
        textui.say()


def _dispatch(context: actions.Context, action: str) -> int:
    """Run one menu item, and report what it needed when it cannot."""
    from . import screens

    if action == "chat":
        return actions.chat(context, actions.Args())
    if action == "draw":
        return screens.draw_screen(context, actions.Args())
    if action == "compare":
        return screens.compare_screen(context, actions.Args())
    if action == "list":
        return actions.list_models(context)
    if action == "inspect":
        return _inspect_screen(context)
    if action == "settings":
        return screens.settings_screen(context)
    if action == "transcripts":
        return screens.transcripts_screen(context)
    if action == "help":
        return screens.help_screen(context)
    if action == "install":
        return actions.install(context, None, platforms.STAGE_RUNTIME, apply=False)
    if action.startswith("install:"):
        key = action.split(":", 1)[1]
        stage = platforms.STAGE_ALL if key == "all" else platforms.STAGE_RUNTIME
        return actions.install(
            context, None if key == "all" else key, stage, apply=False
        )
    textui.warn(f"'{action}' is not wired to anything")
    return actions.EXIT_OK


def _inspect_screen(context: actions.Context) -> int:
    """Choose a model to read, from the ones registered."""
    from . import inspect as inspector, textui as ui

    entries = context.registry.entries
    if not entries:
        ui.fail("nothing is registered in models.toml")
        return actions.EXIT_ENVIRONMENT
    for index, entry in enumerate(entries, 1):
        state = "present" if context.registry.present(entry) else "missing"
        ui.say(f"    {ui.pad(str(index), 4)}{ui.pad(entry.key, 24)}{ui.pad(entry.label, 22)}{state}")
    answer = ui.ask("which one (a number or a name)", default="1")
    if answer is None:
        return actions.EXIT_CANCELLED
    if answer.isdigit() and 1 <= int(answer) <= len(entries):
        chosen = entries[int(answer) - 1]
    else:
        try:
            chosen = context.registry.get(answer)
        except actions.RegistryError as error:
            ui.fail(str(error))
            return actions.EXIT_MISMATCH
    ui.say()
    for line in inspector.report(chosen, context.device()):
        ui.say(line)
    return actions.EXIT_OK
