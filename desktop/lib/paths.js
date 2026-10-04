'use strict';

/**
 * Where everything is.
 *
 * The interface holds no paths of its own. Every location it needs is either
 * derived from the harness directory it was pointed at or overridden by a
 * setting, so the application can be run against a second checkout of the
 * harness without a code change. The defaults are the ones that make the
 * application start with nothing configured, which is the state a reader of this
 * repository is in.
 */

const path = require('node:path');
const fs = require('node:fs');

const HARNESS_ROOT = path.resolve(__dirname, '..', '..');
const DESKTOP_ROOT = path.resolve(__dirname, '..');

function defaults() {
  return {
    harnessRoot: HARNESS_ROOT,
    // local runs the harness with the interpreter on this machine, container
    // runs it inside the compose service whose walls were measured. The default
    // is container because that is the configuration the containment reading
    // belongs to.
    runtime: 'container',
    phpBin: process.env.HARNESS_PHP || 'php',
    dockerBin: process.env.HARNESS_DOCKER || 'docker',
    composeFile: path.join(HARNESS_ROOT, 'docker', 'docker-compose.yml'),
    engineUrl: 'http://127.0.0.1:8081',
    // The review dashboard of the lite agent harness. It is a setting rather
    // than a constant because the harness can be run on another port, and it is
    // held to the endpoint rule before it is ever opened, because opening an
    // address hands it to the machine's browser.
    dashboardUrl: 'http://127.0.0.1:8420',
    // The same endpoint seen from inside the compose network. The two are not
    // interchangeable: the published port is bound to the host's loopback
    // address, so a container that was handed it fails to connect, and the
    // failure arrives as a transport error on the first turn rather than as a
    // configuration error. The service name and the in network port are what the
    // harness service already uses.
    containerEngineUrl: 'http://gemma:8080',
    workspaceRoot: '',
    run: {
      tasks: [],
      guard: true,
      guard_repeat_limit: 2,
      strict_schema: true,
      decoder: 'none',
      decoder_scope: 'local',
      decoder_field: 'grammar',
      sandbox: 'documented',
      tool_mode: 'prompt',
      stream: true,
      scripted: false,
      temperature: 0,
      top_p: 1,
      max_tokens: 768,
      timeout: 300,
      limit: 0
    }
  };
}

/**
 * The paths a run writes to, in the order a reader wants to find them.
 *
 * The transcripts live under the harness results directory because the harness
 * writes them and the application only reads them. A run the window cannot find
 * on disk is a run the window invented, which is the failure this layout is
 * arranged to make impossible.
 */
function derive(settings) {
  const harnessRoot = path.resolve(settings.harnessRoot || HARNESS_ROOT);
  const resultsRoot = path.join(harnessRoot, 'results', 'agent');
  const localWorkspace = settings.workspaceRoot && settings.workspaceRoot !== ''
    ? path.resolve(settings.workspaceRoot)
    : path.join(harnessRoot, 'workspace');

  return {
    harnessRoot,
    desktopRoot: DESKTOP_ROOT,
    streamEntry: path.join(harnessRoot, 'stream.php'),
    agentEntry: path.join(harnessRoot, 'agent.php'),
    composeFile: settings.composeFile || path.join(harnessRoot, 'docker', 'docker-compose.yml'),
    resultsRoot,
    localWorkspace,
    // Inside the container the mount for the harness is read only and the
    // workspace is a volume, so the two entrances are not the same string.
    containerWorkspace: '/work',
    containerEntry: '/opt/harness/stream.php',
    settingsFile: path.join(settings.userData || DESKTOP_ROOT, 'settings.json')
  };
}

function exists(target) {
  try {
    fs.accessSync(target, fs.constants.R_OK);
    return true;
  } catch (error) {
    return false;
  }
}

module.exports = { defaults, derive, exists, HARNESS_ROOT };
