#!/usr/bin/env bash
#
# Runs the local benchmark: every weight file in models/, one run each, then the
# table that reads them together.
#
# The study compares one local model against a hosted reference. This script
# answers the other question a reader of that study asks next: of the local
# models this machine can actually hold, which one is worth the memory. It does
# that by running the same task suite, with the same controls, against each
# weight file in turn and leaving one run directory per model behind.
#
# The engine serves one model at a time, so the models are measured in sequence
# rather than side by side. That is a property of the runtime and not a choice:
# four models at two to three gigabytes each do not fit beside each other in the
# memory ceiling this project declares, and a benchmark that had to raise the
# ceiling to run would no longer be a measurement of the device the study is
# about. Each model therefore costs one engine restart, and the swap is the only
# thing this script does that the study's own runner does not.
#
# The model set is read from `php benchmark.php --list --tsv` rather than listed
# here. A second copy of a directory listing is a second copy that goes stale,
# and the failure it causes is silent: a model added to the directory would
# simply be missing from the table.
#
# Usage:
#   ./benchmark-models.sh                        every model, the whole suite
#   ./benchmark-models.sh --suite core           one suite
#   ./benchmark-models.sh --models phi,llama     named models only
#   ./benchmark-models.sh --quick                two tasks per model
#   ./benchmark-models.sh --dry-run              print the plan and stop
#
# The script is idempotent: it restores the engine to the model the runtime is
# pointed at by default, which is the state it found the machine in.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

COMPOSE=(docker compose -f docker/docker-compose.yml)
ENGINE_CONTAINER="gemma-agent-server"
OUT_DIR="$SCRIPT_DIR/results/benchmark"
SUITE="all"
MODELS="all"
TASKS=""
QUICK=0
DRY_RUN=0
DO_COMPARE=1
PHP_BIN="$(command -v php || true)"

while [ $# -gt 0 ]; do
  case "$1" in
    --suite) shift; SUITE="$1" ;;
    --suite=*) SUITE="${1#--suite=}" ;;
    --models) shift; MODELS="$1" ;;
    --models=*) MODELS="${1#--models=}" ;;
    --tasks) shift; TASKS="$1" ;;
    --tasks=*) TASKS="${1#--tasks=}" ;;
    --out) shift; OUT_DIR="$1" ;;
    --out=*) OUT_DIR="${1#--out=}" ;;
    --quick) QUICK=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-compare) DO_COMPARE=0 ;;
    --help)
      sed -n '/^# Usage:/,/^# The script is idempotent/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

note() { printf '%s\n' "$*"; }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    note "missing command: $1"
    exit 3
  fi
}

need docker
if [ -z "$PHP_BIN" ]; then
  note "php is not available, so the model set cannot be read from benchmark.php"
  exit 3
fi

mkdir -p "$OUT_DIR" || exit 4

# The model set, one row per model: label, file, path, compose source, bytes.
MODEL_TSV="$("$PHP_BIN" "$SCRIPT_DIR/benchmark.php" --list --tsv --models "$MODELS")"
if [ $? -ne 0 ] || [ -z "$MODEL_TSV" ]; then
  note "no local model matched --models $MODELS"
  exit 5
fi

TOTAL=0
while IFS= read -r _line; do
  [ -n "$_line" ] && TOTAL=$((TOTAL + 1))
done <<< "$MODEL_TSV"

if [ "$TOTAL" -eq 0 ]; then
  note "no local model was found; put a .gguf file in models/ and run this again"
  exit 5
fi

# The request the harness container runs. It is built once so the plan a dry run
# prints is the command that would actually run.
REQUEST_ARGS=(--no-deepseek --suite "$SUITE" --out-dir /opt/harness/results/benchmark)
if [ -n "$TASKS" ]; then
  IFS=',' read -r -a TASK_LIST <<< "$TASKS"
  for task in "${TASK_LIST[@]}"; do
    REQUEST_ARGS+=(--task "$task")
  done
fi
if [ "$QUICK" -eq 1 ]; then
  REQUEST_ARGS+=(--limit 2)
fi

note "model benchmark: $TOTAL model(s), suite $SUITE, evidence in $OUT_DIR"

engine_healthy() {
  local status
  status="$(docker inspect --format '{{.State.Health.Status}}' "$ENGINE_CONTAINER" 2>/dev/null || echo missing)"
  [ "$status" = "healthy" ]
}

# Waiting is bounded and its outcome is reported, because the run behind it is
# the whole cost of the tier: an engine that never became healthy must not be
# followed by four model runs that each fail for the same unrecorded reason.
wait_healthy() {
  local waited=0
  while [ "$waited" -lt 300 ]; do
    if engine_healthy; then
      note "engine healthy after ${waited}s"
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  note "the engine did not become healthy within 300s"
  return 1
}

restore_engine() {
  note "restoring the engine to the default model"
  "${COMPOSE[@]}" up -d --force-recreate gemma >/dev/null 2>&1 || true
  wait_healthy || note "the engine was left as it was found; check it by hand"
}

FAILURES=0
INDEX=0
while IFS=$'\t' read -r label file path source bytes; do
  INDEX=$((INDEX + 1))
  [ -z "${label:-}" ] && continue

  note ""
  note "[$INDEX/$TOTAL] $label"
  note "  file    $file"
  note "  source  $source"

  if [ "$DRY_RUN" -eq 1 ]; then
    note "  would run: GEMMA_MODEL_FILE=$source ${COMPOSE[*]} up -d --force-recreate gemma"
    note "  would run: ${COMPOSE[*]} run --rm -T -e GEMMA_LABEL=$label agent ${REQUEST_ARGS[*]} --run-id $label"
    continue
  fi

  # The engine is recreated rather than restarted, because the weight file is a
  # bind mount: a restart would keep the mount, and a benchmark that restarted
  # instead of recreating would measure the same model four times and report it
  # under four names.
  if ! GEMMA_MODEL_FILE="$source" "${COMPOSE[@]}" up -d --force-recreate gemma >/dev/null 2>&1; then
    note "  the engine could not be recreated for this model"
    FAILURES=$((FAILURES + 1))
    continue
  fi
  if ! wait_healthy; then
    note "  skipping $label: the engine never served this file"
    FAILURES=$((FAILURES + 1))
    continue
  fi

  # The label is passed in rather than derived from a path inside the container.
  # The harness derives it from GEMMA_GGUF when nothing says otherwise, and the
  # container's models directory is the read only repository mount, whose path is
  # an implementation detail of the mount rather than a name a document should
  # carry.
  if "${COMPOSE[@]}" run --rm -T -e "GEMMA_LABEL=$label" agent "${REQUEST_ARGS[@]}" \
      --run-id "$label" >"$OUT_DIR/$label.log" 2>&1; then
    note "  run complete: $OUT_DIR/$label.log"
  else
    note "  the run reported a failure; its output is in $OUT_DIR/$label.log"
    FAILURES=$((FAILURES + 1))
  fi
done <<< "$MODEL_TSV"

if [ "$DRY_RUN" -eq 1 ]; then
  note ""
  note "dry run: nothing was started and nothing was written"
  exit 0
fi

restore_engine

if [ "$DO_COMPARE" -eq 1 ]; then
  note ""
  note "building the comparison"
  if "$PHP_BIN" "$SCRIPT_DIR/benchmark.php" --compare --dir "$OUT_DIR" --out "$OUT_DIR"; then
    note "comparison written to $OUT_DIR/comparison.txt"
  else
    COMPARE_STATUS=$?
    if [ "$COMPARE_STATUS" -eq 5 ]; then
      note "the runs did not measure the same task set; the table was still written"
    else
      note "the comparison could not be built (exit $COMPARE_STATUS)"
      FAILURES=$((FAILURES + 1))
    fi
  fi
fi

note ""
if [ "$FAILURES" -eq 0 ]; then
  note "benchmark complete: $TOTAL model(s), no failures"
  exit 0
fi

note "benchmark complete with $FAILURES failure(s); read the logs in $OUT_DIR"
exit 1
