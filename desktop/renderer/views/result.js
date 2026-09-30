'use strict';

/**
 * The result screen: the verifier reading, then the counters that explain it.
 *
 * The primary reading is per capability rather than a composite, because the
 * recorded comparison found the whole gap in one capability and a composite
 * hides exactly that. The counter columns sit beside the capability rows for the
 * same reason: a run that improved its composite while its recovery counter
 * stayed at zero has not shown recovery, it has shown an easier task.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const dom = UI.dom;
  const format = UI.format;

  function verdictBanner(state) {
    const verifications = state.verifications;
    if (verifications.length === 0) {
      return dom.el('div', { class: 'banner', dataset: { state: 'live' } }, [
        dom.el('span', { text: 'No verifier reading yet. The verifier is the pass condition: the repository builds, its tests pass and the linter is clean on the changed files.' }),
        dom.el('span', { class: 'mono', text: 'no reading' })
      ]);
    }
    const passed = verifications.filter((entry) => entry.passed).length;
    const all = passed === verifications.length;

    return dom.el('div', { class: 'banner', dataset: { state: all ? 'pass' : 'fail' } }, [
      dom.el('span', {
        text: `The verifier passed ${passed} of ${verifications.length} task(s). A patch that resolves an issue and leaves the linter dirty is a failure.`
      }),
      dom.el('span', { class: 'mono', text: `${state.runId || 'run'}` })
    ]);
  }

  function capabilityCard(state) {
    const table = ctxTable(state);
    const rows = Object.values(table);
    if (rows.length === 0) {
      return dom.card('By capability', { right: 'none' }, [dom.el('p', { class: 'muted', text: 'Nothing has been verified yet.' })]);
    }

    return dom.card('By capability', { right: `${rows.length} capability row(s)` }, [
      dom.table([
        { label: 'capability', value: (row) => row.capability },
        { label: 'tasks', value: (row) => row.tasks, numeric: true },
        { label: 'passed', value: (row) => row.passed, numeric: true },
        { label: 'pass rate', value: (row) => `${format.number(row.pass_rate, 2)}%`, numeric: true },
        { label: 'composite', value: (row) => format.number(row.composite, 2), numeric: true },
        { label: 'guard refusals', value: (row) => row.counters.guard_refusals || 0, numeric: true },
        { label: 'guard holds', value: (row) => row.counters.guard_interventions || 0, numeric: true },
        { label: 'schema rejects', value: (row) => row.counters.schema_rejections || 0, numeric: true },
        { label: 'repeats', value: (row) => row.counters.repeated_failed_turns || 0, numeric: true },
        { label: 'recovered', value: (row) => row.counters.recovered || 0, numeric: true }
      ], rows)
    ]);
  }

  function taskCard(state) {
    const rows = state.verifications;
    if (rows.length === 0) {
      return conformanceCard(state);
    }

    return dom.card('By task', { right: `${rows.length} task(s)` }, [
      dom.table([
        { label: 'task', value: (row) => row.task_id },
        { label: 'verdict', value: (row) => (row.passed ? 'pass' : 'fail') },
        { label: 'composite', value: (row) => format.number(row.composite, 2), numeric: true },
        { label: 'steps', value: (row) => `${row.steps_used}/${row.budget}`, numeric: true },
        { label: 'checks', value: (row) => `${(row.checks || []).filter((check) => check.passed).length}/${(row.checks || []).length}`, numeric: true },
        { label: 'errors', value: (row) => row.counters.tool_errors || 0, numeric: true },
        { label: 'repeats', value: (row) => row.counters.repeated_failed_turns || 0, numeric: true }
      ], rows)
    ]);
  }

  function checksCard(state) {
    const checks = [];
    for (const row of state.verifications) {
      for (const check of row.checks || []) {
        checks.push({ task: row.task_id, name: check.name, passed: check.passed, detail: check.detail });
      }
    }
    if (checks.length === 0) {
      return conformanceCard(state);
    }

    return dom.card('Checks', { right: `${checks.length} reading(s)` }, [
      dom.table([
        { label: 'task', value: (row) => row.task },
        { label: 'check', value: (row) => row.name },
        { label: 'reading', value: (row) => (row.passed ? 'pass' : 'fail') },
        { label: 'detail', value: (row) => row.detail }
      ], checks)
    ]);
  }

  function conformanceCard(state) {
    return dom.card('Conformance', { right: 'recorded for this run' }, [
      dom.keyValue([
        ['run', state.runId || null],
        ['transport', state.record ? state.record.transport : null],
        ['php', state.settings && state.settings.locked && state.settings.locked.php_version ? state.settings.locked.php_version.value : null],
        ['prompt', state.settings && state.settings.locked && state.settings.locked.prompt_sha256 ? String(state.settings.locked.prompt_sha256.value).slice(0, 12) : null],
        ['tool specs', state.settings && state.settings.locked && state.settings.locked.tool_specs_sha256 ? String(state.settings.locked.tool_specs_sha256.value).slice(0, 12) : null],
        ['step budget cap', state.settings && state.settings.locked && state.settings.locked.step_budget_cap ? state.settings.locked.step_budget_cap.value : null]
      ])
    ]);
  }

  function environmentCard(ctx) {
    const state = ctx.store.state;
    const engine = state.engine || {};
    const containment = state.containment || {};
    const measurement = engine.described || {};

    return dom.card('Environment the run reported', { right: containment.measured === true ? 'containment measured' : 'no containment reading' }, [
      dom.keyValue([
        ['engine', engine.summary || null],
        ['endpoint answered', engine.endpoint_answered === undefined ? null : String(engine.endpoint_answered)],
        ['vision', measurement.vision && measurement.vision.enabled !== undefined ? String(measurement.vision.enabled) : null],
        ['load mode', measurement.load_mode || null],
        ['kv cache', measurement.kv_type_k && measurement.kv_type_v ? `${measurement.kv_type_k} / ${measurement.kv_type_v}` : null],
        ['context', measurement.endpoint && measurement.endpoint.n_ctx ? String(measurement.endpoint.n_ctx) : null],
        ['runtime', ctx.settings.runtime],
        ['workspace', state.transcript || null]
      ]),
      dom.el('p', {
        class: 'muted',
        text: 'Values the endpoint cannot report over HTTP are read back from the record the engine wrote about itself, which is why they appear here and not in a request.'
      })
    ]);
  }

  function artifactCard(ctx) {
    const state = ctx.store.state;
    const artifacts = state.finished && state.finished.artifacts ? state.finished.artifacts : {};
    const rows = Object.entries(artifacts);

    const list = rows.length === 0
      ? dom.el('p', { class: 'muted', text: 'Artifacts are listed when the run finishes.' })
      : dom.el('div', { class: 'rows' }, rows.map(([name, target]) => {
        const row = dom.el('button', { class: 'row', type: 'button' }, [
          dom.el('span', { text: name }),
          dom.el('span', { class: 'mono', text: target })
        ]);
        row.addEventListener('click', () => ctx.bridge.reveal(target));

        return row;
      }));

    return dom.card('Artifacts', { right: 'click a row to reveal it' }, [list]);
  }

  function ctxTable(state) {
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
      table[key].composite = composites.length === 0 ? 0 : composites.reduce((sum, value) => sum + value, 0) / composites.length;
      table[key].pass_rate = table[key].tasks === 0 ? 0 : (100 * table[key].passed) / table[key].tasks;
    }

    return table;
  }

  function render(ctx) {
    const state = ctx.store.state;
    const body = dom.el('div', { class: 'grid' }, [
      verdictBanner(state),
      capabilityCard(state),
      dom.el('div', { class: 'grid grid-2' }, [taskCard(state), environmentCard(ctx)]),
      checksCard(state),
      artifactCard(ctx)
    ]);

    return {
      title: 'Result',
      crumb: state.runId || 'no run loaded',
      actions: [
        dom.button('Transcript', {}, () => ctx.go('run')),
        dom.button('History', {}, () => ctx.go('history'))
      ],
      body
    };
  }

  UI.views = UI.views || {};
  UI.views.result = { id: 'result', label: 'Result', render };
}(window.AgentUI));
