#!/usr/bin/env bash
#
# Stops everything the agent experiment starts, and then proves that it stopped.
#
# The experiment has three moving parts and each of them can outlive a crashed
# session: the compose stack (the pinned llama.cpp server and whichever harness
# or ladder container was running), the containers it leaves in an exited state,
# and the host processes a run can leave behind. A stray server is not a cosmetic
# problem. It holds the published port, so the next run either fails to bind or
# silently measures the previous model, and a stray harness process keeps writing
# to the results directory the next comparison reads.
#
# The script is deliberately narrow. It stops the containers of this compose
# project and the host processes that name this harness, and it refuses to touch
# anything else: a listener on one of the experiment's ports that belongs to
# another project is reported with its process id and its working directory
# rather than killed, because a cleanup script that kills by port number is a
# cleanup script that will one day kill somebody else's server. The default
# invocation is read only against the host and destructive only against the
# stack this project owns.
#
#   ./stop-test-env.sh                 stop the stack and verify
#   ./stop-test-env.sh --dry-run       print what would be stopped, change nothing
#   ./stop-test-env.sh --all           also stop this harness's stray host processes
#   ./stop-test-env.sh --volumes       also remove the workspace volume and images
#   ./stop-test-env.sh --runtime       also stop the container runtime, when idle
#
# Exits non zero when the stack is still up or the published port is still held
# after the attempt, so it can be used as the last step of a test script rather
# than as a note to a human.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker/docker-compose.yml"
PROJECT="gemma-agent"
SERVER_CONTAINER="gemma-agent-server"
HARNESS_CONTAINERS="gemma-agent-harness gemma-agent-ladder"
PUBLISHED_PORT="${GEMMA_PORT:-8081}"

VOLUMES=0
RUNTIME=0
STRAY=0
DRY_RUN=0

for argument in "$@"; do
    case "$argument" in
        --volumes) VOLUMES=1 ;;
        --runtime) RUNTIME=1 ;;
        --all) STRAY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            awk 'NR > 2 && /^#/ { sub(/^# ?/, ""); print; next } NR > 2 { exit }' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "stop-test-env: unknown option: $argument" >&2
            exit 2
            ;;
    esac
done

say() {
    printf '[stop] %s\n' "$1"
}

have() {
    command -v "$1" >/dev/null 2>&1
}

if [ "$DRY_RUN" -eq 1 ]; then
    say "dry run: nothing will be changed"
fi

if ! have docker; then
    say "docker is not on PATH, so there is no stack to stop"
    exit 0
fi

# 1. The stack itself.
#
# Both profiles are named so that a container started with either one is found
# even when the invocation that started it has been forgotten. `--remove-orphans`
# covers a container whose service was renamed or deleted in the compose file.
if docker info >/dev/null 2>&1; then
    if [ "$DRY_RUN" -eq 1 ]; then
        say "would run: docker compose -f $COMPOSE_FILE --profile agent --profile ladder down --remove-orphans --timeout 30"
    else
        say "stopping the compose stack"
        docker compose -f "$COMPOSE_FILE" --profile agent --profile ladder down \
            --remove-orphans --timeout 30 || say "the compose down reported an error, continuing to sweep by name"
    fi
else
    say "the docker daemon is not reachable, so no container of this project can be running"
fi

# 2. The sweep.
#
# `down` removes what the compose file still knows about. A container started by
# an earlier revision of the file, or one whose compose state was lost, is found
# here by the project label and by name, which is the only identification that
# survives a lost project state.
sweep_ids() {
    {
        docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true
        for name in $SERVER_CONTAINER $HARNESS_CONTAINERS; do
            docker ps -aq --filter "name=^/${name}$" 2>/dev/null || true
        done
    } | sort -u | sed '/^$/d'
}

if docker info >/dev/null 2>&1; then
    LEFTOVER="$(sweep_ids)"
    if [ -n "$LEFTOVER" ]; then
        say "the sweep found $(echo "$LEFTOVER" | wc -l | tr -d ' ') container(s) of this project by label or by name"
        for id in $LEFTOVER; do
            name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || echo "$id")"
            if [ "$DRY_RUN" -eq 1 ]; then
                say "would stop and remove $name"
                continue
            fi
            say "stopping $name"
            docker rm -f "$id" >/dev/null 2>&1 || say "could not remove $name, it may need a manual look"
        done
    else
        say "no container of this project is present"
    fi
fi

# 3. The verification.
#
# A stop script that reports success without checking is the reason stray
# processes are found by hand later. The two readings below are the ones that
# actually break the next run: a surviving container and a held port.
FAILED=0

if docker info >/dev/null 2>&1 && [ "$DRY_RUN" -eq 0 ]; then
    REMAINING="$(sweep_ids)"
    if [ -n "$REMAINING" ]; then
        say "FAIL: $(echo "$REMAINING" | wc -l | tr -d ' ') container(s) of this project are still present"
        docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '  {{.Names}}  {{.Status}}'
        FAILED=1
    else
        say "ok: no container of this project remains"
    fi
fi

# 4. The ports.
#
# The published port is the server's, and it must be gone once the stack is down.
# A listener on the default native port is reported rather than killed, because a
# server on it was not started by this compose file and killing it would be a
# guess. The report names the owning process and its directory so the decision
# costs one line of reading.
describe_listener() {
    local pid="$1"
    local command
    command="$(ps -o command= -p "$pid" 2>/dev/null | cut -c1-80 || true)"
    local directory
    directory="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | tail -1 || true)"
    printf '  pid %s: %s (cwd %s)\n' "$pid" "$command" "${directory:-unknown}"
}

is_ours() {
    local command="$1"
    case "$command" in
        *llama-server*|*agent.php*|*ladder.php*|*docker-proxy*|*limactl*|*colima*|*ssh*) return 0 ;;
        *) return 1 ;;
    esac
}

if have lsof && [ "$DRY_RUN" -eq 0 ]; then
    # The substitution carries `|| true` because a listening check that finds
    # nothing exits non zero, and this script runs with pipefail on: without it
    # the first quiet port would end the cleanup instead of reporting it.
    PORT_LINE="$(lsof -nP -iTCP:"$PUBLISHED_PORT" -sTCP:LISTEN 2>/dev/null | sed -n '2p' || true)"
    if [ -n "$PORT_LINE" ]; then
        PORT_PID="$(echo "$PORT_LINE" | awk '{print $2}')"
        PORT_CMD="$(ps -o command= -p "$PORT_PID" 2>/dev/null | cut -c1-80 || true)"
        if is_ours "$PORT_CMD"; then
            say "FAIL: port $PUBLISHED_PORT is still held by a process of this experiment"
            describe_listener "$PORT_PID"
            FAILED=1
        else
            say "note: port $PUBLISHED_PORT is held by another project and was left alone"
            describe_listener "$PORT_PID"
        fi
    else
        say "ok: nothing is listening on the published port $PUBLISHED_PORT"
    fi
fi

# 5. Host processes.
#
# A run started by hand, outside the container, leaves a PHP process behind that
# the container sweep cannot see. It is reported always and stopped with --all.
HOST_STRAY="$(ps -eo pid=,command= 2>/dev/null | grep -E 'php [^ ]*(agent|ladder)\.php' | grep -v grep | awk '{print $1}' || true)"
if [ -n "$HOST_STRAY" ]; then
    for pid in $HOST_STRAY; do
        if [ "$STRAY" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
            say "stopping stray harness process $pid"
            kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
        else
            say "note: a harness process is running on the host, pass --all to stop it"
            describe_listener "$pid"
        fi
    done
else
    say "ok: no harness process is running on the host"
fi

if [ "$VOLUMES" -eq 1 ] && docker info >/dev/null 2>&1; then
    if [ "$DRY_RUN" -eq 1 ]; then
        say "would remove the workspace volume and the images built for this experiment"
    else
        say "removing the workspace volume and the images"
        docker volume rm "${PROJECT}_agent-work" >/dev/null 2>&1 || true
        docker image rm gemma-agent/llama-server:pinned >/dev/null 2>&1 || true
    fi
fi

# 6. The runtime.
#
# The runtime is stopped only when it is idle. A container belonging to another
# project is a reason to leave it running, because stopping it is a decision
# about somebody else's work.
if [ "$RUNTIME" -eq 1 ]; then
    if ! have colima; then
        say "colima is not on PATH, so the runtime is left as it is"
    else
        FOREIGN=0
        if docker info >/dev/null 2>&1; then
            FOREIGN="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -v "^${SERVER_CONTAINER}$" | wc -l | tr -d ' ' || true)"
        fi
        if [ "${FOREIGN:-0}" -gt 0 ]; then
            say "the runtime still hosts $FOREIGN container(s) from other projects, so it is left running"
        elif [ "$DRY_RUN" -eq 1 ]; then
            say "would stop the container runtime"
        else
            say "stopping the container runtime"
            colima stop || say "colima stop reported an error"
        fi
    fi
fi

if [ "$FAILED" -ne 0 ]; then
    say "the experiment was not fully stopped"
    exit 1
fi

say "the experiment is stopped"
