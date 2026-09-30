#!/usr/bin/env python3
"""The entry point: a menu when given nothing, a verb when given one.

    python3 local-model-quick-tester/run.py                     the menu
    python3 local-model-quick-tester/run.py chat                a conversation
    python3 local-model-quick-tester/run.py chat --prompt "..." one answer
    python3 local-model-quick-tester/run.py draw --prompt "..."  one image
    python3 local-model-quick-tester/run.py list                 what is here
    python3 local-model-quick-tester/run.py inspect gemma-4-e2b   what a file is
    python3 local-model-quick-tester/run.py compare --prompt "..."  every model
    python3 local-model-quick-tester/run.py install apple-silicon
    python3 local-model-quick-tester/run.py settings --show

Two things happen before anything else, and both are about being runnable rather
than about doing the work.

The repository root is put on the import path, because the diffusion and GGUF
pieces live in `tools/` and are shared with the other entry points rather than
copied here.

And the interpreter is checked. A machine that already has a virtual environment
with the runtime installed has been built once, and running the tool from a
different interpreter is the common way to get a confusing "module not found"
from a machine that has everything it needs. So when this interpreter cannot
import the runtime and another one beside the tool can, the work is handed to
that one. It is guarded by an environment variable so it can only happen once,
and `--no-reexec` turns it off.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: The repository first, then this folder, so `tools.imgmodels` resolves to this
#: checkout's package and `quicktester` to the one beside this file.
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from quicktester import actions, image, inspect as inspector, menu, paths, screens  # noqa: E402
from quicktester import platforms, textui, theme  # noqa: E402
from quicktester.settings import apply_pairs, catalogue  # noqa: E402
from quicktester.settings import load as load_settings, save as save_settings  # noqa: E402

REEXEC_ENV = "QUICKTESTER_REEXEC"

#: What has to be importable for the work the tool does. Named so the re-exec
#: decision and the capability messages agree about what "installed" means.
RUNTIME_MODULES = ("llama_cpp", "diffusers")


def can_import(module: str) -> bool:
    try:
        __import__(module)
    except ImportError:
        return False
    return True


def maybe_reexec(argv: list[str]) -> None:
    """Hand the work to the environment beside the tool, once, if it can do more.

    Only when there is something to gain: an interpreter that already has the
    runtime is kept, and a machine with no environment is left alone so the
    install menu can be reached from wherever the tool was started.
    """
    if os.environ.get(REEXEC_ENV) == "1" or "--no-reexec" in argv:
        return
    if any(flag in argv for flag in ("-h", "--help", "--version")):
        return
    if all(can_import(module) for module in RUNTIME_MODULES):
        return

    settings = load_settings(HERE)
    venv = paths.expand(settings.venv) or actions.detect_venv(ROOT)
    if venv is None:
        return
    interpreter = platforms.venv_python(venv)
    if not interpreter.is_file():
        return
    # Compared by environment, not by binary. Two virtual environments built from
    # the same interpreter both resolve to that interpreter's real path, so a
    # binary comparison reports them as the same environment and skips the hand
    # over exactly when it is needed. `sys.prefix` is the environment in use.
    try:
        if Path(sys.prefix).resolve() == venv.resolve():
            return
    except OSError:
        return

    os.environ[REEXEC_ENV] = "1"
    os.execv(str(interpreter), [str(interpreter), str(Path(__file__).resolve()), *argv])


def common_flags(parser: argparse.ArgumentParser) -> None:
    """The flags that describe how to draw, on every verb."""
    parser.add_argument(
        "--colour",
        choices=("auto", "always", "never"),
        default=None,
        help="whether to colour the output; auto follows the terminal",
    )
    parser.add_argument(
        "--theme",
        choices=("auto", "dark", "light"),
        default=None,
        help="which background to assume; auto reads COLORFGBG",
    )
    parser.add_argument("--no-reexec", action="store_true", help="run in this interpreter")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-model-quick-tester",
        description="Run the GGUF models on this disk: converse with a language model, or draw with an image model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "with no verb the menu is shown.\n"
            "a model may be named by its key, its label, or its file name; leaving\n"
            "it out picks the first one this machine holds weights for."
        ),
    )
    parser.add_argument("--version", action="version", version="local-model-quick-tester 1.0.0")
    common_flags(parser)
    verbs = parser.add_subparsers(dest="verb")

    def verb(name: str, help_text: str) -> argparse.ArgumentParser:
        child = verbs.add_parser(name, help=help_text, description=help_text)
        common_flags(child)
        return child

    chat = verb("chat", "hold a conversation with a language model")
    chat.add_argument("--model", help="which model")
    chat.add_argument("--mode", help="which conversation mode")
    chat.add_argument("--prompt", help="ask one thing and stop, instead of conversing")
    chat.add_argument("--no-stream", action="store_true", help="wait for the whole answer")
    chat.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")

    draw = verb("draw", "generate an image with a diffusion model")
    draw.add_argument("--model", help="which image model")
    draw.add_argument("--prompt", help="what to generate, or how to change the condition image")
    draw.add_argument("--image", help="a condition image to edit")
    draw.add_argument("--out", help="output file; defaults to images/<model>/<prompt>.png")
    draw.add_argument("--size", type=int, default=0, help="square side in pixels; 0 uses the model's own")
    draw.add_argument("--steps", type=int, default=0, help="denoising steps; 0 uses the model's own")
    draw.add_argument("--seed", type=int, default=42)
    draw.add_argument("--negative", help="negative prompt, where the model takes one")
    draw.add_argument("--cfg", type=float, help="guidance scale, used with --negative")
    draw.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    draw.add_argument("--allow-paging", action="store_true", help="run even when the machine cannot hold the model")

    verb("list", "every registered model, and what is on disk")

    inspect = verb("inspect", "read one weight file without running it")
    inspect.add_argument("name", help="a model key, label or file name")
    inspect.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")

    check = verb("check", "verify a model's file and say whether it runs here")
    check.add_argument("--model", help="which model")
    check.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")

    install = verb("install", "build a runtime, or print the plan for another machine")
    install.add_argument("target", nargs="?", help="a platform key; the default is this machine")
    install.add_argument("--stage", choices=platforms.STAGES, default=platforms.STAGE_RUNTIME)
    install.add_argument("--apply", action="store_true", help="run the steps without asking")

    settings_verb = verb("settings", "show or change the settings for this machine")
    settings_verb.add_argument("pairs", nargs="*", help="KEY=VALUE to change")
    settings_verb.add_argument("--show", action="store_true", help="print every setting and stop")

    transcripts = verb("transcripts", "list the markdown copies of past conversations")
    transcripts.add_argument("--dir", help="read a different directory")

    compare = verb("compare", "put one question to every text model on this disk")
    compare.add_argument("--prompt", help="the question; without it you are asked")
    compare.add_argument("--model", action="append", default=[], dest="models")
    compare.add_argument("--mode", help="which conversation mode")
    compare.add_argument("--no-stream", action="store_true")

    verb("help", "what this is and where its pieces are")
    return parser


def configure(args, context=None) -> None:
    """Set the palette from the flags, then the settings, then the terminal."""
    settings = context.settings if context is not None else load_settings(HERE)
    colour = args.colour or settings.colour
    theme_name = args.theme or settings.theme
    depth = {"auto": None, "always": 24, "never": 0}.get(colour, None)
    theme.configure(depth=depth, mode=theme_name, root=ROOT)


def run(args) -> int:
    context = actions.build(ROOT, HERE)
    if getattr(args, "colour", None):
        context.settings.colour = args.colour
    if getattr(args, "theme", None):
        context.settings.theme = args.theme
    configure(args, context)

    verb = args.verb or "menu"
    if verb == "menu":
        return menu.loop(context)
    if verb == "list":
        return actions.list_models(context)
    if verb == "chat":
        return actions.chat(context, args)
    if verb == "draw":
        return screens.draw_screen(context, args)
    if verb == "inspect":
        return actions.inspect_one(context, args.name, args.device)
    if verb == "check":
        return check(context, args)
    if verb == "install":
        return actions.install(context, args.target, args.stage, args.apply)
    if verb == "settings":
        return settings_verb(context, args)
    if verb == "transcripts":
        if args.dir:
            context.settings.transcript_dir = args.dir
        return screens.transcripts_screen(context)
    if verb == "compare":
        return screens.compare_screen(context, args)
    if verb == "help":
        return screens.help_screen(context)
    raise SystemExit(f"no verb named '{verb}'")


def check(context: actions.Context, args) -> int:
    """Verify one model, whichever kind it is.

    The two kinds answer different questions, so they are answered separately: a
    language model is checked against the runtime that will open it, and an image
    model against the memory plan and the quantisation support.
    """
    entry = actions.resolve_entry(context, "image" if _is_image(context, args.model) else "text", args.model)
    textui.heading(f"checking {entry.key}")
    if entry.has("image"):
        for line in image.check(entry, args.device):
            textui.say(line)
        ready = None
        try:
            ready = image.prepare(entry, args.device)
        except image.ImageError as error:
            textui.fail(str(error))
            return actions.EXIT_MISMATCH
        refusal = image.too_big(ready)
        textui.say()
        if refusal:
            textui.warn(refusal)
            return actions.EXIT_MISMATCH
        textui.ok("this machine can hold the model, so it should run here")
        return actions.EXIT_OK

    from quicktester import text as text_models

    for line in inspector.report(entry, args.device):
        textui.say(line)
    textui.say()
    present, reason = text_models.library()
    if not present:
        textui.fail(f"llama.cpp is not installed in this interpreter\n  {reason}")
        return actions.EXIT_ENVIRONMENT
    textui.ok(f"llama.cpp is installed here, and {entry.file} is readable")
    return actions.EXIT_OK


def _is_image(context: actions.Context, name: str | None) -> bool:
    """Whether a named model is an image model, defaulting to a text one."""
    if not name:
        return False
    try:
        return context.registry.get(name).has("image")
    except actions.RegistryError:
        return False


def settings_verb(context: actions.Context, args) -> int:
    """Show the settings, or apply `KEY=VALUE` pairs and write them back."""
    if args.show or not args.pairs:
        for name, default, described in catalogue():
            value = getattr(context.settings, name)
            mark = "" if value == default else " (changed)"
            textui.say(textui.row(name, f"{value}{mark}"))
            textui.note(described, indent="      ")
        textui.say()
        textui.note(f"file {paths.record(context.folder / 'settings.local.json', context.root)}")
        if context.settings.taken:
            for taken in context.settings.taken:
                textui.warn(f"{taken} was taken from the default rather than from the file")
        return actions.EXIT_OK

    problems = apply_pairs(context.settings, args.pairs)
    for problem in problems:
        textui.fail(problem)
    if problems:
        return actions.EXIT_MISMATCH
    path = save_settings(context.folder, context.settings)
    textui.ok(f"saved {paths.record(path, context.root)}")
    return actions.EXIT_OK


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    maybe_reexec(raw)
    args = build_parser().parse_args(raw)
    try:
        return run(args)
    except actions.RegistryError as error:
        textui.fail(str(error))
        return actions.EXIT_ENVIRONMENT
    except KeyboardInterrupt:
        textui.say()
        textui.note("stopped")
        return actions.EXIT_CANCELLED


if __name__ == "__main__":
    raise SystemExit(main())
