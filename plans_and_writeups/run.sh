#!/usr/bin/env bash
#
# Serve the plans_and_writeups public view locally with PHP.
#
#   ./run.sh              pick an open port, start the server, open a browser
#   PORT=9000 ./run.sh    use an exact port (fails if it is taken)
#   OPEN=0 ./run.sh       do not open a browser
#   HOST=0.0.0.0 ./run.sh bind all interfaces (browse via localhost)
#
# Stop the server with Ctrl+C.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
BASE_PORT="${BASE_PORT:-8181}"
PORT_LIMIT="${PORT_LIMIT:-40}"
OPEN="${OPEN:-1}"

# The viewer resolves article links against "/pages" only when it is reached
# through localhost or 127.0.0.1. Any other hostname makes it use the live
# server path, so the URL we advertise has to use a loopback name.
BROWSE_HOST="$HOST"
case "$HOST" in
    0.0.0.0|::|\[::\]) BROWSE_HOST="localhost" ;;
esac

die() {
    echo "Error: $*" >&2
    exit 1
}

# --- Preflight ------------------------------------------------------------

[ -f "$DIR/index.html" ] || die "index.html is missing from $DIR"
[ -f "$DIR/exports.json" ] || echo "Warning: exports.json is missing, the viewer will show an empty state." >&2

command -v php >/dev/null 2>&1 || die "PHP is not installed. Install it first (brew install php)."

PHP_MAJOR="$(php -r 'echo PHP_MAJOR_VERSION;' 2>/dev/null || echo 0)"
[ "$PHP_MAJOR" -ge 7 ] 2>/dev/null || die "PHP 7.0 or newer is required (found: $(php -v 2>/dev/null | head -1))."

# --- Port discovery -------------------------------------------------------

port_in_use() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
        return 1
    fi
    if command -v nc >/dev/null 2>&1; then
        nc -z 127.0.0.1 "$port" >/dev/null 2>&1 && return 0
        return 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

port_desc() {
    command -v lsof >/dev/null 2>&1 || return 0
    lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print " (" $1 " pid " $2 ")"}'
}

if [ -n "${PORT:-}" ]; then
    port_in_use "$PORT" && die "port $PORT is already in use$(port_desc "$PORT"). Unset PORT to auto-select, or pick another."
    SELECTED_PORT="$PORT"
else
    SELECTED_PORT=""
    for ((candidate = BASE_PORT; candidate < BASE_PORT + PORT_LIMIT; candidate++)); do
        if ! port_in_use "$candidate"; then
            SELECTED_PORT="$candidate"
            break
        fi
    done
    [ -n "$SELECTED_PORT" ] || die "no free port found in ${BASE_PORT}-$((BASE_PORT + PORT_LIMIT - 1)). Set BASE_PORT or PORT."
fi

# --- Run ------------------------------------------------------------------

SERVER_PID=""
PHP_LOG=""
cleanup() {
    trap - INT TERM HUP EXIT
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        echo "Server stopped."
    fi
    if [ -n "$PHP_LOG" ] && [ -f "$PHP_LOG" ]; then
        rm -f "$PHP_LOG"
    fi
}
trap cleanup INT TERM HUP EXIT

PHP_LOG="$(mktemp -t plans_and_writeups)"

cd "$DIR"
php -S "${HOST}:${SELECTED_PORT}" >"$PHP_LOG" 2>&1 &
SERVER_PID=$!

# Confirm the bind actually took before advertising the URL.
READY=0
for _ in $(seq 1 40); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    if port_in_use "$SELECTED_PORT"; then
        READY=1
        break
    fi
    sleep 0.1
done

if [ "$READY" -ne 1 ]; then
    echo "Error: the PHP server did not start on ${HOST}:${SELECTED_PORT}." >&2
    if [ -s "$PHP_LOG" ]; then
        sed 's/^/  /' "$PHP_LOG" >&2
    fi
    exit 1
fi

URL="http://${BROWSE_HOST}:${SELECTED_PORT}"
echo "plans_and_writeups"
echo "  URL:      ${URL}"
echo "  Root:     ${DIR}"
echo "  Bind:     ${HOST}:${SELECTED_PORT}"
echo "  PID:      ${SERVER_PID}"
if [ "$BROWSE_HOST" != "$HOST" ]; then
    echo "  Bound to ${HOST}, browse via ${BROWSE_HOST} so article links resolve."
fi
echo "Press Ctrl+C to stop."

if [ "$OPEN" = "1" ] && [ -t 1 ] && command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
fi

wait "$SERVER_PID"
