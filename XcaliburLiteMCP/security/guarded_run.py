"""
The guarded runner.

Sits directly on top of command_guard: it refuses anything the scanner rejects
and only otherwise spawns the process. No module in this project calls
subprocess for a model-supplied command except through here.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from config import COMMAND_OUTPUT_LIMIT, COMMAND_TIMEOUT, WORKSPACE
from security import command_guard


def run(command: str, cwd: Path | None = None, timeout: int = COMMAND_TIMEOUT) -> dict:
    """
    Scan and, if allowed, execute the command.

    The program is started directly, without a shell. The guard refuses the
    operators that would need one, so the string is split into an argument list
    and never reaches a shell to be interpreted.

    Returns a dict that always carries ``allowed`` so the caller can tell a
    refusal apart from a failing command.
    """
    allowed, reason = command_guard.scan(command)
    if not allowed:
        return {
            "command": command,
            "allowed": False,
            "success": False,
            "error": f"command rejected by guard: {reason}",
        }

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return {
            "command": command,
            "allowed": False,
            "success": False,
            "error": f"command could not be read: {exc}",
        }
    if not argv:
        return {
            "command": command,
            "allowed": False,
            "success": False,
            "error": "empty command",
        }

    work_dir = Path(cwd) if cwd else WORKSPACE
    try:
        result = subprocess.run(
            argv,
            shell=False,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "allowed": True,
            "success": False,
            "error": f"timed out after {timeout}s",
        }

    return {
        "command": command,
        "allowed": True,
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "stdout": result.stdout[:COMMAND_OUTPUT_LIMIT],
        "stderr": result.stderr[:COMMAND_OUTPUT_LIMIT // 2],
    }
