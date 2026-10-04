"""
Central configuration for XcaliburLiteMCP.

Every tunable lives here so no module hardcodes a path, a port, or a colour.
Values can be overridden with environment variables or a local .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Location of this package and the workspace the agent operates inside.
# ---------------------------------------------------------------------------
ROOT: Path = Path(__file__).resolve().parent

WORKSPACE: Path = Path(os.getenv("XCALIBUR_WORKSPACE", ROOT / "workspace")).resolve()
ORIGINAL_COPY: Path = WORKSPACE / "original_copy"   # pristine snapshot of touched files
SHADOW_COPY: Path = WORKSPACE / "shadow_copy"       # timestamped proposed edits
JOBS_DIR: Path = WORKSPACE / "jobs"                 # persisted dashboard job records
TESTS_DIR: Path = WORKSPACE / "tests"               # throwaway env used by "test"

# The codebase the agent is allowed to read and propose edits against.
# Defaults to the project that contains this package.
TARGET_ROOT: Path = Path(
    os.getenv("XCALIBUR_TARGET_ROOT", ROOT.parent)
).resolve()

# ---------------------------------------------------------------------------
# Local model runtime (llama.cpp server, OpenAI compatible).
# ---------------------------------------------------------------------------
MODEL_PATH: Path = Path(
    os.path.expanduser(
        os.getenv(
            "XCALIBUR_MODEL",
            str(TARGET_ROOT / "models" / "gemma-agent-coding-Q4_K_M.gguf"),
        )
    )
).resolve()

LLAMA_SERVER_BIN: str = os.getenv("XCALIBUR_LLAMA_BIN", "llama-server")
LLAMA_HOST: str = os.getenv("XCALIBUR_HOST", "127.0.0.1")
LLAMA_PORT: int = int(os.getenv("XCALIBUR_PORT", "8080"))
LLAMA_CTX: int = int(os.getenv("XCALIBUR_CTX", "32768"))
LLAMA_GPU_LAYERS: int = int(os.getenv("XCALIBUR_NGL", "99"))
LLAMA_THREADS: int = int(os.getenv("XCALIBUR_THREADS", str(max(1, (os.cpu_count() or 4) - 2))))
LLAMA_PARALLEL: int = int(os.getenv("XCALIBUR_PARALLEL", "1"))
LLAMA_STARTUP_TIMEOUT: int = int(os.getenv("XCALIBUR_STARTUP_TIMEOUT", "180"))

# Generation defaults.
REQUEST_TIMEOUT: int = int(os.getenv("XCALIBUR_REQUEST_TIMEOUT", "600"))
MAX_TOOL_ITERATIONS: int = int(os.getenv("XCALIBUR_MAX_TOOL_ITERS", "24"))
GENERATION_TEMPERATURE: float = float(os.getenv("XCALIBUR_TEMPERATURE", "0.2"))
GENERATION_MAX_TOKENS: int = int(os.getenv("XCALIBUR_MAX_TOKENS", "4096"))
# The interactive first pass must fit an entire new file inside one tool call,
# so it gets a larger budget than the follow-up turns that summarise.
FIRST_PASS_MAX_TOKENS: int = int(os.getenv("XCALIBUR_FIRST_PASS_TOKENS", "2048"))
# A single generous retry budget. The first attempt is kept tight so a
# compliant answer returns in seconds; when the model over-produces and the
# call is cut off, the retry gets enough room to finish the smaller file the
# corrective hint asks for.
RETRY_MAX_TOKENS: int = int(os.getenv("XCALIBUR_RETRY_TOKENS", "8192"))
# Hard ceiling on an interactive first pass or retry. The local model runs near
# 27 tokens/sec, so 420s covers the retry budget without hanging.
FIRST_PASS_TIMEOUT: int = int(os.getenv("XCALIBUR_FIRST_PASS_TIMEOUT", "420"))

# ---------------------------------------------------------------------------
# Context safety. file_chunker keeps every prompt under this budget.
# ---------------------------------------------------------------------------
MAX_FILE_BYTES: int = int(os.getenv("XCALIBUR_MAX_FILE_BYTES", str(2 * 1024 * 1024)))
CONTEXT_CHAR_BUDGET: int = int(os.getenv("XCALIBUR_CONTEXT_CHARS", str(24000)))
CHUNK_MAX_CHARS: int = int(os.getenv("XCALIBUR_CHUNK_CHARS", "6000"))
CHUNK_OVERLAP_LINES: int = int(os.getenv("XCALIBUR_CHUNK_OVERLAP", "20"))

# ---------------------------------------------------------------------------
# Network / dashboard.
# ---------------------------------------------------------------------------
DASHBOARD_HOST: str = os.getenv("XCALIBUR_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT: int = int(os.getenv("XCALIBUR_DASHBOARD_PORT", "8420"))
DASHBOARD_URL: str = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"

# ---------------------------------------------------------------------------
# Safety limits enforced by the command guard.
# ---------------------------------------------------------------------------
COMMAND_TIMEOUT: int = int(os.getenv("XCALIBUR_COMMAND_TIMEOUT", "120"))
COMMAND_OUTPUT_LIMIT: int = int(os.getenv("XCALIBUR_COMMAND_OUTPUT", "16000"))

MAX_ROWS: int = 2000  # file listing cap so a huge tree cannot flood the prompt

# ---------------------------------------------------------------------------
# Palette - the exact split-complementary set used by desktop/renderer/styles.css
# so the terminal, the dashboard, and the desktop app read as one product.
# ---------------------------------------------------------------------------
PALETTE: dict[str, str] = {
    "tint": "#f0f9ff",
    "tint_deep": "#e2f3fd",
    "ink": "#000000",
    "paper": "#ffffff",
    "transform": "#ff0062",
    "transform_deep": "#a60040",
    "harmonious": "#00ff1e",
    "communicative": "#00e1ff",
}

# Ignored while walking the target tree.
IGNORE_DIRS: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "venv_q_mdl_tester",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".next", ".cache",
    "XcaliburLiteMCP",
}

TEXT_SUFFIXES: set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".sh", ".bash", ".zsh", ".css", ".scss", ".html", ".htm",
    ".php", ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".rb",
    ".ini", ".cfg", ".conf", ".env", ".xml", ".svg",
}
