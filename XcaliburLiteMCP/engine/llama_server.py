"""
llama.cpp server supervision.

Runs the project's GGUF with llama-server and Metal offload, so the model is
served once and reused across turns instead of being reloaded per request. The
whole dashboard and TUI talk to it over the OpenAI-compatible HTTP API.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from config import (
    LLAMA_CTX,
    LLAMA_GPU_LAYERS,
    LLAMA_HOST,
    LLAMA_PARALLEL,
    LLAMA_PORT,
    LLAMA_SERVER_BIN,
    LLAMA_STARTUP_TIMEOUT,
    LLAMA_THREADS,
    MODEL_PATH,
    WORKSPACE,
)

LOG_PATH: Path = WORKSPACE / "llama-server.log"


def base_url() -> str:
    return f"http://{LLAMA_HOST}:{LLAMA_PORT}"


def is_up(timeout: float = 2.0) -> bool:
    try:
        resp = requests.get(urljoin(base_url(), "/health"), timeout=timeout)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def build_command() -> list[str]:
    return [
        LLAMA_SERVER_BIN,
        "-m", str(MODEL_PATH),
        "-c", str(LLAMA_CTX),
        "-ngl", str(LLAMA_GPU_LAYERS),
        "-np", str(LLAMA_PARALLEL),
        "-t", str(LLAMA_THREADS),
        "-fa", "on",
        "--host", LLAMA_HOST,
        "--port", str(LLAMA_PORT),
    ]


def start(wait: bool = True) -> subprocess.Popen | None:
    """Launch llama-server in the background. Returns the process or None."""
    if is_up():
        return None
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"model not found: {MODEL_PATH}")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_PATH.open("ab")
    proc = subprocess.Popen(build_command(), stdout=log, stderr=subprocess.STDOUT)

    if wait:
        wait_until_up(proc)
    return proc


def wait_until_up(proc: subprocess.Popen | None = None, timeout: int = LLAMA_STARTUP_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"llama-server exited early; see {LOG_PATH}")
        if is_up():
            return True
        time.sleep(1.0)
    return False


def ensure(wait: bool = True) -> bool:
    """Make sure a server is reachable, starting one when necessary."""
    if is_up():
        return True
    start(wait=wait)
    return is_up()


def stop() -> None:
    """Best-effort shutdown of a server this user owns."""
    import signal

    try:
        out = subprocess.run(
            ["pgrep", "-f", f"llama-server.*--port {LLAMA_PORT}"],
            capture_output=True, text=True,
        )
    except OSError:
        return
    for pid in out.stdout.split():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            continue
