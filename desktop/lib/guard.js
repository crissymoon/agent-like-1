'use strict';

/**
 * What the window is allowed to change, and where a path it names may point.
 *
 * The main process is the only part of this application that holds a privilege:
 * it starts a process, reads a file and writes a setting. The renderer holds
 * none, and the bridge between them is the surface where that difference can be
 * lost. Every call the bridge offers is therefore treated as a request from a
 * caller that might not be the screen the reader is looking at, because a page
 * that renders a model's own text is a page whose output is not trusted, and a
 * value that reaches `spawn` or `readFile` is a value that has been executed.
 *
 * Two rules live here rather than in the handlers that use them, so both can be
 * read at once and both can be tested without a window:
 *
 *   1. A setting is a value of a name the application already has. A patch may
 *      not invent a name, may not change the type of one, and may not carry a
 *      value that would turn a setting into a command: a binary is a name on the
 *      path or an executable file, a directory is an absolute path to one that
 *      exists, an endpoint is an http address with no credential in it.
 *
 *   2. A path the window names is confined to the roots the interface is about:
 *      the results directory, the workspace it stages into, and the checkout it
 *      was pointed at. The confinement is compared on resolved paths, so `..`
 *      and a symlinked prefix are read the same way as the path a person typed.
 *
 * Nothing here decides anything about a run. It answers one question - may this
 * value be kept - and returns the reason when the answer is no, because a
 * refusal that is not said out loud is a screen that quietly disagrees with the
 * file on disk.
 */

const fs = require('node:fs');
const path = require('node:path');

/** The longest string a setting may carry. A path or an address, not a document. */
const MAX_TEXT = 4096;

/** A binary named without a directory: it is resolved on the path, so its name is all it may be. */
const COMMAND_NAME = /^[A-Za-z0-9._+-]+$/;

/** A task identifier, which is what the workspace screen multiplies into a selection. */
const TASK_ID = /^[a-z0-9_]{1,64}$/;

/** Endpoints, which are what a run may be pointed at. */
const HTTP = /^https?:\/\/[^\s/]+(?::\d{1,5})?(?:\/[^\s]*)?$/;

/** Numbers, with the range the harness accepts for each. A value outside it is refused, not clamped. */
const RUN_NUMBERS = Object.freeze({
  guard_repeat_limit: [1, 100],
  temperature: [0, 5],
  top_p: [0, 1],
  max_tokens: [1, 262144],
  timeout: [1, 86400],
  limit: [0, 10000]
});

/** Enumerations, which are closed sets rather than text. */
const RUN_ENUMS = Object.freeze({
  tool_mode: ['prompt', 'native'],
  decoder: ['none', 'grammar', 'schema'],
  decoder_scope: ['local', 'both'],
  sandbox: ['documented', 'open']
});

const TOP_ENUMS = Object.freeze({ runtime: ['container', 'local'] });

/** Keys whose value is a path on this machine, and what has to be at it. */
const TOP_PATHS = Object.freeze({
  harnessRoot: 'directory',
  workspaceRoot: 'directory-or-empty',
  composeFile: 'file'
});

/** Keys whose value is a program this process will start. */
const TOP_COMMANDS = Object.freeze(['phpBin', 'dockerBin']);

/** Keys whose value is an address a run talks to, or one the window opens. */
const TOP_ENDPOINTS = Object.freeze(['engineUrl', 'containerEngineUrl', 'dashboardUrl']);

function has(object, key) {
  return object !== null && typeof object === 'object' && Object.prototype.hasOwnProperty.call(object, key);
}

/** A refusal, as a value rather than an exception: the caller reports it and keeps the old one. */
function refuse(key, reason) {
  return { key, reason };
}

/**
 * A string that is one line, not empty after trimming, and not absurdly long.
 *
 * A newline is refused rather than trimmed because a value that carries one is a
 * value that could become two arguments in a place that joins a command line.
 */
function checkText(value, key) {
  if (typeof value !== 'string') {
    return refuse(key, 'the value is not text');
  }
  if (value.length > MAX_TEXT) {
    return refuse(key, `the value is longer than ${MAX_TEXT} characters`);
  }
  if (/[\u0000\r\n]/.test(value)) {
    return refuse(key, 'the value carries a control character');
  }
  return null;
}

/** Is this an absolute path to something that exists, of the kind the key names? */
function checkPath(value, key, kind) {
  const text = checkText(value, key);
  if (text !== null) {
    return text;
  }
  if (value === '' && kind === 'directory-or-empty') {
    return null;
  }
  if (!path.isAbsolute(value)) {
    return refuse(key, 'the value is not an absolute path');
  }
  if (path.normalize(value).split(path.sep).includes('..')) {
    return refuse(key, 'the path walks upwards rather than naming a location');
  }
  const stats = statOf(value);
  if (stats === null) {
    return refuse(key, 'there is nothing at that path');
  }
  if (kind === 'directory' && !stats.isDirectory()) {
    return refuse(key, 'the path is not a directory');
  }
  if (kind === 'file' && !stats.isFile()) {
    return refuse(key, 'the path is not a file');
  }
  return null;
}

/** A program: a bare name resolved on the path, or an executable file named in full. */
function checkCommand(value, key) {
  const text = checkText(value, key);
  if (text !== null) {
    return text;
  }
  if (value === '') {
    return refuse(key, 'the value names no program');
  }
  if (!value.includes(path.sep)) {
    if (!COMMAND_NAME.test(value)) {
      return refuse(key, 'the name carries a character a program name cannot have');
    }
    return null;
  }
  if (!path.isAbsolute(value)) {
    return refuse(key, 'a path to a program is written in full or left as a name');
  }
  const stats = statOf(value);
  if (stats === null || !stats.isFile()) {
    return refuse(key, 'there is no file at that path');
  }
  if ((stats.mode & 0o111) === 0) {
    return refuse(key, 'the file is not executable');
  }
  return null;
}

/** An address, with no credential in it: a userinfo in a URL is a password in a settings file. */
function checkEndpoint(value, key) {
  const text = checkText(value, key);
  if (text !== null) {
    return text;
  }
  if (!HTTP.test(value)) {
    return refuse(key, 'the value is not an http address');
  }
  let parsed = null;
  try {
    parsed = new URL(value);
  } catch (error) {
    return refuse(key, 'the value is not an address');
  }
  if (parsed.username !== '' || parsed.password !== '') {
    return refuse(key, 'the address carries a credential');
  }
  if (value.includes('#')) {
    return refuse(key, 'the address carries a fragment');
  }
  return null;
}

/** A number in the range the harness accepts. */
function checkNumber(value, key, range) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return refuse(key, 'the value is not a number');
  }
  if (value < range[0] || value > range[1]) {
    return refuse(key, `the value is outside ${range[0]} to ${range[1]}`);
  }
  return null;
}

/** A selection of tasks: identifiers the harness knows, or an empty list for all of them. */
function checkTasks(value, key) {
  if (!Array.isArray(value)) {
    return refuse(key, 'the selection is not a list');
  }
  if (value.length > 64) {
    return refuse(key, 'the selection names more tasks than the suite holds');
  }
  for (const item of value) {
    if (typeof item !== 'string' || !TASK_ID.test(item)) {
      return refuse(key, 'the selection names something that is not a task');
    }
  }
  return null;
}

function statOf(target) {
  try {
    return fs.statSync(target);
  } catch (error) {
    return null;
  }
}

/**
 * One value, held against the rule for its own name.
 *
 * The name decides the rule, and a name that has no rule is an ordinary value:
 * a boolean, a number, or text of the length a screen can show.
 */
function checkSetting(key, value, current) {
  const text = checkText(value, key);
  if (text !== null && typeof value === 'string') {
    return text;
  }
  if (has(TOP_ENUMS, key)) {
    if (!TOP_ENUMS[key].includes(value)) {
      return refuse(key, `the value is not one of ${TOP_ENUMS[key].join(', ')}`);
    }
    return null;
  }
  if (has(TOP_PATHS, key)) {
    return checkPath(value, key, TOP_PATHS[key]);
  }
  if (TOP_COMMANDS.includes(key)) {
    return checkCommand(value, key);
  }
  if (TOP_ENDPOINTS.includes(key)) {
    return checkEndpoint(value, key);
  }
  if (typeof value === 'object' && value !== null) {
    return refuse(key, 'the value is not one this setting takes');
  }
  if (typeof value !== typeof current) {
    return refuse(key, `the value is not the kind of thing this setting holds`);
  }
  if (typeof value === 'string' && value.trim() === '') {
    return refuse(key, 'the value is empty');
  }
  return null;
}

/**
 * A patch, held against the settings that are in force.
 *
 * A name the application does not already have is refused rather than added,
 * which is what keeps a renderer from widening the shape of the settings file,
 * and a value is refused rather than clamped so that the screen and the file
 * cannot disagree about what is in force.
 *
 * @returns {{settings: object, accepted: string[], refused: {key: string, reason: string}[]}}
 */
function sanitizeSettings(patch, current) {
  const accepted = [];
  const refused = [];
  if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) {
    return { settings: current, accepted, refused: [refuse('(patch)', 'the patch is not an object')] };
  }

  const next = { ...current };
  for (const key of Object.keys(patch)) {
    const value = patch[key];
    if (key === 'run') {
      const held = sanitizeRun(value, current.run);
      next.run = held.run;
      accepted.push(...held.accepted.map((name) => `run.${name}`));
      refused.push(...held.refused);
      continue;
    }
    if (!has(current, key)) {
      refused.push(refuse(key, 'this is not a setting'));
      continue;
    }
    const problem = checkSetting(key, value, current[key]);
    if (problem !== null) {
      refused.push(problem);
      continue;
    }
    next[key] = value;
    accepted.push(key);
  }

  return { settings: next, accepted, refused };
}

/**
 * The run block, held against the run settings that are in force.
 *
 * The names a refusal carries are written the way the settings screen writes
 * them, `run.something`, so a refusal from this function can be reported beside
 * one from the block above it without the caller rewording either.
 */
function sanitizeRun(patch, current) {
  const accepted = [];
  const refused = [];
  const next = { ...current };
  if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) {
    return { run: current, accepted, refused: [refuse('run', 'the run block is not an object')] };
  }

  for (const key of Object.keys(patch)) {
    const value = patch[key];
    if (!has(current, key)) {
      refused.push(refuse(`run.${key}`, 'this is not a run setting'));
      continue;
    }
    let problem = null;
    if (key === 'tasks') {
      problem = checkTasks(value, key);
    } else if (has(RUN_ENUMS, key)) {
      problem = RUN_ENUMS[key].includes(value)
        ? null
        : refuse(key, `the value is not one of ${RUN_ENUMS[key].join(', ')}`);
    } else if (has(RUN_NUMBERS, key)) {
      problem = checkNumber(value, key, RUN_NUMBERS[key]);
    } else if (typeof current[key] === 'boolean') {
      problem = typeof value === 'boolean' ? null : refuse(key, 'the value is not a yes or a no');
    } else {
      problem = checkText(value, key);
    }
    if (problem !== null) {
      refused.push(refuse(`run.${problem.key}`, problem.reason));
      continue;
    }
    next[key] = value;
    accepted.push(key);
  }

  return { run: next, accepted, refused };
}

/**
 * A location as the filesystem names it, whether or not the path exists yet.
 *
 * `realpath` answers this only for a path that is there, and the question this
 * module asks is about paths that are usually there and occasionally not. The
 * difference matters on a machine where the temporary directory is a symlink -
 * `/var` against `/private/var` - because a path that does not exist would then
 * be compared unresolved against a root that is resolved and the two would never
 * match. So the nearest existing ancestor is resolved and the missing tail is
 * joined back on, which is the same location written the same way as the root.
 */
function canonical(target) {
  const absolute = path.resolve(target);
  let current = absolute;
  const trailing = [];
  for (;;) {
    try {
      const real = fs.realpathSync(current);
      return trailing.length === 0 ? real : path.join(real, ...trailing.slice().reverse());
    } catch (error) {
      const parent = path.dirname(current);
      if (parent === current) {
        return absolute;
      }
      trailing.push(path.basename(current));
      current = parent;
    }
  }
}

/**
 * Is a path inside one of these roots?
 *
 * Both sides are canonicalised first, so a relative path, a `..`, a missing
 * component and a symlinked prefix are all read as the location they name rather
 * than as the string they are written as.
 */
function isInside(candidate, roots) {
  if (typeof candidate !== 'string' || candidate === '') {
    return false;
  }
  const resolved = canonical(candidate);
  for (const root of roots) {
    if (typeof root !== 'string' || root === '') {
      continue;
    }
    const base = canonical(root);
    if (resolved === base || resolved.startsWith(base + path.sep)) {
      return true;
    }
  }
  return false;
}

/** The first root a path is inside, or null. Reported rather than assumed, so the caller can say which. */
function rootOf(candidate, roots) {
  for (const root of roots) {
    if (isInside(candidate, [root])) {
      return root;
    }
  }
  return null;
}

/**
 * A directory the window wants staged, held against where the harness lives.
 *
 * The drop zone reaches this without a picker, because a dropped file is handed
 * to the window by the browser rather than chosen through a dialog, so the rule
 * cannot be "a path the main process chose". What it can be is the set of places
 * a project is never: the filesystem root, the home directory itself, and any
 * directory that contains the harness, each of which would copy a machine into
 * the workspace rather than a project.
 *
 * @returns {{ok: boolean, path: string, error: string}}
 */
function checkProject(target, roots) {
  if (typeof target !== 'string' || target === '') {
    return { ok: false, path: '', error: 'no path was given' };
  }
  if (/[\u0000\r\n]/.test(target)) {
    return { ok: false, path: '', error: 'the path carries a control character' };
  }
  if (!path.isAbsolute(target)) {
    return { ok: false, path: '', error: 'the path is not absolute' };
  }
  const resolved = path.resolve(target);
  const stats = statOf(resolved);
  if (stats === null || !stats.isDirectory()) {
    return { ok: false, path: '', error: 'there is no directory at that path' };
  }
  if (resolved === path.parse(resolved).root) {
    return { ok: false, path: '', error: 'the filesystem root is not a project' };
  }
  for (const root of roots) {
    if (typeof root !== 'string' || root === '') {
      continue;
    }
    const hold = path.resolve(root);
    if (resolved === hold) {
      return { ok: false, path: '', error: 'that directory is one of the places the interface uses' };
    }
    // The project would contain the harness, so staging it would copy the
    // checkout into the workspace the agent then reads and writes.
    if (hold.startsWith(resolved + path.sep)) {
      return { ok: false, path: '', error: 'that directory contains the harness rather than a project' };
    }
  }
  return { ok: true, path: resolved, error: '' };
}

module.exports = {
  MAX_TEXT,
  RUN_ENUMS,
  RUN_NUMBERS,
  TOP_ENDPOINTS,
  TOP_COMMANDS,
  TOP_ENUMS,
  TOP_PATHS,
  checkProject,
  checkSetting,
  isInside,
  rootOf,
  sanitizeRun,
  sanitizeSettings
};
