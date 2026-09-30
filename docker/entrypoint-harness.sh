#!/bin/sh
# The staged ladder, run inside the harness container.
#
# The ladder the study recommends has three rungs and this script runs the first
# two of them in order, in the container, with every step's evidence left on
# disk:
#
#   1. the two controls, against nothing but the harness's own files
#      (`agent.php --self-check`), which is also where the containment boundary
#      is proved in both directions;
#   2. the decoder probe, which is the only question the harness cannot answer
#      from a run: does this engine honour the constraint field it was sent. A
#      constraint that was ignored looks exactly like one that was never needed,
#      so the answer is recorded rather than assumed, and an engine that refused
#      the field is noted and the run continues so the record still exists;
#   3. the same six tasks unchanged, under the guarded condition, on the local
#      model alone, because the hosted reference run is already recorded;
#   4. the per capability comparison of that run against the recorded baseline,
#      which exits non-zero if the two runs are not the same suite.
#
# Nothing here writes outside the results directory. The third rung of the
# ladder, the supervised pass, is deliberately not in this script: it is
# conditional on the second rung's reading and it would run somewhere else.

set -eu

log() {
    printf '[ladder] %s\n' "$1" >&2
}

cd /opt/harness

BASE="${LADDER_BASE:-results/agent/baseline}"
OUT_DIR="${LADDER_OUT_DIR:-results/agent}"
RUN_ID="${LADDER_RUN_ID:-guarded}"
EVIDENCE="${LADDER_EVIDENCE:-results/agent/${RUN_ID}-self-check.log}"
TRIAL="${OUT_DIR}/${RUN_ID}"

log "guard=${AGENT_GUARD:-off} strict_schema=${AGENT_STRICT_SCHEMA:-off} decoder=${AGENT_DECODER:-none} scope=${AGENT_DECODER_SCOPE:-local} sandbox=${AGENT_SANDBOX_POLICY:-documented}"
log "boundary=${HARNESS_BOUNDARY:-auto} (require fails a run that cannot read its own walls)"
log "engine=${GEMMA_SERVER_URL:-unset} base=${BASE} run=${TRIAL}"

mkdir -p "$(dirname "$EVIDENCE")"

# Rung 1a: the controls and the boundary, against the harness's own files.
log "self-check: the controls and the containment boundary"
if ! php agent.php --self-check > "$EVIDENCE" 2>&1; then
    sed 's/^/  /' "$EVIDENCE" >&2
    log "the self-check failed, so a run would measure code that is not proven; stopping"
    exit 1
fi
tail -n 1 "$EVIDENCE" | sed 's/^/  /' >&2
log "wrote $EVIDENCE"

# Rung 1b: whether the engine honoured the constraint field.
#
# The probe is given the same decoder the run will be given. Without this the
# probe sends an unconstrained request, and an unconstrained request that
# happens to answer with a declared object would certify the engine for a
# constraint it never applied, which is the one error the probe exists to
# prevent.
if [ "${AGENT_DECODER:-none}" != "none" ]; then
    log "decoder probe: does the engine honour the field it was sent"
    probe_status=0
    php agent.php --decoder-probe \
        --gemma-url "${GEMMA_SERVER_URL:-http://gemma:8080}" \
        --decoder="${AGENT_DECODER:-none}" \
        --decoder-scope="${AGENT_DECODER_SCOPE:-local}" \
        --decoder-field="${AGENT_DECODER_FIELD:-grammar}" \
        >> "$EVIDENCE" 2>&1 || probe_status=$?
    tail -n 4 "$EVIDENCE" | sed 's/^/  /' >&2
    if [ "$probe_status" -ne 0 ]; then
        log "the engine did not answer with a declared object, so this run is recorded as unconstrained at the engine and constrained at the harness"
    fi
fi

# Rung 2: the same six tasks, unchanged, under the guarded condition.
log "running the six tasks: guard=${AGENT_GUARD:-off} strict_schema=${AGENT_STRICT_SCHEMA:-off}"
php agent.php \
    --run-id "$RUN_ID" \
    --out-dir "$OUT_DIR" \
    --gemma-url "${GEMMA_SERVER_URL:-http://gemma:8080}" \
    --guard="${AGENT_GUARD:-off}" \
    --guard-repeat-limit="${AGENT_GUARD_REPEAT_LIMIT:-2}" \
    --strict-schema="${AGENT_STRICT_SCHEMA:-off}" \
    --decoder="${AGENT_DECODER:-none}" \
    --decoder-scope="${AGENT_DECODER_SCOPE:-local}" \
    --decoder-field="${AGENT_DECODER_FIELD:-grammar}" \
    --sandbox="${AGENT_SANDBOX_POLICY:-documented}" \
    --no-deepseek

# Rung 2 read per capability, against the recorded run.
log "comparing per capability"
php ladder.php --base "$BASE" --trial "$TRIAL"

log "done: $TRIAL"
