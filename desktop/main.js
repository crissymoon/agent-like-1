'use strict';

/**
 * The main process: it owns the child process, the filesystem and the window.
 *
 * The process model is the plain one. This file starts the harness, reads its
 * events, and hands them to the renderer over one channel. The renderer holds
 * the view and no privileges: it cannot start a process, open a file or read a
 * path, and it reaches the main process only through the four calls the preload
 * script exposes. A dropped repository is staged by this process, never by the
 * window, which is what keeps the intake path from being a path traversal the
 * window could be talked into.
 */

const { app, BrowserWindow, ipcMain, dialog, webContents } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');

const paths = require('./lib/paths');
const settingsStore = require('./lib/settings');
const runs = require('./lib/runs');
const intake = require('./lib/intake');
const { Runner } = require('./lib/runner');

let settings = null;
let settingsFile = '';
let mainWindow = null;
let runner = null;
let currentRun = null;

/**
 * Flags that make the window measurable rather than only usable.
 *
 * `--smoke` renders the application, reads what the window made of it, prints it
 * as one line and exits. `--smoke-run` starts a run from the window itself, so a
 * measurement covers the interface driving a run rather than a run beside an
 * idle interface. `--smoke-hold=<seconds>` keeps the window open while a run is
 * in progress, which is what a load measurement samples against.
 * `--smoke-screens` walks every screen the window declares and reads each one.
 */
const FLAGS = process.argv.slice(1).filter((argument) => argument.startsWith('--'));
const SMOKE = FLAGS.some((argument) => argument === '--smoke' || argument.startsWith('--smoke-'));
const SMOKE_RUN = FLAGS.includes('--smoke-run');
/**
 * Visit every screen and read it, twice: once as the window opens and once after
 * the run, because a screen that renders empty and a screen that renders filled
 * are two different pieces of evidence and only the second one says the view
 * works. Without this the smoke path exercised two screens out of five, so a
 * broken settings screen, a broken history screen or a broken result screen
 * would have passed the check by never being opened.
 */
const SMOKE_SCREENS = FLAGS.includes('--smoke-screens');
/**
 * Replay the newest run on disk from the history screen.
 *
 * This is the seam for the one property a live run cannot demonstrate: that a
 * window killed in the middle of a run reopens in the same state. The check
 * kills this application mid run, and a second launch with this flag opens the
 * history screen, presses the first row and reads what the window made of the
 * transcript that was left behind. Pressing the row rather than calling the
 * replay directly is deliberate: the button is what a person presses.
 */
const SMOKE_REPLAY = FLAGS.includes('--smoke-replay');
const HOLD_SECONDS = (() => {
  const flag = FLAGS.find((argument) => argument.startsWith('--smoke-hold='));
  const value = flag === undefined ? 0 : Number(flag.split('=')[1]);

  return Number.isFinite(value) && value > 0 ? value : 0;
})();

function smoke(line, payload) {
  process.stdout.write(`SMOKE ${line} ${JSON.stringify(payload)}\n`);
}

function send(channel, payload) {
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function runId(seed) {
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  const tail = seed && seed !== '' ? `-${intake.slugify(seed)}` : '';

  return `ui-${stamp}${tail}`;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 560,
    backgroundColor: '#F0F9FF',
    title: 'Agent-Like',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false
    }
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  if (SMOKE) {
    mainWindow.webContents.once('did-finish-load', async () => {
      // Give the window one round of the frame loop to paint its first screen,
      // because a reading taken before the first render would describe an empty
      // document rather than a broken one.
      await new Promise((resolve) => setTimeout(resolve, 1200));
      smoke('opened', await readWindow());
      if (SMOKE_SCREENS) {
        smoke('screens-empty', await readScreens());
      }
      if (SMOKE_RUN) {
        await mainWindow.webContents.executeJavaScript('window.__agentUi.startRun()').catch((error) => smoke('run-error', { error: error.message }));
        smoke('started', { run_id: currentRun === null ? null : currentRun.run_id });
      }
      if (HOLD_SECONDS > 0) {
        const until = Date.now() + HOLD_SECONDS * 1000;
        while (Date.now() < until) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          smoke('hold', await readWindow());
        }
      }
      smoke('final', await readWindow());
      if (SMOKE_SCREENS) {
        smoke('screens-filled', await readScreens());
      }
      if (SMOKE_REPLAY) {
        smoke('replay', await replayNewest());
      }
      app.quit();
    });
  }
}

/** What the window made of the events it received. */
async function readWindow() {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return { ready: false };
  }

  return mainWindow.webContents.executeJavaScript('window.__agentUi ? window.__agentUi.summary() : { ready: false }')
    .catch((error) => ({ ready: false, error: error.message }));
}

/**
 * Read every screen the window declares, by asking the window for its own list.
 *
 * The list comes from the renderer rather than from a literal here, so a screen
 * that is added and not reachable is a reading this check reports rather than a
 * fact a reader has to notice. A failure on one screen is recorded against that
 * screen and the visit continues, because a check that stops at the first
 * defect tells you about one screen and hides the rest.
 */
async function readScreens() {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return [];
  }

  const ids = await mainWindow.webContents
    .executeJavaScript('Object.keys(window.AgentUI.views)')
    .catch(() => []);
  const out = [];
  for (const id of ids) {
    const moved = await mainWindow.webContents
      .executeJavaScript(`window.__agentUi.go(${JSON.stringify(id)})`)
      .then(() => true)
      .catch((error) => error.message);
    if (moved !== true) {
      out.push({ screen: id, error: moved });
      continue;
    }
    // One frame is requested per render, and the reading is taken after it, so
    // the figures describe a painted screen rather than a scheduled one.
    await new Promise((resolve) => setTimeout(resolve, 250));
    out.push(await readWindow());
  }

  return out;
}

/**
 * Open the newest run on disk through the history screen and read the result.
 *
 * The row is found by its own class inside the screen the window has just
 * rendered, so this drives the same handler a click drives. Describe runs are
 * skipped: asking the harness what it can be set to writes a transcript of its
 * own, and those are newer than the run under test while containing no task, so
 * pressing the first row would have replayed a settings read and called it a
 * recovery. It reports how many rows the list had, because a replay of the only
 * run on disk and a replay of one of forty are different readings.
 */
async function replayNewest() {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return { opened: false };
  }

  const pressed = await mainWindow.webContents.executeJavaScript(`(() => {
    window.__agentUi.go('history');
    const rows = Array.from(document.querySelectorAll('#screen button.row'));
    const chosen = rows.find((row) => !/-describe\\s*$/.test(row.textContent.trim()));
    if (chosen === undefined) { return { opened: false, rows: rows.length }; }
    chosen.click();
    return { opened: true, rows: rows.length, run: chosen.textContent.trim() };
  })()`).catch((error) => ({ opened: false, error: error.message }));

  // A replay reads a file and rebuilds the projection, so the read is taken
  // after the frame that the replay schedules rather than before it.
  await new Promise((resolve) => setTimeout(resolve, 600));

  return { ...pressed, summary: await readWindow() };
}

app.whenReady().then(() => {
  const loaded = settingsStore.load(app.getPath('userData'));
  settings = loaded.settings;
  settingsFile = loaded.file;
  settings.userData = app.getPath('userData');
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (runner !== null) {
    runner.cancel();
  }
  app.quit();
});

ipcMain.handle('app:info', () => {
  const derived = paths.derive(settings);
  return {
    version: app.getVersion(),
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    harnessRoot: derived.harnessRoot,
    resultsRoot: derived.resultsRoot,
    settingsFile,
    workspace: settings.runtime === 'container' ? derived.containerWorkspace : derived.localWorkspace,
    runtime: settings.runtime,
    eventTypes: require('./lib/events').TYPES
  };
});

ipcMain.handle('settings:get', () => settings);

ipcMain.handle('settings:set', (event, patch) => {
  const next = { ...settings, ...(patch || {}) };
  if (patch && patch.run) {
    next.run = { ...settings.run, ...patch.run };
  }
  settings = next;
  settingsStore.save(app.getPath('userData'), settings);
  send('settings:changed', settings);

  return settings;
});

ipcMain.handle('runs:list', () => runs.list(paths.derive(settings).resultsRoot));

ipcMain.handle('runs:replay', (event, runDirectory) => {
  const transcript = path.join(runDirectory, 'events.ndjson');
  const read = runs.replay(transcript);

  return { events: read.events, errors: read.errors, record: runs.readJson(`${transcript}.result.json`) };
});

ipcMain.handle('engine:status', async () => {
  const url = `${settings.engineUrl.replace(/\/$/, '')}/health`;
  const started = Date.now();
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(4000) });
    const body = await response.text();
    return { ok: response.ok, status: response.status, latency_ms: Date.now() - started, body: body.slice(0, 400), url };
  } catch (error) {
    return { ok: false, status: 0, latency_ms: Date.now() - started, body: error.message, url };
  }
});

/**
 * The harness's own check, run in the runtime the window is configured for.
 *
 * It is offered here because the first thing a reader should be able to do with
 * this application is confirm that the thing behind it is sound, and because a
 * check that can only be run from a terminal is a check the window's users will
 * not run.
 */
ipcMain.handle('harness:selfcheck', () => new Promise((resolve) => {
  const derived = paths.derive(settings);
  const argv = settings.runtime === 'container'
    ? [
        settings.dockerBin, 'compose', '-f', derived.composeFile,
        'run', '--rm', '-T', '-e', 'HARNESS_BOUNDARY=require',
        '--entrypoint', 'php', 'agent', '/opt/harness/agent.php', '--self-check'
      ]
    : [settings.phpBin, derived.agentEntry, '--self-check'];
  const child = spawn(argv[0], argv.slice(1), { cwd: derived.harnessRoot, env: { ...process.env } });
  let out = '';
  child.stdout.on('data', (chunk) => {
    out += chunk;
    const lines = out.split('\n');
    out = lines.pop();
    for (const line of lines) {
      if (line.trim() !== '') {
        send('harness:line', line.trim());
      }
    }
  });
  child.stderr.on('data', (chunk) => send('harness:line', String(chunk).trim()));
  child.on('close', (code) => resolve({ code, passed: code === 0 }));
  child.on('error', (error) => resolve({ code: null, passed: false, error: error.message }));
}));

/**
 * Ask the harness what it can be set to, without running a task.
 *
 * This is the only call the window makes that contacts the harness outside a
 * run, and it exists because the settings screen must show the locked values and
 * the task catalogue before the first run rather than after it. It spends no
 * turn and, in the scripted case, does not touch an engine at all.
 */
ipcMain.handle('harness:describe', () => new Promise((resolve) => {
  const derived = paths.derive(settings);
  const request = Runner.request(settings, runId('describe'), { describe: true });
  let started = null;
  const runner = new Runner(
    settings,
    (agentEvent) => {
      if (agentEvent.type === 'run.started' && started === null) {
        started = agentEvent;
      }
    },
    () => {}
  );
  runner.start(request).then((outcome) => {
    resolve({
      ok: outcome.code === 0 && started !== null,
      event: started,
      code: outcome.code,
      errors: outcome.errors
    });
  });
}));

ipcMain.handle('project:pick', async () => {
  const picked = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
  if (picked.canceled || picked.filePaths.length === 0) {
    return null;
  }

  return picked.filePaths[0];
});

ipcMain.handle('project:stage', (event, projectPath) => {
  const result = intake.stage(settings, projectPath, (line) => send('harness:line', line));
  if (result.ok) {
    settings = { ...settings, stagedProject: result.slug };
    settingsStore.save(app.getPath('userData'), settings);
  }

  return result;
});

ipcMain.handle('run:start', (event, overrides) => {
  if (runner !== null) {
    return { started: false, reason: 'a run is already in progress' };
  }
  const id = runId(settings.stagedProject || '');
  const request = Runner.request(settings, id, overrides);
  const derived = paths.derive(settings);
  currentRun = {
    run_id: id,
    request,
    runtime: settings.runtime,
    transcript: path.join(derived.resultsRoot, id, 'events.ndjson'),
    started: Date.now()
  };
  send('run:started', currentRun);

  runner = new Runner(settings, (agentEvent) => send('agent:event', agentEvent), (line) => send('harness:line', line));
  const ended = runner.start(request);
  ended.then((outcome) => {
    const directory = path.join(derived.resultsRoot, id);
    const described = fs.existsSync(path.join(directory, 'events.ndjson'))
      ? runs.describe(directory, path.join(directory, 'events.ndjson.result.json'))
      : null;
    send('run:ended', {
      run_id: id,
      code: outcome.code,
      signal: outcome.signal,
      errors: outcome.errors,
      log_tail: outcome.log.slice(-40),
      run: described,
      duration_s: Math.round((Date.now() - currentRun.started) / 100) / 10
    });
    runner = null;
  });

  return { started: true, run_id: id, request, transcript: currentRun.transcript };
});

ipcMain.handle('run:cancel', () => {
  if (runner === null) {
    return { cancelled: false };
  }

  return { cancelled: runner.cancel() };
});

ipcMain.handle('shell:reveal', (event, target) => {
  const { shell } = require('electron');
  if (typeof target === 'string' && fs.existsSync(target)) {
    shell.showItemInFolder(target);

    return true;
  }

  return false;
});

process.on('uncaughtException', (error) => {
  send('harness:line', `the interface failed: ${error.message}`);
});
