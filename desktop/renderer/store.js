'use strict';

/**
 * The event store: an append only list, and the projection built from it.
 *
 * Nothing in the window owns state that the stream did not bring. A turn is not
 * created by the view, it is created by a `turn.started`; the counter strip is
 * not incremented by a handler, it is recomputed from the events. That is what
 * makes a refresh unnecessary rather than merely avoided: there is nothing to
 * refresh, because the screen is a function of the list, and the list only ever
 * grows.
 *
 * A recorded run loads through the same door as a live one, so replaying history
 * and watching a run produce the same state from the same input. A bug that made
 * a live run render differently from the same run replayed would be visible
 * immediately, which is the point of having one path.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const COUNTERS = [
    ['tool_calls', 'calls'],
    ['successful_tool_calls', 'ok calls'],
    ['invalid_actions', 'invalid'],
    ['unknown_tools', 'unknown tool'],
    ['invalid_args', 'bad args'],
    ['tool_errors', 'tool errors'],
    ['redundant_calls', 'redundant'],
    ['repeated_failed_turns', 'repeats'],
    ['guard_refusals', 'guard refusals'],
    ['guard_interventions', 'guard holds'],
    ['schema_rejections', 'schema rejects'],
    ['recovered', 'recovered']
  ];

  function emptyState() {
    return {
      runId: '',
      transcript: '',
      live: false,
      loading: false,
      aborted: null,
      model: '',
      tasks: [],
      settings: null,
      controls: null,
      engine: null,
      containment: null,
      turns: [],
      controlsFired: [],
      verifications: [],
      deltas: 0,
      malformed: 0,
      finished: null,
      ended: null,
      record: null,
      log: []
    };
  }

  function create() {
    let state = emptyState();
    const listeners = new Set();

    function notify() {
      for (const listener of listeners) {
        listener(state);
      }
    }

    function subscribe(listener) {
      listeners.add(listener);

      return () => listeners.delete(listener);
    }

    function reset(patch) {
      state = { ...emptyState(), ...(patch || {}) };
      notify();
    }

    function turn(taskId, step) {
      for (let index = state.turns.length - 1; index >= 0; index -= 1) {
        const candidate = state.turns[index];
        if (candidate.task_id === taskId && candidate.step === step) {
          return candidate;
        }
      }

      return null;
    }

    /**
     * Apply one event.
     *
     * An event whose fields are missing is still applied and recorded as
     * malformed, because a dropped event would leave a turn waiting forever
     * while the run behind it has already finished.
     */
    function apply(event) {
      if (!event || typeof event.type !== 'string') {
        return;
      }
      if (typeof event.error === 'string' && event.error !== '') {
        state.malformed += 1;
      }

      switch (event.type) {
        case 'run.started':
          state.runId = event.run || state.runId;
          state.transcript = event.transcript || state.transcript;
          state.model = event.model || '';
          state.tasks = Array.isArray(event.tasks) ? event.tasks : [];
          state.settings = event.settings || null;
          state.controls = event.controls || null;
          state.engine = event.engine || null;
          state.containment = event.containment || null;
          state.live = true;
          break;
        case 'turn.started':
          state.turns.push({
            task_id: event.task_id,
            capability: event.capability,
            step: event.step,
            turns_left: event.turns_left,
            budget: event.budget,
            text: '',
            actions: [],
            observations: [],
            control_ids: [],
            streaming: true,
            at: event.ts
          });
          break;
        case 'model.delta': {
          const current = turn(event.task_id, event.step);
          if (current !== null) {
            current.text += event.text;
          } else {
            state.malformed += 1;
          }
          state.deltas += 1;
          break;
        }
        case 'action.parsed': {
          const current = turn(event.task_id, event.step);
          if (current !== null) {
            current.actions.push({
              tool: event.tool,
              args: event.args,
              layout: event.layout,
              verdict: event.verdict
            });
          }
          break;
        }
        case 'observation': {
          const current = turn(event.task_id, event.step);
          if (current !== null) {
            current.observations.push({
              tool: event.tool,
              ok: event.ok,
              output: event.output,
              error_kind: event.error_kind
            });
            current.streaming = false;
          }
          break;
        }
        case 'control.fired': {
          const record = {
            control: event.control,
            detail: event.detail,
            count: event.count,
            task_id: event.task_id,
            step: event.step,
            at: event.ts
          };
          state.controlsFired.push(record);
          const current = turn(event.task_id, event.step);
          if (current !== null) {
            current.control_ids.push(record);
          }
          break;
        }
        case 'verify.result':
          state.verifications.push({
            task_id: event.task_id,
            capability: event.capability,
            passed: event.passed,
            checks: event.checks || [],
            composite: event.composite,
            steps_used: event.steps_used,
            budget: event.budget,
            counters: event.counters || {}
          });
          break;
        case 'run.finished':
          state.finished = event;
          state.aborted = event.aborted;
          state.live = false;
          for (const open of state.turns) {
            open.streaming = false;
          }
          break;
        default:
          state.malformed += 1;
      }

      notify();
    }

    /** Load a recorded run: its events first, then its record as a fallback. */
    function load(events, record) {
      state = emptyState();
      state.loading = true;
      for (const event of events || []) {
        apply(event);
      }
      state.loading = false;
      state.live = false;
      if (record) {
        if (!state.runId) {
          state.runId = record.run_id || '';
        }
        if (state.settings === null) {
          state.settings = record.settings || null;
        }
        if (state.controls === null) {
          state.controls = record.controls || null;
        }
        if (state.engine === null) {
          state.engine = record.engine || null;
        }
        if (state.containment === null) {
          state.containment = record.containment || null;
        }
        if (state.verifications.length === 0 && Array.isArray(record.tasks)) {
          for (const row of record.tasks) {
            state.verifications.push({
              task_id: row.task_id,
              capability: row.capability,
              passed: row.passed,
              checks: row.checks || [],
              composite: row.composite,
              steps_used: row.steps_used,
              budget: row.budget,
              counters: row.counters || {}
            });
          }
        }
        state.record = record;
      }
      notify();
    }

    /** Record how the child process ended, which the stream itself cannot say. */
    function end(payload) {
      state.ended = payload;
      notify();
    }

    function counters() {
      const totals = {};
      for (const verification of state.verifications) {
        for (const [key, value] of Object.entries(verification.counters || {})) {
          if (typeof value === 'number') {
            totals[key] = (totals[key] || 0) + value;
          }
        }
      }
      if (state.verifications.length === 0 && state.finished && state.finished.counters) {
        for (const [key, value] of Object.entries(state.finished.counters)) {
          totals[key] = value;
        }
      }

      return totals;
    }

    function perCapability() {
      const table = {};
      for (const verification of state.verifications) {
        const key = verification.capability || 'unknown';
        table[key] = table[key] || { capability: key, tasks: 0, passed: 0, composites: [], counters: {} };
        table[key].tasks += 1;
        table[key].passed += verification.passed ? 1 : 0;
        table[key].composites.push(Number(verification.composite || 0));
        for (const [counter, value] of Object.entries(verification.counters || {})) {
          if (typeof value === 'number') {
            table[key].counters[counter] = (table[key].counters[counter] || 0) + value;
          }
        }
      }
      for (const key of Object.keys(table)) {
        const composites = table[key].composites;
        table[key].composite = composites.length === 0
          ? 0
          : composites.reduce((sum, value) => sum + value, 0) / composites.length;
        table[key].pass_rate = table[key].tasks === 0 ? 0 : (100 * table[key].passed) / table[key].tasks;
      }

      return table;
    }

    function currentTask() {
      if (state.turns.length === 0) {
        return null;
      }
      const last = state.turns[state.turns.length - 1];

      return { task_id: last.task_id, capability: last.capability, step: last.step, turns_left: last.turns_left };
    }

    return {
      COUNTERS,
      subscribe,
      reset,
      apply,
      load,
      end,
      counters,
      perCapability,
      currentTask,
      get state() {
        return state;
      }
    };
  }

  UI.store = { create, COUNTERS };
}(window.AgentUI));
