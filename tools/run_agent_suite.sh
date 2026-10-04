#!/usr/bin/env bash
#
# Measure one gguf through the harness's own agent suite, reproducibly.
#
# Why this is a script and not four commands typed twice. The comparison the
# paper needs is one run per model over the same six tasks, judged by the same
# verifiers, with the same step budget, and the only thing that may differ
# between the runs is the weights. A hand-typed command line is where that stops
# being true, and this project has already recorded two runs whose labels and
# whose recorded model ids disagreed because a flag was typed once and not
# again.
#
# So: the server is started here, the suite is run here, the server is stopped
# here, and the run's label, the endpoint it was posted to and the digest of the
# action schema all end up in the run's own json rather than in a shell history.
#
# Two spellings of the endpoint are accepted on purpose. `--gemma-url` is
# documented as a base url and every recorded run passes the completions
# endpoint, and the two must mean one address; that is the defect
# `EngineProfile::rootOf()` repairs. Passing one spelling for one model and the
# other for the next is therefore a free end-to-end check of the repair, and a
# run that still produced nothing would show up as a transport error in the
# run's own task rows rather than as a plausible low score.
#
# Usage:
#   tools/run_agent_suite.sh --model models/x.gguf --label gap-q4 \
#       [--run-id ID] [--out-dir DIR] [--url URL] [--port N] \
#       [--suite core|levels|all] [--projector PATH] [--deepseek] [--keep-server]
#
#   --deepseek   also re-measure the hosted reference in this run (costs api
#                calls; the recorded reference is reused by default)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

MODEL=""
LABEL=""
RUN_ID=""
OUT_DIR=""
URL=""
PORT="8081"
SUITE="core"
PROJECTOR="$ROOT/models/mmproj-F16.gguf"
DEEPSEEK=0
KEEP_SERVER=0
SERVER_LOG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --suite) SUITE="$2"; shift 2 ;;
    --projector) PROJECTOR="$2"; shift 2 ;;
    --no-projector) PROJECTOR=""; shift ;;
    --deepseek) DEEPSEEK=1; shift ;;
    --keep-server) KEEP_SERVER=1; shift ;;
    --help|-h) sed -n '2,40p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [ -z "$MODEL" ] || [ -z "$LABEL" ]; then
  printf 'error: --model and --label are required\n' >&2
  exit 2
fi
if [ ! -f "$MODEL" ]; then
  printf 'error: %s is not a file\n' "$MODEL" >&2
  exit 2
fi
[ -n "$RUN_ID" ] || RUN_ID="$LABEL"
# `agent.php` writes into `<out-dir>/<run-id>/`, so the base directory and the run
# identifier are two different things and the run's own directory is their join.
# Passing the run's directory as the base is what produced
# `results/agent/gap-q4/gap-q4/`, a manifest one level below where `ladder.php`
# was told to look, which reports as a run that has no manifest rather than as a
# directory that was named twice.
[ -n "$OUT_DIR" ] || OUT_DIR="$ROOT/results/agent"
RUN_DIR="$OUT_DIR/$RUN_ID"
# The documented spelling for a local server is the completions endpoint; the
# root spelling is what the other run uses so that both are exercised.
[ -n "$URL" ] || URL="http://127.0.0.1:$PORT/v1/chat/completions"

if ! command -v llama-server >/dev/null 2>&1; then
  printf 'error: llama-server is not on PATH\n' >&2
  exit 2
fi
if ! command -v php >/dev/null 2>&1; then
  printf 'error: php is not on PATH\n' >&2
  exit 2
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  printf 'error: port %s is already listening; stop it or pass --port\n' "$PORT" >&2
  exit 2
fi

mkdir -p "$RUN_DIR"
SERVER_LOG="$RUN_DIR/serve.log"

PROJECTOR_ARGS=()
[ -n "$PROJECTOR" ] && [ -f "$PROJECTOR" ] && PROJECTOR_ARGS=(--mmproj "$PROJECTOR")

printf '== serving ==\n'
printf 'model      : %s\n' "$MODEL"
printf 'projector  : %s\n' "${PROJECTOR:-none}"
printf 'port       : %s\n' "$PORT"
printf 'serve log  : %s\n' "$SERVER_LOG"

# `-ngl 999` is not cosmetic on this machine. Without it llama.cpp runs the
# whole stack on the cpu, which turns a suite of six tasks from minutes into
# tens of minutes and, more importantly, makes the recorded latency a statement
# about the default rather than about the machine the other rows were taken on.
nohup llama-server \
  -m "$MODEL" \
  "${PROJECTOR_ARGS[@]}" \
  --port "$PORT" \
  --jinja \
  --ctx-size 16384 \
  -ngl 999 \
  >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
  if [ "$KEEP_SERVER" -eq 0 ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    printf '\n== server stopped (pid %s) ==\n' "$SERVER_PID"
  fi
}
trap cleanup EXIT

# Readiness is the endpoint's own /health at the root, which is the whole point
# of the url rule: `/health` under the completions surface answers 404 on a
# healthy server. The wait is bounded because a model that fails to load leaves
# a process alive and a port unbound, and a wait with no bound would hang here
# rather than in the place that can say what went wrong.
HEALTH="$(php -r '
  require $argv[1] . "/lib/EngineProfile.php";
  echo EngineProfile::rootOf($argv[2]) . "/health";
' "$ROOT" "$URL" 2>/dev/null)"
[ -n "$HEALTH" ] || HEALTH="http://127.0.0.1:$PORT/health"
printf 'health     : %s\n' "$HEALTH"

READY=0
for _ in $(seq 1 120); do
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    if curl -sf -o /dev/null "$HEALTH"; then READY=1; break; fi
  else
    printf 'error: the server exited before it became ready; last lines:\n' >&2
    tail -20 "$SERVER_LOG" >&2
    exit 1
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  printf 'error: the server did not answer %s within 120s; last lines:\n' "$HEALTH" >&2
  tail -20 "$SERVER_LOG" >&2
  exit 1
fi
printf 'ready\n\n'

printf '== the suite ==\n'
AGENT_ARGS=(
  "$ROOT/agent.php"
  --out-dir "$OUT_DIR"
  --run-id "$RUN_ID"
  --gemma-url "$URL"
  --tool-mode prompt
  --suite "$SUITE"
)
[ "$DEEPSEEK" -eq 0 ] && AGENT_ARGS+=(--no-deepseek)

# The run records the local model's name from `GEMMA_LABEL`, which defaults to the
# stem of `GEMMA_GGUF_PATH`, and `config.php` compiles the base model's path in.
# Without these two the suite served the post-trained weights and wrote down
# `gemma-4-E2B-it-Q4_K_M`, which is the model the post-training started from: a
# reader comparing rows would have found the base model's name sitting on the
# best score in the table, which is the most damaging way for a label to be
# wrong. Both are set here so the recorded name is the model that answered.
export GEMMA_GGUF="$MODEL"
export GEMMA_LABEL="$LABEL"
printf 'label      : %s\n\n' "$GEMMA_LABEL"

php "${AGENT_ARGS[@]}"
STATUS=$?
if [ "$STATUS" -ne 0 ]; then
  printf 'error: agent.php exited %s\n' "$STATUS" >&2
  exit "$STATUS"
fi

printf '\n== the gap to the hosted reference ==\n'
# `post-train-gap.json` is the document the study cites, and it is written only
# when the run measured both the local model and the reference. `ladder.php` is
# deliberately not used here: it compares the same model name across two runs,
# which is the tool for asking whether the harness moved, not whether the model
# did. The baseline's own gap is printed beside this run's because the number
# that matters is the difference between the two gaps, and printing one without
# the other is how a 14.58 turns into a 5.39 with nothing to compare it to.
python3 - "$RUN_DIR" "$ROOT/results/agent/baseline" "$LABEL" <<'PY'
import json, sys
from pathlib import Path

run_dir, base_dir, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]


def gap(directory: Path) -> tuple[float, float, float, str]:
    path = directory / "post-train-gap.json"
    if not path.is_file():
        return (0.0, 0.0, 0.0, "not measured in this run")
    document = json.loads(path.read_text(encoding="utf-8"))
    composite = document["composite"]
    return (composite["local"], composite["reference"], composite["gap"], composite["distance"])


def subject_of(directory: Path) -> str | None:
    path = directory / "post-train-gap.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("subject")


run = None
subject = subject_of(run_dir)


for path in sorted(run_dir.glob("*.json")):
    if path.name in ("comparison.json", "manifest.json", "ladder.json"):
        continue
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("document") != "agent-model-run":
        continue
    # A run that measured the reference beside the local model holds both, and the
    # two are told apart by the gap document's own subject. Picking the first file
    # in alphabetical order picks `deepseek-flash.json`, which reports the
    # reference's composite as though it were this run's and is the exact
    # confusion the label column exists to prevent.
    label = document.get("model", {}).get("label")
    if subject is None or label == subject:
        run = document
        if label == subject:
            break
else:
    print("no model run json was written")
    raise SystemExit(0)

local, reference, gap_value, distance = gap(run_dir)

aggregate = run["aggregate"]
print(f"{label:10s} recorded as {run['model']['label']}  "
      f"composite {aggregate['composite']:6.2f}  "
      f"passed {aggregate['tasks_passed']}/{aggregate['tasks']}  "
      f"tool calls {aggregate['counters']['tool_calls']}  "
      f"invalid actions {aggregate['counters']['invalid_actions']}  "
      f"mean latency {aggregate.get('latency_ms_mean', 0):.0f} ms")
print()
print(f"{'run':<24s} {'local':>8s} {'reference':>10s} {'gap':>8s}  distance")
base_local, base_reference, base_gap, base_distance = gap(base_dir)
rows = [
    (f"baseline {base_dir.name}", base_local, base_reference, base_gap, base_distance),
    (f"this run ({label})", local, reference, gap_value, distance),
]
for name, local_value, reference_value, gap_number, note in rows:
    print(f"{name:<24s} {local_value:8.2f} {reference_value:10.2f} {gap_number:8.2f}  {note}")

if base_gap > 0 and gap_value > 0:
    closed = base_gap - gap_value
    share = 100.0 * closed / base_gap
    print()
    print(f"closed {closed:.2f} of {base_gap:.2f} points, {share:.1f} per cent of the gap to the reference")

for name, task in run["tasks"].items():
    print(f"  {name:24s} {task.get('composite', 0):7.2f}")
PY
exit 0
