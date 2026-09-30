'use strict';

/**
 * The history screen: every run the harness left on disk.
 *
 * The list is read from the results directory rather than from anything the
 * application remembers, so a run that was started from a terminal appears here
 * beside one started from the window. A window that could only show its own runs
 * would be a window whose history is a claim rather than a record.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const dom = UI.dom;
  const format = UI.format;

  function render(ctx) {
    const entries = ctx.history || [];
    const body = dom.el('div', { class: 'grid' }, [
      dom.card('Runs on disk', { right: ctx.info.resultsRoot }, [
        entries.length === 0
          ? dom.el('p', { class: 'muted', text: 'No run has written a transcript under this results directory yet.' })
          : dom.el('div', { class: 'rows' }, entries.map((entry) => row(ctx, entry)))
      ]),
      dom.card('Why a replay is not a re-run', { paper: true }, [
        dom.el('p', {
          class: 'muted',
          text: 'Opening a run replays its events into the same projection a live run uses. Nothing is executed, no engine is contacted and no file is written, so the screen shows the run that happened rather than a second run that might differ.'
        })
      ])
    ]);

    return {
      title: 'History',
      crumb: `${entries.length} run(s)`,
      actions: [dom.button('Reload the list', {}, () => ctx.reloadHistory())],
      body
    };
  }

  function row(ctx, entry) {
    const button = dom.el('button', {
      class: 'row',
      type: 'button',
      'aria-current': ctx.store.state.transcript === entry.transcript ? 'true' : 'false'
    }, [
      dom.el('span', { class: 'mono', text: entry.run_id }),
      dom.el('span', { class: 'muted mono', text: `${format.when(entry.modified)}  ${format.seconds(entry.duration_s)}  ${entry.tasks_passed === null ? 'no record' : `${entry.tasks_passed}/${entry.tasks} pass`}  ${format.bytes(entry.bytes)}` })
    ]);
    button.addEventListener('click', () => ctx.openRun(entry));

    return button;
  }

  UI.views = UI.views || {};
  UI.views.history = { id: 'history', label: 'History', render };
}(window.AgentUI));
