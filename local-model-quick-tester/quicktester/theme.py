"""The terminal palette, read from the design system this project already has.

The window's colours are written once in `desktop/renderer/styles.css`, and the
terminal is not a second place for them to live. So the tokens are parsed out of
that stylesheet at import time rather than typed again here, and a checkout whose
stylesheet has moved falls back to the same values so the tool still runs.

Three things follow from the stylesheet's own rules rather than from taste:

  * There are three accents and they are a split complementary set, so the
    terminal uses them for the same three jobs: `transform` for the thing being
    decided, `harmonious` for a result, `communicative` for information.
  * "The neon hues are never used for text on a light surface." A terminal may be
    light, and the stylesheet cannot know which. On a light terminal every accent
    is shaded toward ink until it carries contrast, which is the same rule the
    window keeps, applied to the other surface.
  * "No border radius" is a statement about geometry, so the box characters here
    have square corners and a selected row is a solid bar rather than a rounded
    pill.

Colour is decided in three steps so the output is correct in every terminal, and
correct again when there is no colour at all: 24 bit when the terminal says it
can, 256 colour when it only claims that, the sixteen system colours otherwise,
and nothing when the output is a pipe or `NO_COLOR` is set. Every derived colour
is computed from a token by `mix`, so there is one place where a colour is
chosen and no place where a hex value is guessed.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass

#: The stylesheet is the source of truth. These are the values it holds, kept
#: only so that a checkout without it still renders; the reader prefers the file.
STYLESHEET = ("desktop", "renderer", "styles.css")

FALLBACK_TOKENS: dict[str, str] = {
    "tint": "#f0f9ff",
    "tint-deep": "#e2f3fd",
    "ink": "#000000",
    "paper": "#ffffff",
    "transform": "#ff0062",
    "harmonious": "#00ff1e",
    "communicative": "#00e1ff",
}

#: The role each token plays. A role name is what the rest of the tool asks for,
#: so no caller needs to know which token is which.
TOKEN_ROLE: dict[str, str] = {
    "tint": "text",
    "ink": "text",
    "tint-deep": "surface",
    "paper": "strong",
    "transform": "accent",
    "harmonious": "ok",
    "communicative": "info",
}

#: How much of an accent is kept when the terminal is light. Shading toward ink
#: is what makes a neon hue readable on a pale background, and 0.55 is where all
#: three still read as themselves rather than as grey.
LIGHT_SHADE = 0.55

#: How far a colour moves toward the background to make a quieter version of it.
MUTED_MIX = 0.55
LINE_MIX = 0.72

#: The sixteen system colours, as the values a terminal usually renders them at.
#: Used only to pick the nearest one when the terminal offers nothing finer.
ANSI16: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
    (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
    (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)

ANSI_NAMES: tuple[str, ...] = (
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "bright-black", "bright-red", "bright-green", "bright-yellow",
    "bright-blue", "bright-magenta", "bright-cyan", "bright-white",
)


# ---------------------------------------------------------------------------
# Colour arithmetic. One mix function, so every derived value has a stated
# origin and the palette can be read as a set of relationships.
# ---------------------------------------------------------------------------


def parse(colour: str) -> tuple[int, int, int]:
    """`#rrggbb` to three bytes, with the long form accepted as well."""
    text = colour.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6:
        raise ValueError(f"not a hex colour: {colour!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def render(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def mix(left: str, right: str, weight: float) -> str:
    """`weight` of the way from `left` to `right`, so 0 is left and 1 is right."""
    first, second = parse(left), parse(right)
    blended = tuple(
        round(a + (b - a) * weight) for a, b in zip(first, second)
    )
    return render(blended)


def luminance(colour: str) -> float:
    """Perceived brightness, 0 to 1. Decides whether ink or paper sits on a chip."""
    red, green, blue = (channel / 255 for channel in parse(colour))

    def linear(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def readable_on(colour: str, ink: str, paper: str) -> str:
    """Which of two inks stands on a solid colour, chosen by contrast."""
    return ink if luminance(colour) > 0.45 else paper


def to_256(rgb: tuple[int, int, int]) -> int:
    """The nearest xterm 256 index: the 6x6x6 cube, or the grey ramp if closer."""
    red, green, blue = rgb

    def level(value: int) -> int:
        return round((value / 255) * 5)

    cube = 16 + 36 * level(red) + 6 * level(green) + level(blue)
    grey = round((red + green + blue) / 3 / 255 * 23)
    ramp = 232 + grey

    def distance(index: int) -> float:
        steps = (0, 95, 135, 175, 215, 255)
        if index >= 232:
            shade = 8 + (index - 232) * 10
            point = (shade, shade, shade)
        else:
            offset = index - 16
            point = (
                steps[offset // 36],
                steps[(offset % 36) // 6],
                steps[offset % 6],
            )
        return sum((a - b) ** 2 for a, b in zip(rgb, point))

    return cube if distance(cube) <= distance(ramp) else ramp


def to_16(rgb: tuple[int, int, int]) -> int:
    """The nearest of the sixteen system colours."""
    return min(range(16), key=lambda index: sum(
        (a - b) ** 2 for a, b in zip(rgb, ANSI16[index])
    ))


def to_ansi8(index: int) -> int:
    """A palette index as the SGR number the terminal takes for a foreground."""
    return 30 + index if index < 8 else 90 + index - 8


# ---------------------------------------------------------------------------
# Reading the stylesheet.
# ---------------------------------------------------------------------------

_DECLARATION = re.compile(r"--([a-z-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")


def read_tokens(root) -> tuple[dict[str, str], str]:
    """The tokens a stylesheet declares, and where they came from.

    Only `#rrggbb` values are read. The stylesheet also declares lengths, shadows
    and a font stack, and those are the window's business rather than a
    terminal's, so they are left where they are.
    """
    path = root.joinpath(*STYLESHEET)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return dict(FALLBACK_TOKENS), "built in, the stylesheet was not readable"

    found = {name: value for name, value in _DECLARATION.findall(text)}
    if not found:
        return dict(FALLBACK_TOKENS), "built in, the stylesheet declares no colours"
    merged = dict(FALLBACK_TOKENS)
    merged.update(found)
    return merged, f"read from {'/'.join(STYLESHEET)}"


# ---------------------------------------------------------------------------
# The palette.
# ---------------------------------------------------------------------------


def colour_support() -> int:
    """How many bits of colour this terminal will render, and nothing else.

    `NO_COLOR` wins over everything, including `FORCE_COLOR`, because a person
    who set it meant it. A pipe is not a terminal, so an unredirected run has
    colour and a redirected one has text a file can hold.
    """
    if os.environ.get("NO_COLOR") is not None:
        return 0
    forced = os.environ.get("FORCE_COLOR")
    if forced is not None and forced != "0":
        return 24
    if not _is_tty():
        return 0
    if os.environ.get("TERM", "") in ("", "dumb"):
        return 0
    term_colour = os.environ.get("COLORTERM", "").lower()
    if term_colour in ("truecolor", "24bit"):
        return 24
    if "256" in os.environ.get("TERM", ""):
        return 8
    return 4


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def terminal_width(ceiling: int = 96, floor: int = 44) -> int:
    """A box wide enough to read and not wider than the terminal.

    Piped output gets the ceiling, because a file has no width to fit.
    """
    if not _is_tty():
        return ceiling
    columns = shutil.get_terminal_size((80, 24)).columns
    return max(floor, min(columns - 2, ceiling))


@dataclass(frozen=True)
class Palette:
    """Named roles resolved to escape sequences once, for one terminal."""

    depth: int
    mode: str
    roles: dict[str, str]
    on_accent: str
    note: str

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m"

    def code(self, role: str, background: bool = False, bold: bool = False) -> str:
        """The SGR body for a role, so two roles can share one sequence."""
        rgb = parse(self.roles[role])
        parts: list[str] = ["1"] if bold else []
        if background:
            if self.depth == 24:
                parts.append(f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}")
            elif self.depth == 8:
                parts.append(f"48;5;{to_256(rgb)}")
            else:
                parts.append(f"{to_ansi8(to_16(rgb)) + 10}")
        else:
            if self.depth == 24:
                parts.append(f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}")
            elif self.depth == 8:
                parts.append(f"38;5;{to_256(rgb)}")
            else:
                parts.append(f"{to_ansi8(to_16(rgb))}")
        return ";".join(parts)

    def paint(
        self,
        text: str,
        role: str = "text",
        bold: bool = False,
        background: bool = False,
        underline: bool = False,
    ) -> str:
        """One role applied to one string. The only place an escape is written."""
        if self.depth == 0 or not text:
            return text
        body = self.code(role, background=background, bold=bold)
        if underline:
            body += ";4"
        return self._wrap(body, text)

    def chip(self, text: str, role: str = "accent") -> str:
        """Solid colour with an ink chosen for contrast, as the window does it."""
        if self.depth == 0:
            return f"[{text}]"
        body = self.code(role, background=True, bold=True)
        ink = self.code(self.on_accent)
        return f"\x1b[{body}m\x1b[{ink}m {text} \x1b[0m"

    def rule(self, label: str, width: int, role: str = "line") -> str:
        """A horizontal rule that carries a label, drawn in square strokes."""
        if not label:
            return self.paint("─" * width, role)
        head = f"─ {label} "
        tail = "─" * max(0, width - len(head))
        return self.paint(head + tail, role)

    def roles_available(self) -> str:
        return ", ".join(sorted(self.roles))


def build(tokens: dict[str, str], depth: int, mode: str, note: str) -> Palette:
    """Resolve tokens to roles for one terminal.

    A dark terminal keeps the accents as the stylesheet wrote them. A light one
    shades each toward ink, because a neon hue on a pale surface is unreadable,
    and that is the stylesheet's own stated rule rather than a preference here.
    """
    ink = tokens["ink"]
    paper = tokens["paper"]
    light = mode == "light"

    def accent(name: str) -> str:
        return mix(tokens[name], ink, LIGHT_SHADE) if light else tokens[name]

    transform = accent("transform")
    quiet_ground = paper if light else ink

    roles = {
        "text": ink if light else tokens["tint"],
        "strong": paper if not light else ink,
        "muted": mix(ink if light else tokens["tint"], quiet_ground, MUTED_MIX),
        "line": mix(ink if light else tokens["tint"], quiet_ground, LINE_MIX),
        "accent": transform,
        "ok": accent("harmonious"),
        "info": accent("communicative"),
        "warn": mix(transform, paper, 0.35),
        "alarm": transform,
        "surface": tokens["tint-deep"] if light else mix(tokens["tint-deep"], ink, 0.9),
    }
    return Palette(
        depth=depth,
        mode=mode,
        roles=roles,
        on_accent=readable_on(transform, ink, paper),
        note=note,
    )


def detect_mode(requested: str = "auto") -> str:
    """The terminal's own background, when it will say.

    `COLORFGBG` is set by several terminals as `foreground;background`, and a
    background below 8 is dark on every one of them. Nothing else is guessed: a
    terminal that stays quiet gets the dark palette, which is the more common
    default and the one the accents were written for.
    """
    if requested in ("dark", "light"):
        return requested
    raw = os.environ.get("COLORFGBG", "")
    if ";" in raw:
        _, _, background = raw.rpartition(";")
        if background.strip().isdigit() and int(background) >= 8:
            return "light"
    return "dark"


_active: Palette | None = None


def configure(depth: int | None = None, mode: str = "auto", root=None) -> Palette:
    """Set the palette for this process and return it."""
    global _active
    depth = colour_support() if depth is None else depth
    tokens, note = read_tokens(root) if root is not None else (dict(FALLBACK_TOKENS), "built in")
    _active = build(tokens, depth, detect_mode(mode), note)
    return _active


def active() -> Palette:
    """The palette in force, configured default if `configure` was never called."""
    if _active is None:
        return configure(root=None)
    return _active
