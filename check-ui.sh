#!/usr/bin/env bash
#
# Is the window still good to look at.
#
# One command, one verdict. Three tiers run in order, cheapest first: the
# interface's JavaScript is parsed, the interface's own contract check is run,
# and then the window is actually opened, driven through every screen it
# declares, and read back. The last tier is the one that matters, because a
# syntax check and a contract check both pass on a window whose side menu has
# folded away or whose counters sit below the bottom edge.
#
# Nothing here inspects the stylesheet. The window reports what it rendered and
# the readings are checked as properties rather than as copies of values: the
# ground under the counters has to be a deep pink rather than the ink it used to
# be, not the particular pink someone picked today. The same rule is why the
# target profile is read out of the build instead of written down here.
#
# The window is opened against a scratch profile rather than the one in use, so
# the check cannot read a half configured runtime and cannot write over settings
# a person set. It is opened twice: once the way a person opens it, which is
# clamped to the display, and once at the profile the build declares, so a
# machine with a short screen still measures the layout that ships.
#
# Every fold on the opening screen is put into both states before any of that is
# read, and the frame is read again in each state. A fold is the one part of this
# window whose failure does not look like a failure: a body that carries the
# attribute saying it is hidden while still holding its full height draws exactly
# like a body that is meant to be there, so the check reads the painted height of
# the body rather than the state the card declares.
#
# Usage: ./check-ui.sh
# Exit code is zero when every check that ran passed.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="$SCRIPT_DIR/desktop"
ELECTRON="$DESKTOP/node_modules/.bin/electron"
NODE="${NODE:-node}"
WINDOW_TIMEOUT="${WINDOW_TIMEOUT:-90}"

CHECKS=0
FAILED=0
SKIPPED=0

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/agent-like-ui-check.XXXXXX")"
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

skip() {
  SKIPPED=$((SKIPPED + 1))
  printf 'skip %s\n' "$1"
}

if ! command -v "$NODE" >/dev/null 2>&1; then
  printf 'FAIL node is required and was not found on PATH\n'
  exit 1
fi

# Tier one: every file the window loads parses.
while IFS= read -r file; do
  relative="${file#"$DESKTOP"/}"
  if "$NODE" --check "$file" >/dev/null 2>&1; then
    pass "parses: $relative"
  else
    fail "parses: $relative"
    "$NODE" --check "$file" 2>&1 | sed 's/^/     /'
  fi
done < <(find "$DESKTOP" -name '*.js' -not -path '*/node_modules/*' | sort)

# Tier two: the interface's own check, which holds its copy of the contract
# against the transcripts the harness actually wrote.
if (cd "$DESKTOP" && "$NODE" selftest.js) >"$SCRATCH/selftest.log" 2>&1; then
  pass "the interface's own check passes ($(grep -c '^ok' "$SCRATCH/selftest.log") reading(s))"
else
  fail "the interface's own check failed"
  sed 's/^/     /' "$SCRATCH/selftest.log"
fi

# Tier three: the window. This is what the script is for.
#
# The window is run in the background with a clock on it, because a window that
# opens and never closes is a reading too, and a reading that never arrives is
# not something to wait on forever.
run_window() {
  local log="$1"
  shift
  (cd "$DESKTOP" && "$ELECTRON" . --user-data-dir="$SCRATCH/profile" --smoke --smoke-screens "$@") >"$log" 2>&1 &
  local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$WINDOW_TIMEOUT" ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    printf 'note the window was still open after %ss and was stopped\n' "$waited"
  fi
  wait "$pid" 2>/dev/null
}

cat >"$SCRATCH/read-window.js" <<'NODE'
'use strict';

/*
 * The readings from a smoke transcript, checked as properties.
 *
 * The window reports what it rendered; this says which of those reports are
 * true. A property rather than a value is checked wherever the exact figure is
 * a taste rather than a requirement, so restyling the window does not break the
 * check while restyling it badly does.
 *
 * The target profile is read from the running build rather than kept here, for
 * the same reason: a check that carries its own copy of a number is a check
 * that agrees with itself.
 */

const fs = require('node:fs');

const SCREENS = ['workspace', 'run', 'result', 'settings', 'history'];
const NARROW = 600;
const AS_IT_OPENS = 'as it opens';
const AT_THE_PROFILE = 'at the profile';

let failed = 0;
function report(name, passed, detail) {
  if (passed) {
    process.stdout.write(`ok   ${name}\n`);

    return;
  }
  failed += 1;
  process.stdout.write(`FAIL ${name}${detail === undefined ? '' : `: ${detail}`}\n`);
}

/** A deep pink: red dominant, green near gone, blue present. Not ink, not the bright accent. */
function isDeepPink(value) {
  const parts = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(String(value));
  if (parts === null) {
    return false;
  }
  const red = Number(parts[1]);
  const green = Number(parts[2]);
  const blue = Number(parts[3]);

  return red >= 110 && red <= 200 && green < 60 && blue >= 40 && blue <= 120 && red > blue && blue > green;
}

function parse(text) {
  const readings = [];
  for (const line of text.split('\n')) {
    if (!line.startsWith('SMOKE ')) {
      continue;
    }
    const first = line.indexOf(' ');
    const second = line.indexOf(' ', first + 1);
    const kind = line.slice(first + 1, second);
    let payload = null;
    try {
      payload = JSON.parse(line.slice(second + 1));
    } catch (error) {
      payload = null;
    }
    readings.push({ kind, payload });
  }

  return readings;
}

function dump(text) {
  for (const line of text.split('\n').slice(0, 20)) {
    process.stdout.write(`     ${line}\n`);
  }
}

function check(label, file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (error) {
    report(`${label}: the window wrote a transcript`, false, error.message);

    return;
  }

  const readings = parse(text);
  report(`${label}: the window wrote a reading rather than only an error`, readings.length > 0, `${text.split('\n').length} line(s) of output, none of them a reading`);

  const declared = readings.find((entry) => entry.kind === 'profile');
  const tablet = declared === undefined ? null : declared.payload.tablet;
  report(`${label}: the window declares the profile it is laid out for`, Boolean(tablet && tablet.width > 0 && tablet.height > 0), JSON.stringify(declared === undefined ? null : declared.payload));

  const opened = readings.find((entry) => entry.kind === 'opened');
  const opening = opened === undefined ? null : opened.payload;
  report(`${label}: the window opened and answered`, Boolean(opening && opening.ready === true));
  if (opening === null || opening.ready !== true) {
    dump(text);

    return;
  }

  const requested = opening.requested || {};
  const viewport = opening.viewport || {};
  const layout = opening.layout || {};
  const sideBySide = Number(viewport.width) > NARROW;

  report(`${label}: the reading carries a viewport and a layout`, Number.isFinite(Number(viewport.width)) && Number.isFinite(Number(viewport.height)) && layout !== null && typeof layout === 'object');
  report(`${label}: the window was never asked to be wider than the profile`, tablet === null || requested.width <= tablet.width, `${requested.width} against ${tablet && tablet.width}`);
  report(`${label}: the rendered width is not wider than the profile`, tablet === null || Number(viewport.width) <= tablet.width, `${viewport.width} against ${tablet && tablet.width}`);

  if (label === AT_THE_PROFILE && tablet !== null) {
    report(`${label}: the window was opened at the profile it declares`, requested.width === tablet.width && requested.height === tablet.height, JSON.stringify({ requested, tablet }));
  }

  if (sideBySide) {
    report(`${label}: the side menu is on the side at ${viewport.width}px`, layout.navOnSide === true, JSON.stringify({ railWidth: layout.railWidth, stageWidth: layout.stageWidth }));
    report(`${label}: the side menu is a column at this width`, layout.navDirection === 'column', layout.navDirection);
  }
  report(`${label}: the page does not scroll sideways`, layout.pageScrollsSideways === false);
  report(`${label}: the page does not scroll down`, layout.pageScrollsVertically === false);
  report(`${label}: the screen does not scroll sideways`, layout.screenScrollsSideways === false);
  report(`${label}: the counter strip is inside the window rather than below it`, layout.counterInView === true, JSON.stringify({ rows: layout.counterRows, height: viewport.height }));
  report(`${label}: the ground under the counters is a deep pink rather than ink`, isDeepPink(layout.counterGround), layout.counterGround);
  report(`${label}: the ground under the counters is painted`, typeof layout.counterGround === 'string' && layout.counterGround !== 'rgba(0, 0, 0, 0)', layout.counterGround);
  report(`${label}: the nav carries every declared screen`, opening.navButtons === SCREENS.length, `${opening.navButtons} button(s) for ${SCREENS.length} screen(s)`);
  report(`${label}: the opening screen has content`, opening.screenSections > 0 && opening.counters > 0, JSON.stringify({ sections: opening.screenSections, counters: opening.counters }));

  const walked = readings.find((entry) => entry.kind === 'screens-empty');
  const visited = walked === undefined || !Array.isArray(walked.payload) ? [] : walked.payload;
  report(`${label}: every screen was visited and read`, visited.length === SCREENS.length, `${visited.length} screen(s) read`);
  for (const screen of SCREENS) {
    const reading = visited.find((entry) => entry.screen === screen);
    if (reading === undefined) {
      report(`${label}: the ${screen} screen renders`, false, 'the screen was not read');
      continue;
    }
    report(
      `${label}: the ${screen} screen renders content`,
      reading.ready === true && typeof reading.title === 'string' && reading.title !== '' && reading.screenSections > 0 && reading.counters > 0 && reading.error === undefined,
      JSON.stringify({ title: reading.title, sections: reading.screenSections, counters: reading.counters, error: reading.error })
    );
    const screenLayout = reading.layout || {};
    report(`${label}: the ${screen} screen stays inside the window`, reading.layout === undefined || (screenLayout.screenScrollsSideways === false && screenLayout.pageScrollsVertically === false && screenLayout.counterInView === true), JSON.stringify({ sideways: screenLayout.screenScrollsSideways, down: screenLayout.pageScrollsVertically, strip: screenLayout.counterInView }));
  }

  /*
   * The folds, read in both states.
   *
   * The height of a fold's body is the reading that matters, because the way a
   * fold fails is not by refusing to close but by closing without giving up its
   * space: the attribute is set, the state reports folded, and the screen is
   * exactly as tall as it was. Nothing but a measured height can tell those two
   * apart, and a fold that keeps its height is a fold that has made nothing fit.
   *
   * The frame is read in both states as well. Folding every card shut is the
   * state that has to fit, and opening every one of them is the state that has
   * to stay inside the window, and a window that passes the first and fails the
   * second has been made to fit by hiding the content rather than by laying it
   * out.
   */
  const trial = readings.find((entry) => entry.kind === 'disclosures');
  const folds = trial === undefined ? null : trial.payload;
  if (folds === null || folds.before === null || folds.before === undefined) {
    report(`${label}: the window reported its folds`, false, 'no fold reading was written');
  } else {
    const before = folds.before.nodes || [];
    const collapsed = folds.collapsed === null || folds.collapsed === undefined ? [] : (folds.collapsed.folds || {}).nodes || [];
    const expanded = folds.expanded === null || folds.expanded === undefined ? [] : (folds.expanded.folds || {}).nodes || [];
    const after = folds.after === null || folds.after === undefined ? [] : folds.after.nodes || [];

    report(`${label}: the window carries a fold to check`, folds.before.total > 0, `${folds.before.total} fold(s)`);
    report(
      `${label}: the folds open in more than one state`,
      before.some((node) => node.open === true) && before.some((node) => node.open === false),
      before.map((node) => `${node.key}=${node.open}`).join(' ')
    );
    report(
      `${label}: each fold announces the state it is drawn in`,
      before.every((node) => node.expanded === (node.open ? 'true' : 'false')),
      before.filter((node) => node.expanded !== (node.open ? 'true' : 'false')).map((node) => `${node.key} aria=${node.expanded} drawn=${node.open}`).join(' ')
    );
    report(
      `${label}: a fold that is closed gives up its height`,
      collapsed.length === before.length && collapsed.every((node) => node.height === 0),
      collapsed.filter((node) => node.height !== 0).map((node) => `${node.key} still ${node.height}px tall`).join(' ')
    );
    report(
      `${label}: a fold that is open takes its height back`,
      expanded.length === before.length && expanded.every((node) => node.height > 0),
      expanded.filter((node) => node.height <= 0).map((node) => `${node.key} still ${node.height}px tall`).join(' ')
    );
    const collapsedLayout = folds.collapsed === null || folds.collapsed === undefined ? {} : folds.collapsed.layout || {};
    const expandedLayout = folds.expanded === null || folds.expanded === undefined ? {} : folds.expanded.layout || {};
    report(`${label}: folding every card shut keeps the counter strip in the window`, collapsedLayout.counterInView === true, JSON.stringify(collapsedLayout));
    report(`${label}: opening every fold keeps the counter strip in the window`, expandedLayout.counterInView === true, JSON.stringify(expandedLayout));
    report(`${label}: opening every fold does not widen the page`, expandedLayout.pageScrollsSideways === false, JSON.stringify(expandedLayout));
    report(
      `${label}: the state the screen opened with is put back`,
      after.length === before.length && after.every((node, index) => node.key === before[index].key && node.open === before[index].open),
      after.map((node) => `${node.key}=${node.open}`).join(' ')
    );
  }
}

if (process.argv.length < 3) {
  process.stdout.write('FAIL the reading script was given no transcripts\n');
  process.exit(1);
}
for (const pair of process.argv.slice(2)) {
  const split = pair.indexOf('=');
  check(pair.slice(0, split), pair.slice(split + 1));
}

process.exit(failed === 0 ? 0 : 1);
NODE

if [ -x "$ELECTRON" ]; then
  # A scratch profile, so the check reads a configuration it wrote and leaves
  # the one in use alone. The local runtime is chosen because it needs no
  # container to be running, and a scripted run touches no engine.
  mkdir -p "$SCRATCH/profile"
  printf '{\n  "runtime": "local",\n  "run": { "scripted": true }\n}\n' >"$SCRATCH/profile/settings.json"

  run_window "$SCRATCH/smoke-open.log"

  # The size for the second pass is read out of the first one, so the profile the
  # check measures is the profile the build declares and not a copy kept here.
  # When the first pass wrote no profile line the window is not coming up at all,
  # and the second pass is not worth a clock.
  PROFILE_SIZE="$("$NODE" -e '
    const fs = require("node:fs");
    const line = fs.readFileSync(process.argv[1], "utf8").split("\n").find((entry) => entry.startsWith("SMOKE profile "));
    if (line === undefined) { process.exit(1); }
    const profile = JSON.parse(line.replace("SMOKE profile ", ""));
    process.stdout.write(`${profile.tablet.width}x${profile.tablet.height}`);
  ' "$SCRATCH/smoke-open.log" 2>/dev/null || true)"

  PAIRS=("as it opens=$SCRATCH/smoke-open.log")
  if [ -n "$PROFILE_SIZE" ]; then
    printf 'note the build declares the tablet profile at %s\n' "$PROFILE_SIZE"
    run_window "$SCRATCH/smoke-fit.log" "--smoke-fit=$PROFILE_SIZE"
    PAIRS+=("at the profile=$SCRATCH/smoke-fit.log")
  else
    printf 'note the first pass declared no profile, so the size pass was skipped\n'
  fi

  if [ -s "$SCRATCH/smoke-open.log" ] || [ -s "$SCRATCH/smoke-fit.log" ]; then
    "$NODE" "$SCRATCH/read-window.js" "${PAIRS[@]}" >"$SCRATCH/readings.log" 2>&1
    cat "$SCRATCH/readings.log"
    READ=$(grep -c '^ok' "$SCRATCH/readings.log" 2>/dev/null || true)
    BAD=$(grep -c '^FAIL' "$SCRATCH/readings.log" 2>/dev/null || true)
    CHECKS=$((CHECKS + READ + BAD))
    FAILED=$((FAILED + BAD))
    if [ "$BAD" -eq 0 ] && [ "$READ" -eq 0 ]; then
      fail 'the window produced no readings'
      sed 's/^/     /' "$SCRATCH/smoke-open.log"
    fi
  else
    fail 'the window produced no readings'
    sed 's/^/     /' "$SCRATCH/smoke-open.log"
  fi
else
  skip "the window is not installed at $ELECTRON, so the layout was not read"
fi

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf 'PASS: %d check(s), %d skipped, 0 failed\n' "$CHECKS" "$SKIPPED"
  exit 0
fi
printf 'FAIL: %d check(s), %d skipped, %d failed\n' "$CHECKS" "$SKIPPED" "$FAILED"
exit 1
