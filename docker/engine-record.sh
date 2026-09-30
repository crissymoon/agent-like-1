#!/bin/sh
# Writes the engine record for the local agent runtime.
#
# This is the half of the server entrypoint that reads the engine, kept apart
# from the half that starts it, because they have different failure modes. A
# start that fails must report why and stop. A reading that fails must not stop
# anything: the engine is up and answering, and a record that is missing one
# field is a record with a gap in it, which is a fact a reader can see, where an
# entrypoint that aborted on an unreadable file would take down a working
# engine over its own bookkeeping.
#
# Two modes:
#
#   engine-record.sh success
#       Reads the running engine and writes the full record. The process is
#       identified by $SERVER_PID; everything else arrives in the environment.
#
#   engine-record.sh failure "reason"
#       Writes a record that says the engine did not start, with the tail of the
#       engine's own log. This exists because a stale record read as a current
#       one is a condition that is not in force, and that is a worse error than
#       no record at all.
#
# The full record is assembled from three sources and each one is named in the
# record so a reader knows which is which:
#
#   this script's reading of the process  the memory map, resident memory and
#                                         the number of mapped ranges, taken
#                                         from /proc, which is what an mmap
#                                         setting actually did rather than what
#                                         it asked for;
#   the engine's own log                  the slot count, the context per slot
#                                         and whether the cache is unified;
#   the endpoint's own answer             the modalities it will accept and the
#                                         build it reports, from /props.
#
# Environment, all required unless stated:
#   RECORD_DIR      where the record and the engine log live
#   SERVER_PID      the engine's process id                 (success only)
#   WAITED          seconds the engine took to answer       (success only)
#   ENGINE_IMAGE    the image reference the container was started from
#   MODEL_PATH      the weight file the engine was given, as the container sees
#                   it; this is the name /props echoes back and therefore the
#                   name the record must use for the two to be comparable
#   MODEL_SOURCE, MMPROJ_SOURCE   optional; the source file that was bound to
#                   MODEL_PATH and MMPROJ_PATH, recorded so a run made from a
#                   directory of several weight files can say which one it used
#   VISION          on or off
#   LOAD_MODE       the load mode that was requested
#   LOAD_MODES      the modes the engine declares, space separated, or empty
#   KV_TYPE_K / KV_TYPE_V   the cache element types
#   CTX_SIZE, PARALLEL, THREADS, GPU_LAYERS, HOST, PORT
#   KEEP_LOG        yes when the engine's output was kept to a file
#   SERVER_LOG      that file
#   MODEL_BYTES, PROJ_BYTES, MODEL_SHA256, MODEL_HASHED   read by the caller,
#                   because hashing 3.1 GB belongs to startup and not here

set -eu

log() {
    printf '[engine-record] %s\n' "$1" >&2
}

json_number() {
    if [ -z "${1:-}" ]; then
        printf 'null'
    else
        printf '%s' "$1"
    fi
}

json_string() {
    printf '%s' "${1:-}" | tr -d '"\\'
}

element_bytes() {
    case "$1" in
        f32) printf '4' ;;
        f16|bf16) printf '2' ;;
        q8_0) printf '1.0625' ;;
        q4_0) printf '0.5625' ;;
        q4_1) printf '0.6250' ;;
        q5_0) printf '0.6875' ;;
        q5_1) printf '0.7500' ;;
        *) printf 'null' ;;
    esac
}

RECORD_DIR="${RECORD_DIR:-/records}"
SERVER_LOG="${SERVER_LOG:-${RECORD_DIR}/engine.log}"
record="${RECORD_DIR}/engine-profile.json"
MODE="${1:-success}"

# What the record calls the weights.
#
# `path` is the mounted name, model.gguf, because that is the name the endpoint
# reports about itself and the harness compares the two. It is also the name
# every mount of this engine shares, so once more than one weight file can be
# mounted it no longer says which file ran. The source file travels beside it as
# `source`. With no source given it falls back to the mounted name, so an older
# invocation still records a name rather than nothing.
model_name="$(basename "${MODEL_PATH:-}")"
model_source="$(basename "${MODEL_SOURCE:-$MODEL_PATH}")"
proj_source="$(basename "${MMPROJ_SOURCE:-${MMPROJ_PATH:-}}")"

if [ ! -d "$RECORD_DIR" ]; then
    log "no record directory at $RECORD_DIR, so nothing was written"
    exit 0
fi

# ------------------------------------------------------------------ failure

if [ "$MODE" = failure ]; then
    reason="${2:-the engine did not start}"
    tail_text=""
    if [ "${KEEP_LOG:-no}" = yes ] && [ -r "$SERVER_LOG" ]; then
        tail_text=$(tail -n 8 "$SERVER_LOG" | tr -d '"\\' | tr '\n' '|')
    fi
    cat > "$record" <<JSON
{
  "document": "engine-profile",
  "schema_version": "1",
  "written_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "started": false,
  "reason": "$(json_string "$reason")",
  "engine": {
    "image_digest": "$(json_string "${ENGINE_IMAGE##*@}")",
    "version": "$(json_string "$(/app/llama-server --version 2>&1 | head -n 1)")"
  },
  "requested": {
    "load_mode": "${LOAD_MODE:-}",
    "vision": "${VISION:-}",
    "ctx_size": ${CTX_SIZE:-0},
    "parallel": ${PARALLEL:-0},
    "kv_type_k": "${KV_TYPE_K:-}",
    "kv_type_v": "${KV_TYPE_V:-}"
  },
  "engine_log_tail": "$(json_string "$tail_text")"
}
JSON
    log "wrote the failure record: $record"
    exit 0
fi

# ------------------------------------------------------------------ success

if [ -z "${SERVER_PID:-}" ]; then
    log "SERVER_PID is not set, so the process could not be read"
    exit 1
fi

rss_bytes=0
vs_size_bytes=0
swapped=0
if [ -r "/proc/${SERVER_PID}/status" ]; then
    rss_kb=$(awk '/^VmRSS:/ {print $2}' "/proc/${SERVER_PID}/status" 2>/dev/null || printf '0')
    vsz_kb=$(awk '/^VmSize:/ {print $2}' "/proc/${SERVER_PID}/status" 2>/dev/null || printf '0')
    swap_kb=$(awk '/^VmSwap:/ {print $2}' "/proc/${SERVER_PID}/status" 2>/dev/null || printf '0')
    rss_bytes=$(( ${rss_kb:-0} * 1024 ))
    vs_size_bytes=$(( ${vsz_kb:-0} * 1024 ))
    swapped=$(( ${swap_kb:-0} * 1024 ))
fi

# Every range of the process's map whose path ends in a given file name, summed,
# with the number of ranges. The path is matched on the basename so a bind mount
# that renames the target still matches the file it is serving. The arithmetic
# is done by the shell from the hexadecimal range boundaries rather than by an
# awk extension, because the base image ships an awk without strtonum and a
# reading that comes back as zero on one machine and as the file size on another
# is worse than no reading at all.
mapped_bytes() {
    maps="/proc/${SERVER_PID}/maps"
    if [ ! -r "$maps" ]; then
        printf '0 0'
        return
    fi
    total=0
    count=0
    for range in $(awk -v name="$1" '
        {
            path = $6
            if (NF > 6) { for (i = 7; i <= NF; i++) path = path " " $i }
            base = path
            sub(/.*\//, "", base)
            if (base == name) { print $1 }
        }' "$maps" 2>/dev/null); do
        total=$(( total + 0x${range##*-} - 0x${range%%-*} ))
        count=$(( count + 1 ))
    done
    printf '%d %d' "$total" "$count"
}

model_read=$(mapped_bytes "$(basename "$MODEL_PATH")")
model_map_bytes=$(printf '%s' "$model_read" | awk '{print $1}')
model_map_ranges=$(printf '%s' "$model_read" | awk '{print $2}')

proj_map_bytes=0
if [ "${VISION:-off}" = on ]; then
    proj_map_bytes=$(mapped_bytes "$(basename "${MMPROJ_PATH:-}")" | awk '{print $1}')
fi

# Whether the mapping is real: at least nine tenths of the weight file appears
# as a file backed range. A partial map from a lazy loader is still a map; a
# size that does not reach the file at all is not.
weights_mapped=no
map_share=0
if [ "${MODEL_BYTES:-0}" -gt 0 ]; then
    map_share=$(awk -v mapped="${model_map_bytes:-0}" -v total="${MODEL_BYTES:-0}" \
        'BEGIN { printf "%.4f", (total > 0 ? mapped / total : 0) }')
    if awk -v share="$map_share" 'BEGIN { exit !(share >= 0.9) }'; then
        weights_mapped=yes
    fi
fi

# The engine's own account of the session, read from the log it wrote. The slot
# count and the context per slot are the engine's numbers and not this script's,
# which is the difference between a record and a restatement.
kv_line=""
kv_unified=""
slots_reported=""
ctx_reported=""
kv_buffer=""
if [ "${KEEP_LOG:-no}" = yes ] && [ -r "$SERVER_LOG" ]; then
    kv_line=$(grep -m1 'load_model: initializing' "$SERVER_LOG" 2>/dev/null || true)
    kv_unified=$(printf '%s' "$kv_line" | sed -n "s/.*kv_unified = '\([a-z]*\)'.*/\1/p")
    slots_reported=$(printf '%s' "$kv_line" | sed -n 's/.*n_slots = \([0-9]*\).*/\1/p')
    ctx_reported=$(printf '%s' "$kv_line" | sed -n 's/.*n_ctx_slot = \([0-9]*\).*/\1/p')
    # Present only at a higher verbosity, so its absence is recorded as absence.
    kv_buffer=$(grep -m1 'KV buffer size' "$SERVER_LOG" 2>/dev/null \
        | sed 's/^[0-9.]* [A-Za-z]* *//' || true)
fi

# What the endpoint says about itself. The modalities block is the engine's own
# answer to whether a projector is loaded, which is a reading and not a repeat
# of the setting this script was given.
props_json=$(curl -fsS "http://127.0.0.1:${PORT:-8080}/props" 2>/dev/null || true)
modalities=$(printf '%s' "$props_json" | sed -n 's/.*"modalities":{\([^}]*\)}.*/\1/p')

modal_flag() {
    got=$(printf '%s' "$modalities" | sed -n "s/.*\"$1\":\(true\|false\).*/\1/p")
    if [ -z "$got" ]; then
        printf 'null'
    else
        printf '%s' "$got"
    fi
}

cat > "$record" <<JSON
{
  "document": "engine-profile",
  "schema_version": "1",
  "written_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "started": true,
  "engine": {
    "image_digest": "$(json_string "${ENGINE_IMAGE##*@}")",
    "version": "$(json_string "$(/app/llama-server --version 2>&1 | head -n 1)")",
    "build_info": "$(json_string "$(printf '%s' "$props_json" | sed -n 's/.*"build_info":"\([^"]*\)".*/\1/p')")",
    "host": "${HOST:-0.0.0.0}",
    "port": ${PORT:-8080},
    "startup_seconds": ${WAITED:-0},
    "log_kept": $( [ "${KEEP_LOG:-no}" = yes ] && printf true || printf false )
  },
  "model": {
    "path": "$(json_string "$model_name")",
    "source": "$(json_string "$model_source")",
    "bytes": ${MODEL_BYTES:-0},
    "sha256": "$(json_string "${MODEL_SHA256:-}")",
    "hashed": $( [ "${MODEL_HASHED:-false}" = true ] && [ -n "${MODEL_SHA256:-}" ] && printf true || printf false )
  },
  "vision": {
    "enabled": $( [ "${VISION:-off}" = on ] && printf true || printf false ),
    "projector_path": "$(json_string "$(basename "${MMPROJ_PATH:-}")")",
    "projector_source": "$(json_string "$proj_source")",
    "projector_bytes": ${PROJ_BYTES:-0},
    "projector_mapped_bytes": ${proj_map_bytes:-0},
    "saved_bytes": $( [ "${VISION:-off}" = on ] && printf 0 || printf "%s" "${PROJ_BYTES:-0}" ),
    "modalities_reported_by_engine": {
      "vision": $(modal_flag vision),
      "video": $(modal_flag video),
      "audio": $(modal_flag audio)
    }
  },
  "load": {
    "mode_requested": "${LOAD_MODE:-}",
    "modes_accepted_by_engine": "$(json_string "${LOAD_MODES:-}")",
    "weights_mapped": $( [ "$weights_mapped" = yes ] && printf true || printf false ),
    "model_bytes": ${MODEL_BYTES:-0},
    "model_file_mapping_bytes": ${model_map_bytes:-0},
    "model_mapping_ranges": ${model_map_ranges:-0},
    "mapped_share": ${map_share}
  },
  "kv_cache": {
    "type_k": "${KV_TYPE_K:-}",
    "type_v": "${KV_TYPE_V:-}",
    "type_k_bytes_element": $(element_bytes "${KV_TYPE_K:-}"),
    "type_v_bytes_element": $(element_bytes "${KV_TYPE_V:-}"),
    "ctx_size": ${CTX_SIZE:-0},
    "parallel": ${PARALLEL:-0},
    "threads": ${THREADS:-0},
    "gpu_layers": ${GPU_LAYERS:-0},
    "slots_reported_by_engine": $(json_number "$slots_reported"),
    "ctx_per_slot_reported_by_engine": $(json_number "$ctx_reported"),
    "kv_unified": "$(json_string "$kv_unified")",
    "kv_buffer_reported_by_engine": "$(json_string "$kv_buffer")"
  },
  "memory": {
    "rss_bytes": ${rss_bytes:-0},
    "vm_size_bytes": ${vs_size_bytes:-0},
    "swap_bytes": ${swapped:-0},
    "shared_model_bytes": $( [ "$weights_mapped" = yes ] && printf "%s" "${model_map_bytes:-0}" || printf 0 )
  }
}
JSON

log "wrote $record"
