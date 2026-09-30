'use strict';

/**
 * Reading what previous runs left on disk.
 *
 * The window is a projection of a transcript, and the transcript belongs to the
 * harness. This module is the only thing in the application that opens a file,
 * and it opens exactly two kinds: the event log of a run, and the run record
 * beside it. Both are written by the harness, so the history screen shows what
 * happened rather than what the application remembers.
 */

const fs = require('node:fs');
const path = require('node:path');
const events = require('./events');

/**
 * Run directories, newest first, with whatever the record says about each.
 */
function list(resultsRoot, limit = 60) {
  if (!fs.existsSync(resultsRoot)) {
    return [];
  }

  const entries = fs.readdirSync(resultsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const dir = path.join(resultsRoot, entry.name);
      const transcript = path.join(dir, 'events.ndjson');
      const recordPath = `${transcript}.result.json`;
      const stat = safeStat(transcript);
      const record = readJson(recordPath);
      return {
        run_id: record && record.run_id ? record.run_id : entry.name,
        dir,
        transcript,
        record: recordPath,
        has_transcript: stat !== null,
        bytes: stat === null ? 0 : stat.size,
        modified: stat === null ? 0 : stat.mtimeMs,
        aborted: record ? Boolean(record.aborted) : null,
        duration_s: record ? record.duration_s : null,
        composite: record && record.aggregate ? record.aggregate.composite ?? null : null,
        tasks_passed: record && record.aggregate ? record.aggregate.tasks_passed ?? null : null,
        tasks: record && record.aggregate ? record.aggregate.tasks ?? null : null,
        settings: record ? record.settings : null,
        controls: record ? record.controls : null,
        aggregate: record ? record.aggregate : null,
        rows: record ? record.tasks : null,
        transport: record ? record.transport : null
      };
    })
    .filter((entry) => entry.has_transcript)
    .sort((left, right) => right.modified - left.modified);

  return entries.slice(0, limit);
}

/**
 * Replace a run in the list without re-reading the disk.
 *
 * The window calls this when a run finishes, so the history and result screens
 * are current without a refresh, which is the rule the interface follows
 * everywhere: nothing is drawn from state that a screen has to be told to
 * reload.
 */
function describe(dir, recordPath) {
  const record = readJson(recordPath);
  const transcript = path.join(dir, 'events.ndjson');
  const stat = safeStat(transcript);

  return {
    run_id: record && record.run_id ? record.run_id : path.basename(dir),
    dir,
    transcript,
    record: recordPath,
    has_transcript: stat !== null,
    bytes: stat === null ? 0 : stat.size,
    modified: stat === null ? 0 : stat.mtimeMs,
    aborted: record ? Boolean(record.aborted) : null,
    duration_s: record ? record.duration_s : null,
    composite: record && record.aggregate ? record.aggregate.composite ?? null : null,
    tasks_passed: record && record.aggregate ? record.aggregate.tasks_passed ?? null : null,
    tasks: record && record.aggregate ? record.aggregate.tasks ?? null : null,
    settings: record ? record.settings : null,
    controls: record ? record.controls : null,
    aggregate: record ? record.aggregate : null,
    rows: record ? record.tasks : null,
    transport: record ? record.transport : null
  };
}

/**
 * Every event in one run, in order, with the malformed lines counted.
 */
function replay(transcript) {
  return new events.LineReader().feed(safeRead(transcript));
}

function readJson(target) {
  const raw = safeRead(target);
  if (raw === '') {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    return null;
  }
}

function safeRead(target) {
  try {
    return fs.readFileSync(target, 'utf8');
  } catch (error) {
    return '';
  }
}

function safeStat(target) {
  try {
    return fs.statSync(target);
  } catch (error) {
    return null;
  }
}

module.exports = { list, describe, replay, readJson };
