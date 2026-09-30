'use strict';

/**
 * The application's own settings, kept apart from the harness's.
 *
 * The harness settings travel with a run, because the controls a result was
 * produced under are part of the result. These settings describe the machine the
 * window is on: where the harness is, which runtime to start it with, where the
 * workspace is. They are stored in one JSON file under the application's data
 * directory, so a reader can see the whole configuration and delete it.
 *
 * A stored value that no longer parses is not repaired silently. The file is
 * read, the bad value falls back to its default, and every fallback is reported
 * so the settings screen can say which values it took from the default rather
 * than from the file.
 */

const fs = require('node:fs');
const path = require('node:path');
const paths = require('./paths');

function load(userData) {
  const defaults = paths.defaults();
  defaults.userData = userData;
  const file = paths.derive(defaults).settingsFile;
  const taken = [];

  if (!fs.existsSync(file)) {
    return { settings: defaults, file, taken, firstRun: true };
  }

  let parsed = null;
  try {
    parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    return { settings: defaults, file, taken: ['the settings file could not be parsed'], firstRun: false };
  }

  const settings = defaults;
  for (const key of Object.keys(defaults)) {
    if (!Object.prototype.hasOwnProperty.call(parsed, key)) {
      continue;
    }
    if (key === 'run' && typeof parsed.run === 'object' && parsed.run !== null) {
      for (const runKey of Object.keys(defaults.run)) {
        if (Object.prototype.hasOwnProperty.call(parsed.run, runKey)) {
          settings.run[runKey] = parsed.run[runKey];
        }
      }
      continue;
    }
    if (typeof parsed[key] === typeof defaults[key] || parsed[key] === null) {
      settings[key] = parsed[key];
    } else {
      taken.push(key);
    }
  }

  return { settings, file, taken, firstRun: false };
}

function save(userData, settings) {
  const defaults = paths.defaults();
  defaults.userData = userData;
  const file = paths.derive(defaults).settingsFile;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(settings, null, 2)}\n`, 'utf8');

  return file;
}

module.exports = { load, save };
