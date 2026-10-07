"""Everything the tool can do, once, for both the menu and the command line.

The interactive menu and the command line are two ways into the same work and
must not drift apart. So the work is here, each action taking a `Context` and
returning an exit status, and `menu.py` and `run.py` are both thin layers over
this module: one reads a key, the other reads arguments.

`Context` exists to stop every action from re-deriving the same four things. It
resolves the weight directory, the transcript directory, the virtual environment
and the device once, honouring the settings and the environment, and reports what
it resolved so a caller can print it.
"""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tools.imgmodels import catalog as catalog_module

from . import image, inspect, paths, picker, platforms, textui, theme
from .modes import ModeSet, read as read_modes
from .registry import Entry, Registry, RegistryError, read as read_registry
from .settings import DEFAULTS, Settings, load as load_settings

#: What a run leaves with when it cannot start. Distinct from a failure inside a
#: generation so a script can tell "wrong input" from "the model broke".
EXIT_OK = 0
EXIT_ENVIRONMENT = 2
EXIT_MISMATCH = 3
EXIT_CANCELLED = 130

#: The name a new environment gets on a machine that has none. Not a fixed name
#: for an existing one: a machine that already has an environment keeps it, which
#: is what stops this tool from making a second copy of eight gigabytes of wheels.
DEFAULT_VENV_NAME = ".venv-local-model-quick-tester"


class Args:
    """The arguments a screen needs, whether it came from the menu or the shell.

    A screen takes the same shape of arguments either way, so there is one
    implementation and not two. What the menu does not fill in falls back to the
    settings, which is what choosing an item with no questions means.
    """

    def __init__(self, **fields):
        self.model = None
        self.mode = None
        self.prompt = None
        self.image = None
        self.out = None
        self.size = 0
        self.steps = 0
        self.seed = 42
        self.negative = None
        self.cfg = None
        self.device = "auto"
        self.no_stream = False
        self.allow_paging = False
        self.models: list[str] = []
        for name, value in fields.items():
            setattr(self, name, value)


@dataclass
class Context:
    """The four things every action needs, resolved once."""

    root: Path
    folder: Path
    settings: Settings
    registry: Registry
    modes: ModeSet
    reexec: str = field(default="", compare=False)

    @property
    def models_dir(self) -> Path:
        return _models_dir(self.root, self.settings)

    @property
    def transcript_dir(self) -> Path:
        chosen = paths.expand(self.settings.transcript_dir)
        if chosen is None:
            return self.root / DEFAULTS["transcript_dir"]
        return chosen

    def venv(self) -> Path:
        """The environment to run in: the setting, else the one already here.

        A machine that has an environment keeps it rather than getting a second
        one, and a machine that has none is offered the name above.
        """
        chosen = paths.expand(self.settings.venv)
        if chosen is not None:
            return chosen
        found = detect_venv(self.root)
        return found if found is not None else self.root / DEFAULT_VENV_NAME

    def runtime(self) -> str:
        """The platform key in force, with `auto` already resolved."""
        return platforms.resolve(self.settings.platform).key

    def device(self) -> str:
        """Where the work will run.

        Resolved from the machine rather than from a library import, because the
        answer is wanted for a menu line before anything heavy is loaded. A CUDA
        machine is recognised by its driver being present, which is the same test
        the installer uses to choose a build. The image path asks the diffusion
        library for its own answer instead, since it can measure the memory.
        """
        if self.settings.device != "auto":
            return self.settings.device
        family = platforms.host_family(platforms.current_host())
        if family == platforms.MACOS:
            return "mps"
        if platforms.has_nvidia():
            return "cuda"
        return "cpu"

    def command(self, fallback: list[str]) -> str:
        """This invocation, written the way it may be recorded.

        The real arguments are recorded when there were any, because a transcript
        that names the model but omits the prompt is a transcript that cannot be
        reproduced. The `fallback` is for a run that came from the menu, where
        there were no arguments to record and the choice made on screen is the
        closest thing to one.

        The interpreter is named by its file name and the script by its path
        shortened to the repository, so the recorded line is one a person on
        another checkout can follow without describing this machine.
        """
        arguments = list(sys.argv[1:])
        if not arguments:
            arguments = list(fallback)
        entry = paths.record(Path(sys.argv[0]).resolve(), self.root)
        interpreter = Path(sys.executable).name
        return " ".join([shlex.quote(interpreter), entry, *map(shlex.quote, arguments)])


def detect_venv(root: Path) -> Path | None:
    """A virtual environment already in the checkout, if there is one."""
    try:
        candidates = sorted(child for child in root.iterdir() if child.is_dir())
    except OSError:
        return None
    for candidate in candidates:
        if (candidate / "pyvenv.cfg").is_file() and platforms.venv_python(candidate).is_file():
            return candidate
    return None


def build(root: Path, folder: Path, reexec: str = "") -> Context:
    """Read the settings, the registry and the modes, reporting what was wrong."""
    settings = load_settings(folder)
    registry = read_registry(folder, _models_dir(root, settings))
    modes = read_modes(folder)
    return Context(
        root=root,
        folder=folder,
        settings=settings,
        registry=registry,
        modes=modes,
        reexec=reexec,
    )


def _models_dir(root: Path, settings: Settings) -> Path:
    chosen = paths.expand(settings.models_dir)
    if chosen is not None:
        return chosen
    environ = os.environ.get("AGENT_LIKE_MODEL_DIR")
    if environ:
        return Path(environ).expanduser()
    return root / "models"


# ---------------------------------------------------------------------------
# Resolving what to run.
# ---------------------------------------------------------------------------


def resolve_entry(context: Context, kind: str, name: str | None) -> Entry:
    """The entry asked for, or the best one for this machine."""
    if name:
        return context.registry.get(name)
    return context.registry.default_for(kind)


def text_model(context: Context, name: str | None) -> Entry:
    entry = resolve_entry(context, "text", name)
    if not entry.has("text"):
        raise RegistryError(
            f"{entry.label} is registered as {', '.join(entry.roles)} and not as a text model"
        )
    return entry


def image_model(context: Context, name: str | None) -> Entry:
    entry = resolve_entry(context, "image", name)
    ok, detail = image.capability(entry)
    if not ok:
        raise image.ImageError(f"{entry.label} cannot be run here\n  {detail}")
    return entry


# ---------------------------------------------------------------------------
# The actions.
# ---------------------------------------------------------------------------


def list_models(context: Context) -> int:
    """Every registered model, what is present, and what is not accounted for."""
    palette = theme.active()
    textui.heading("models")
    for line in (
        textui.row("registry", f"{context.folder.name}/models.toml"),
        textui.row("weight directory", paths.record(context.models_dir, context.root)),
        textui.row("runs in", paths.record(context.venv(), context.root)),
        textui.row("platform", f"{context.runtime()} on {context.device()}"),
    ):
        textui.say(line)
    textui.say()

    for role in ("text", "image", "projector", "encoder"):
        entries = context.registry.by_role(role)
        if not entries:
            continue
        textui.say(f"  {palette.paint(role, 'accent', bold=True)}")
        for entry in entries:
            present = context.registry.present(entry)
            if present:
                size = f"{entry.path(context.models_dir).stat().st_size / 1024**3:.2f} GB"
                mark = palette.paint("present", "ok")
            else:
                size = "absent"
                mark = palette.paint("missing", "warn")
            textui.say(
                f"    {textui.pad(entry.key, 24)}{textui.pad(entry.label, 22)}"
                f"{textui.pad(size, 10)}{mark}"
            )
            if entry.note:
                textui.note(entry.note, indent="      ")
        textui.say()

    if context.registry.unregistered_files:
        textui.warn("weight files in the directory that no entry mentions")
        for name in context.registry.unregistered_files:
            textui.say(f"      {name}")
        textui.note("add a block to models.toml to place one, or ignore it by naming it", indent="      ")

    for problem in context.registry.problems:
        textui.fail(problem)
    if context.registry.missing():
        textui.note(
            f"{len(context.registry.missing())} registered model(s) are not on this disk; "
            "they cannot be chosen but they are still listed"
        )
    return EXIT_OK


def inspect_one(context: Context, name: str, device: str = "auto") -> int:
    """Everything known about one model, read from its file."""
    entry = context.registry.get(name)
    textui.heading(f"inspecting {entry.key}")
    for line in inspect.report(entry, device if device != "auto" else context.device()):
        textui.say(line)
    return EXIT_OK


def chat(context: Context, args) -> int:
    """A conversation with a local model, streamed, and written down after."""
    from . import text as text_models

    name = getattr(args, "model", None)
    entry = text_model(context, name) if name else picker.select(context, "text")
    if entry is None:
        textui.note("nothing was chosen, so no conversation was started")
        return EXIT_CANCELLED
    path = entry.path(context.models_dir)
    if not path.is_file():
        textui.fail(f"{entry.label} is not on this disk: expected {path.name}")
        return EXIT_ENVIRONMENT

    device = args.device if args.device != "auto" else context.device()
    mode = context.modes.first_of(entry.modes, args.mode or context.settings.default_text_mode)

    try:
        with textui.Spinner(f"opening {entry.label}", "sweep") as turn:
            loaded = text_models.load(
                entry=entry,
                path=path,
                device=device,
                context=context.settings.text_context,
                threads=context.settings.threads,
            )
            turn.set(f"opened {entry.label}")
    except text_models.TextError as error:
        textui.fail(str(error))
        return EXIT_ENVIRONMENT

    session = text_models.Session(
        loaded=loaded,
        mode=mode,
        stream=context.settings.stream and not getattr(args, "no_stream", False),
    )
    for note in loaded.notes:
        textui.note(note)

    textui.say()
    textui.say(f"  {theme.active().paint(loaded.describe(), 'accent', bold=True)}")
    textui.note(f"mode: {mode.label}, temperature {mode.temperature:g}")
    if getattr(args, "prompt", None):
        _one_shot(session, args.prompt)
    else:
        _converse(session, context)

    _finish(session, context, ["chat", "--model", entry.key, "--mode", mode.key])
    return EXIT_OK


def _one_shot(session, prompt: str) -> None:
    """A single exchange, for a script or a pipe."""
    palette = theme.active()
    textui.say()
    textui.say(f"  {palette.paint('you', 'info', bold=True)}")
    for line in prompt.splitlines():
        textui.say(f"    {line}")
    textui.say()
    textui.say(f"  {palette.paint(session.loaded.entry.label, 'accent', bold=True)}")
    reply = session.ask(prompt)
    textui.say()
    textui.note(_timing(reply))


def _timing(reply) -> str:
    """The numbers one answer is judged by, in one line."""
    detail = f"{reply.seconds:.1f}s"
    if reply.first_token is not None:
        detail += f", first token at {reply.first_token:.2f}s"
    if reply.tokens:
        decode = reply.seconds - (reply.first_token or 0.0)
        rate = f", {reply.tokens / decode:.1f} tokens/s" if decode > 0 else ""
        detail += f", {reply.tokens} tokens{rate}"
    else:
        detail += ", tokens not measured"
    return detail


def _converse(session, context: Context) -> None:
    """The interactive loop: a message sends, a slash commands. /help lists them."""
    palette = theme.active()
    terminator = context.settings.prompt_end or ""
    textui.say()
    textui.note("a line sends. a pasted block arrives whole. a trailing backslash continues onto the next line.")
    if terminator:
        textui.note(f"on a terminal that cannot report a paste, a line holding {terminator} ends the message.")
    textui.say()

    while True:
        try:
            body = textui.compose(palette.paint("you", "info"), terminator)
        except KeyboardInterrupt:
            textui.say()
            break
        if body is None:
            break
        message = body.strip()
        if not message:
            continue
        # A slash command is a whole line. A message that runs to several lines is
        # prose, and prose that opens with a slash is not a command.
        if "\n" not in message and message.startswith("/"):
            if not _command(message, session, context):
                break
            continue

        textui.say()
        textui.say(f"  {palette.paint(session.loaded.entry.label, 'accent', bold=True)}")
        try:
            reply = session.ask(message)
        except KeyboardInterrupt:
            textui.say()
            textui.note("stopped; the partial answer was not kept")
            continue
        textui.say()
        textui.say()
        detail = f"{reply.seconds:.1f}s"
        if reply.tokens:
            detail += f", {reply.tokens} tokens"
        if reply.first_token is not None:
            detail += f", first token at {reply.first_token:.2f}s"
        textui.note(detail)


def _command(line: str, session, context: Context) -> bool:
    """One slash command. Returns whether the conversation continues."""
    parts = line.split(maxsplit=1)
    name = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if name in ("/quit", "/exit", "/q"):
        return False
    if name == "/help":
        for command, described in COMMANDS:
            textui.say(f"    {textui.pad(command, 14)}{described}")
        return True
    if name == "/stats":
        textui.note(f"turns {sum(1 for turn in session.turns if turn.is_user)}")
        textui.note(f"time {session.elapsed:.1f}s")
        textui.note(f"tokens generated {session.generated_tokens or 'not measured'}")
        textui.note(f"context in use {session.sent_tokens() or 'not measured'} of {session.loaded.context}")
        for note in session.notes:
            textui.note(note)
        return True
    if name == "/clear":
        session.messages = session.messages[:1]
        textui.note("the conversation was cleared; the mode was kept")
        return True
    if name == "/save":
        path = session.report(
            context.transcript_dir,
            context.root,
            context.command(["chat", "--model", session.loaded.entry.key, "--mode", session.mode.key]),
            context.runtime(),
        )
        textui.ok(f"wrote {paths.record(path, context.root)}")
        return True
    if name == "/mode":
        if not argument:
            for mode in context.modes.modes:
                mark = "*" if mode.key == session.mode.key else " "
                textui.say(f"    {mark} {textui.pad(mode.key, 12)}{mode.label}")
            return True
        try:
            chosen = context.modes.get(argument)
        except Exception as error:  # noqa: BLE001 - the message is the report
            textui.fail(str(error))
            return True
        session.mode = chosen
        session.messages[0] = {"role": "system", "content": chosen.system}
        textui.ok(f"mode is now {chosen.label}, temperature {chosen.temperature:g}")
        return True
    textui.warn(f"'{name}' is not a command; /help lists them")
    return True


COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "what the commands are"),
    ("/mode [name]", "list the modes, or switch to one"),
    ("/stats", "time, tokens and how much of the window is in use"),
    ("/clear", "forget the conversation, keep the mode"),
    ("/save", "write the transcript now"),
    ("/quit", "finish, and write the transcript"),
)


def _finish(session, context: Context, arguments: list[str]) -> None:
    """Write the transcript, unless the settings say not to."""
    if not context.settings.save_transcript:
        textui.note("the transcript was not written, by request")
        return
    textui.say()
    try:
        path = session.report(
            context.transcript_dir,
            context.root,
            context.command(arguments),
            context.runtime(),
        )
    except OSError as error:
        textui.fail(f"the transcript could not be written: {error}")
        return
    textui.ok(f"transcript  {paths.record(path, context.root)}")


def draw(context: Context, args) -> int:
    """Generate one image, showing the denoising as it happens."""
    try:
        entry = image_model(context, args.model)
    except (image.ImageError, RegistryError) as error:
        textui.fail(str(error))
        return EXIT_MISMATCH if isinstance(error, image.ImageError) else EXIT_ENVIRONMENT

    condition = Path(args.image).expanduser() if getattr(args, "image", None) else None
    if condition is not None and not condition.is_file():
        textui.fail(f"the condition image was not found: {condition}")
        return EXIT_ENVIRONMENT

    try:
        target, elapsed, notes = image.draw(
            entry=entry,
            prompt=args.prompt,
            size=args.size or context.settings.image_size,
            steps=args.steps or context.settings.image_steps,
            seed=args.seed,
            condition=condition,
            negative=args.negative,
            cfg=args.cfg,
            device=args.device if args.device != "auto" else context.settings.device,
            out=getattr(args, "out", None),
            allow_paging=getattr(args, "allow_paging", False),
        )
    except image.ImageError as error:
        textui.fail(str(error))
        return EXIT_MISMATCH
    except Exception as error:  # noqa: BLE001 - a build failure is the report
        textui.fail(f"{type(error).__name__}: {error}")
        return EXIT_ENVIRONMENT

    for note in notes:
        textui.note(note)
    textui.say()
    textui.ok(f"wrote {paths.record(target, context.root)} in {elapsed:.1f}s")
    return EXIT_OK


def install(context: Context, key: str | None, stage: str, apply: bool) -> int:
    """Build or print the runtime for a platform."""
    from . import installer

    chosen = key or context.settings.platform
    try:
        plan = platforms.plan(chosen, context.venv(), stage)
    except KeyError as error:
        textui.fail(str(error))
        return EXIT_ENVIRONMENT

    # Shown with its placeholders filled in. A plan printed as `{venv_python}`
    # is a plan nobody can follow, and the point of showing it before running it
    # is that a person can read it and stop.
    prepared, _ = installer.prepare(plan)

    textui.heading(f"install for {prepared.target.label}")
    for line in installer.describe(prepared):
        textui.say(f"  {line}")
    for warning in prepared.warnings:
        textui.warn(warning)
    textui.say()

    textui.say(f"  {theme.active().paint('the plan', 'accent', bold=True)}")
    for index, step in enumerate(prepared.steps, 1):
        textui.say(f"    {index}. {step.label}")
        textui.say(f"       {theme.active().paint(step.display(), 'muted')}")
        if step.note:
            textui.note(step.note, indent="       ")
    textui.say()

    if not platforms.matches(prepared.target):
        textui.warn(
            f"this plan is for {prepared.target.host} and this machine is "
            f"{platforms.current_host()}, so it is printed and not run"
        )
        return EXIT_OK

    if not apply:
        if not textui.interactive():
            textui.note("not run: nothing is at the terminal to confirm, and --apply was not given")
            return EXIT_OK
        if not textui.confirm("run these steps now", default=False):
            return EXIT_CANCELLED
    outcome = installer.run(prepared, emit=lambda line: textui.say(f"  {line}"), apply=True)
    textui.say()
    if outcome.ok:
        textui.ok(f"install finished: {outcome.summary()}")
        textui.note(f"the environment is {paths.record(prepared.venv, context.root)}")
        return EXIT_OK
    textui.fail(f"install stopped: {outcome.summary()}")
    return EXIT_ENVIRONMENT
