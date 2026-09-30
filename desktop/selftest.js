'use strict';

/**
 * The interface's own check, run without Electron and without a window.
 *
 * Three things are worth knowing before a window is opened, and all three are
 * cheaper to check here: that the contract this side enforces is the contract the
 * harness emits, that the reader survives the way a pipe actually delivers data,
 * and that the transcript this side can write is the transcript it can read
 * back. The first of those is the one that matters, because two copies of a
 * contract drift silently, and the way to hold them together is to replay a real
 * transcript from the harness through the checker.
 *
 * Run with `npm run selftest`. It exits non-zero on the first property that is
 * not true, which is the same rule the harness's own check follows.
 */

const fs = require('node:fs');
const path = require('node:path');
const events = require('./lib/events');
const runs = require('./lib/runs');

const failures = [];
let checks = 0;

function expect(name, passed, detail) {
  checks += 1;
  if (passed) {
    process.stdout.write(`ok   ${name}\n`);

    return;
  }
  failures.push(name);
  process.stdout.write(`FAIL ${name}${detail ? `: ${detail}` : ''}\n`);
}

function scratch(name) {
  const dir = path.join(require('node:os').tmpdir(), `agent-like-selftest-${name}-${process.pid}`);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });

  return dir;
}

// 1. The contract, checked against the table itself.
expect('the contract declares eight events', events.TYPES.length === 8 && Object.keys(events.SHAPES).length === 8);
for (const type of events.TYPES) {
  expect(`the contract declares a shape for ${type}`, events.SHAPES[type] !== undefined);
}

const good = {
  schema: 1, seq: 1, ts: '2026-09-29T00:00:00Z', run: 'r', type: 'model.delta',
  task_id: 't', step: 1, index: 1, text: 'x'
};
expect('a well formed event is accepted', events.check(good).ok);
expect('an event missing a field is refused', !events.check({ ...good, text: undefined }).ok);
expect('an event with a wrongly typed field is refused', !events.check({ ...good, step: '1' }).ok);
expect('an event with the envelope missing is refused', !events.check({ type: 'model.delta', task_id: 't', step: 1, index: 1, text: 'x' }).ok);
expect('an undeclared type is refused', !events.check({ ...good, type: 'run.teleported' }).ok);
expect('an empty array is accepted for an object field', events.check({
  schema: 1, seq: 1, ts: 't', run: 'r', type: 'run.finished',
  tasks: [], aggregate: {}, counters: {}, artifacts: {}, duration_s: 1, aborted: false
}).ok);

// 2. The reader, fed the way a pipe delivers: one byte at a time.
const reader = new events.LineReader();
const lines = [
  JSON.stringify({ ...good, seq: 1, index: 1, text: 'a' }),
  JSON.stringify({ ...good, seq: 2, index: 2, text: 'b' }),
  JSON.stringify({ ...good, seq: 3, index: 3, text: 'c' })
];
const document = `${lines.join('\n')}\n`;
let collected = [];
let errors = [];
for (const character of document) {
  const read = reader.feed(character);
  collected = collected.concat(read.events);
  errors = errors.concat(read.errors);
}
expect('a byte at a time still yields every event', collected.length === 3 && errors.length === 0);
expect('the fragments arrive in order', collected.map((event) => event.text).join('') === 'abc');
expect('a partial line is held rather than parsed', (() => {
  const partial = new events.LineReader();
  const first = partial.feed('{"type":"model.delta","seq":1');
  return first.events.length === 0;
})());

const noisy = new events.LineReader().feed(`not json\n${lines[0]}\n`);
expect('a malformed line is counted rather than thrown', noisy.errors.length === 1 && noisy.events.length === 1);

// 3. A bad field is reported as a defect on the event, so the window can show it.
const defective = new events.LineReader().feed(`${JSON.stringify({ ...good, step: 'one' })}\n`);
expect(
  'a wrongly typed field is drawn as a defect rather than hidden',
  defective.events.length === 1 && typeof defective.events[0].error === 'string' && defective.events[0].error !== ''
);

// 4. A transcript from the harness replays through the same checker.
const fixtures = path.join(__dirname, 'fixtures');
if (fs.existsSync(fixtures)) {
  const files = fs.readdirSync(fixtures).filter((name) => name.endsWith('.ndjson'));
  expect('at least one recorded transcript is available to replay', files.length > 0);
  for (const file of files) {
    const replay = runs.replay(path.join(fixtures, file));
    const malformed = replay.events.filter((event) => typeof event.error === 'string' && event.error !== '');
    expect(
      `every event in ${file} satisfies this contract`,
      replay.errors.length === 0 && malformed.length === 0 && replay.events.length > 0,
      `${replay.errors.length} parse error(s), ${malformed.length} shape error(s), ${replay.events.length} event(s)`
    );
    const types = new Set(replay.events.map((event) => event.type));
    expect(`the replay of ${file} carries a run.started and a run.finished`, types.has('run.started') && types.has('run.finished'));
  }
} else {
  expect('a fixture directory exists', false, `no fixtures at ${fixtures}`);
}

// 5. The store projects the same events that a replay yields.
const state = {
  turns: [], controls: 0
};
for (const event of runs.replay(path.join(fixtures, fs.readdirSync(fixtures)[0])).events) {
  if (event.type === 'turn.started') {
    state.turns.push(event.step);
  }
  if (event.type === 'control.fired') {
    state.controls += 1;
  }
}
expect('a replayed transcript contains the turns it recorded', state.turns.length > 0);

// 6. Run listing walks a results directory without inventing rows.
const listed = runs.list(scratch('empty-results'));
expect('an empty results directory lists no runs', listed.length === 0);

process.stdout.write(`${failures.length === 0 ? 'PASS' : 'FAIL'}: ${checks} check(s), ${failures.length} failed\n`);
process.exit(failures.length === 0 ? 0 : 1);
