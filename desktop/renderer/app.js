'use strict';

/**
 * The shell: navigation, the one subscription, and the frame that renders.
 *
 * Two rules are enforced here rather than trusted to the views. The first is
 * that a screen is rebuilt from the store and never mutated in place, so a
 * refresh has nothing to do. The second is that a burst of events costs one
 * frame rather than one render each: a streamed turn arrives as dozens of
 * fragments, and redrawing the transcript for every fragment would spend the
 * window's whole budget on layout.
 */

(function (UI) {
  const dom = UI.dom;
  const bridge = window.agentBridge;

  const bridgeCtx = {
    store: UI.store.create(),
    settings: null,
    info: null,
    history: [],
    engine: null,
    staged: null,
    note(message) {
      appendLog(message);
    }
  };

  let active = 'workspace';
  let dirty = false;

  function appendLog(message) {
    const log = document.getElementById('harness-log');
    const line = dom.el('div', { text: message });
    log.appendChild(line);
    while (log.childElementCount > 120) {
      log.removeChild(log.firstChild);
    }
    log.scrollTop = log.scrollHeight;
  }

  function schedule() {
    if (dirty) {
      return;
    }
    dirty = true;
    window.requestAnimationFrame(() => {
      dirty = false;
      render();
    });
  }

  function render() {
    const screen = document.getElementById('screen');
    const view = (UI.views[active] || UI.views.workspace);
    const out = view.render(bridgeCtx);
    document.getElementById('title').textContent = out.title;
    document.getElementById('crumb').textContent = out.crumb;
    dom.replace(document.getElementById('actions'), out.actions || []);
    dom.replace(screen, out.body);
    renderNav();
    renderCounters();
  }

  function renderNav() {
    const state = bridgeCtx.store.state;
    const counts = {
      workspace: bridgeCtx.settings && bridgeCtx.settings.stagedProject ? 1 : 0,
      run: state.turns.length,
      result: state.verifications.length,
      settings: state.settings ? Object.keys(state.settings.settable || {}).length : 0,
      history: bridgeCtx.history.length
    };
    const views = Object.values(UI.views);
    const nav = document.getElementById('nav');
    dom.replace(nav, views.map((view) => dom.el('button', {
      type: 'button',
      'aria-current': view.id === active ? 'true' : 'false',
      onclick: () => {
        active = view.id;
        render();
      }
    }, [
      dom.el('span', { text: view.label }),
      dom.el('span', { class: 'count', text: String(counts[view.id] === undefined ? '' : counts[view.id]) })
    ])));
    const heading = document.getElementById('nav-count');
    if (heading !== null) {
      heading.textContent = `${views.length} screens`;
    }
  }

  function renderCounters() {
    const state = bridgeCtx.store.state;
    const totals = bridgeCtx.store.counters();
    const strip = document.getElementById('counterstrip');
    const nodes = [
      dom.el('div', { class: 'counter' }, [dom.el('span', { text: 'turns' }), dom.el('b', { text: `${state.turns.length}` })]),
      dom.el('div', { class: 'counter' }, [dom.el('span', { text: 'fragments' }), dom.el('b', { text: `${state.deltas}` })])
    ];

    for (const [key, label] of UI.store.COUNTERS) {
      const value = totals[key] || 0;
      const serious = (key === 'guard_refusals' || key === 'guard_interventions' || key === 'schema_rejections' || key === 'repeated_failed_turns') && value > 0;
      const clean = key === 'recovered' && value > 0;
      nodes.push(dom.el('div', {
        class: 'counter',
        dataset: { serious: serious ? 'true' : 'false', clean: clean ? 'true' : 'false' }
      }, [dom.el('span', { text: label }), dom.el('b', { text: `${value}` })]));
    }

    nodes.push(dom.el('div', { class: 'counter' }, [
      dom.el('span', { text: 'malformed' }),
      dom.el('b', { text: `${state.malformed}` })
    ]));

    dom.replace(strip, nodes);
  }

  /** The context the views are given: bridge, store, settings and the actions. */
  Object.assign(bridgeCtx, {
    bridge,
    go(screen) {
      active = screen;
      render();
    },
    note: appendLog,
    save(patch) {
      bridge.settings.set(patch).then((next) => {
        bridgeCtx.settings = next;
        schedule();
      });
    },
    setStaged(result) {
      bridgeCtx.staged = result;
      schedule();
    },
    startRun() {
      bridge.run.start(null).then((started) => {
        if (!started.started) {
          appendLog(`run not started: ${started.reason}`);
          return;
        }
        bridgeCtx.store.reset({ runId: started.run_id, transcript: started.transcript, live: true });
        active = 'run';
        appendLog(`run ${started.run_id} started from ${started.transcript}`);
        render();
      });
    },
    cancelRun() {
      bridge.run.cancel().then((result) => appendLog(result.cancelled ? 'stop requested' : 'nothing to stop'));
    },
    reloadHistory() {
      bridge.runs.list().then((entries) => {
        bridgeCtx.history = entries;
        schedule();
      });
    },
    openRun(entry) {
      bridge.runs.replay(entry.dir).then((replayed) => {
        bridgeCtx.store.load(replayed.events, replayed.record);
        active = 'run';
        appendLog(`replayed ${entry.run_id}: ${replayed.events.length} event(s), ${replayed.errors.length} malformed`);
        render();
      });
    },
    checkEngine() {
      return bridge.engine().then((status) => {
        bridgeCtx.engine = status;
        const dot = document.querySelector('#engine-status .dot');
        dot.className = `dot ${status.ok ? 'dot-ok' : 'dot-bad'}`;
        document.getElementById('engine-label').textContent = status.ok ? `engine ok ${status.latency_ms} ms` : `engine down`;
        schedule();

        return status;
      });
    },
    /**
     * Ask the harness what it can be set to, before any turn is spent.
     *
     * The catalogue, the locked constants and the control description all arrive
     * in the run.started event of a describe, so the settings and workspace
     * screens are complete before the first run. A describe that fails is not
     * fatal: the screens say the catalogue is missing and the run still works,
     * because the run does not depend on the window having read anything.
     */
    describe() {
      return bridge.describe().then((result) => {
        if (result.ok && result.event) {
          bridgeCtx.store.apply(result.event);
          appendLog(`describe: ${Object.keys((result.event.settings || {}).settable || {}).length} settable value(s) read from the harness`);

          return result;
        }
        appendLog(`describe failed: ${(result.errors && result.errors[0]) || `exit ${result.code}`}`);

        return result;
      });
    },
    selfCheck() {
      appendLog('harness check started');
      active = 'settings';
      render();

      return bridge.selfCheck().then((result) => {
        appendLog(`harness check ${result.passed ? 'passed' : 'failed'} (exit ${result.code})`);
        appendLog('the check writes its own lines above and its full log to the terminal it was started from');
        schedule();

        return result;
      });
    }
  });

  bridge.onEvent((event) => {
    bridgeCtx.store.apply(event);
    schedule();
  });
  bridge.onLog((line) => appendLog(line));
  bridge.onSettingsChanged((next) => {
    bridgeCtx.settings = next;
    schedule();
  });
  bridge.onRunStarted((payload) => {
    bridgeCtx.store.reset({ runId: payload.run_id, transcript: payload.transcript, live: true });
    active = 'run';
    render();
  });
  bridge.onRunEnded((payload) => {
    bridgeCtx.store.end(payload);
    appendLog(`run ${payload.run_id} exited ${payload.code === null ? `on signal ${payload.signal}` : `with code ${payload.code}`} after ${payload.duration_s}s`);
    for (const error of payload.errors || []) {
      appendLog(`stream defect: ${error}`);
    }
    bridgeCtx.reloadHistory();
    schedule();
  });

  bridgeCtx.store.subscribe(() => schedule());

  /**
   * What the layout actually did, read from the painted document.
   *
   * A window asked to fit a tablet can fail in ways no build step catches: the
   * rail can fold and the side menu can disappear, the ground under the counters
   * can be the ink it started as, and a card can be wide enough to push the page
   * sideways. Each of those is a property of a rendered box, so each is read
   * from a rendered box rather than from the stylesheet that asked for it.
   */
  function readLayout() {
    const rail = document.getElementById('rail');
    const stage = document.getElementById('stage');
    const nav = document.getElementById('nav');
    const strip = document.getElementById('counterstrip');
    const screen = document.getElementById('screen');
    const railBox = rail.getBoundingClientRect();
    const stageBox = stage.getBoundingClientRect();
    const rows = new Set(Array.from(strip.children).map((node) => Math.round(node.getBoundingClientRect().top)));
    const stripBox = strip.getBoundingClientRect();

    return {
      railWidth: Math.round(railBox.width),
      railHeight: Math.round(railBox.height),
      stageWidth: Math.round(stageBox.width),
      navOnSide: railBox.right <= stageBox.left + 1 && railBox.height >= stageBox.height - 1,
      navDirection: window.getComputedStyle(nav).flexDirection,
      navScrolls: nav.scrollHeight > nav.clientHeight,
      counterGround: window.getComputedStyle(strip).backgroundColor,
      counterRows: rows.size,
      // The strip is the bottom edge of the window, so whether it is inside the
      // frame is the reading that says the layout fits rather than merely wraps.
      counterInView: stripBox.bottom <= window.innerHeight + 1 && stripBox.top >= 0,
      pageScrollsSideways: document.documentElement.scrollWidth > window.innerWidth,
      pageScrollsVertically: document.documentElement.scrollHeight > window.innerHeight + 1,
      screenScrollsSideways: screen.scrollWidth > screen.clientWidth
    };
  }

  /**
   * The seam a check drives the window through.
   *
   * A smoke check and a load measurement both need to start a run and read what
   * the window made of it, without a human clicking. Exposing four calls is
   * cheaper and more honest than a second entry point that renders a different
   * application, because the thing being measured stays the thing users run.
   *
   * Folding is part of that seam rather than beside it. A fold that is drawn but
   * not exercised is a fold nobody has watched work, and the readings below let
   * a check put every fold on a screen into both states and read the frame each
   * way, which is how a body that keeps its height while claiming to be hidden
   * is caught rather than shipped.
   */
  window.__agentUi = {
    go: (screen) => bridgeCtx.go(screen),
    startRun: () => bridgeCtx.startRun(),
    cancelRun: () => bridgeCtx.cancelRun(),
    describe: () => bridgeCtx.describe(),
    /** What the folds on the current screen are, with the height each one has. */
    disclosures: () => UI.disclosure.reading(document.getElementById('screen')),
    /** Fold or unfold one drawn fold by its key, so a single card can be measured. */
    setDisclosure: (key, open) => {
      const node = document.querySelector(`#screen .disclosure[data-key="${key}"]`);
      if (node === null) {
        return false;
      }
      UI.disclosure.apply(node, open === true);

      return true;
    },
    /** Fold or unfold every fold on the current screen at once. */
    setDisclosures: (open) => {
      const nodes = Array.from(document.querySelectorAll('#screen .disclosure'));
      for (const node of nodes) {
        UI.disclosure.apply(node, open === true);
      }

      return nodes.length;
    },
    /** What the window currently shows, as a reading rather than an opinion. */
    summary() {
      const state = bridgeCtx.store.state;
      return {
        ready: true,
        title: document.getElementById('title').textContent,
        screen: active,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        layout: readLayout(),
        navButtons: document.querySelectorAll('#nav button').length,
        actions: document.getElementById('actions').childElementCount,
        screenSections: document.getElementById('screen').querySelectorAll('section.card').length,
        disclosures: UI.disclosure.reading(document.getElementById('screen')),
        counters: document.getElementById('counterstrip').childElementCount,
        turns: state.turns.length,
        deltas: state.deltas,
        controlsFired: state.controlsFired.length,
        verifications: state.verifications.length,
        malformed: state.malformed,
        logLines: document.getElementById('harness-log').childElementCount,
        transcript: state.transcript,
        runId: state.runId,
        catalogue: (() => {
          const locked = state.settings && state.settings.locked ? state.settings.locked : {};
          return locked.task_catalogue && Array.isArray(locked.task_catalogue.value) ? locked.task_catalogue.value.length : 0;
        })()
      };
    }
  };

  Promise.all([bridge.info(), bridge.settings.get()]).then(([info, settings]) => {
    bridgeCtx.info = info;
    bridgeCtx.settings = settings;
    document.getElementById('brand-runtime').textContent = `${info.runtime} runtime, ${info.harnessRoot.split('/').pop()}`;
    renderNav();
    render();
    bridgeCtx.checkEngine();
    bridgeCtx.reloadHistory();
    bridgeCtx.describe();
  });
}(window.AgentUI));
