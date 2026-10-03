#!/usr/bin/env bash
#
# The whole security reading, in one command, with one verdict.
#
# The repository already refuses a credential before it reaches a remote, and
# that refusal runs on its own through the git hooks. What this adds is the rest
# of the reading, in the order that makes each tier mean something:
#
#   the scanners' own check   the rules catch what they claim and stay quiet
#                             when they should, which is the only thing that
#                             makes a clean run of them worth reading
#   the syntax preflight      nothing is being scanned that does not parse, and
#                             a file that does not parse was not scanned
#   the four surfaces         secrets, source, file modes and dependencies, read
#                             together and reported as one list
#
# The last tier reads the registry only when it is asked for, because an answer
# that depends on the day it was taken is a different kind of claim from one that
# depends on the commit. `--with-registry` asks.
#
# Usage: ./security-scan.sh [--fail-on high|medium|low|note] [--with-registry]
#                           [--json FILE] [--quiet]
# Exit code is zero when every tier passed, one when a tier failed a level the
# caller set, and two when a reading could not be taken at all.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
FAIL_ON="high"
WITH_REGISTRY=0
QUIET=0
REPORT="$SCRIPT_DIR/results/security/scan.json"

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --fail-on)
      FAIL_ON="${2:-}"
      shift 2
      ;;
    --with-registry)
      WITH_REGISTRY=1
      shift
      ;;
    --json)
      REPORT="${2:-}"
      shift 2
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'security-scan: unknown argument %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$FAIL_ON" in
  high|medium|low|note) ;;
  *)
    printf 'security-scan: --fail-on takes high, medium, low or note, not %s\n' "$FAIL_ON" >&2
    exit 2
    ;;
esac

CHECKS=0
FAILED=0

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/security-scan.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT

pass() {
  CHECKS=$((CHECKS + 1))
  printf 'ok   %s\n' "$1"
}

fail() {
  CHECKS=$((CHECKS + 1))
  FAILED=$((FAILED + 1))
  printf 'FAIL %s\n' "$1"
}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  printf 'FAIL %s is required and was not found on PATH\n' "$PYTHON"
  exit 2
fi

show() {
  [ "$QUIET" -eq 1 ] && return 0
  sed 's/^/     /' "$1"
}

# Tier one: the scanners' own check. A scanner that reports nothing and a scanner
# that reads nothing print the same thing, so the rules are exercised against a
# fixture before the repository is read with them.
if "$PYTHON" "$SCRIPT_DIR/tools/security/selftest.py" >"$SCRATCH/selftest.log" 2>&1; then
  pass "the scanners' own check passes ($(grep -c '^ok' "$SCRATCH/selftest.log") reading(s))"
else
  fail "the scanners' own check failed"
  show "$SCRATCH/selftest.log"
fi

# Tier two: the syntax preflight. It is the same command the repository already
# uses before a push, asked here so that a scan of a file that does not parse is
# not read as a scan that found nothing in it.
if "$PYTHON" "$SCRIPT_DIR/tools/lint/check.py" --tracked --fail-on error --quiet >"$SCRATCH/lint.log" 2>&1; then
  pass "the syntax preflight is clean ($(tail -n 1 "$SCRATCH/lint.log" | sed 's/^lint: //'))"
else
  fail "the syntax preflight reports something that does not parse"
  show "$SCRATCH/lint.log"
fi

# Tier three: the four surfaces. The registry is asked for only when the caller
# asks, so a clean run means the same thing on every machine.
REGISTRY=""
if [ "$WITH_REGISTRY" -eq 1 ]; then
  REGISTRY="--with-registry"
fi

mkdir -p "$(dirname "$REPORT")"
if "$PYTHON" "$SCRIPT_DIR/tools/security/scan_all.py" \
    --fail-on "$FAIL_ON" $REGISTRY --json "$REPORT" >"$SCRATCH/scan.log" 2>&1; then
  pass "the surfaces are clean at $FAIL_ON: $(grep -m1 '^security: ' "$SCRATCH/scan.log" | sed 's/^security: //')"
else
  fail "a surface reported something at or above $FAIL_ON"
  show "$SCRATCH/scan.log"
fi

if [ "$QUIET" -eq 0 ]; then
  printf '\n'
  grep -E '^[a-z_]+: ' "$SCRATCH/scan.log" | sed 's/^/     /'
  printf '     report: %s\n' "$REPORT"
fi

printf '\nsecurity-scan: %d check(s), %d failed, fail level %s\n' "$CHECKS" "$FAILED" "$FAIL_ON"
if [ "$FAILED" -gt 0 ]; then
  printf 'security-scan: the reading is above. A credential is rotated, not deleted.\n'
  exit 1
fi

exit 0
