"""
Startup splash and loading animation.

Reads the banner art from ``on-load.txt``, stamps the version into its
placeholder, and animates it while the session boots. The reveal travels down
the art with a bright scan band, a column gradient is drawn from the shared
palette, the star characters twinkle, and the moon block pulses. The boot steps
ride along underneath as moon-phase ticks with a progress bar, so the wait for
the model server is shown rather than hidden.

Colour is read from ``config.PALETTE`` and nowhere else, so the splash, the
dashboard, and the desktop app cannot drift apart. Hex interpolation lives here
because a Rich style takes a colour, not a gradient.

The animation is skipped and a plain banner printed instead when the output is
not a terminal, when ``NO_COLOR`` or a dumb terminal is set, or when it is
switched off. Nothing is printed at import time, so importing this is safe from
any entry point, including the one that owns standard output.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.text import Text

from config import (
    APP_NAME,
    PALETTE,
    SPLASH_ENABLED,
    SPLASH_FILE,
    SPLASH_FPS,
    SPLASH_HOLD_SECONDS,
    SPLASH_REVEAL_SECONDS,
    VERSION,
    VERSION_TOKEN,
)

#: Moon phases, dark through full and back, used as the activity tick so the
#: spinner matches the moon in the art rather than being a generic dot.
PHASES = "○◔◑◕●◕◑◔"
_STAR = "+"
_BAR_FULL = "█"
_BAR_EMPTY = "░"
_BAR_WIDTH = 26
_SCAN_ROWS = 2
_BRIGHT = "#ffffff"
_BG = "#000000"

#: Gradient stops, ordered left to right across the art. They are the desktop
#: palette's split complements, so the terminal reads as the same product.
GRADIENT: tuple[str, ...] = (
    PALETTE["transform"],
    PALETTE["communicative"],
    PALETTE["harmonious"],
)


# ---------------------------------------------------------------------------
# Colour helpers. Pure functions, so a frame can be checked without a terminal.
# ---------------------------------------------------------------------------

def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def to_hex(colour: tuple[int, int, int]) -> str:
    channels = (clamp(float(channel), 0.0, 255.0) for channel in colour)
    return "#{:02x}{:02x}{:02x}".format(*(int(round(channel)) for channel in channels))


def mix(first: str, second: str, ratio: float) -> str:
    """Blend two hex colours; ``ratio`` 0 gives the first, 1 gives the second."""
    ratio = clamp(ratio)
    a, b = to_rgb(first), to_rgb(second)
    return to_hex(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))


def ramp(ratio: float, stops: tuple[str, ...] = GRADIENT) -> str:
    """Sample a multi-stop gradient at ``ratio`` in 0..1."""
    ratio = clamp(ratio)
    spans = max(1, len(stops) - 1)
    position = ratio * spans
    index = min(int(position), spans - 1)
    return mix(stops[index], stops[index + 1], position - index)


def sink(colour: str, amount: float) -> str:
    """Darken a colour toward the terminal background."""
    return mix(colour, _BG, clamp(amount))


# ---------------------------------------------------------------------------
# Art loading.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Art:
    """The banner, already version-stamped."""

    lines: tuple[str, ...]
    version: str = VERSION
    token: str = VERSION_TOKEN

    @property
    def rows(self) -> int:
        return len(self.lines)

    @property
    def cols(self) -> int:
        return max((len(line) for line in self.lines), default=0)

    @property
    def moon_rows(self) -> frozenset[int]:
        """
        Rows that make up the moon block and its captions.

        Anchored on the moon's top edge and then run downward while each row
        still begins with a moon character, so editing the art moves the pulse
        with it instead of leaving it behind, and the starfield below stays out
        of the glow.
        """
        anchor = next(
            (index for index, line in enumerate(self.lines) if ".-'" in line),
            None,
        )
        if anchor is None:
            return frozenset()
        rows = []
        for line in self.lines[anchor:]:
            stripped = line.strip()
            if not stripped or stripped[0] not in ".-'|":
                break
            rows.append(anchor + len(rows))
        return frozenset(rows)

    def stamped(self) -> str:
        return "\n".join(self.lines)


def load_art(
    path: Path | str = SPLASH_FILE,
    version: str = VERSION,
    token: str = VERSION_TOKEN,
) -> Art:
    """
    Read the banner and replace the version placeholder.

    A missing or unreadable art file is not fatal: the banner degrades to the
    product name and the version so the session still starts.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raw = f"{APP_NAME}  Version: {token}"

    stamped = raw.replace(token, version) if token else raw
    lines = [line.rstrip() for line in stamped.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        lines = [f"{APP_NAME} {version}"]
    return Art(tuple(lines), version, token)


# ---------------------------------------------------------------------------
# Boot steps and the dynamic view.
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """One unit of startup work, reported while it runs."""

    key: str
    label: str
    action: Callable[[], Any]
    summarise: Callable[[Any], str] | None = None
    status: str = "pending"      # pending | running | done | failed
    summary: str = ""
    error: str = ""

    def mark(self, status: str, summary: str = "", error: str = "") -> None:
        self.status = status
        self.summary = summary
        self.error = error


@dataclass
class _State:
    """Everything a frame needs, sampled fresh on each refresh."""

    art: Art
    started: float
    steps: list[Step]
    caption: str = ""
    reveal_seconds: float = SPLASH_REVEAL_SECONDS

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def progress(self) -> float:
        if not self.steps:
            return 1.0
        finished = sum(1 for step in self.steps if step.status in ("done", "failed"))
        return 1.0 if finished == len(self.steps) else finished / len(self.steps)


class SplashView:
    """A renderable that redraws the banner and the boot steps every frame."""

    def __init__(self, state: _State) -> None:
        self.state = state

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        state = self.state
        if options.max_width < state.art.cols:
            yield Text(f"{APP_NAME} {state.art.version}", style=f"bold {PALETTE['communicative']}")
        else:
            yield self._banner()
        yield Text()
        yield from self._steps()

    # -- banner ------------------------------------------------------------
    def _banner(self) -> Text:
        state = self.state
        art = state.art
        elapsed = state.elapsed
        reveal = clamp(elapsed / max(state.reveal_seconds, 1e-6))
        revealed = math.ceil(reveal * art.rows)
        scan = revealed - 1
        moon = art.moon_rows
        moon_pulse = 0.5 + 0.5 * math.sin(elapsed * 2.4)

        text = Text(no_wrap=True, overflow="crop")
        for row, line in enumerate(art.lines):
            if row:
                text.append("\n")
            if row > scan:
                continue                      # not yet revealed: leave it blank
            in_scan = scan - row < _SCAN_ROWS
            for col, char in enumerate(line):
                if char == " ":
                    text.append(char)
                    continue
                colour = ramp(col / max(art.cols - 1, 1))
                if char == _STAR:
                    twinkle = 0.5 + 0.5 * math.sin(elapsed * 5.0 + row * 1.7 + col * 0.9)
                    text.append(char, style=f"bold {mix('#3a3a3a', colour, 0.25 + 0.75 * twinkle)}")
                elif in_scan:
                    text.append(char, style=f"bold {mix(_BRIGHT, colour, 0.35)}")
                elif row in moon:
                    text.append(char, style=f"bold {mix(sink(colour, 0.45), _BRIGHT, moon_pulse)}")
                else:
                    text.append(char, style=colour)
        return text

    # -- steps -------------------------------------------------------------
    def _steps(self) -> RenderResult:
        state = self.state
        phase = PHASES[int(state.elapsed * 8) % len(PHASES)]
        for step in state.steps:
            yield self._step_line(step, phase)
        yield self._bar()

    @staticmethod
    def _step_line(step: Step, phase: str) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        if step.status == "running":
            line.append(f"  {phase} ", style=f"bold {PALETTE['harmonious']}")
            line.append(f"{step.label}...", style=mix(PALETTE["communicative"], _BRIGHT, 0.2))
        elif step.status == "done":
            line.append("  + ", style=f"bold {PALETTE['harmonious']}")
            line.append(step.label, style=f"bold {PALETTE['communicative']}")
            if step.summary:
                line.append(f"  {step.summary}", style="dim #888888")
        elif step.status == "failed":
            line.append("  ! ", style=f"bold {PALETTE['transform']}")
            line.append(step.label, style=f"bold {PALETTE['transform']}")
            if step.error:
                line.append(f"  {step.error}", style=f"dim {PALETTE['transform']}")
        else:
            line.append(f"    {step.label}", style="dim #555555")
        return line

    def _bar(self) -> Text:
        state = self.state
        filled = int(round(clamp(state.progress) * _BAR_WIDTH))
        bar = Text(no_wrap=True, overflow="crop")
        bar.append("  ")
        bar.append(_BAR_FULL * filled, style=f"bold {PALETTE['harmonious']}")
        bar.append(_BAR_EMPTY * (_BAR_WIDTH - filled), style="dim #333333")
        if state.caption:
            bar.append(f"  {state.caption}", style="dim #888888")
        return bar


def frame(
    art: Art,
    steps: list[Step],
    elapsed: float,
    *,
    caption: str = "",
    reveal_seconds: float = SPLASH_REVEAL_SECONDS,
) -> SplashView:
    """
    Build a view pinned to a given elapsed time.

    The animation is a function of elapsed time, so a frame can be rendered and
    inspected without waiting or without a terminal. The live view uses the same
    builder with the real clock.
    """
    state = _State(
        art=art,
        started=time.monotonic() - max(0.0, elapsed),
        steps=steps,
        caption=caption,
        reveal_seconds=reveal_seconds,
    )
    return SplashView(state)


# ---------------------------------------------------------------------------
# The boot runner.
# ---------------------------------------------------------------------------

class Boot:
    """
    Run the startup steps under the animated banner.

    Steps are added in the order they should run and executed in that order. A
    step that raises is recorded as failed and the boot continues, because a
    missing model server should leave the user at a prompt that can explain
    itself rather than at a traceback. The return value maps each step key to
    whatever it returned, or ``None`` when it failed.
    """

    def __init__(
        self,
        *,
        art_path: Path | str = SPLASH_FILE,
        version: str = VERSION,
        caption: str = "",
        enabled: bool = SPLASH_ENABLED,
        fps: int = SPLASH_FPS,
        reveal_seconds: float = SPLASH_REVEAL_SECONDS,
        hold_seconds: float = SPLASH_HOLD_SECONDS,
    ) -> None:
        self.art = load_art(art_path, version)
        self.caption = caption
        self.steps: list[Step] = []
        self._console = Console()
        self._enabled = enabled
        self._fps = max(1, fps)
        self._reveal_seconds = reveal_seconds
        self._hold_seconds = hold_seconds

    def add(
        self,
        key: str,
        label: str,
        action: Callable[[], Any],
        summarise: Callable[[Any], str] | None = None,
    ) -> None:
        self.steps.append(Step(key, label, action, summarise))

    # -- mode --------------------------------------------------------------
    def animate(self, console: Console | None = None) -> bool:
        """Animate only on a colour terminal, and only when it was not refused."""
        if not self._enabled:
            return False
        if os.getenv("NO_COLOR") or os.getenv("TERM", "") == "dumb":
            return False
        return (console or self._console).is_terminal

    def run(self, console: Console | None = None) -> dict[str, Any]:
        self._console = console or Console()
        results: dict[str, Any] = {}
        if self.animate(self._console):
            self._run_live(results)
        else:
            self._run_plain(results)
        return results

    # -- execution ---------------------------------------------------------
    def _execute(self, step: Step, results: dict[str, Any]) -> None:
        step.mark("running")
        try:
            value = step.action()
        except Exception as exc:  # noqa: BLE001 - the message is the report
            step.mark("failed", error=str(exc))
            results[step.key] = None
            return
        step.mark("done", summary=step.summarise(value) if step.summarise else "")
        results[step.key] = value

    def _run_live(self, results: dict[str, Any]) -> None:
        state = _State(
            art=self.art,
            started=time.monotonic(),
            steps=self.steps,
            caption=self.caption,
            reveal_seconds=self._reveal_seconds,
        )
        with Live(
            SplashView(state),
            console=self._console,
            refresh_per_second=self._fps,
            transient=False,
            vertical_overflow="visible",
        ):
            for step in self.steps:
                self._execute(step, results)
            # The steps can finish in milliseconds when the model is already
            # running, which would leave the art half drawn. Let the reveal
            # travel the rest of the way before settling, so the frame the user
            # keeps is always whole. A slow boot pays nothing here.
            remaining = self._reveal_seconds - state.elapsed
            if remaining > 0:
                time.sleep(remaining)
            if self._hold_seconds:
                time.sleep(self._hold_seconds)

    def _run_plain(self, results: dict[str, Any]) -> None:
        """No animation: the banner once, then one line per step as it finishes."""
        console = self._console
        # markup and highlight off: the banner is art, not prose, so no span
        # should be invented inside it.
        console.print(
            self.art.stamped(),
            style=PALETTE["communicative"],
            markup=False,
            highlight=False,
        )
        for step in self.steps:
            self._execute(step, results)
            if step.status == "done":
                tail = f"  [dim]{step.summary}[/dim]" if step.summary else ""
                console.print(f"  [success]+[/success] {step.label}{tail}")
            else:
                console.print(f"  [error]![/error] {step.label} [dim]{step.error}[/dim]")


def plain_banner(version: str = VERSION, art_path: Path | str = SPLASH_FILE) -> Text:
    """The banner as a plain, unanimated renderable."""
    return Text(load_art(art_path, version).stamped(), style=PALETTE["communicative"])


__all__ = [
    "Art",
    "Boot",
    "GRADIENT",
    "PHASES",
    "SplashView",
    "Step",
    "clamp",
    "frame",
    "load_art",
    "mix",
    "plain_banner",
    "ramp",
    "sink",
    "to_hex",
    "to_rgb",
]
