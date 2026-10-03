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

const { app, BrowserWindow, ipcMain, dialog, screen, webContents } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const { spawn } = require('node:child_process');

const paths = require('./lib/paths');
const settingsStore = require('./lib/settings');
const runs = require('./lib/runs');
const intake = require('./lib/intake');
const guard = require('./lib/guard');
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
 * The folds are exercised on the opening screen whether or not the screens are
 * walked, because the fold reading is what catches a body that keeps its height
 * while claiming to be hidden, and that defect looks like a correctly drawn card
 * in every other reading this path takes.
 */
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
/**
 * Open the window at an exact size rather than at the profile it would choose.
 *
 * A display shorter than the profile makes the window smaller, which is right
 * for a person and useless for a measurement: a check that wants to know the
 * layout fits the profile has to be able to ask for the profile on a machine
 * that cannot show it whole. Only the smoke path reads this.
 */
const SMOKE_FIT = (() => {
  const flag = FLAGS.find((argument) => argument.startsWith('--smoke-fit='));
  if (flag === undefined) {
    return null;
  }
  const match = /^(\d{3,4})x(\d{3,4})$/.exec(flag.slice('--smoke-fit='.length));

  return match === null ? null : { width: Number(match[1]), height: Number(match[2]) };
})();
/**
 * Write a picture of every screen into this directory, then exit.
 *
 * A screenshot is the one reading no check in this project takes: a layout can
 * fit, have its side menu on the side and its counter strip in the frame, and
 * still look wrong in a way only a picture shows. This is the seam for that
 * picture, and it is on the smoke path so the thing photographed is the window
 * a person runs rather than a second application built to be photographed.
 *
 * The size is the tablet profile the build declares, not the display: a
 * photograph of a window clamped to a short screen would document this machine
 * rather than the layout that ships. `--smoke-fit` still wins when it is given,
 * because a caller that asks for a size means it.
 */
const SMOKE_CAPTURE = (() => {
  const flag = FLAGS.find((argument) => argument.startsWith('--smoke-capture='));
  if (flag === undefined) {
    return null;
  }
  const directory = flag.slice('--smoke-capture='.length);

  return directory === '' ? null : directory;
})();
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

/**
 * The profile the window opens at, and the clamp that keeps it on screen.
 *
 * The interface is laid out for a seven to eight inch tablet held upright, so the
 * window asks for that profile and takes the smaller of it and the work area of
 * the display it lands on. A window larger than the screen it opens on is the
 * one sizing mistake a user cannot undo without hunting for an edge, so the
 * clamp is taken here rather than left to the window manager.
 */
const TABLET = { width: 768, height: 1024 };

function windowSize() {
  const area = screen.getPrimaryDisplay().workAreaSize;

  return {
    width: Math.min(TABLET.width, area.width),
    height: Math.min(TABLET.height, area.height)
  };
}

function createWindow() {
  // A capture is taken at the profile the layout is designed for, even on a
  // display too short to show it whole, because the picture is of the design and
  // not of the screen it happened to be taken on.
  const size = SMOKE_FIT !== null ? SMOKE_FIT : (SMOKE_CAPTURE !== null ? TABLET : windowSize());
  mainWindow = new BrowserWindow({
    width: size.width,
    height: size.height,
    minWidth: 360,
    minHeight: 480,
    center: true,
    show: SMOKE_CAPTURE === null,
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
  const entry = path.join(__dirname, 'renderer', 'index.html');
  mainWindow.loadFile(entry);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  /**
   * The window stays on the one document it was given.
   *
   * Nothing in the interface is a link, and everything the window draws is text
   * set as text, but the thing the window renders includes a model's own output
   * and a run's own record. A page that can be talked into navigating is a page
   * that can be talked into rendering somewhere this process did not choose, and
   * the file it would land on would be loaded with the preload script attached.
   * So the one document is allowed, a same-document fragment is allowed, and
   * everything else is refused rather than followed.
   */
  const home = pathToFileURL(entry).toString();
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url.split('#')[0] === home) {
      return;
    }
    event.preventDefault();
    send('harness:line', `refused navigation to ${url}`);
  });
  // Nothing opens a second window, so nothing may ask for one. A default of
  // "let the operating system decide" is how a link in a rendered transcript
  // becomes a browser window the application does not control.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    send('harness:line', `refused a window for ${url}`);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-attach-webview', (event) => {
    event.preventDefault();
    send('harness:line', 'refused a webview');
  });

  if (SMOKE) {
    mainWindow.webContents.once('did-finish-load', async () => {
      // The profile travels first, so a check can measure against the size this
      // build declares rather than against a copy of it kept beside the check.
      smoke('profile', { tablet: TABLET, applied: size, display: screen.getPrimaryDisplay().workAreaSize });
      // Give the window one round of the frame loop to paint its first screen,
      // because a reading taken before the first render would describe an empty
      // document rather than a broken one.
      await new Promise((resolve) => setTimeout(resolve, 1200));
      // The requested profile travels beside the reading, so a check can tell a
      // layout that fits the tablet from a window that was never asked for one.
      smoke('opened', { ...(await readWindow()), requested: size });
      smoke('disclosures', await disclosureTrial());
      // The picture is taken before the screens are walked, so the fold state
      // the capture records is the state the window opened in: the trial above
      // puts every fold back the way it found it, and the walk below leaves
      // whichever screen it finished on.
      if (SMOKE_CAPTURE !== null) {
        smoke('captured', await captureScreens(SMOKE_CAPTURE));
      }
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
 * Write a picture of every screen the window declares into a directory.
 *
 * The list of screens comes from the renderer, the same way the screen walk gets
 * it, so a screen that is added and not photographed is a reading this reports
 * rather than a file a reader has to notice is missing. Each picture is taken
 * after a frame has been requested and one more has been drawn, because a
 * capture issued in the same turn as a navigation photographs the screen that
 * was there before it.
 *
 * The viewport is emulated at the profile rather than inherited from the window.
 * A window is clamped to the display it opens on: on a screen shorter than the
 * profile it cannot be grown past the work area, by its own options or by a
 * later resize, so a picture taken through a window that is on screen is a
 * picture of this machine with the bottom of the layout cut away, and the bottom
 * of this layout is the counter strip.
 *
 * The window is therefore never shown for a capture. A window that is not on
 * screen is not constrained to it, so it can be given the profile in full; the
 * cost is that the pictures are taken without a person watching them being
 * taken, and the benefit is that the same command produces the same pictures on
 * a laptop and on a large display. The viewport each picture was drawn at is
 * reported beside it, so a machine that refuses even this says so in the reading
 * rather than in a crop nobody notices.
 */
async function captureScreens(directory) {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return { saved: [], error: 'there was no window to photograph' };
  }
  try {
    fs.mkdirSync(directory, { recursive: true });
  } catch (error) {
    return { saved: [], error: error.message };
  }

  const size = SMOKE_FIT === null ? TABLET : SMOKE_FIT;
  mainWindow.setContentSize(size.width, size.height);
  await new Promise((resolve) => setTimeout(resolve, 300));
  const ids = await mainWindow.webContents
    .executeJavaScript('Object.keys(window.AgentUI.views)')
    .catch(() => []);
  const saved = [];
  for (const id of ids) {
    const moved = await mainWindow.webContents
      .executeJavaScript(`window.__agentUi.go(${JSON.stringify(id)})`)
      .then(() => true)
      .catch((error) => error.message);
    if (moved !== true) {
      saved.push({ screen: id, error: moved });
      continue;
    }
    // One frame is requested per render and the size was set before the loop,
    // so the wait here is for the repaint rather than for a resize, and it is
    // longer than the screen walk's because the reading this loop exists to take
    // is of a painted picture and not of a scheduled one.
    await new Promise((resolve) => setTimeout(resolve, 400));
    const image = await mainWindow.webContents.capturePage();
    const drawn = image.getSize();
    const file = `${String(saved.length + 1).padStart(2, '0')}-${id}.png`;
    try {
      fs.writeFileSync(path.join(directory, file), image.toPNG());
    } catch (error) {
      saved.push({ screen: id, error: error.message });
      continue;
    }
    const reading = await readWindow();
    saved.push({
      screen: id,
      file,
      width: drawn.width,
      height: drawn.height,
      viewport: reading.viewport,
      title: reading.title
    });
  }

  return { saved, profile: size };
}
/**
 * Put every fold on the opening screen into both states and read the frame each
 * way.
 *
 * A fold is one of the few pieces of this window whose failure is invisible in a
 * screenshot: a body that still occupies its height while carrying the attribute
 * that says it is gone looks exactly like a body that is meant to be there. So
 * the trial reads the painted height of every fold in every state rather than
 * the state it declares, and it reads the layout in both states too, because a
 * fold that makes the screen fit when it is closed is worth nothing if opening
 * it puts the counter strip under the bottom edge.
 *
 * The state the screen opened with is restored before anything else reads it, so
 * the screen walk that follows describes the window a person sees and not the
 * one this trial left behind.
 */
async function disclosureTrial() {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return { before: { total: 0, open: 0, nodes: [] }, collapsed: null, expanded: null, after: null };
  }
  const before = await readDisclosures().catch(() => ({ total: 0, open: 0, nodes: [] }));
  const collapsed = await setEveryFold(false);
  const expanded = await setEveryFold(true);
  // The state the screen opened with is put back one key at a time, because the
  // keys are the reader's folds and a trial that left them all open would have
  // changed the window it was measuring.
  await restoreFolds(before.nodes);
  const after = await readDisclosures().catch(() => null);

  return { before, collapsed, expanded, after };
}

/** Fold or unfold every fold on the screen, then read the folds and the frame. */
async function setEveryFold(open) {
  await mainWindow.webContents.executeJavaScript(`window.__agentUi.setDisclosures(${open === true})`).catch(() => 0);
  // One frame is requested per render and the fold is applied to the painted
  // box, so the reading is taken after the layout the fold caused, not before.
  await new Promise((resolve) => setTimeout(resolve, 250));

  return { folds: await readDisclosures().catch(() => null), layout: (await readWindow()).layout || null };
}

async function readDisclosures() {
  if (mainWindow === null || mainWindow.isDestroyed()) {
    return { total: 0, open: 0, nodes: [] };
  }

  return mainWindow.webContents.executeJavaScript('window.__agentUi.disclosures()');
}

/** Put a set of folds back into the state a reading found them in, by key. */
async function restoreFolds(nodes) {
  if (mainWindow === null || mainWindow.isDestroyed() || !Array.isArray(nodes) || nodes.length === 0) {
    return 0;
  }
  const wanted = nodes.map((node) => ({ key: node.key, open: node.open }));

  return mainWindow.webContents.executeJavaScript(`(() => {
    const wanted = ${JSON.stringify(wanted)};
    for (const fold of wanted) { window.__agentUi.setDisclosure(fold.key, fold.open); }
    return wanted.length;
  })()`).catch(() => 0);
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

/**
 * A setting is a value of a name this application already has.
 *
 * The merge is a copy of a validated patch rather than a copy of whatever
 * arrived, because this is the one handler that writes a file and the one whose
 * values reach a process: `phpBin`, `dockerBin`, `harnessRoot` and `composeFile`
 * decide what gets executed on the next run. A name the settings do not already
 * hold is refused rather than added, and a value that is not of the kind its name
 * takes is refused rather than repaired, so the screen and the settings file can
 * never disagree about what is in force.
 */
ipcMain.handle('settings:set', (event, patch) => {
  const held = guard.sanitizeSettings(patch, settings);
  for (const refusal of held.refused) {
    send('harness:line', `refused ${refusal.key}: ${refusal.reason}`);
  }
  if (held.accepted.length === 0) {
    return settings;
  }
  settings = { ...held.settings, userData: app.getPath('userData') };
  settingsStore.save(app.getPath('userData'), settings);
  send('settings:changed', settings);

  return settings;
});

ipcMain.handle('runs:list', () => runs.list(paths.derive(settings).resultsRoot));

/**
 * Replay one run, which is one directory under the results root.
 *
 * The window holds the directory a row was built from, and a row is the only
 * thing that produces one, but the call is still a string from the renderer
 * reaching `readFile`. So the directory is confined to the results root before
 * anything is opened: a path that resolves outside it is refused, and the reason
 * is said rather than a read of a file nobody asked for.
 */
ipcMain.handle('runs:replay', (event, runDirectory) => {
  const derived = paths.derive(settings);
  if (guard.rootOf(runDirectory, [derived.resultsRoot]) === null) {
    send('harness:line', `refused a replay outside the results directory: ${runDirectory}`);
    return { events: [], errors: ['that run is not under the results directory'], record: null };
  }
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

/**
 * Stage a dropped project, once the path is one a project can be at.
 *
 * A directory chosen in the dialog and a directory dropped on the zone arrive by
 * different routes and only the second one is a string the window built, so the
 * rule cannot be "the path this process chose". It is instead the set of places a
 * project is never: the filesystem root, the directory the harness is pointed at,
 * and the home directory itself. Each of those would copy a machine into the
 * workspace rather than a project, and the copy is what the agent then reads.
 */
ipcMain.handle('project:stage', (event, projectPath) => {
  const derived = paths.derive(settings);
  const checked = guard.checkProject(projectPath, [derived.harnessRoot, app.getPath('home')]);
  if (!checked.ok) {
    send('harness:line', `refused staging ${projectPath}: ${checked.error}`);
    return {
      ok: false,
      slug: '',
      workspace: '',
      inventory: { files: 0, directories: 0, bytes: 0, truncated: false },
      error: checked.error,
      files_and_directories: 0
    };
  }
  const result = intake.stage(settings, checked.path, (line) => send('harness:line', line));
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

/**
 * Show one file in the file manager, if it is one this application wrote.
 *
 * Only the two roots the window is about are allowed: the results directory it
 * reads runs from, and the workspace a run writes into. Revealing is the mildest
 * privilege on the bridge and it is still a privilege - it names any location on
 * the machine to the operating system - so it gets the same confinement as a
 * read rather than being left as the call that trusts its input.
 */
ipcMain.handle('shell:reveal', (event, target) => {
  const { shell } = require('electron');
  const derived = paths.derive(settings);
  if (guard.rootOf(target, [derived.resultsRoot, derived.localWorkspace]) === null) {
    send('harness:line', `refused to reveal a path outside the run directories: ${target}`);
    return false;
  }
  if (typeof target === 'string' && fs.existsSync(target)) {
    shell.showItemInFolder(target);

    return true;
  }

  return false;
});

process.on('uncaughtException', (error) => {
  send('harness:line', `the interface failed: ${error.message}`);
});
