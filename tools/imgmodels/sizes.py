"""Byte formatting, in one place because two modules report the same numbers.

A weight file's size is quoted before anything is loaded (the plan) and after
(the observation), and the two quotes sit beside each other in the output. They
have to be the same arithmetic, so they are this module.
"""

from __future__ import annotations

UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def human_bytes(size: int | float | None) -> str:
    """A byte count to one decimal place, in the largest unit that fits."""
    if size is None:
        return "unknown"
    value = float(size)
    for unit in UNITS:
        if abs(value) < 1024 or unit == UNITS[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {UNITS[-1]}"


def saved(packed: int | None, exact: int | None) -> str:
    """How much smaller a packed representation is than an exact one."""
    if not packed or not exact or packed >= exact:
        return ""
    return f"{1 - packed / exact:.1%}"
