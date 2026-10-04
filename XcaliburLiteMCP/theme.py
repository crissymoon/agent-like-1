"""
Shared Rich console and theme.

Colours are pulled from config.PALETTE so the TUI matches the desktop app.
The desktop palette is designed for light surfaces, so on the dark terminal the
neon hues carry the interactive layer while white carries the words.
"""
from rich.console import Console
from rich.theme import Theme

from config import PALETTE

_C = PALETTE["communicative"]
_T = PALETTE["transform"]
_H = PALETTE["harmonious"]

console = Console(
    theme=Theme(
        {
            "header": f"bold {_C}",
            "path": f"bold {_C}",
            "error": f"bold {_T}",
            "ai": f"bold {_C}",
            "tool": f"bold {_C}",
            "success": f"bold {_H}",
            "warn": f"bold {_T}",
            "dim": "dim #888888",
            "markdown.h1": f"bold {_C} underline",
            "markdown.h2": f"bold {_C}",
            "markdown.h3": f"bold {_T}",
            "markdown.h4": f"bold {_H}",
            "markdown.code": f"bold {_C} on #0a0f14",
            "markdown.link": f"underline {_C}",
            "markdown.link_url": f"dim {_C}",
            "markdown.item.bullet": f"bold {_C}",
            "markdown.item.number": f"bold {_C}",
            "markdown.block_quote": "italic #888888",
            "markdown.hr": _C,
            "markdown.h1.border": _C,
        }
    )
)
