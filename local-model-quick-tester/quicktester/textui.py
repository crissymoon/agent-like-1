"""The terminal, drawn with the project's own geometry and its own colours.

Not a terminal user interface library. A whole screen redrawing itself owns the
terminal, which makes a build log unreadable and a transcript impossible to pipe,
and this tool is used as often from a script as from a person. So the output is
ordinary lines that happen to be aligned and coloured, written with carriage
returns only where something is being animated, and every animation knows how to
turn itself off.

Three rules hold the drawing together.

Alignment is measured in characters that are visible. An escape sequence occupies
no columns, so the width of a string is counted with the sequences removed; a
table that pads by `len` drifts as soon as one cell is coloured.

Every animation has a non animated path. Output that is not a terminal gets one
line per event instead of a redrawing line, which is what makes a redirected run
readable and what keeps a log honest.

Nothing is drawn that carries meaning only in colour. A pass and a failure differ
by a word as well as by an accent, because a terminal without colour is a normal
terminal and not a degraded one.
"""

from __future__ import annotations

import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field

from . import theme

#: One lock for every write, so an animated line and a streamed token cannot
#: interleave inside a single line of output.
_LOCK = threading.RLock()

_SGR = re.compile(r"\x1b\[[0-9;]*m")

#: The spinner is a rotating square, drawn in the project's own geometry: four
#: quadrants turning rather than the usual braille dots.
SPIN_FRAMES: tuple[str, ...] = ("▖", "▘", "▝", "▗")

#: A bar that sweeps and reverses, for work with no known end. Two cells of ramp
#: on each side of the head read as motion rather than as a block being dragged.
SWEEP_BLOCK = "█"
SWEEP_TAIL = "▓"
SWEEP_TRACK = "░"

FRAME_SECONDS = 1.0 / 12


def strip(text: str) -> str:
    """A string with its escape sequences removed, for measuring."""
    return _SGR.sub("", text)


def width(text: str) -> int:
    """How many columns a string occupies when printed."""
    return len(strip(text))


def truncate(text: str, limit: int) -> str:
    """A string cut to a column budget, with the cut made visible."""
    if width(text) <= limit:
        return text
    plain = strip(text)
    return plain[: max(0, limit - 3)].rstrip() + "..."


def pad(text: str, limit: int) -> str:
    """A string padded to a column budget, counting only visible characters."""
    return text + " " * max(0, limit - width(text))


def say(text: str = "", *, error: bool = False, end: str = "\n") -> None:
    """Write a line, or a fragment when `end` is given."""
    palette = theme.active()
    target = sys.stderr if error else sys.stdout
    with _LOCK:
        target.write(text + end)
        target.flush()


def row(label: str, value: str, indent: str = "  ") -> str:
    """One `label   value` line, the way the rest of the project reports."""
    palette = theme.active()
    return f"{indent}{palette.paint(pad(label, 18), 'muted')}{value}"


def note(text: str, indent: str = "  ") -> None:
    palette = theme.active()
    say(f"{indent}{palette.paint('·', 'line')} {palette.paint(text, 'muted')}")


def ok(text: str) -> None:
    palette = theme.active()
    say(f"  {palette.paint('ok', 'ok', bold=True)}    {text}")


def warn(text: str) -> None:
    palette = theme.active()
    say(f"  {palette.paint('warn', 'warn', bold=True)}  {text}")


def fail(text: str) -> None:
    palette = theme.active()
    say(f"  {palette.paint('FAIL', 'alarm', bold=True)}  {text}", error=True)


def plain(text: str, indent: str = "  ") -> None:
    say(f"{indent}{text}")


def rule(label: str = "", indent: str = "  ") -> None:
    palette = theme.active()
    columns = theme.terminal_width()
    say(f"{indent}{palette.rule(label, columns - len(indent))}")


def heading(text: str, indent: str = "  ") -> None:
    palette = theme.active()
    say(f"{indent}{palette.paint(text, 'accent', bold=True)}")


# ---------------------------------------------------------------------------
# Boxes and menus.
# ---------------------------------------------------------------------------


def box(title: str, lines: list[str], subtitle: str = "", indent: str = "  ") -> list[str]:
    """A square cornered box, so the drawing obeys the no radius rule.

    Built as a list of strings rather than printed, so a caller can measure it or
    hold it while something else animates.
    """
    palette = theme.active()
    inner = max([width(title), *(width(line) for line in lines)] + [0])
    inner = min(max(inner, width(subtitle)), theme.terminal_width() - 6 - len(indent))
    edge = palette.paint("─" * (inner + 2), "line")
    corner = palette.paint

    drawn = [f"{indent}{corner('┌', 'line')}{edge}{corner('┐', 'line')}"]
    drawn.append(
        f"{indent}{corner('│', 'line')} {palette.paint(pad(title, inner), 'accent', bold=True)} {corner('│', 'line')}"
    )
    if subtitle:
        drawn.append(
            f"{indent}{corner('│', 'line')} {palette.paint(pad(truncate(subtitle, inner), inner), 'muted')} {corner('│', 'line')}"
        )
    drawn.append(f"{indent}{corner('├', 'line')}{edge}{corner('┤', 'line')}")
    for line in lines:
        drawn.append(f"{indent}{corner('│', 'line')} {pad(truncate(line, inner), inner)} {corner('│', 'line')}")
    drawn.append(f"{indent}{corner('└', 'line')}{edge}{corner('┘', 'line')}")
    return drawn


@dataclass(frozen=True)
class Item:
    """One line of a menu: what to press, what it does, and why you would.

    `action` is a name the caller resolves rather than a function, so the menu
    can be printed, inspected and tested without anything being run, and so the
    same item can be offered by the menu and reached from the command line.
    """

    key: str
    label: str
    hint: str = ""
    group: str = ""
    action: str = ""

    def line(self, key_width: int, label_width: int) -> str:
        palette = theme.active()
        pressed = palette.paint(pad(self.key, key_width), "info", bold=True)
        named = palette.paint(pad(self.label, label_width), "text")
        tail = palette.paint(self.hint, "muted") if self.hint else ""
        return f"  {pressed}{named}{tail}".rstrip()


class Menu:
    """A list of items, grouped, with the groups named where they change."""

    def __init__(self, items: list[Item], title: str = "", subtitle: str = ""):
        self.items = items
        self.title = title
        self.subtitle = subtitle

    def render(self) -> list[str]:
        key_width = max((len(item.key) for item in self.items), default=1) + 2
        label_width = max((width(item.label) for item in self.items), default=0) + 2
        lines: list[str] = []
        group = None
        for item in self.items:
            if item.group and item.group != group:
                if lines:
                    lines.append("")
                lines.append(theme.active().paint(item.group.upper(), "accent"))
                group = item.group
            lines.append(item.line(key_width, label_width))
        return box(self.title, lines, self.subtitle) if self.title else lines

    def show(self) -> None:
        for line in self.render():
            say(line)


def ask(prompt: str, default: str = "", indent: str = "  ") -> str | None:
    """One line of input, with the default shown and a closed stream understood.

    `None` means there was nobody to ask, which is not an error: running the tool
    without a terminal is a normal way to use its command line part, and the
    answer to "what should I do" with nobody there is to stop asking. An empty
    line is a different answer and comes back as the default.
    """
    palette = theme.active()
    shown = f" [{default}]" if default else ""
    try:
        sys.stdout.write(f"{indent}{palette.paint('>', 'accent', bold=True)} {prompt}{shown} ")
        sys.stdout.flush()
    except (OSError, ValueError):
        return None
    try:
        answer = input().strip()
    except EOFError:
        say()
        return None
    except KeyboardInterrupt:
        say()
        return None
    return answer or default


#: A paste the terminal fences with these two, once it has been asked to. The
#: markers travel inside the text rather than around it, so a message is read
#: without asking the stream a question it cannot answer while its own buffer
#: holds the rest of the paste.
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"

#: Drawn at the left of a line that continues a message, so a message that runs
#: past one line reads as one message rather than as several.
CONTINUED = "\u2502"


def _fence(on: bool) -> None:
    """Ask the terminal to mark a paste, when there is a terminal to ask.

    Only `interactive` and `_is_tty` are consulted: a run from a script has
    nobody pasting into it, and a write the host refuses is not worth reporting.
    """
    if not interactive() or not _is_tty():
        return
    try:
        sys.stdout.write("\x1b[?2004h" if on else "\x1b[?2004l")
        sys.stdout.flush()
    except (OSError, ValueError):
        return


def compose(label: str, terminator: str = "", indent: str = "  ") -> str | None:
    """One message, which may run to several lines.

    A chat needs a paragraph, a list and a code block, and one line of input
    gives none of them. Three ways in, because a person pastes, a person types,
    and a script writes.

    - A paste is read as one message when the terminal marks it, which is what
      `_fence` asks for. Bracketed paste is the reliable signal, because the
      marker arrives inside the text; watching the stream for a line that is
      already waiting cannot be relied on, since the reader buffering input
      holds the rest of the paste where a watch cannot see it.
    - The terminator ends the message, which is the way out on a terminal that
      does not mark a paste, and for a stream that is not a terminal at all. It
      is read from the settings rather than fixed here, and it is treated as
      content, not as an ending, once a marked paste has begun.
    - A trailing backslash continues onto the next line, for a person typing.
      It is likewise not read inside a marked paste, where a backslash may be
      part of what was copied.

    An empty line is kept, because a blank line between two paragraphs is
    content. Nothing is stripped here: leading spaces are how a list and a fence
    are written, and the caller decides what to send on.

    `None` means there was nobody to ask. A stream that closes mid message
    returns what had already arrived, so a piped paragraph is sent and the loop
    then ends on the `None` that follows.
    """
    palette = theme.active()
    marker = terminator.strip() if isinstance(terminator, str) else ""
    lines: list[str] = []
    pasting = False
    first = True
    _fence(True)
    try:
        while True:
            if not pasting:
                leader = label if first else palette.paint(CONTINUED, "muted")
                try:
                    sys.stdout.write(
                        f"{indent}{palette.paint('>', 'accent', bold=True)} {leader} "
                    )
                    sys.stdout.flush()
                except (OSError, ValueError):
                    return None
            try:
                raw = input()
            except EOFError:
                say()
                if not lines:
                    return None
                break
            except KeyboardInterrupt:
                say()
                raise
            first = False

            text = raw.replace("\r", "")
            ended = False
            if PASTE_END in text:
                text = text.split(PASTE_END, 1)[0]
                ended = True
            if PASTE_START in text:
                text = text.split(PASTE_START, 1)[1]
                pasting = True

            if ended:
                pasting = False
                # A paste ends with a newline, so the line carrying the closing
                # marker is normally empty. Appending it would put a blank line
                # at the end of every pasted message.
                if text != "" or not lines:
                    lines.append(text)
                break

            if marker and not pasting and text.strip() == marker:
                break

            continued = False
            if not pasting and text.rstrip().endswith("\\"):
                text = text.rstrip()[:-1].rstrip()
                continued = True
            lines.append(text)

            if pasting or continued:
                continue
            break
    finally:
        _fence(False)
    return "\n".join(lines)


def confirm(prompt: str, default: bool = False, indent: str = "  ") -> bool:
    shown = "y/N" if not default else "Y/n"
    answer = ask(f"{prompt} [{shown}]", default="y" if default else "n", indent=indent)
    if answer is None:
        return default
    return answer.casefold().startswith("y")


def choose(menu: Menu, prompt: str = "what next", default_key: str = "") -> Item | None:
    """Show a menu and read a key.

    Three answers, and they are deliberately different. A key on the menu is that
    item. An empty line is the default item, which is how "press return to carry
    on" works. Nothing at all, which is what a closed input stream gives, is
    `None`, so a run with no terminal stops rather than starting a model.
    """
    menu.show()
    while True:
        answer = ask(prompt)
        if answer is None:
            return None
        if not answer and default_key:
            for item in menu.items:
                if item.key == default_key:
                    return item
        for item in menu.items:
            if item.key.casefold() == answer.casefold():
                return item
        if not answer:
            return None
        warn(f"'{answer}' is not one of {', '.join(item.key for item in menu.items)}")


# ---------------------------------------------------------------------------
# Animation. Both of these fall back to one line when nothing is watching.
# ---------------------------------------------------------------------------


class Spinner:
    """A line that turns while something takes time, then reports how long.

    Used as a context manager, which is what makes it impossible to leave one
    running: the exit path stops the thread and clears the line whatever
    happened inside, including an exception.
    """

    def __init__(self, label: str, style: str = "spin", track: int = 11):
        self.label = label
        self.style = style
        self.track = track
        self.started = time.time()
        self.finished: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick = 0
        self._animated = theme.active().depth > 0 and _is_tty()

    def __enter__(self) -> Spinner:
        if self._animated:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        else:
            say(f"  {self.label} ...")
        return self

    def __exit__(self, kind, value, trace) -> bool:
        # The elapsed line is the useful part of a wait, so it is printed on the
        # way out unless the caller already reported one of its own.
        self.done()
        return False

    def set(self, label: str) -> None:
        """Change the label, which is how a multi stage wait says where it is."""
        self.label = label

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    def _frame(self) -> str:
        if self.style == "sweep":
            span = self.track
            position = self._tick % (span * 2)
            index = position if position < span else span * 2 - position - 1
            cells = [SWEEP_TRACK] * span
            for offset in (-2, -1, 0, 1):
                at = index + offset
                if 0 <= at < span:
                    cells[at] = SWEEP_BLOCK if offset == 0 else SWEEP_TAIL
            return "".join(cells) + " "
        return SPIN_FRAMES[self._tick % len(SPIN_FRAMES)]

    def _loop(self) -> None:
        palette = theme.active()
        while not self._stop.is_set():
            frame = self._frame()
            shown = palette.paint(frame, "accent", bold=True)
            seconds = palette.paint(f"{self.elapsed:5.1f}s", "muted")
            text = truncate(self.label, max(20, theme.terminal_width() - 30))
            with _LOCK:
                sys.stdout.write(f"\r  {shown} {text} {seconds}   ")
                sys.stdout.flush()
            self._tick += 1
            time.sleep(FRAME_SECONDS)

    def stop(self, done: str | None = None) -> str:
        """Stop turning and replace the line with one that stays.

        The elapsed seconds are part of the result rather than decoration: the
        question a wait answers is how long the thing took.
        """
        if not self._stop.is_set():
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=1.0)
            if self._animated:
                with _LOCK:
                    sys.stdout.write("\r" + " " * (theme.terminal_width() - 1) + "\r")
                    sys.stdout.flush()
        if done is None:
            done = self.label
        return done

    def done(self, text: str | None = None) -> None:
        """Stop and report the outcome with the time beside it, once."""
        palette = theme.active()
        if self.finished is not None:
            return
        self.stop()
        body = text if text is not None else self.label
        self.finished = body
        say(f"  {palette.paint('·', 'line')} {pad(body, 52)}{palette.paint(f'{self.elapsed:.1f}s', 'muted')}")


class Progress:
    """A bar a callback drives, for work whose length is known in advance.

    Driven rather than animated on its own thread, because the thing it counts is
    the thing it must not run ahead of. Denoising steps report through this, so
    the bar is the model's real position and not an estimate.
    """

    def __init__(self, label: str, total: int, track: int = 26):
        self.label = label
        self.total = max(1, total)
        self.done = 0
        self.track = track
        self.started = time.time()
        self._animated = theme.active().depth > 0 and _is_tty()

    def __enter__(self) -> Progress:
        self.tick(0)
        return self

    def __exit__(self, kind, value, trace) -> bool:
        self.finish()
        return False

    def tick(self, step: int | None = None) -> None:
        self.done = min(self.total, (self.done + 1) if step is None else step)
        self._draw()

    def _draw(self) -> None:
        palette = theme.active()
        filled = round(self.track * self.done / self.total)
        bar = SWEEP_BLOCK * filled + SWEEP_TRACK * (self.track - filled)
        percent = f"{self.done * 100 // self.total:3d}%"
        elapsed = time.time() - self.started
        rate = elapsed / self.done if self.done else 0.0
        left = f"{rate * (self.total - self.done):5.1f}s left" if self.done and self.done < self.total else ""
        drawn = (
            f"  {palette.paint(bar, 'accent')} {palette.paint(percent, 'info', bold=True)} "
            f"{palette.paint(truncate(self.label, 34), 'text')} "
            f"{palette.paint(f'{self.done}/{self.total}', 'muted')} "
            f"{palette.paint(left, 'muted')}"
        )
        if self._animated:
            with _LOCK:
                sys.stdout.write("\r" + drawn + " " * 6)
                sys.stdout.flush()
        elif self.done in (0, self.total):
            say(drawn.rstrip())

    def finish(self) -> None:
        self.done = self.total
        self._draw()
        if self._animated:
            with _LOCK:
                sys.stdout.write("\n")
                sys.stdout.flush()


def stream_text(chunk: str) -> None:
    """Write a model's output as it arrives, holding the lock for the fragment."""
    with _LOCK:
        sys.stdout.write(chunk)
        sys.stdout.flush()


def interactive() -> bool:
    """Whether there is a person at the other end of standard input.

    A prompt that nobody can answer is not a prompt, it is a hang. Anything that
    asks a question checks this first, so a run from a script prints what it
    would have asked about and carries on.
    """
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def columns(ceiling: int = 92) -> int:
    """Usable columns, for a caller laying out its own text."""
    if not _is_tty():
        return ceiling
    return max(40, min(shutil.get_terminal_size((80, 24)).columns - 2, ceiling))


@dataclass
class Summary:
    """Rows collected during a run, printed once at the end in one aligned block."""

    rows: list[tuple[str, str]] = field(default_factory=list)

    def add(self, label: str, value) -> Summary:
        self.rows.append((label, str(value)))
        return self

    def show(self, indent: str = "  ") -> None:
        for label, value in self.rows:
            say(row(label, value, indent))
