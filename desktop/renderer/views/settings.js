'use strict';

/**
 * The settings screen: everything the record carries, and what set it.
 *
 * The rule the interface follows is that a value which is in force is on screen
 * and a value which is not on screen is not in force. That is why a locked value
 * is drawn rather than hidden: the step budget, the observation clip and the
 * cache element types are constants of the running harness, and a screen that
 * silently omitted them would invite a reader to believe they are adjustable.
 *
 * Every change is saved immediately and written into the next run's record, so
 * the settings that produced a result are recoverable from the result itself.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const dom = UI.dom;
  const format = UI.format;

  const TEXT = {
    engineUrl: 'The endpoint the run talks to. In the container runtime this is the published port on the loopback address.',
    runtime: 'container runs the harness inside the measured walls. local runs the same entry point with the interpreter on this machine.',
    tool_mode: 'prompt puts the protocol in the system prompt. native asks the engine for its own tool call layout.',
    decoder: 'The application side check is always on when strict schema is set. The engine side grammar was refuted on the pinned engine and is kept as a named experiment.',
    stream: 'Streams the turn to the window as the engine writes it. A scored run keeps this off, because a scored run does not care when the text arrived.',
    scripted: 'Answers from a fixed script with no engine and no network. The record, the events and the provenance all say scripted, so a screenshot of one cannot be read as a model result.'
  };

  function bool(ctx, key, label, hint) {
    const box = dom.el('input', { type: 'checkbox', checked: ctx.settings.run[key] ? 'checked' : null });
    box.addEventListener('change', () => ctx.save({ run: { ...ctx.settings.run, [key]: box.checked } }));

    return dom.el('label', { class: 'task' }, [box, dom.el('span', {}, [
      dom.el('b', { text: label }),
      hint ? dom.el('span', { class: 'muted', text: ` ${hint}` }) : null
    ])]);
  }

  function text(ctx, path, label, hint, type) {
    const value = path.reduce((carry, key) => carry[key], ctx.settings);
    const input = dom.el('input', { type: type || 'text', value: String(value === undefined || value === null ? '' : value) });
    input.addEventListener('change', () => {
      const next = type === 'number' ? Number(input.value) : input.value;
      if (path.length === 1) {
        ctx.save({ [path[0]]: next });
      } else {
        ctx.save({ [path[0]]: { ...ctx.settings[path[0]], [path[1]]: next } });
      }
    });

    return dom.field(label, input, hint);
  }

  function choice(ctx, key, label, options, hint) {
    const node = dom.select(key, ctx.settings.run[key], options);
    node.addEventListener('change', () => ctx.save({ run: { ...ctx.settings.run, [key]: node.value } }));

    return dom.field(label, node, hint);
  }

  function runCard(ctx) {
    return dom.card('Run controls', { right: 'saved on change' }, [
      dom.el('div', { class: 'grid grid-2' }, [
        choice(ctx, 'tool_mode', 'Tool mode', [
          { value: 'prompt', label: 'prompt' },
          { value: 'native', label: 'native' }
        ], TEXT.tool_mode),
        choice(ctx, 'decoder', 'Engine side decoder', [
          { value: 'none', label: 'none, application side only' },
          { value: 'grammar', label: 'grammar, refuted on the pinned engine' },
          { value: 'schema', label: 'response format' }
        ], TEXT.decoder),
        choice(ctx, 'decoder_scope', 'Decoder scope', [
          { value: 'local', label: 'local engine only' },
          { value: 'both', label: 'both endpoints' }
        ], null),
        choice(ctx, 'sandbox', 'Sandbox policy', [
          { value: 'documented', label: 'documented' },
          { value: 'open', label: 'open' }
        ], 'Documented refuses what the policy document refuses, including the arguments that turn an allowed utility into a shell.')
      ]),
      dom.el('div', { class: 'grid grid-3' }, [
        bool(ctx, 'guard', 'Loop guard', 'refuses a call that already failed the allowed number of times'),
        bool(ctx, 'strict_schema', 'Strict schema', 'refuses a turn that is not a declared action object'),
        bool(ctx, 'stream', 'Stream the turn', TEXT.stream),
        bool(ctx, 'scripted', 'Scripted, no model', TEXT.scripted)
      ]),
      dom.el('div', { class: 'grid grid-3' }, [
        text(ctx, ['run', 'guard_repeat_limit'], 'Guard repeat limit', 'the third identical failed call is refused', 'number'),
        text(ctx, ['run', 'temperature'], 'Temperature', null, 'number'),
        text(ctx, ['run', 'max_tokens'], 'Max tokens per turn', null, 'number'),
        text(ctx, ['run', 'timeout'], 'Turn timeout, seconds', null, 'number'),
        text(ctx, ['run', 'limit'], 'Task limit, zero for all', null, 'number')
      ])
    ]);
  }

  function machineCard(ctx) {
    return UI.disclosure.card('settings.machine', {
      title: 'Machine',
      summary: ctx.info.runtime,
      open: false
    }, [
      dom.el('div', { class: 'grid grid-2' }, [
        text(ctx, ['engineUrl'], 'Engine endpoint, host runtime', TEXT.engineUrl),
        text(ctx, ['containerEngineUrl'], 'Engine endpoint, container runtime', 'The service name and in network port. The published port is bound to the host loopback and is not reachable from inside the compose network.'),
        text(ctx, ['harnessRoot'], 'Harness root', null),
        text(ctx, ['phpBin'], 'PHP binary, local runtime', null),
        text(ctx, ['dockerBin'], 'Docker binary, container runtime', null),
        text(ctx, ['composeFile'], 'Compose file', null),
        text(ctx, ['workspaceRoot'], 'Workspace override', 'empty uses the harness workspace'),
        dom.field('Runtime', (() => {
          const node = dom.select('runtime', ctx.settings.runtime, [
            { value: 'container', label: 'container' },
            { value: 'local', label: 'local' }
          ]);
          node.addEventListener('change', () => ctx.save({ runtime: node.value }));

          return node;
        })(), TEXT.runtime)
      ]),
      dom.keyValue([
        ['results directory', ctx.info.resultsRoot],
        ['settings file', ctx.info.settingsFile],
        ['electron', ctx.info.electron],
        ['chrome', ctx.info.chrome],
        ['node', ctx.info.node]
      ])
    ]);
  }

  function lockedCard(state) {
    const locked = state.settings && state.settings.locked ? state.settings.locked : {};
    const rows = Object.entries(locked)
      .filter(([key]) => key !== 'task_catalogue' && key !== 'tools');

    return UI.disclosure.card('settings.locked', {
      title: 'In force, not settable here',
      summary: 'constants of the running harness',
      open: false
    }, [
      rows.length === 0
        ? dom.el('p', { class: 'muted', text: 'The locked values arrive with the first run, because the harness is what knows them.' })
        : dom.table([
            { label: 'value', value: (row) => row[0] },
            { label: 'in force', value: (row) => format.clip(String(row[1].value), 80) },
            { label: 'source', value: (row) => row[1].source }
          ], rows),
      dom.el('p', {
        class: 'muted',
        text: 'A value that is in force is on screen and a value that is on screen is in force. These are read from the process that will run, not typed into the window.'
      })
    ]);
  }

  function engineCard(ctx) {
    const status = ctx.engine || { ok: false, body: 'not checked' };

    return UI.disclosure.card('settings.engine', {
      title: 'Engine',
      summary: status.ok ? 'answering' : 'not answering',
      open: false
    }, [
      dom.keyValue([
        ['url', status.url || ctx.settings.engineUrl],
        ['status', status.status === 0 ? 'no response' : status.status],
        ['latency', `${status.latency_ms} ms`],
        ['answer', format.clip(status.body, 200)]
      ]),
      dom.el('div', { class: 'topbar-actions' }, [
        dom.button('Check the engine', {}, () => ctx.checkEngine()),
        dom.button('Run the harness check', {}, () => ctx.selfCheck())
      ])
    ]);
  }

  function render(ctx) {
    const state = ctx.store.state;
    const body = dom.el('div', { class: 'grid' }, [
      runCard(ctx),
      dom.el('div', { class: 'grid grid-2' }, [machineCard(ctx), engineCard(ctx)]),
      lockedCard(state)
    ]);

    return {
      title: 'Settings',
      crumb: 'what the record will carry',
      actions: [dom.button('Start the run', { variant: 'primary', disabled: state.live }, () => ctx.startRun())],
      body
    };
  }

  UI.views = UI.views || {};
  UI.views.settings = { id: 'settings', label: 'Settings', render };
}(window.AgentUI));
