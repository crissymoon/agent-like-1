'use strict';

/**
 * The workspace screen: what is being worked on, and what a run will do.
 *
 * The screen refuses to imply a capability the harness does not have. A dropped
 * repository is staged into the workspace the agent runs against, and the run
 * that follows is the task suite running inside that directory, not a
 * repository-specific task plan: per project task generation is stage S3 of the
 * plan and is not built. Saying so here, next to the button, is the difference
 * between a limitation and a surprise.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const dom = UI.dom;
  const format = UI.format;

  const CATALOGUE_FALLBACK = [];

  function staged(ctx) {
    return ctx.settings.stagedProject
      ? { slug: ctx.settings.stagedProject, staged: ctx.staged || null }
      : null;
  }

  function catalogue(ctx) {
    const locked = ctx.store.state.settings && ctx.store.state.settings.locked;
    const entry = locked && locked.task_catalogue;
    if (entry && Array.isArray(entry.value)) {
      return entry.value;
    }

    return CATALOGUE_FALLBACK;
  }

  function taskPicker(ctx) {
    const known = catalogue(ctx);
    if (known.length === 0) {
      return dom.el('p', {
        class: 'muted',
        text: 'The task catalogue arrives with the first run. Start the harness check to see it before a run spends a turn.'
      });
    }

    const selected = new Set((ctx.settings.run.tasks || []).length > 0 ? ctx.settings.run.tasks : known.map((task) => task.id));
    const grid = dom.el('div', { class: 'tasks' }, known.map((task) => {
      const box = dom.el('input', {
        type: 'checkbox',
        checked: selected.has(task.id) ? 'checked' : null
      });
      box.addEventListener('change', () => {
        const next = new Set(ctx.settings.run.tasks || []);
        if (box.checked) {
          next.add(task.id);
        } else {
          next.delete(task.id);
        }
        ctx.save({ run: { ...ctx.settings.run, tasks: Array.from(next) } });
      });

      return dom.el('label', { class: 'task' }, [
        box,
        dom.el('span', {}, [
          dom.el('b', { text: task.id }),
          dom.el('span', { class: 'muted', text: ` ${task.capability}, budget ${task.budget}` })
        ])
      ]);
    }));

    return grid;
  }

  function dropZone(ctx) {
    const zone = dom.el('div', { class: 'drop' }, [
      dom.el('h3', { text: 'Drop a project here' }),
      dom.el('p', {
        class: 'muted',
        text: 'It is copied into the workspace the agent runs against. Nothing outside the workspace is touched.'
      }),
      dom.button('Choose a directory', { variant: 'primary' }, async () => {
        const picked = await ctx.bridge.project.pick();
        if (picked) {
          await stage(ctx, picked);
        }
      })
    ]);

    zone.addEventListener('dragover', (event) => {
      event.preventDefault();
      zone.dataset.active = 'true';
    });
    zone.addEventListener('dragleave', () => {
      zone.dataset.active = 'false';
    });
    zone.addEventListener('drop', async (event) => {
      event.preventDefault();
      zone.dataset.active = 'false';
      const files = Array.from(event.dataTransfer.files || []);
      if (files.length === 0) {
        ctx.note('the drop carried no readable path');
        return;
      }
      const target = ctx.bridge.pathFor(files[0]);
      if (!target) {
        ctx.note('the dropped item has no path on this machine');
        return;
      }
      await stage(ctx, target);
    });

    return zone;
  }

  async function stage(ctx, target) {
    ctx.note(`staging ${target}`);
    const result = await ctx.bridge.project.stage(target);
    ctx.setStaged(result);
    if (!result.ok) {
      ctx.note(`staging failed: ${result.error}`);
      return;
    }
    ctx.note(`staged ${result.slug}: ${result.files_and_directories} entries, ${format.bytes(result.inventory.bytes)}`);
  }

  function stagedCard(ctx) {
    const current = staged(ctx);
    if (current === null) {
      return dom.card('Staged project', { right: 'none' }, [
        dom.el('p', { class: 'muted', text: 'No project is staged, so a run uses the workspace itself.' })
      ]);
    }

    const inventory = current.staged && current.staged.inventory ? current.staged.inventory : null;

    return dom.card('Staged project', { right: current.staged && current.staged.ok ? 'staged' : 'not staged' }, [
      dom.keyValue([
        ['name', current.slug],
        ['workspace', current.staged ? current.staged.workspace : ctx.info.workspace],
        ['files', inventory ? format.integer(inventory.files) : null],
        ['directories', inventory ? format.integer(inventory.directories) : null],
        ['bytes', inventory ? format.bytes(inventory.bytes) : null],
        ['count truncated', inventory ? String(inventory.truncated) : null]
      ]),
      dom.el('p', {
        class: 'muted',
        text: 'The run below is the task suite inside this directory. A task plan written for an arbitrary repository is stage S3 and is not built.'
      })
    ]);
  }

  function render(ctx) {
    const state = ctx.store.state;
    const body = dom.el('div', { class: 'grid' }, [
      dropZone(ctx),
      dom.el('div', { class: 'grid grid-2' }, [
        stagedCard(ctx),
        dom.card('This run', { right: `${ctx.settings.runtime} runtime` }, [
          dom.keyValue([
            ['engine', ctx.settings.engineUrl],
            ['tool mode', ctx.settings.run.tool_mode],
            ['loop guard', String(ctx.settings.run.guard)],
            ['strict schema', String(ctx.settings.run.strict_schema)],
            ['decoder', `${ctx.settings.run.decoder} (${ctx.settings.run.decoder_scope})`],
            ['turn streamed', String(ctx.settings.run.stream)],
            ['scripted', ctx.settings.run.scripted ? 'yes, no model is called' : 'no, a real engine is called'],
            ['workspace', state.tasks.length > 0 ? 'from the last run' : ctx.info.workspace]
          ]),
          dom.el('p', {
            class: 'muted',
            text: 'Every value here is adjusted on the settings screen and recorded in the run record.'
          })
        ])
      ]),
      dom.card('Tasks', { right: `${catalogue(ctx).length} in the catalogue` }, [taskPicker(ctx)]),
      dom.card('Capability, held out', { paper: true }, [
        dom.el('p', {
          class: 'muted',
          text: 'At least one task per capability is held out of any training data, so an improvement cannot be the suite memorised. The held out count is reported by the comparison, not chosen after a run.'
        })
      ])
    ]);

    return {
      title: 'Workspace',
      crumb: 'workspace',
      actions: [
        dom.button('Start the run', { variant: 'primary', disabled: state.live }, () => ctx.startRun()),
        dom.button('Harness check', {}, () => ctx.selfCheck())
      ],
      body
    };
  }

  UI.views = UI.views || {};
  UI.views.workspace = { id: 'workspace', label: 'Workspace', render };
}(window.AgentUI));
