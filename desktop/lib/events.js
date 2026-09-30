'use strict';

/**
 * The contract, mirrored on this side of the process boundary.
 *
 * The harness checks every event it emits against the same table. The check is
 * repeated here for one reason: the two sides are separate programs, and a
 * window that trusts its input renders a missing field as an empty panel. A
 * malformed line is therefore reported as a defect event rather than drawn, so
 * the failure appears where a reader can see it instead of as a blank space.
 *
 * The table is not the only source of truth about the contract. `selftest.js`
 * replays a transcript recorded by the harness and refuses to pass unless every
 * event in it satisfies this table, which is what stops the two copies drifting
 * apart silently.
 */

const TYPES = [
  'run.started',
  'turn.started',
  'model.delta',
  'action.parsed',
  'observation',
  'control.fired',
  'verify.result',
  'run.finished'
];

const SHAPES = {
  'run.started': {
    model: 'string', tasks: 'list', settings: 'object', controls: 'object',
    containment: 'object', engine: 'object', transcript: 'string'
  },
  'turn.started': {
    task_id: 'string', capability: 'string', step: 'number', turns_left: 'number', budget: 'number'
  },
  'model.delta': { task_id: 'string', step: 'number', index: 'number', text: 'string' },
  'action.parsed': {
    task_id: 'string', step: 'number', tool: 'string', args: 'object',
    layout: 'string', verdict: 'object'
  },
  'observation': {
    task_id: 'string', step: 'number', tool: 'string', ok: 'boolean',
    output: 'string', error_kind: 'string'
  },
  'control.fired': {
    task_id: 'string', step: 'number', control: 'string', detail: 'string', count: 'number'
  },
  'verify.result': {
    task_id: 'string', capability: 'string', passed: 'boolean', checks: 'list',
    composite: 'number', steps_used: 'number', budget: 'number', counters: 'object'
  },
  'run.finished': {
    tasks: 'list', aggregate: 'object', counters: 'object', artifacts: 'object',
    duration_s: 'number', aborted: 'boolean'
  }
};

function kindOf(value) {
  if (Array.isArray(value)) {
    return 'list';
  }
  if (value === null) {
    return 'null';
  }
  return typeof value;
}

/**
 * @returns {{ok: boolean, errors: string[]}}
 */
function check(event) {
  const errors = [];
  if (event === null || typeof event !== 'object') {
    return { ok: false, errors: ['not an object'] };
  }
  const shape = SHAPES[event.type];
  if (shape === undefined) {
    return { ok: false, errors: [`undeclared event type: ${event.type}`] };
  }
  for (const key of ['schema', 'seq', 'ts', 'run', 'type']) {
    if (!(key in event)) {
      errors.push(`the envelope is missing ${key}`);
    }
  }
  for (const [key, expected] of Object.entries(shape)) {
    if (!(key in event)) {
      errors.push(`missing field ${key}`);
      continue;
    }
    const actual = kindOf(event[key]);
    // An empty array arrives from PHP as a list and is a legitimate empty map,
    // so it is accepted for either, exactly as the emitting side does.
    if (actual === 'list' && event[key].length === 0 && expected === 'object') {
      continue;
    }
    if (actual !== expected) {
      errors.push(`field ${key} is ${actual} where ${expected} is declared`);
    }
  }

  return { ok: errors.length === 0, errors };
}

/**
 * Parse one line of the stream.
 *
 * @returns {{event: object|null, error: string}}
 */
function parse(line) {
  const trimmed = line.trim();
  if (trimmed === '') {
    return { event: null, error: '' };
  }
  let decoded;
  try {
    decoded = JSON.parse(trimmed);
  } catch (error) {
    return { event: null, error: `not JSON: ${error.message}` };
  }
  if (decoded === null || typeof decoded !== 'object') {
    return { event: null, error: 'not an event object' };
  }

  return { event: decoded, error: '' };
}

/**
 * An incremental line reader.
 *
 * A child process writes in whatever sizes the pipe gives it, so a line is
 * assembled here rather than assumed. The tail is kept between chunks, which is
 * the only difference between this and splitting a whole document.
 */
class LineReader {
  constructor() {
    this.buffer = '';
  }

  /** @returns {{events: object[], errors: string[]}} */
  feed(chunk) {
    this.buffer += chunk;
    const events = [];
    const errors = [];
    let index = this.buffer.indexOf('\n');
    while (index !== -1) {
      const line = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 1);
      const { event, error } = parse(line);
      if (error !== '') {
        errors.push(error);
      } else if (event !== null) {
        const verdict = check(event);
        if (verdict.ok) {
          events.push(event);
        } else {
          errors.push(`${event.type}: ${verdict.errors.join('; ')}`);
          events.push({ ...event, error: verdict.errors.join('; ') });
        }
      }
      index = this.buffer.indexOf('\n');
    }

    return { events, errors };
  }
}

module.exports = { TYPES, SHAPES, check, parse, LineReader };
