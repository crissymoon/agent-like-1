#!/bin/sh
# Entrypoint for the local agent runtime.
#
# It exists so that a misconfigured container fails immediately and loudly with
# a specific message, instead of starting a server that answers health checks
# while loading no weights. Every check below is a condition that would
# otherwise surface as a confusing error several layers deeper.
#
# It also decides three properties of the running engine that a run's numbers
# depend on and that the harness cannot see over HTTP:
#
#   vision    whether the multimodal projector is loaded at all. The agent study
#             is text only and the projector is 985,654,080 bytes.
#   load mode how the weights are brought in, and whether they end up as a file
#             backed mapping or an anonymous allocation.
#   kv cache  the per sequence context, the slot count and the element types of
#             the key and value caches.
#
# The reading itself is not here. `engine-record.sh` reads the engine and writes
# the record, because a start that fails must stop and a reading that fails must
# not: those are two different behaviours and they are kept in two files.
#
#   VISION=off|on       load the projector or do not. Default off.
#   LOAD_MODE=MODE      how the weights are brought in. Default mmap, which
#                       means they are a file backed mapping. `none` is the mode
#                       that does not map; the rest are auto, mlock, mmap+mlock
#                       and dio. The value is checked against the modes this
#                       engine declares before the server is started, because
#                       the `--no-mmap` flag an older build accepted is refused
#                       by the pinned one with `invalid argument: --no-mmap`.
#   KV_TYPE_K=NAME      key cache element type, f16 by default.
#   KV_TYPE_V=NAME      value cache element type, f16 by default.
#   MODEL_HASH=off|on   hash the weight file into the record. Off by default
#                       because it reads 3.1 GB before the server starts.

set -eu

log() {
    printf '[entrypoint] %s\n' "$1" >&2
}

require_file() {
    if [ ! -r "$1" ]; then
        log "required file is missing or unreadable: $1"
        return 1
    fi
    if [ ! -s "$1" ]; then
        log "required file is empty: $1"
        return 1
    fi
    return 0
}

onoff() {
    case "$(printf '%s' "$2" | tr 'A-Z' 'a-z')" in
        on|1|true|yes) printf '%s' on ;;
        off|0|false|no) printf '%s' off ;;
        *) log "$1 must be on or off, not '$2'"; return 1 ;;
    esac
}

# ------------------------------------------------------------------ settings

VISION="$(onoff VISION "${VISION:-off}")" || exit 78
MODEL_HASH="$(onoff MODEL_HASH "${MODEL_HASH:-off}")" || exit 78
LOAD_MODE="${LOAD_MODE:-mmap}"
KV_TYPE_K="${KV_TYPE_K:-f16}"
KV_TYPE_V="${KV_TYPE_V:-f16}"
RECORD_DIR="${RECORD_DIR:-/records}"
# The image reference the container was started from, which carries the digest
# the engine is pinned to. Only the digest is kept, because a tag beside it
# would be read as the authority when the digest is what was run.
ENGINE_IMAGE="${ENGINE_IMAGE:-unknown}"

MMPROJ_PATH="${MMPROJ_PATH:-}"
waited=0

export VISION MODEL_HASH LOAD_MODE KV_TYPE_K KV_TYPE_V RECORD_DIR ENGINE_IMAGE
export MODEL_PATH MMPROJ_PATH MODEL_SOURCE MMPROJ_SOURCE CTX_SIZE THREADS GPU_LAYERS PARALLEL HOST PORT
export MODEL_BYTES PROJ_BYTES MODEL_SHA256 MODEL_HASHED LOAD_MODES
export SERVER_PID WAITED KEEP_LOG SERVER_LOG
MODEL_BYTES=0
PROJ_BYTES=0
MODEL_SHA256=""
MODEL_HASHED=false
LOAD_MODES=""
SERVER_PID=""
WAITED=0
KEEP_LOG=no
SERVER_LOG="${RECORD_DIR}/engine.log"

# ------------------------------------------------------------------- record
#
# Established before any check runs, because every refusal below writes a record
# and a record with no directory to live in is a refusal nobody can read. The
# previous record is removed here rather than left behind: an engine that fails
# to start writes its own, and an engine that fails before it can write one must
# not leave the last run's record in place to be read as this run's condition.

record="${RECORD_DIR}/engine-profile.json"
if mkdir -p "$RECORD_DIR" 2>/dev/null && [ -d "$RECORD_DIR" ] \
        && : > "$SERVER_LOG" 2>/dev/null; then
    KEEP_LOG=yes
fi
rm -f "$record" 2>/dev/null || true

# Every refusal on the way to a running engine goes through here, so a container
# that cannot start is a container whose record says why.
fail() {
    log "$1"
    /usr/local/bin/engine-record.sh failure "$1" || true
    exit "${2:-78}"
}

log "engine: $(/app/llama-server --version 2>&1 | head -n 1)"

# The load mode is checked against the engine's own declaration of the modes it
# accepts. The help block is read rather than a list being written down here, so
# a future engine that adds a mode is usable without an edit and one that
# removes a mode cannot be asked for a flag it will refuse. When the block
# cannot be found the check says so and the flag is passed through, because a
# parse that failed must not fail the container.
LOAD_MODES=$(/app/llama-server --help 2>&1 \
    | sed -n '/--load-mode MODE/,/--lazy-mode MODE/p' \
    | sed -n 's/^ *- \([a-z+]*\):.*/\1/p' | tr '\n' ' ')
if [ -n "$LOAD_MODES" ]; then
    case " $LOAD_MODES " in
        *" $LOAD_MODE "*) ;;
        *) fail "LOAD_MODE=${LOAD_MODE} is not a mode this engine accepts; it declares: ${LOAD_MODES}" ;;
    esac
else
    log "the engine's help did not list its load modes, so ${LOAD_MODE} is passed through unchecked"
fi

if [ -z "${MODEL_PATH:-}" ]; then
    fail "MODEL_PATH must be set"
fi

# The projector is the file most often forgotten, because a text GGUF alone
# loads and serves chat while every image request fails. It is only required
# when vision is on: a text only runtime is allowed to run without it, and that
# is the point of the setting.
if [ "$VISION" = on ] && [ -z "${MMPROJ_PATH:-}" ]; then
    fail "VISION=on but MMPROJ_PATH is not set"
fi

if [ "$VISION" = on ] && [ ! -s "${MMPROJ_PATH:-/dev/null}" ] && [ -n "${MMPROJ_URL:-}" ]; then
    log "projector not found at $MMPROJ_PATH, downloading from $MMPROJ_URL"
    mkdir -p "$(dirname "$MMPROJ_PATH")"
    if ! curl -fL --retry 3 --retry-delay 2 -o "$MMPROJ_PATH.partial" "$MMPROJ_URL"; then
        rm -f "$MMPROJ_PATH.partial"
        fail "the projector download failed" 69
    fi
    mv "$MMPROJ_PATH.partial" "$MMPROJ_PATH"
fi

require_file "$MODEL_PATH" || fail "the weight file at $MODEL_PATH is unusable"
if [ "$VISION" = on ]; then
    require_file "$MMPROJ_PATH" || fail "the projector at $MMPROJ_PATH is unusable"
fi

# ------------------------------------------------------------------ prepare

MODEL_BYTES=$(wc -c < "$MODEL_PATH" | tr -d ' ')
if [ "$MODEL_HASH" = on ]; then
    log "hashing the weights before the server starts, which reads ${MODEL_BYTES} bytes"
    MODEL_SHA256=$(sha256sum "$MODEL_PATH" 2>/dev/null | awk '{print $1}')
    if [ -n "$MODEL_SHA256" ]; then
        MODEL_HASHED=true
    fi
    log "model sha256: ${MODEL_SHA256}"
fi

if [ -s "${MMPROJ_PATH:-/dev/null}" ]; then
    PROJ_BYTES=$(wc -c < "$MMPROJ_PATH" | tr -d ' ')
fi

# The mounted name is always model.gguf, so the source name is what a reader of
# the log needs in order to know which file this engine is actually serving.
log "model: $(basename "${MODEL_SOURCE:-$MODEL_PATH}") (${MODEL_BYTES} bytes, mounted at ${MODEL_PATH})"
if [ "$VISION" = on ]; then
    log "vision: on ($(basename "$MMPROJ_PATH") ${PROJ_BYTES} bytes)"
else
    log "vision: off, projector not loaded, ${PROJ_BYTES} bytes left on disk"
fi
log "load mode: ${LOAD_MODE}$( [ -n "$LOAD_MODES" ] && printf ' (this engine declares: %s)' "$LOAD_MODES" || printf '' )"
log "kv cache: type_k=${KV_TYPE_K} type_v=${KV_TYPE_V}"
log "context=${CTX_SIZE} threads=${THREADS} parallel=${PARALLEL} gpu_layers=${GPU_LAYERS}"

set -- \
    --model "$MODEL_PATH" \
    --host "$HOST" \
    --port "$PORT" \
    --ctx-size "$CTX_SIZE" \
    --threads "$THREADS" \
    --n-gpu-layers "$GPU_LAYERS" \
    --parallel "$PARALLEL" \
    --no-webui \
    --metrics \
    --load-mode "$LOAD_MODE" \
    "$@"

if [ "$VISION" = on ]; then
    set -- "$@" --mmproj "$MMPROJ_PATH"
fi

# The cache element types are always declared, so the record says which of them
# was in force rather than leaving the reader to assume the engine's default.
set -- "$@" --cache-type-k "$KV_TYPE_K" --cache-type-v "$KV_TYPE_V"

if [ -n "${API_KEY:-}" ]; then
    set -- "$@" --api-key "$API_KEY"
fi

# EXTRA_ARGS is intentionally word-split so a caller can append flags such as
# "--jinja" or "--flash-attn" without rebuilding the image.
if [ -n "${EXTRA_ARGS:-}" ]; then
    # shellcheck disable=SC2086
    set -- "$@" $EXTRA_ARGS
fi

# ------------------------------------------------------------------ start
#
# The server runs in the background rather than under exec, because the memory
# map can only be read from the process itself and reading it is the point of
# the record. An init process is PID 1 in this container, so a signal reaches
# this shell and the trap below turns it into the server's own signal.
#
# Its output goes to a file in the record directory and is followed back to the
# container's log, so the engine's own report of the slots, the context and the
# cache it built is kept as evidence and is still visible in `docker logs`. When
# there is no writable record directory the server inherits this shell's output
# and the record says the log was not kept.

log "starting llama-server"
if [ "$KEEP_LOG" = yes ]; then
    /app/llama-server "$@" >> "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    tail -n +1 -f "$SERVER_LOG" >&2 2>/dev/null &
    tail_pid=$!
else
    /app/llama-server "$@" &
    SERVER_PID=$!
fi

stop_server() {
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    if [ "$KEEP_LOG" = yes ]; then
        kill -TERM "${tail_pid:-0}" 2>/dev/null || true
    fi
}
trap stop_server TERM INT

# Wait for the health endpoint, because an engine that has not loaded its
# weights yet has an empty memory map and would be recorded as a runtime that
# mapped nothing.
until curl -fsS "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"ok"'; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        log "llama-server exited during startup"
        WAITED=$waited
        /usr/local/bin/engine-record.sh failure "the engine exited before it answered a health check" || true
        wait "$SERVER_PID"
        exit $?
    fi
    if [ "$waited" -ge 900 ]; then
        WAITED=$waited
        /usr/local/bin/engine-record.sh failure "the engine was still loading after ${waited}s" || true
        stop_server
        exit 70
    fi
    waited=$(( waited + 1 ))
    sleep 1
done
log "server healthy after ${waited}s"
WAITED=$waited

/usr/local/bin/engine-record.sh success || log "the engine is serving but its record could not be written"

wait "$SERVER_PID"
