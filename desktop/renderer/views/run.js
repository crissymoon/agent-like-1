'use strict';

/**
 * The run screen: the transcript, as it arrives.
 *
 * Three things are drawn from one event and no state of the view's own: the turn
 * a fragment belongs to, the verdict the schema returned for a call, and the
 * control that acted on a turn. A fragment that arrives before its
 * `turn.started` would be a defect in the harness rather than a layout problem,
 * and it is therefore shown as one instead of being attached to the wrong turn.
 *
 * The constraint status is shown beside every turn intentionally. A constrained
 * turn is not evidence that the model would have been right, and the interface
 * must never let a reader read a number produced by a muzzle as a number
 * produced by a model.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const dom = UI.dom;
  const format = UI.format;

  function turnHead(turn) {
    const chips = [
      dom.el('span', { class: 'chip chip-communicative', text: `step ${turn.step}` }),
      dom.el('span', { class: 'chip', text: `${turn.capability}` }),
      dom.el('span', { class: 'chip', text: `${turn.turns_left} left` })
    ];

    for (const control of turn.control_ids) {
      chips.push(dom.el('span', { class: 'chip chip-transform', text: control.control }));
    }
    for (const action of turn.actions) {
      chips.push(dom.el('span', {
        class: `chip${action.verdict && action.verdict.ok ? '' : ' chip-transform'}`,
        text: `${action.tool}${action.verdict && action.verdict.ok ? '' : ' refused'}`
      }));
    }

    return dom.el('div', { class: 'turn-head' }, [dom.el('div', { class: 'chips' }, chips)]);
  }

  /**
   * One turn of the transcript, foldable by its own step.
   *
   * The transcript is the longest column in the window and it only grows, so a
   * reader following the newest turn pays for every turn above it. The fold is
   * keyed on the task and the step rather than on the position in the list, so a
   * turn that is folded stays folded as later turns arrive and the redraw that
   * each fragment costs cannot reopen it.
   */
  function turnNode(turn) {
    const children = [];

    if (turn.actions.length > 0) {
      for (const action of turn.actions) {
        children.push(dom.el('div', { class: 'rows' }, [
          dom.el('div', { class: 'row row-static' }, [
            dom.el('span', { class: 'mono', text: `${action.tool} ${JSON.stringify(action.args || {})}` }),
            dom.el('span', {
              class: 'muted mono',
              text: `${action.layout} ${action.verdict && action.verdict.ok ? 'accepted' : `refused: ${(action.verdict && action.verdict.errors ? action.verdict.errors.join('; ') : '')}`}`
            })
          ])
        ]));
      }
    }

    children.push(dom.el('div', {
      class: 'turn-body',
      dataset: { streaming: turn.streaming ? 'true' : 'false' },
      text: turn.text === '' && turn.streaming ? 'waiting for the first fragment' : turn.text
    }));

    for (const observation of turn.observations) {
      children.push(dom.el('div', {
        class: 'observe',
        dataset: { ok: observation.ok ? 'true' : 'false' },
        text: `${observation.tool}: ${format.clip(observation.output, 900)}`
      }));
    }

    return UI.disclosure.card(`run.turn.${turn.task_id}.${turn.step}`, {
      title: `step ${turn.step}`,
      head: turnHead(turn),
      open: true,
      sectionClass: 'turn disclosure turn-disclosure'
    }, children);
  }

  function controlsCard(state) {
    if (state.controlsFired.length === 0) {
      return dom.card('Controls that fired', { right: 'none' }, [
        dom.el('p', { class: 'muted', text: 'No control has acted in this run. On a run where the model recovers on its own, that is the reading.' })
      ]);
    }

    const byName = {};
    for (const fired of state.controlsFired) {
      byName[fired.control] = (byName[fired.control] || 0) + 1;
    }

    return UI.disclosure.card('run.controls', {
      title: 'Controls that fired',
      summary: `${state.controlsFired.length} time(s)`,
      open: false
    }, [
      dom.keyValue(Object.entries(byName).map(([name, count]) => [name, `${count}`])),
      dom.el('div', { class: 'rows' }, state.controlsFired.slice(-6).map((fired) => dom.el('div', { class: 'row row-static' }, [
        dom.el('span', { class: 'mono', text: `${fired.control} at step ${fired.step}` }),
        dom.el('span', { class: 'muted mono', text: format.clip(fired.detail, 160) })
      ])))
    ]);
  }

  function constraintCard(state) {
    const controls = state.controls || {};
    const decoder = controls.decoder || {};
    const constrained = decoder.mode && decoder.mode !== 'none';

    return UI.disclosure.card('run.constrained', {
      title: 'What was constrained',
      summary: constrained ? `decoder ${decoder.mode}` : 'application side only',
      open: false
    }, [
      dom.keyValue([
        ['loop guard', controls.loop_guard === undefined ? null : String(controls.loop_guard)],
        ['repeat limit', controls.guard_repeat_limit === undefined ? null : String(controls.guard_repeat_limit)],
        ['strict schema', controls.strict_schema === undefined ? null : String(controls.strict_schema)],
        ['decoder mode', decoder.mode || null],
        ['decoder scope', decoder.scope || null],
        ['engine grammar', decoder.field || 'not sent']
      ]),
      dom.el('p', {
        class: 'muted',
        text: 'A constrained turn is a turn the model was allowed to write, not a turn the model would have written. The engine side grammar was refuted on the pinned engine, so it is shown here as a condition rather than as a default.'
      })
    ]);
  }

  function render(ctx) {
    const state = ctx.store.state;
    const turns = state.turns.slice(-40);
    const body = dom.el('div', { class: 'grid' }, [
      banner(ctx),
      dom.el('div', { class: 'grid grid-2' }, [
        dom.card('Position', { right: state.live ? 'running' : 'stopped' }, [
          dom.keyValue([
            ['run', state.runId || null],
            ['model', state.model || null],
            ['task', currentTask(state)],
            ['turn', state.turns.length === 0 ? null : `${state.turns.length}`],
            ['fragments', `${state.deltas}`],
            ['malformed events', `${state.malformed}`]
          ])
        ]),
        constraintCard(state)
      ]),
      dom.card('Transcript', { right: turns.length === 0 ? 'empty' : `${turns.length} turn(s) shown` }, [
        turns.length === 0
          ? dom.el('p', { class: 'muted', text: 'No turn yet. A turn appears the moment the harness starts one, not when it finishes.' })
          : dom.el('div', { class: 'transcript' }, turns.map(turnNode))
      ]),
      controlsCard(state),
      actionsCard(state)
    ]);

    return {
      title: 'Run',
      crumb: state.transcript || 'no run loaded',
      actions: [
        dom.button('Stop', { variant: 'stop', disabled: !state.live }, () => ctx.cancelRun()),
        dom.button('Result', {}, () => ctx.go('result'))
      ],
      body
    };
  }

  function banner(ctx) {
    const state = ctx.store.state;
    const errors = state.ended && state.ended.errors ? state.ended.errors : [];
    if (state.aborted === true) {
      return dom.el('div', { class: 'banner', dataset: { state: 'fail' } }, [
        dom.el('span', { text: state.finished && state.finished.aggregate && state.finished.aggregate.reason ? state.finished.aggregate.reason : 'the run aborted before a turn' }),
        dom.el('span', { class: 'mono', text: 'aborted' })
      ]);
    }
    if (state.live) {
      return dom.el('div', { class: 'banner', dataset: { state: 'live' } }, [
        dom.el('span', { text: 'A run is in progress. Every line above arrived as an event, so this screen has never been refreshed.' }),
        dom.el('span', { class: 'mono', text: 'live' })
      ]);
    }
    if (errors.length > 0) {
      return dom.el('div', { class: 'banner', dataset: { state: 'fail' } }, [
        dom.el('span', { text: errors[errors.length - 1] }),
        dom.el('span', { class: 'mono', text: `${errors.length} defect(s)` })
      ]);
    }
    if (state.turns.length === 0) {
      return dom.el('div', { class: 'banner', dataset: { state: 'live' } }, [
        dom.el('span', { text: 'Nothing is running. Stage a project or start the run from the workspace screen.' }),
        dom.el('span', { class: 'mono', text: 'idle' })
      ]);
    }

    return dom.el('div', { class: 'banner', dataset: { state: 'pass' } }, [
      dom.el('span', { text: `Run finished in ${format.seconds(state.finished ? state.finished.duration_s : null)}.` }),
      dom.el('span', { class: 'mono', text: format.stateWord({ aborted: false, tasks: state.verifications.length, tasks_passed: state.verifications.filter((entry) => entry.passed).length }) })
    ]);
  }

  function actionsCard(state) {
    if (state.turns.length === 0) {
      return dom.card('Actions taken', { right: 'none' }, [dom.el('p', { class: 'muted', text: 'No action has been read yet.' })]);
    }

    const rows = [];
    for (const turn of state.turns) {
      for (const action of turn.actions) {
        rows.push({
          step: turn.step,
          task: turn.task_id,
          tool: action.tool,
          args: JSON.stringify(action.args || {}),
          ok: Boolean(action.verdict && action.verdict.ok)
        });
      }
    }

    return UI.disclosure.card('run.actions', {
      title: 'Actions taken',
      summary: `${rows.length} call(s)`,
      open: false
    }, [
      dom.table([
        { label: 'task', value: (row) => row.task },
        { label: 'step', value: (row) => row.step, numeric: true },
        { label: 'tool', value: (row) => row.tool },
        { label: 'arguments', value: (row) => format.clip(row.args, 120) },
        { label: 'schema', value: (row) => (row.ok ? 'accepted' : 'refused') }
      ], rows)
    ]);
  }

  function currentTask(state) {
    if (state.turns.length === 0) {
      return null;
    }
    const last = state.turns[state.turns.length - 1];

    return `${last.task_id} step ${last.step}`;
  }

  UI.views = UI.views || {};
  UI.views.run = { id: 'run', label: 'Run', render };
}(window.AgentUI));
