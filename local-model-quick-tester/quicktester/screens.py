"""The screens the menu opens that are not a model run: settings, transcripts,
help, and a comparison of several models on one question.

They are here rather than in `actions.py` because they are all a conversation
with the person using the tool, and because `actions.py` is the part both the
menu and the command line call. A screen is only ever reached from the menu.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from . import actions, paths, textui, theme
from .settings import apply_pairs, catalogue, save as save_settings

#: How long one answer may be when several models are being compared. Short
#: enough that a comparison of six models is minutes rather than an hour, and
#: long enough that a model has room to finish a sentence and a half.
COMPARE_ANSWER_LIMIT = 220

#: The side offered when nothing else says. It is the size both image models are
#: intended to be run at, and it is offered rather than assumed so a smaller one
#: can be chosen for a first run.
DEFAULT_SIDE = 1024


def help_screen(context: actions.Context) -> int:
    """What this is, what it can do, and where the pieces are."""
    palette = theme.active()
    textui.say()
    textui.say(
        f"  {palette.paint('local model quick tester', 'accent', bold=True)}"
    )
    textui.note(
        "one place to talk to a language model and draw with an image model, "
        "using the weight files already on this disk"
    )
    textui.say()
    for title, lines in (
        (
            "what it needs",
            [
                "a virtual environment with llama-cpp-python for the language models",
                "the diffusion stack for the image models",
                "the install menu builds either, for this machine or for another",
            ],
        ),
        (
            "what it writes",
            [
                f"a markdown transcript after a conversation, in {context.settings.transcript_dir}",
                f"images under {paths.record(context.root / 'images', context.root)}, in a folder per model",
                f"settings in {context.folder.name}/settings.local.json",
            ],
        ),
        (
            "what says which file is which",
            [
                f"{context.folder.name}/models.toml  the registry: what is a text model, an image model, or neither",
                f"{context.folder.name}/modes.toml   the system prompt and temperature of each conversation mode",
            ],
        ),
    ):
        textui.say(f"  {palette.paint(title, 'info', bold=True)}")
        for line in lines:
            textui.say(f"    {line}")
        textui.say()
    textui.note("every command the menu offers is also a command line verb; run.py --help lists them")
    return actions.EXIT_OK


def settings_screen(context: actions.Context) -> int:
    """Show every setting, and change one at a time."""
    palette = theme.active()
    while True:
        textui.say()
        textui.heading("settings")
        textui.say(textui.row("file", paths.record(context.folder / "settings.local.json", context.root)))
        textui.say(textui.row("source", context.settings.source))
        textui.say()
        for name, default, described in catalogue():
            value = getattr(context.settings, name)
            changed = "" if value == default else palette.paint(" changed", "accent")
            textui.say(
                f"    {palette.paint(textui.pad(name, 18), 'info')}"
                f"{textui.pad(str(value), 12)}{palette.paint(str(described), 'muted')}{changed}"
            )
        textui.say()
        for taken in context.settings.taken:
            textui.warn(f"{taken} was taken from the default rather than from the file")

        answer = textui.ask("name=value to change, save, reset, or q", default="q")
        if answer is None or answer.lower() in ("q", "quit", "back"):
            return actions.EXIT_OK
        if answer.lower() in ("save", "s"):
            path = save_settings(context.folder, context.settings)
            textui.ok(f"saved {paths.record(path, context.root)}")
            textui.note(
                "platform, device and colour are applied the next time the tool starts"
            )
            return actions.EXIT_OK
        if answer.lower() == "reset":
            for name, default, _ in catalogue():
                setattr(context.settings, name, default)
            textui.ok("every setting is back to its default; save to keep that")
            continue
        problems = apply_pairs(context.settings, [answer])
        for problem in problems:
            textui.fail(problem)


def transcripts_screen(context: actions.Context) -> int:
    """The markdown copies of past conversations, newest first."""
    folder = context.transcript_dir
    palette = theme.active()
    textui.say()
    textui.heading("transcripts")
    textui.say(textui.row("directory", paths.record(folder, context.root)))
    found = sorted(folder.glob("*.md")) if folder.is_dir() else []
    if not found:
        textui.note("nothing written yet; a conversation writes one when it ends")
        return actions.EXIT_OK

    textui.say()
    newest = found[-1]
    for path in reversed(found[-12:]):
        stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        mark = palette.paint("newest", "accent") if path == newest else ""
        textui.say(f"    {textui.pad(stamp, 18)}{textui.pad(path.name, 44)}{mark}")
    if len(found) > 12:
        textui.note(f"{len(found) - 12} older file(s) are there as well")

    chosen = textui.ask("show one (a number from the list, or a name), or q", default="q")
    if chosen is None or chosen.lower() in ("q", "quit", "back"):
        return actions.EXIT_OK
    return _show_transcript(context, found, chosen)


def _show_transcript(context: actions.Context, found: list[Path], chosen: str) -> int:
    """Print one transcript, by position in the listing or by name."""
    recent = list(reversed(found[-12:]))
    target: Path | None = None
    if chosen.isdigit() and 1 <= int(chosen) <= len(recent):
        target = recent[int(chosen) - 1]
    else:
        for path in found:
            if path.name == chosen or path.stem == chosen:
                target = path
                break
    if target is None:
        textui.warn(f"'{chosen}' is not in the list")
        return actions.EXIT_OK

    textui.rule(paths.record(target, context.root))
    try:
        textui.say(target.read_text(encoding="utf-8"))
    except OSError as error:
        textui.fail(f"{target.name} could not be read: {error}")
    return actions.EXIT_OK


def compare_screen(context: actions.Context, args) -> int:
    """One question, every present text model, one table and one document.

    This is the reason the tool exists for research rather than for one model: a
    score is only meaningful beside another model's score on the same input, and
    the quickest way to get there is to ask all of them the same thing.
    """
    from . import text as text_models

    palette = theme.active()
    prompt = args.prompt or textui.ask("the question to put to every model")
    if not prompt:
        textui.note("nothing asked, nothing compared")
        return actions.EXIT_CANCELLED

    wanted = list(args.models) if args.models else None
    entries = [entry for entry in context.registry.here("text") if not wanted or entry.key in wanted or entry.label in wanted]
    if not entries:
        textui.fail("no text model on this disk to compare")
        return actions.EXIT_ENVIRONMENT

    mode = context.modes.first_of((), args.mode or context.settings.default_text_mode)
    device = context.device()
    textui.say()
    textui.say(f"  {palette.paint('comparing', 'accent', bold=True)} {len(entries)} model(s) on one question")
    textui.note(f"mode {mode.label}, at most {COMPARE_ANSWER_LIMIT} tokens each")
    textui.say()

    rows: list[tuple] = []
    for entry in entries:
        path = entry.path(context.models_dir)
        textui.rule(entry.label)
        try:
            with textui.Spinner(f"opening {entry.label}", "sweep"):
                loaded = text_models.load(
                    entry=entry,
                    path=path,
                    device=device,
                    context=context.settings.text_context,
                    threads=context.settings.threads,
                )
            session = text_models.Session(
                loaded=loaded,
                mode=mode,
                stream=context.settings.stream and not getattr(args, "no_stream", False),
                answer_limit=COMPARE_ANSWER_LIMIT,
            )
            reply = session.ask(prompt)
        except (text_models.TextError, OSError) as error:
            textui.fail(f"{entry.label}: {error}")
            rows.append((entry, None, None, None, str(error)))
            continue

        textui.say()
        detail = f"{reply.seconds:.1f}s"
        if reply.tokens:
            decode = reply.seconds - (reply.first_token or 0.0)
            rate = reply.tokens / decode if decode > 0 else 0.0
            detail += f", {reply.tokens} tokens, {rate:.1f} tokens/s"
        if reply.first_token is not None:
            detail += f", first token at {reply.first_token:.2f}s"
        textui.note(detail)
        textui.say()
        rows.append((entry, reply.seconds, reply.tokens, reply.first_token, reply.text))

    _compare_table(rows)
    target = _write_comparison(context, prompt, mode, rows, args)
    if target is not None:
        textui.ok(f"wrote {paths.record(target, context.root)}")
    return actions.EXIT_OK


def _compare_table(rows: list[tuple]) -> None:
    """The same numbers as the document, printed once."""
    palette = theme.active()
    textui.say()
    textui.heading("result")
    header = (
        f"    {textui.pad('model', 26)}{textui.pad('time', 9)}"
        f"{textui.pad('tokens', 9)}{textui.pad('tok/s', 9)}first token"
    )
    textui.say(palette.paint(header, "muted"))
    for entry, seconds, tokens, first, _ in rows:
        if seconds is None:
            textui.say(f"    {textui.pad(entry.label, 26)}{palette.paint('failed', 'alarm')}")
            continue
        decode = seconds - (first or 0.0)
        rate = f"{tokens / decode:.1f}" if tokens and decode > 0 else "-"
        opening = f"{first:.2f}s" if first is not None else "-"
        textui.say(
            f"    {textui.pad(entry.label, 26)}{textui.pad(f'{seconds:.1f}s', 9)}"
            f"{textui.pad(str(tokens or '-'), 9)}{textui.pad(rate, 9)}{opening}"
        )


def _write_comparison(context: actions.Context, prompt: str, mode, rows: list[tuple], args) -> Path | None:
    """One document holding one question and every answer to it."""
    document = [
        "# Model comparison",
        "",
        "| field | value |",
        "| --- | --- |",
        f"| Question | {prompt} |",
        f"| Mode | {mode.label} |",
        f"| Temperature | {mode.temperature:g} |",
        f"| Models | {len(rows)} |",
        f"| Runtime | {context.runtime()} |",
        f"| Device | {context.device()} |",
        f"| Started | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
        "",
        "| model | model path | time | tokens | tokens/s | first token |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry, seconds, tokens, first, _ in rows:
        path = paths.record(entry.path(context.models_dir), context.root)
        if seconds is None:
            document.append(f"| {entry.label} | {path} | failed | - | - | - |")
            continue
        decode = seconds - (first or 0.0)
        rate = f"{tokens / decode:.1f}" if tokens and decode > 0 else "-"
        document.append(
            f"| {entry.label} | {path} | {seconds:.1f}s | {tokens or '-'} | {rate} | "
            f"{f'{first:.2f}s' if first is not None else '-'} |"
        )
    document.append("")
    for entry, seconds, tokens, first, body in rows:
        document.extend([f"## {entry.label}", ""])
        if seconds is None:
            document.extend([f"This model did not answer: {body}", ""])
            continue
        document.extend([f"_{seconds:.1f}s, {tokens or 0} tokens_", "", str(body).strip(), ""])
    document.extend(["## How this was produced", "", "```", context.command(["compare", "--prompt", prompt]), "```", ""])

    folder = context.transcript_dir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"compare-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    try:
        target.write_text("\n".join(document), encoding="utf-8")
    except OSError as error:
        textui.fail(f"the comparison could not be written: {error}")
        return None
    return target


def draw_screen(context: actions.Context, args) -> int:
    """Generate one image, asking for what the caller did not supply.

    The prompt, the size and the step count are the three things a person wants
    to decide at the moment of drawing rather than in a settings file, so they
    are asked for here when they were not given on the command line, and the
    defaults shown are the model's own rather than this module's.
    """
    palette = theme.active()
    try:
        entry = actions.image_model(context, getattr(args, "model", None))
    except (actions.image.ImageError, actions.RegistryError) as error:
        textui.fail(str(error))
        return actions.EXIT_MISMATCH

    profile = actions.image.profile_for(entry)
    prompt = getattr(args, "prompt", None)
    if not prompt:
        prompt = textui.ask("what should it be")
        if not prompt:
            textui.note("nothing asked for, nothing drawn")
            return actions.EXIT_CANCELLED

    # A profile does not declare a side, because the pipeline's own default is
    # the model's intended one. It is offered rather than assumed, and the note
    # says why a smaller square is a reasonable answer.
    size = getattr(args, "size", 0) or context.settings.image_size
    steps = getattr(args, "steps", 0) or context.settings.image_steps
    if not size:
        size = _number(textui.ask("square side in pixels", default=str(DEFAULT_SIDE)), DEFAULT_SIDE)
    if not steps:
        steps = _number(textui.ask("denoising steps", default=str(profile.steps)), profile.steps)
    if size < DEFAULT_SIDE:
        textui.note(f"{size}x{size} is smaller than the model's own side, which is a cheaper way to see that it runs")

    condition = getattr(args, "image", None)
    if condition:
        textui.note(f"editing {paths.record(Path(condition).expanduser(), context.root)}")

    ready = actions.image.prepare(entry, getattr(args, "device", "auto"))
    textui.say()
    for line in actions.image.describe(ready):
        textui.say(line)
    textui.say()

    local = actions.Args(
        model=entry.key,
        prompt=prompt,
        image=condition,
        out=getattr(args, "out", None),
        size=size,
        steps=steps,
        seed=getattr(args, "seed", 42),
        negative=getattr(args, "negative", None),
        cfg=getattr(args, "cfg", None),
        device=getattr(args, "device", "auto"),
        allow_paging=getattr(args, "allow_paging", False),
    )
    return actions.draw(context, local)


def _number(answer: str | None, fallback: int) -> int:
    """A whole number from a prompt, or the fallback when it is not one."""
    if not answer:
        return fallback
    try:
        return int(str(answer).strip())
    except ValueError:
        textui.warn(f"'{answer}' is not a whole number, so {fallback} was used")
        return fallback
