'use strict';

/**
 * Starting the harness and reading its stream.
 *
 * The runner owns exactly one child process and no state beyond it. It writes
 * the request as one JSON object to the child's standard input, reads the event
 * stream from its standard output, and keeps the child's standard error as a
 * diagnostic tail rather than as a channel. A run therefore has one input and
 * one output, which is what makes it reproducible from the record it leaves.
 *
 * Two runtimes start the same entry point. `local` runs `php stream.php` against
 * a workspace directory on this machine. `container` runs the same file inside
 * the compose service whose read only mounts, dropped capabilities and three
 * writable surfaces were measured, with the dropped project staged into the
 * workspace volume first. The request is identical in both cases, and the
 * record says which runtime produced it.
 */

const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const events = require('./events');
const paths = require('./paths');

class Runner {
  /**
   * @param {object} settings the application settings, already resolved
   * @param {(event: object) => void} onEvent
   * @param {(line: string) => void} onLog
   */
  constructor(settings, onEvent, onLog) {
    this.settings = settings;
    this.onEvent = onEvent;
    this.onLog = onLog;
    this.reader = new events.LineReader();
    this.child = null;
    this.errors = [];
    this.running = false;
    this.ended = null;
  }

  /**
   * Build the request the harness will read.
   *
   * The run settings are passed through as the request's own top level keys,
   * which is the form `stream.php` accepts, so nothing in the window is renamed
   * on the way to the process and the record's settings block can be compared
   * with what the screen showed.
   */
  static request(settings, runId, overrides) {
    const run = { ...settings.run, ...(overrides || {}) };
    const request = {
      run_id: runId,
      // The endpoint is resolved per runtime rather than passed through, because
      // the host's published port and the service's own address are different
      // addresses and only one of them is reachable from where the harness runs.
      engine_url: settings.runtime === 'container'
        ? (settings.containerEngineUrl || 'http://gemma:8080')
        : settings.engineUrl,
      tasks: run.tasks && run.tasks.length > 0 ? run.tasks : undefined,
      tool_mode: run.tool_mode,
      temperature: run.temperature,
      top_p: run.top_p,
      max_tokens: run.max_tokens,
      timeout: run.timeout,
      guard: run.guard,
      guard_repeat_limit: run.guard_repeat_limit,
      strict_schema: run.strict_schema,
      decoder: run.decoder,
      decoder_scope: run.decoder_scope,
      decoder_field: run.decoder_field,
      sandbox: run.sandbox,
      stream: run.stream,
      scripted: run.scripted,
      limit: run.limit
    };
    if (settings.runtime === 'container') {
      request.workspace_root = paths.derive(settings).containerWorkspace;
      if (settings.stagedProject !== undefined && settings.stagedProject !== '') {
        request.workspace_root = `${paths.derive(settings).containerWorkspace}/${settings.stagedProject}`;
      }
    } else {
      const local = paths.derive(settings).localWorkspace;
      request.workspace_root = settings.stagedProject
        ? path.join(local, settings.stagedProject)
        : local;
    }
    for (const key of Object.keys(request)) {
      if (request[key] === undefined) {
        delete request[key];
      }
    }
    if (overrides && overrides.describe === true) {
      // The describe mode is a mode rather than a setting, so it is carried on
      // the request without becoming part of the run settings the record shows.
      request.describe = true;
    }

    return request;
  }

  /**
   * @param {object} request
   * @returns {Promise<{code: number|null, signal: string|null, errors: string[], log: string[]}>}
   */
  start(request) {
    const derived = paths.derive(this.settings);
    const argv = this.settings.runtime === 'container'
      ? this.containerArgv(derived)
      : this.localArgv(derived);
    const command = argv[0];
    const args = argv.slice(1);

    fs.mkdirSync(derived.resultsRoot, { recursive: true });
    this.log = [];
    this.running = true;

    this.ended = new Promise((resolve) => {
      let child;
      try {
        child = spawn(command, args, {
          cwd: derived.harnessRoot,
          stdio: ['pipe', 'pipe', 'pipe'],
          env: { ...process.env }
        });
      } catch (error) {
        this.running = false;
        this.errors.push(`could not start ${command}: ${error.message}`);
        resolve({ code: null, signal: null, errors: this.errors, log: this.log });
        return;
      }

      this.child = child;
      child.stdout.setEncoding('utf8');
      child.stderr.setEncoding('utf8');

      child.stdout.on('data', (chunk) => {
        const read = this.reader.feed(chunk);
        this.errors.push(...read.errors);
        for (const event of read.events) {
          this.onEvent(event);
        }
      });
      child.stderr.on('data', (chunk) => {
        for (const line of chunk.split('\n')) {
          if (line.trim() !== '') {
            this.log.push(line.trim());
            this.onLog(line.trim());
          }
        }
        if (this.log.length > 400) {
          this.log.splice(0, this.log.length - 400);
        }
      });
      child.on('error', (error) => {
        this.errors.push(`the harness process failed: ${error.message}`);
      });
      child.on('close', (code, signal) => {
        this.running = false;
        this.child = null;
        resolve({ code, signal, errors: this.errors, log: this.log });
      });

      child.stdin.write(`${JSON.stringify(request)}\n`);
      child.stdin.end();
    });

    return this.ended;
  }

  localArgv(derived) {
    return [this.settings.phpBin, derived.streamEntry];
  }

  /**
   * The container form, with the stdin pipe kept open and no terminal allocated.
   *
   * `run --rm` is used rather than a long lived service because a run is one
   * process with one transcript: a container that outlived its run would be a
   * second place a result could live.
   */
  containerArgv(derived) {
    const argv = [
      this.settings.dockerBin, 'compose',
      '-f', derived.composeFile,
      'run', '--rm', '-T',
      '--entrypoint', 'php',
      'agent', derived.containerEntry
    ];
    if (this.stagedHostPath) {
      argv.splice(6, 0, '-v', `${this.stagedHostPath}:/incoming:ro`);
    }

    return argv;
  }

  /** Stop the child, which closes its stream and lets it write its record. */
  cancel() {
    if (this.child === null) {
      return false;
    }
    this.child.kill('SIGINT');

    return true;
  }

  /** The promise that settles when the child exits. */
  wait() {
    return this.ended || Promise.resolve({ code: null, signal: null, errors: [], log: [] });
  }
}

module.exports = { Runner };
