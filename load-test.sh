#!/usr/bin/env bash
#
# Measures what the whole stack costs, tier by tier, against the 8 GB claim.
#
# The device claim in this project is not "the model is small". It is that a
# device with 8 GB of memory can run the weights, the harness and the window at
# the same time. That is a sum of three things which live in three places: the
# weights inside the container, the harness process that is started per run, and
# the interface on the host. This script samples all three, tier by tier, so the
# total is a reading rather than a hope.
#
# Two readings are taken for every container rather than one. `docker stats`
# reports what the daemon thinks, and the container's own cgroup counter reports
# what the kernel charged it, including the page cache of the memory mapped
# weights. Where they disagree the cgroup is the one this study uses, because it
# is the counter the kernel enforces a limit with, and this study is about a
# memory limit being hit or not hit.
#
# Usage:
#   ./load-test.sh                 every tier, about three minutes
#   ./load-test.sh --quick         shorter holds
#   ./load-test.sh --no-vision     skip the tier that restarts the engine with
#                                  the projector loaded
#   ./load-test.sh --out DIR       where the evidence goes
#
# The script is idempotent: it leaves the engine running with vision off, which
# is the configuration the earlier runs were measured in.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

COMPOSE=(docker compose -f docker/docker-compose.yml)
ENGINE_CONTAINER="gemma-agent-server"
HARNESS_CONTAINER="gemma-agent-harness"
OUT_DIR="$SCRIPT_DIR/results/load"
STAMP="$(date +%Y%m%d-%H%M%S)"
HELD=8
VISION_TIER=1
DESKTOP="$SCRIPT_DIR/desktop"
ELECTRON="$DESKTOP/node_modules/.bin/electron"
PYTHON_BIN="$(command -v python3 || true)"

while [ $# -gt 0 ]; do
  case "$1" in
    --quick) HELD=5 ;;
    --no-vision) VISION_TIER=0 ;;
    --out) shift; OUT_DIR="$1" ;;
    --out=*) OUT_DIR="${1#--out=}" ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$OUT_DIR"
CSV="$OUT_DIR/samples-$STAMP.csv"
printf 'tier,second,engine_cgroup_bytes,engine_anon_bytes,engine_file_bytes,engine_daemon_bytes,harness_cgroup_bytes,electron_kb,vm_kb\n' > "$CSV"

note() { printf '%s\n' "$*"; }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    note "missing command: $1"
    exit 3
  fi
}
need docker
need ps

# The container's own memory counter, in bytes. An empty reading is not a zero:
# it means the container was not there, and a tier that reports nothing must not
# be summed as a tier that reports zero.
container_cgroup_bytes() {
  docker exec "$1" sh -c 'cat /sys/fs/cgroup/memory.current 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || true' 2>/dev/null | tr -d '\r\n' || true
}

# The harness is a run, not a service: `compose run` gives the container a
# generated name, so it is found by the project label and the service it
# belongs to rather than by a name that only exists in compose a `up` starts.
harness_container_ids() {
  docker ps \
    --filter "label=com.docker.compose.project=gemma-agent" \
    --format '{{.ID}} {{.Label "com.docker.compose.service"}}' 2>/dev/null \
  | awk '$2 == "agent" || $2 == "ladder" { print $1 }'
}

# Nothing is printed when no harness container exists, and that is deliberate.
# An empty reading and a zero are different facts: the first says there was no
# harness to measure, the second says there was one and the kernel charged it
# nothing. The first version of this script printed a zero for a run that had
# failed before it started, and the summary then reported a harness run at
# 0.0 MiB: a broken measurement wearing a plausible number. The tier that claims
# a run now proves it, and a tier with no harness reads as not measured.
harness_cgroup_bytes() {
  local total=0
  local id value seen=0
  for id in $(harness_container_ids); do
    value="$(docker exec "$id" sh -c 'cat /sys/fs/cgroup/memory.current 2>/dev/null || true' 2>/dev/null | tr -d '\r\n')"
    if [ -n "$value" ]; then
      total=$((total + value))
      seen=1
    fi
  done
  if [ "$seen" -eq 1 ]; then
    printf '%s' "$total"
  fi
}

# Wait for a harness container to exist before sampling a tier that claims one.
# `compose run` creates the container a moment after the command is issued, and
# a run of one task can finish in a couple of seconds, so the wait and the hold
# have to bracket the container rather than assume it is there.
wait_for_harness() {
  local limit="$1" waited=0
  while [ "$waited" -lt "$limit" ]; do
    if [ -n "$(harness_container_ids)" ]; then
      note "harness container present after ${waited}s"
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

# Stop whatever harness container is alive, used only to bound a stalled run.
stop_harness_containers() {
  local id
  for id in $(harness_container_ids); do
    docker kill "$id" >/dev/null 2>&1 || true
  done
}

# The kernel's own split of what the engine container was charged. `file` is the
# memory mapped weights, which the kernel may reclaim under pressure, and `anon`
# is everything the runtime allocated, which it may not. The same 4.5 GiB means
# opposite things depending on which of the two it is, so both are sampled.
container_stat_bytes() {
  docker exec "$1" sh -c 'cat /sys/fs/cgroup/memory.stat 2>/dev/null || true' 2>/dev/null \
    | awk -v key="$2" '$1 == key { printf "%d", $2 }' || true
}

container_daemon_bytes() {
  docker stats --no-stream --format '{{.MemUsage}}' "$1" 2>/dev/null | awk '
    {
      split($1, parts, "/")
      value = parts[1]
      unit = value
      gsub(/[0-9.]/, "", unit)
      number = value
      gsub(/[A-Za-z]/, "", number)
      factor = 1
      if (unit == "KiB") factor = 1024
      else if (unit == "MiB") factor = 1048576
      else if (unit == "GiB") factor = 1073741824
      else if (unit == "kB") factor = 1000
      else if (unit == "MB") factor = 1000000
      else if (unit == "GB") factor = 1000000000
      printf "%d", number * factor
    }' 2>/dev/null || true
}

# The window is more than one process. Electron starts a main process, a
# renderer per window, a GPU process and a network utility, and a total that
# counted only the first of them would understate the interface by most of it.
electron_kb() {
  ps -Ao rss=,command= 2>/dev/null | awk -v needle="$DESKTOP" '
    index($0, needle) > 0 && index($0, "grep") == 0 && index($0, "awk") == 0 { total += $1 }
    END { printf "%d", total + 0 }' || echo 0
}

# The virtual machine the containers live in is paid for by the host, so it is
# sampled as its own family: a host that is short of memory does not care which
# side of the hypervisor boundary the allocation was made on.
vm_kb() {
  ps -Ao rss=,command= 2>/dev/null | awk '
    /limactl|colima|qemu-system|vz-driver/ && index($0, "grep") == 0 { total += $1 }
    END { printf "%d", total + 0 }' || echo 0
}

sample_once() {
  local tier="$1" second="$2"
  local engine_cg engine_anon engine_file engine_daemon harness_cg electron vm
  engine_cg="$(container_cgroup_bytes "$ENGINE_CONTAINER")"
  engine_anon="$(container_stat_bytes "$ENGINE_CONTAINER" anon)"
  engine_file="$(container_stat_bytes "$ENGINE_CONTAINER" file_mapped)"
  engine_daemon="$(container_daemon_bytes "$ENGINE_CONTAINER")"
  harness_cg="$(harness_cgroup_bytes)"
  electron="$(electron_kb)"
  vm="$(vm_kb)"
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$tier" "$second" "${engine_cg:-}" "${engine_anon:-}" "${engine_file:-}" \
    "${engine_daemon:-}" "${harness_cg:-}" "$electron" "$vm" >> "$CSV"
}

hold() {
  local tier="$1" seconds="$2"
  local second=1
  while [ "$second" -le "$seconds" ]; do
    sample_once "$tier" "$second"
    sleep 1
    second=$((second + 1))
  done
  note "sampled $tier for ${seconds}s"
}

engine_healthy() {
  local status
  status="$(docker inspect --format '{{.State.Health.Status}}' "$ENGINE_CONTAINER" 2>/dev/null || echo missing)"
  [ "$status" = "healthy" ]
}

wait_healthy() {
  local waited=0
  while [ "$waited" -lt 180 ]; do
    if engine_healthy; then
      note "engine healthy after ${waited}s"
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  note "the engine did not become healthy within 180s"
  return 1
}

note "load test $STAMP, evidence in $OUT_DIR"
note "machine: $(sysctl -n hw.ncpu 2>/dev/null || echo '?') core(s), $(sysctl -n hw.memsize 2>/dev/null || echo 0) bytes of physical memory"

# Tier zero: the host and the virtual machine with nothing of ours running. The
# stack is stopped first rather than assumed to be down, because a tier that
# claims to be a baseline while the engine is still loaded is not a baseline.
note "stopping anything this project left running"
"${COMPOSE[@]}" stop >/dev/null 2>&1 || true
sleep 3
hold "t0-host" 3

# Tier one: the weights, loaded, vision off. This is the tier the earlier runs
# were measured in and the one the device claim rests on.
note "starting the engine with vision off"
"${COMPOSE[@]}" up -d gemma >/dev/null 2>&1
if ! wait_healthy; then
  note "no engine to measure; the tiers below that need one will report nothing"
fi
hold "t1-engine-vision-off" "$HELD"

# Tier two: one harness run, in the container, with the window closed. This is
# the tier that separates the harness from the interface, because tier four
# measures both at once and a figure that only ever appears beside the window
# cannot say how much of it was the harness.
#
# The request travels on standard input rather than in a file. A file path is
# resolved inside the container, whose temporary directory is its own memory
# backed filesystem, so a host path passed with `--request` names a file the
# harness cannot open. The first version of this tier did exactly that, and the
# failure arrived as a container that exited in milliseconds and a tier that
# sampled zero five times, which reads as a harness that costs nothing.
#
# The run is real rather than scripted for the same reason: a scripted run
# answers in milliseconds and would be gone before the first sample.
note "running a harness run in the container: one task, engine as it stands"
HARNESS_REQUEST='{"run_id":"load-harness","tasks":["sum_two_files"],"guard":true,"strict_schema":true}'
printf '%s' "$HARNESS_REQUEST" | "${COMPOSE[@]}" run --rm -T --entrypoint php agent \
  /opt/harness/stream.php >"$OUT_DIR/harness-run-$STAMP.log" 2>&1 &
RUNNER_PID=$!
if wait_for_harness 60; then
  hold "t2-harness-run" "$HELD"
else
  note "no harness container was observed within 60s, so tier two has no harness reading"
  note "the run's own output is kept at $OUT_DIR/harness-run-$STAMP.log"
fi
# The run is bounded by the harness's own turn budget. The wait is bounded here
# as well, so a run that stalls cannot hold the whole measurement open.
WAITED=0
while kill -0 "$RUNNER_PID" 2>/dev/null && [ "$WAITED" -lt 180 ]; do
  sleep 3
  WAITED=$((WAITED + 3))
done
if kill -0 "$RUNNER_PID" 2>/dev/null; then
  note "the harness run was still alive after ${WAITED}s; stopping its container"
  stop_harness_containers
fi
wait "$RUNNER_PID" 2>/dev/null || true
note "tier two finished after ${WAITED}s; log in $OUT_DIR/harness-run-$STAMP.log"

# Tier three: the window, with no run behind it.
if [ -x "$ELECTRON" ]; then
  note "opening the window with no run"
  (cd "$DESKTOP" && "$ELECTRON" . --smoke --smoke-screens --smoke-hold=9 >/tmp/load-ui-idle.log 2>&1) &
  UI_PID=$!
  sleep 3
  hold "t3-window-idle" 6
  wait "$UI_PID" 2>/dev/null || true

  # Tier four: everything at once, which is the tier the claim is about.
  note "opening the window and starting a run from it"
  (cd "$DESKTOP" && "$ELECTRON" . --smoke --smoke-run --smoke-screens --smoke-hold=40 >/tmp/load-ui-run.log 2>&1) &
  UI_PID=$!
  sleep 4
  hold "t4-window-and-run" "$HELD"
  wait "$UI_PID" 2>/dev/null || true
  cp /tmp/load-ui-run.log "$OUT_DIR/ui-run-$STAMP.log" 2>/dev/null || true
  # The window reading of this tier stands whether or not the run behind it
  # happened; the harness reading does not. The two are separated here rather
  # than averaged together, because a tier whose harness column is empty is
  # reporting a window and not a stack.
  if [ -z "$(awk -F, '$1 == "t4-window-and-run" && $7 != "" { print $7 }' "$CSV")" ]; then
    note "the harness was not observed during tier four: the window reading stands and the harness reading does not"
  fi
else
  note "the window is not installed at $ELECTRON, so the window tiers are skipped"
fi

# Tier five: the projector loaded, on the same engine, measured the same way.
# This is the cost of the vision setting the runtime now defaults to off.
if [ "$VISION_TIER" -eq 1 ]; then
  note "restarting the engine with vision on"
  GEMMA_VISION=on "${COMPOSE[@]}" up -d --force-recreate gemma >/dev/null 2>&1
  if wait_healthy; then
    hold "t5-engine-vision-on" "$HELD"
  else
    note "the engine with vision on did not come up, so that tier has no reading"
  fi
  note "restoring the engine with vision off"
  "${COMPOSE[@]}" up -d --force-recreate gemma >/dev/null 2>&1
  wait_healthy || true
fi

# Tier six: the same engine with twice the context, which is how the cache cost
# is isolated. The difference in anonymous memory between this tier and the
# first is what the extra 4096 tokens of key and value cache cost, together with
# whatever the runtime grows to hold them: the two are measured as one figure
# because they are allocated as one.
if [ "${CONTEXT_TIER:-1}" -eq 1 ]; then
  note "restarting the engine with a context of 8192 to size the cache"
  GEMMA_CTX_SIZE=8192 "${COMPOSE[@]}" up -d --force-recreate gemma >/dev/null 2>&1
  if wait_healthy; then
    hold "t6-engine-ctx-8192" "$HELD"
  else
    note "the engine with the larger context did not come up, so that tier has no reading"
  fi
  note "restoring the engine at the default context"
  "${COMPOSE[@]}" up -d --force-recreate gemma >/dev/null 2>&1
  wait_healthy || true
fi

"${COMPOSE[@]}" stop >/dev/null 2>&1 || true

if [ -n "$PYTHON_BIN" ]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/tools/summarize-load.py" "$CSV" "$OUT_DIR/load-$STAMP.json" "$OUT_DIR/load-$STAMP.txt" \
    && note "summary written to $OUT_DIR/load-$STAMP.json" \
    || note "the summary could not be written"
else
  note "python3 is not available, so the CSV was left unsummarised: $CSV"
fi

# The newest run is also kept under a stable name. A reader who opens
# `load-final.json` must find the run this script just made: a stale copy under
# a name that says final is a number from a condition nobody checked, and the
# two runs are different measurements of the same stack.
if [ -f "$OUT_DIR/load-$STAMP.json" ]; then
  cp "$OUT_DIR/load-$STAMP.json" "$OUT_DIR/load-final.json"
  cp "$OUT_DIR/load-$STAMP.txt" "$OUT_DIR/load-final.txt"
  note "the newest run is also at $OUT_DIR/load-final.json"
fi

note "samples: $CSV"
