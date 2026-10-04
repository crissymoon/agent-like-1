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


def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean environment flag. Anything but a false word is true."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# The workspace. One folder, one root: the navigator is confined to it, the
# agent reads it, and every proposed edit is staged against it. The default is
# the working folder beside this package, not the repository that contains the
# package, so the terminal opens on the working folder rather than on whatever
# happens to sit next to the harness. Point it at a project with
# XCALIBUR_WORKSPACE; XCALIBUR_TARGET_ROOT is accepted as the older name.
# ---------------------------------------------------------------------------
ROOT: Path = Path(__file__).resolve().parent

# The repository the harness lives in. Fixtures such as the model weights are
# found relative to this and never relative to the workspace, so pointing the
# workspace at a project cannot move them.
HARNESS_REPO: Path = ROOT.parent

WORKSPACE: Path = Path(
    os.getenv("XCALIBUR_WORKSPACE")
    or os.getenv("XCALIBUR_TARGET_ROOT")
    or str(ROOT / "workspace")
).resolve()

# The tree the agent operates on is the workspace itself. Two names for one
# path means a file the navigator green-lights is a file the agent can read and
# stage an edit for; the confinement boundary and the edit boundary cannot
# drift apart.
TARGET_ROOT: Path = WORKSPACE

# The harness keeps its own bookkeeping in a hidden folder inside the workspace:
# job records, pristine snapshots, timestamped shadow copies, and the throwaway
# sandboxes used by "test". Hidden and excluded from every walk, so a listing
# shows the user's code and staging can never read its own output back in.
HARNESS_DIR: Path = WORKSPACE / ".xcalibur"
ORIGINAL_COPY: Path = HARNESS_DIR / "original_copy"   # pristine snapshot of touched files
SHADOW_COPY: Path = HARNESS_DIR / "shadow_copy"       # timestamped proposed edits
JOBS_DIR: Path = HARNESS_DIR / "jobs"                 # persisted dashboard job records
TESTS_DIR: Path = HARNESS_DIR / "tests"               # throwaway env used by "test"
LOG_PATH: Path = HARNESS_DIR / "llama-server.log"     # llama-server stdout and stderr

# ---------------------------------------------------------------------------
# Identity. One name and one version, so the terminal, the splash, the
# dashboard, and the MCP handshake all report the same pair.
# ---------------------------------------------------------------------------
APP_NAME: str = os.getenv("XCALIBUR_APP_NAME", "XcaliburLite")
VERSION: str = os.getenv("XCALIBUR_VERSION", "1.1.0")

# ---------------------------------------------------------------------------
# Startup splash. The art file keeps a version placeholder so the banner and
# the version can never drift apart; the token below is what gets replaced.
# ---------------------------------------------------------------------------
SPLASH_FILE: Path = Path(
    os.path.expanduser(os.getenv("XCALIBUR_SPLASH_FILE", str(ROOT / "on-load.txt")))
).resolve()
SPLASH_ENABLED: bool = _flag("XCALIBUR_SPLASH", True)
SPLASH_FPS: int = int(os.getenv("XCALIBUR_SPLASH_FPS", "30"))
# Seconds for the reveal to travel the full height of the art.
SPLASH_REVEAL_SECONDS: float = float(os.getenv("XCALIBUR_SPLASH_REVEAL", "1.2"))
# Pause on the completed frame before the prompt takes over.
SPLASH_HOLD_SECONDS: float = float(os.getenv("XCALIBUR_SPLASH_HOLD", "0.4"))
VERSION_TOKEN: str = os.getenv("XCALIBUR_VERSION_TOKEN", "-{$the_version}-")

# ---------------------------------------------------------------------------
# Local model runtime (llama.cpp server, OpenAI compatible).
# ---------------------------------------------------------------------------
MODEL_PATH: Path = Path(
    os.path.expanduser(
        os.getenv(
            "XCALIBUR_MODEL",
            str(HARNESS_REPO / "models" / "gemma-agent-coding-Q4_K_M.gguf"),
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
# The first pass is kept tight so a compliant answer returns in seconds.
FIRST_PASS_MAX_TOKENS: int = int(os.getenv("XCALIBUR_FIRST_PASS_TOKENS", "2048"))
# A single generous retry budget: when the model over-produces and the call is
# cut off, the retry gets enough room to finish the smaller file the corrective
# hint asks for.
RETRY_MAX_TOKENS: int = int(os.getenv("XCALIBUR_RETRY_TOKENS", "8192"))
# Any pass after the first can also be asked to write a whole file - a second
# file opened in the same turn, or a retry the model chose to make. It gets the
# retry budget rather than the smaller summarise budget, so a follow-up write is
# never cut off for lack of room. A budget smaller than one file is what left a
# second file unwritten while the terminal still looked like it had finished.
WRITE_MAX_TOKENS: int = int(os.getenv("XCALIBUR_WRITE_TOKENS", str(RETRY_MAX_TOKENS)))
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
# Interpretation, navigation, and agent transparency.
# ---------------------------------------------------------------------------
# The tree the terminal navigator is confined to. It cannot walk above this and
# cannot cd outside it. It defaults to the workspace and a value outside the
# workspace is refused rather than honoured, so confinement always holds; a
# folder inside the workspace is accepted to narrow the view.
def _confined(value: str) -> Path:
    candidate = Path(os.path.expanduser(value)).resolve()
    if candidate == WORKSPACE or WORKSPACE in candidate.parents:
        return candidate
    return WORKSPACE


_nav_root = os.getenv("XCALIBUR_NAV_ROOT", "").strip()
NAV_ROOT: Path = _confined(_nav_root) if _nav_root else WORKSPACE
# Closest-neighbour resolution: below this score a query is treated as unmatched
# so a typo is not silently mapped onto the wrong file.
FUZZY_MIN_SCORE: float = float(os.getenv("XCALIBUR_FUZZY_MIN", "0.55"))
CAT_MAX_LINES: int = int(os.getenv("XCALIBUR_CAT_LINES", "400"))
# Show the model's reasoning and each action as it happens, and stream tokens.
SHOW_THINKING: bool = _flag("XCALIBUR_SHOW_THINKING", True)
STREAM_OUTPUT: bool = _flag("XCALIBUR_STREAM", True)
# Minimal-code bias: a proposed file above this line count draws a corrective
# nudge asking for the smallest version that fully works.
MINIMAL_CODE_LINES: int = int(os.getenv("XCALIBUR_MINIMAL_LINES", "40"))

# ---------------------------------------------------------------------------
# Direct chat (/chat). A plain conversation with the model: no tool is offered,
# no edit is staged, and the answer is prose. The budget is the ordinary
# generation budget, not the first-pass one, because there is no tool call to
# keep short here and a reply is allowed to be as long as it needs to be.
# ---------------------------------------------------------------------------
CHAT_MAX_TOKENS: int = int(os.getenv("XCALIBUR_CHAT_TOKENS", str(GENERATION_MAX_TOKENS)))
# Exchanges kept in the chat history. A long sitting would otherwise fill the
# window and start failing; the oldest exchanges are dropped first.
CHAT_HISTORY_TURNS: int = int(os.getenv("XCALIBUR_CHAT_TURNS", "40"))
# Where a transcript is written when the platform offers no clipboard tool.
CHAT_COPY_DIR: Path = Path(
    os.path.expanduser(os.getenv("XCALIBUR_CHAT_COPY_DIR", str(HARNESS_DIR / "chat")))
).resolve()
# Ask whether to copy the transcript when the chat ends. A scripted or piped
# session turns this off so leaving chat mode never waits on a question.
CHAT_COPY_PROMPT: bool = _flag("XCALIBUR_CHAT_COPY_PROMPT", True)

# ---------------------------------------------------------------------------
# Linting and surgical repair. A linter feeds the model a precise defect list
# so it repairs the offending lines instead of rewriting the file.
# ---------------------------------------------------------------------------
LINT_ON_STAGE: bool = _flag("XCALIBUR_LINT", True)
LINT_TIMEOUT: int = int(os.getenv("XCALIBUR_LINT_TIMEOUT", "60"))
REPAIR_MAX_ATTEMPTS: int = int(os.getenv("XCALIBUR_REPAIR_ATTEMPTS", "2"))

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

# Ignored while walking the workspace. The harness store is named here as well
# as hidden, so no walk, glob or listing can surface the staging area.
IGNORE_DIRS: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "venv_q_mdl_tester",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".next", ".cache",
    HARNESS_DIR.name,
}

TEXT_SUFFIXES: set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".sh", ".bash", ".zsh", ".css", ".scss", ".html", ".htm",
    ".php", ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".rb",
    ".ini", ".cfg", ".conf", ".env", ".xml", ".svg",
}
