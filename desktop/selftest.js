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

// 7. The fold memory, which decides whether a card a reader shut stays shut.
//
// This is the part of the interface that cannot be checked by looking at a
// screen: a fold that forgets is a fold that reopens on the next fragment of a
// streamed turn, and the screen it reopens on looks correct. The memory is
// therefore read directly, without a window and without a document, which is why
// it is a registry of its own rather than a property of an element.
global.window = { AgentUI: {} };
require('./renderer/disclosure.js');
const label = global.window.AgentUI.disclosure;
expect('the interface offers a fold memory', label !== undefined && typeof label.registry === 'function');

const memory = label.registry();
expect('a fold takes the state it is declared with', memory.define('workspace.run', false) === false && memory.define('workspace.tasks', true) === true);
expect('a fold declared again keeps the state it was remembered in', memory.define('workspace.run', true) === false);
expect('folding a card is remembered', memory.toggle('workspace.run') === true && memory.define('workspace.run', false) === true);
expect('folding a card twice returns it to the state it opened with', memory.toggle('workspace.run') === false && memory.isOpen('workspace.run') === false);
expect('a card that was never drawn has no state rather than a folded one', memory.isOpen('settings.never') === null);
expect('the snapshot carries only the cards the window drew', Object.keys(memory.snapshot()).sort().join(',') === 'workspace.run,workspace.tasks');
expect('one window has one fold memory', label.store.define('shared') === false && label.registry().isOpen('shared') === null);

// 8. The guard, which is what the bridge holds a renderer to.
//
// Everything the window can change reaches this file before it reaches a file
// or a process, so the rules are checked here rather than only through a window:
// a refusal that is not a refusal is a setting that saved something else.
const fsGuard = require('node:fs');
const guard = require('./lib/guard');
const os = require('node:os');

const scratchRoot = scratch('guard');
const held = {
  runtime: 'container',
  engineUrl: 'http://127.0.0.1:8081',
  containerEngineUrl: 'http://gemma:8080',
  harnessRoot: scratchRoot,
  phpBin: 'php',
  dockerBin: 'docker',
  composeFile: path.join(scratchRoot, 'compose.yml'),
  workspaceRoot: '',
  userData: scratchRoot,
  stagedProject: '',
  run: { tool_mode: 'prompt', tasks: [], guard: true, strict_schema: true, stream: true, scripted: false, temperature: 0, top_p: 1, max_tokens: 768, timeout: 300, limit: 0, guard_repeat_limit: 2, decoder: 'none', decoder_scope: 'local', sandbox: 'documented' }
};
fsGuard.writeFileSync(held.composeFile, 'services: {}\n');
const refusedKeys = (patch) => guard.sanitizeSettings(patch, held).refused.map((entry) => entry.key);

expect('a setting the application already has is accepted', guard.sanitizeSettings({ runtime: 'local' }, held).settings.runtime === 'local');
expect('a name that is not a setting is refused', refusedKeys({ teleport: true }).includes('teleport'));
expect('a run name that is not a run setting is refused', refusedKeys({ run: { teleport: 1 } }).includes('run.teleport'));
expect('a patch that changes a type is refused', refusedKeys({ runtime: 7 }).includes('runtime'));
expect('a patch that replaces the run block with a string is refused', refusedKeys({ run: 'x' }).includes('run'));
expect('a value carrying a newline is refused rather than trimmed', refusedKeys({ harnessRoot: `${scratchRoot}\nrm -rf /` }).includes('harnessRoot'));
expect('a path that is not absolute is refused', refusedKeys({ harnessRoot: 'relative/dir' }).includes('harnessRoot'));
expect('a directory that is not there is refused', refusedKeys({ harnessRoot: path.join(scratchRoot, 'nowhere') }).includes('harnessRoot'));
expect('a directory that is a file is refused', refusedKeys({ harnessRoot: held.composeFile }).includes('harnessRoot'));
expect('a file that is not there is refused for compose', refusedKeys({ composeFile: path.join(scratchRoot, 'nope.yml') }).includes('composeFile'));
expect('an empty workspace override is allowed, because empty means the default', guard.sanitizeSettings({ workspaceRoot: '' }, held).refused.length === 0);
expect('a program named as a path that does not exist is refused', refusedKeys({ phpBin: path.join(scratchRoot, 'bin', 'php') }).includes('phpBin'));
expect('a program whose name carries a shell character is refused', refusedKeys({ phpBin: 'php;rm -rf /' }).includes('phpBin'));
expect('a bare program name is allowed, because it is resolved on the path', guard.sanitizeSettings({ phpBin: 'php8' }, held).refused.length === 0);
expect('a command that runs through the shell is refused', refusedKeys({ phpBin: 'sh -c' }).includes('phpBin'));
// security-allow: the credential in this line is a fixture, and the assertion is that the guard refuses the shape
expect('an address with a credential in it is refused', refusedKeys({ engineUrl: 'http://user:secret@host:8081' }).includes('engineUrl')); // security-allow: a fixture, asserted to be refused
expect('an address that is not http is refused', refusedKeys({ engineUrl: 'file:///etc/passwd' }).includes('engineUrl'));
expect('a number outside the range the harness takes is refused', refusedKeys({ run: { temperature: 99 } }).includes('run.temperature'));
expect('a number that is not a number is refused', refusedKeys({ run: { max_tokens: 'many' } }).includes('run.max_tokens'));
expect('a run value outside its closed set is refused', refusedKeys({ run: { tool_mode: 'telepathy' } }).includes('run.tool_mode'));
expect('a yes or no that is not one is refused', refusedKeys({ run: { stream: 'yes' } }).includes('run.stream'));
expect('a task list that names something else is refused', refusedKeys({ run: { tasks: ['../etc'] } }).includes('run.tasks'));
expect('an empty task list is allowed, and means every task', guard.sanitizeSettings({ run: { tasks: [] } }, held).refused.length === 0);
expect('a run setting the harness declares is accepted', guard.sanitizeSettings({ run: { sandbox: 'open' } }, held).settings.run.sandbox === 'open');
const held_result = guard.sanitizeSettings({ runtime: 'local', phpBin: '/no/such/php' }, held);
expect('one refused value does not lose the accepted ones', held_result.settings.runtime === 'local' && held_result.accepted.join(',') === 'runtime');
expect('a good patch leaves the value it did not name alone', guard.sanitizeSettings({ runtime: 'local' }, held).settings.engineUrl === held.engineUrl);
expect('a patch that is not an object is refused whole', guard.sanitizeSettings(null, held).refused.length === 1 && guard.sanitizeSettings(null, held).settings === held);

const inner = path.join(scratchRoot, 'run', 'sample');
fsGuard.mkdirSync(inner, { recursive: true });
expect('a path under a root is inside it', guard.isInside(inner, [scratchRoot]) === true);
expect('a path with .. that stays under the root is inside it', guard.isInside(path.join(inner, '..', '..', 'other'), [scratchRoot]) === true);
expect('a path that climbs out of the root is not inside it', guard.isInside(path.join(inner, '..', '..', '..', 'etc'), [scratchRoot]) === false);
expect('a path under the root that does not exist yet is inside it', guard.isInside(path.join(scratchRoot, 'not', 'yet', 'written.ndjson'), [scratchRoot]) === true);
expect('a sibling with the root as its prefix is not inside it', guard.isInside(`${scratchRoot}-other`, [scratchRoot]) === false);
expect('the root itself counts as inside, because a row is a directory in it', guard.isInside(scratchRoot, [scratchRoot]) === true);
expect('an empty path is not inside anything', guard.isInside('', [scratchRoot]) === false);

const project = path.join(scratchRoot, 'project');
fsGuard.mkdirSync(project, { recursive: true });
expect('a directory a project could be at is accepted', guard.checkProject(project, [held.harnessRoot]).ok === true);
expect('the filesystem root is refused as a project', guard.checkProject('/', [held.harnessRoot]).ok === false);
expect('a directory that does not exist is refused as a project', guard.checkProject(path.join(scratchRoot, 'gone'), [held.harnessRoot]).ok === false);
expect('a relative path is refused as a project', guard.checkProject('project', [held.harnessRoot]).ok === false);
expect('a directory that holds the harness is refused, because the copy would hold the checkout', guard.checkProject(scratchRoot, [project]).ok === false);
expect('the directory the harness is pointed at is refused even when it is a project', guard.checkProject(project, [project]).ok === false);

process.stdout.write(`${failures.length === 0 ? 'PASS' : 'FAIL'}: ${checks} check(s), ${failures.length} failed\n`);
process.exit(failures.length === 0 ? 0 : 1);
