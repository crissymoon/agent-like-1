'use strict';

/**
 * Getting a project into the workspace the agent runs against.
 *
 * The workspace is not a scratch directory the application is free to invent.
 * In the container runtime it is the declared volume the harness mounts, and in
 * the local runtime it is the harness's own workspace directory, so staging
 * means one thing in both cases: the dropped repository is copied into that
 * workspace under its own name and nothing outside it is touched.
 *
 * The copy is done by the container itself in the container runtime, because the
 * application has no path inside the container's filesystem and inventing one
 * would mean giving the window a folder mount it does not need. The dropped
 * directory is mounted read only at /incoming, copied with `cp -a`, and that
 * mount is the only thing the helper process can see.
 */

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const paths = require('./paths');

/** Names that are refused, because a project called .. is not a project. */
const UNSAFE = /[^A-Za-z0-9._-]/;

function slugify(name) {
  const slug = path.basename(name).replace(/[^A-Za-z0-9._-]/g, '-').replace(/^[.-]+/, '');

  return slug === '' ? 'project' : slug;
}

function isSafe(slug) {
  return !UNSAFE.test(slug) && !slug.startsWith('.');
}

/**
 * Everything the directory holds, counted, so the copy is verifiable.
 *
 * The count is reported rather than assumed: a stage that copied the directory
 * entry but not its contents reads as a successful intake and leaves the agent
 * with an empty workspace, which is the failure this count exists to catch.
 */
function inventory(dir, limit = 20000) {
  const state = { files: 0, directories: 0, bytes: 0, truncated: false };
  const walk = (current) => {
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (error) {
      return;
    }
    for (const entry of entries) {
      if (state.files + state.directories >= limit) {
        state.truncated = true;
        return;
      }
      const target = path.join(current, entry.name);
      if (entry.isSymbolicLink()) {
        continue;
      }
      if (entry.isDirectory()) {
        state.directories++;
        walk(target);
      } else if (entry.isFile()) {
        state.files++;
        try {
          state.bytes += fs.statSync(target).size;
        } catch (error) {
          state.bytes += 0;
        }
      }
    }
  };
  walk(dir);

  return state;
}

/**
 * Stage a dropped project into the workspace.
 *
 * @param {object} settings
 * @param {string} projectPath the dropped directory
 * @param {(line: string) => void} onLog
 * @returns {{ok: boolean, slug: string, workspace: string, inventory: object, error: string, files_and_directories: number}}
 */
function stage(settings, projectPath, onLog) {
  const derived = paths.derive(settings);
  const slug = slugify(projectPath);
  if (!isSafe(slug)) {
    return { ok: false, slug: '', workspace: '', inventory: inventory(dirOrEmpty(projectPath)), error: 'the project name is not usable', files_and_directories: 0 };
  }
  if (!fs.existsSync(projectPath)) {
    return { ok: false, slug, workspace: '', inventory: { files: 0, directories: 0, bytes: 0, truncated: false }, error: 'no such path', files_and_directories: 0 };
  }

  const counted = inventory(projectPath);
  if (settings.runtime === 'container') {
    const argv = [
      settings.dockerBin, 'compose', '-f', derived.composeFile,
      'run', '--rm', '-T',
      '-v', `${path.resolve(projectPath)}:/incoming:ro`,
      '--entrypoint', '/bin/sh',
      'agent', '-c', `set -e; mkdir -p /work; rm -rf /work/${slug}; mkdir -p /work/${slug}; cp -a /incoming/. /work/${slug}/`
    ];
    const result = spawnSync(argv[0], argv.slice(1), { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    if (onLog) {
      for (const line of `${result.stdout || ''}${result.stderr || ''}`.split('\n')) {
        if (line.trim() !== '') {
          onLog(line.trim());
        }
      }
    }
    if (result.status !== 0) {
      return {
        ok: false,
        slug,
        workspace: '',
        inventory: counted,
        error: `the container copy exited ${result.status}`,
        files_and_directories: counted.files + counted.directories
      };
    }
  } else {
    const destination = path.join(derived.localWorkspace, slug);
    try {
      fs.rmSync(destination, { recursive: true, force: true });
      fs.mkdirSync(destination, { recursive: true });
      fs.cpSync(projectPath, destination, { recursive: true, force: true, dereference: false });
    } catch (error) {
      return {
        ok: false,
        slug,
        workspace: '',
        inventory: counted,
        error: `the copy failed: ${error.message}`,
        files_and_directories: counted.files + counted.directories
      };
    }
  }

  const workspace = settings.runtime === 'container'
    ? `${derived.containerWorkspace}/${slug}`
    : path.join(derived.localWorkspace, slug);

  return {
    ok: true,
    slug,
    workspace,
    inventory: counted,
    error: '',
    files_and_directories: counted.files + counted.directories
  };
}

function dirOrEmpty(target) {
  return fs.existsSync(target) ? target : '.';
}

module.exports = { stage, inventory, slugify, isSafe };
