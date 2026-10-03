#!/usr/bin/env bash
#
# Take a picture of every screen in the window and keep the pictures.
#
# One command, one folder of pictures. The window is opened with the application
# itself rather than a second program built to be photographed, walked through
# every screen it declares, and each screen is written out as a PNG and then
# converted to WebP. The result lands in images/gui/<timestamp>/, so a run never
# overwrites the run before it and a reader can tell when a picture was taken.
#
# The capture is taken by the application, not by the screen. Asking the window
# for a picture of itself means no screen recording permission, no other window
# in frame, and no dependence on where the window happened to be placed. The
# application writes the PNGs; this script only converts them and files them
# away, which is why it can be read end to end in a minute.
#
# The pictures are of the layout, not of this machine. The window is not shown
# during a capture, because a window that is on screen is clamped to the display
# it opens on: on a screen shorter than the profile the bottom of the layout,
# which is the counter strip, would be cut away. The size is the profile the
# build declares unless --size says otherwise, and the viewport each picture was
# actually drawn at is printed beside it, so a machine that refuses even that is
# a warning rather than a crop nobody notices.
#
# The window is opened against a scratch profile rather than the one in use, so a
# capture cannot read a half configured runtime and cannot write over settings a
# person set. It needs no container and touches no engine: the profile runs the
# local runtime and answers tasks from a script.
#
# Usage:
#   ./capture-ui.sh                     every screen, into images/gui/<timestamp>/
#   ./capture-ui.sh --size 768x1024     capture at an exact size
#   ./capture-ui.sh --quality 90        WebP quality, 0 to 100, default 82
#   ./capture-ui.sh --out DIR           write the timestamp folder under DIR
#   ./capture-ui.sh --keep-png          keep the PNGs beside the WebP files
#   ./capture-ui.sh --open              reveal the folder when it is done
#
# Exit code is zero when every screen was captured and converted.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="$SCRIPT_DIR/desktop"
ELECTRON="$DESKTOP/node_modules/.bin/electron"
NODE="${NODE:-node}"
OUT_ROOT="$SCRIPT_DIR/images/gui"
SIZE=""
QUALITY=82
KEEP_PNG=0
REVEAL=0
WINDOW_TIMEOUT="${WINDOW_TIMEOUT:-90}"

while [ $# -gt 0 ]; do
  case "$1" in
    --size) shift; SIZE="${1:-}" ;;
    --size=*) SIZE="${1#--size=}" ;;
    --quality) shift; QUALITY="${1:-82}" ;;
    --quality=*) QUALITY="${1#--quality=}" ;;
    --out) shift; OUT_ROOT="${1:-}" ;;
    --out=*) OUT_ROOT="${1#--out=}" ;;
    --keep-png) KEEP_PNG=1 ;;
    --open) REVEAL=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

if [ ! -x "$ELECTRON" ]; then
  printf 'FAIL the window is not installed at %s\n' "$ELECTRON" >&2
  exit 1
fi
if ! command -v "$NODE" >/dev/null 2>&1; then
  printf 'FAIL node is required and was not found on PATH\n' >&2
  exit 1
fi
case "$SIZE" in
  ""|*x*) ;;
  *) printf 'FAIL --size wants WIDTHxHEIGHT, for example 768x1024, not %s\n' "$SIZE" >&2; exit 2 ;;
esac

# The converter, looked for in the order it is worth having them. cwebp writes
# the smallest file at a given quality, and macOS ships sips, which is a hundred
# times slower and slightly larger but is always there. A machine with neither is
# a machine that cannot do this, and saying so now is better than writing a
# folder of PNGs under a name that promises WebP.
CONVERTER=""
CONVERT_KIND=""
if command -v cwebp >/dev/null 2>&1; then
  CONVERTER="$(command -v cwebp)"
  CONVERT_KIND="cwebp"
elif command -v sips >/dev/null 2>&1; then
  CONVERTER="$(command -v sips)"
  CONVERT_KIND="sips"
else
  printf 'FAIL no WebP converter found: install cwebp (brew install webp) or use a system with sips\n' >&2
  exit 1
fi

# A folder named for the moment the pictures were taken. A second run inside the
# same second gets a suffix rather than merging with the first, because two runs
# that overwrite each other are one run with a confusing count.
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$OUT_ROOT/$STAMP"
if [ -e "$DEST" ]; then
  SUFFIX=2
  while [ -e "$DEST-$SUFFIX" ]; do
    SUFFIX=$((SUFFIX + 1))
  done
  DEST="$DEST-$SUFFIX"
fi
mkdir -p "$DEST" || exit 1

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/agent-like-capture.XXXXXX")"
cleanup() {
  rm -rf "$SCRATCH"
  # A failed run must not leave behind a folder that says a run happened and
  # holds nothing: rmdir removes the destination only while it is empty, so a
  # run that wrote some pictures keeps the ones it wrote.
  rmdir "$DEST" 2>/dev/null || true
}
trap cleanup EXIT

note() { printf '%s\n' "$*"; }

# A scratch profile, so a capture reads a configuration it wrote and leaves the
# one in use alone. The local runtime is chosen because it needs no container and
# a scripted run touches no engine.
mkdir -p "$SCRATCH/profile"
printf '{\n  "runtime": "local",\n  "run": { "scripted": true }\n}\n' >"$SCRATCH/profile/settings.json"

ARGS=(. --user-data-dir="$SCRATCH/profile" --smoke --smoke-capture="$SCRATCH/pngs")
if [ -n "$SIZE" ]; then
  ARGS+=("--smoke-fit=$SIZE")
fi

note "capturing every screen of the window"
(cd "$DESKTOP" && "$ELECTRON" "${ARGS[@]}") >"$SCRATCH/capture.log" 2>&1 &
PID=$!
WAITED=0
while kill -0 "$PID" 2>/dev/null && [ "$WAITED" -lt "$WINDOW_TIMEOUT" ]; do
  sleep 1
  WAITED=$((WAITED + 1))
done
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null
  note "note the window was still open after ${WAITED}s and was stopped"
fi
wait "$PID" 2>/dev/null

# The reading the application wrote, turned into one line per picture. Parsing is
# done by the same interpreter that wrote it, because the payload is JSON and a
# shell that split it on spaces would be reading a format it does not know.
if ! "$NODE" -e '
  const fs = require("node:fs");
  const text = fs.readFileSync(process.argv[1], "utf8");
  const line = text.split("\n").find((entry) => entry.startsWith("SMOKE captured "));
  if (line === undefined) { process.exit(1); }
  const payload = JSON.parse(line.slice("SMOKE captured ".length));
  const profile = payload.profile || {};
  for (const entry of payload.saved || []) {
    if (entry.error !== undefined) {
      process.stdout.write(`error\t${entry.screen}\t${entry.error}\n`);
      continue;
    }
    const view = entry.viewport || {};
    process.stdout.write(`image\t${entry.file}\t${entry.screen}\t${entry.title}\t${entry.width}x${entry.height}\t${view.width}x${view.height}\t${profile.width}x${profile.height}\n`);
  }
' "$SCRATCH/capture.log" >"$SCRATCH/plan.tsv" 2>/dev/null; then
  printf 'FAIL the window wrote no capture reading\n' >&2
  sed 's/^/     /' "$SCRATCH/capture.log" | head -20 >&2
  printf '     the folder is empty: %s\n' "$DEST" >&2
  exit 1
fi

CAPTURED=0
CONVERTED=0
FAILED=0
MISMATCH=0

while IFS=$'\t' read -r kind a b c d e f; do
  if [ "$kind" = "error" ]; then
    printf 'FAIL the %s screen could not be photographed: %s\n' "$a" "$b" >&2
    FAILED=$((FAILED + 1))
    continue
  fi
  PNG="$SCRATCH/pngs/$a"
  if [ ! -s "$PNG" ]; then
    printf 'FAIL %s was reported and is missing from %s\n' "$a" "$SCRATCH/pngs" >&2
    FAILED=$((FAILED + 1))
    continue
  fi
  CAPTURED=$((CAPTURED + 1))
  BASE="${a%.png}"
  OUT="$DEST/$BASE.webp"
  if [ "$CONVERT_KIND" = "cwebp" ]; then
    "$CONVERTER" -quiet -q "$QUALITY" -m 6 -metadata none "$PNG" -o "$OUT"
  else
    "$CONVERTER" -s format webp -s formatOptions "$QUALITY" "$PNG" --out "$OUT" >/dev/null 2>&1
  fi
  if [ ! -s "$OUT" ]; then
    printf 'FAIL %s did not convert to WebP\n' "$a" >&2
    FAILED=$((FAILED + 1))
    continue
  fi
  CONVERTED=$((CONVERTED + 1))
  if [ "$KEEP_PNG" -eq 1 ]; then
    cp "$PNG" "$DEST/$a"
  fi
  SIZE_KB=$(( ($(wc -c <"$OUT") + 1023) / 1024 ))
  # The picture is of the profile, and the one thing that silently stops being
  # true is that. A window clamped to a short display draws the layout at the
  # height it has, so the reading is compared rather than trusted.
  if [ "$f" != "$e" ] && [ -n "$f" ]; then
    printf 'warn %s was drawn at %s rather than the declared %s\n' "$b" "$e" "$f"
    MISMATCH=$((MISMATCH + 1))
  fi
  printf 'ok   %-14s %-18s %s at %s, %s KB of WebP\n' "$b" "$c" "$d" "$e" "$SIZE_KB"
done <"$SCRATCH/plan.tsv"

if [ "$CONVERTED" -eq 0 ]; then
  printf 'FAIL no picture was written; the folder is %s\n' "$DEST" >&2
  exit 1
fi

note ""
note "$CONVERTED of $CAPTURED picture(s) in $DEST"
if [ "$MISMATCH" -gt 0 ]; then
  note "$MISMATCH picture(s) were drawn at a size other than the declared profile"
fi

if [ "$REVEAL" -eq 1 ] && command -v open >/dev/null 2>&1; then
  open "$DEST"
fi

if [ "$FAILED" -gt 0 ]; then
  printf 'FAIL: %d picture(s), %d failed\n' "$CONVERTED" "$FAILED"
  exit 1
fi
printf 'PASS: %d picture(s) in %s\n' "$CONVERTED" "$DEST"
exit 0
