'use strict';

/**
 * Formatting, kept in one place so two screens cannot disagree about a number.
 *
 * The rule the reports in this house follow is that a number that was not
 * measured is not shown. These helpers therefore return an explicit absence
 * rather than a zero, because a zero reads as a measurement and a dash reads as
 * a missing one.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const ABSENT = '\u2014';

  function present(value) {
    return value !== null && value !== undefined && value !== '';
  }

  function number(value, digits) {
    if (!present(value) || Number.isNaN(Number(value))) {
      return ABSENT;
    }

    return Number(value).toFixed(digits === undefined ? 2 : digits);
  }

  function integer(value) {
    if (!present(value) || Number.isNaN(Number(value))) {
      return ABSENT;
    }

    return String(Math.round(Number(value)));
  }

  function seconds(value) {
    if (!present(value)) {
      return ABSENT;
    }
    const seconds = Number(value);
    if (seconds < 60) {
      return `${seconds.toFixed(1)}s`;
    }
    const minutes = Math.floor(seconds / 60);

    return `${minutes}m ${Math.round(seconds - minutes * 60)}s`;
  }

  function bytes(value) {
    if (!present(value)) {
      return ABSENT;
    }
    const size = Number(value);
    const units = ['B', 'KiB', 'MiB', 'GiB'];
    let index = 0;
    let scaled = size;
    while (scaled >= 1024 && index < units.length - 1) {
      scaled /= 1024;
      index += 1;
    }

    return `${scaled.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function when(ms) {
    if (!present(ms)) {
      return ABSENT;
    }
    const at = new Date(Number(ms));

    return `${at.toISOString().slice(0, 10)} ${at.toISOString().slice(11, 19)}Z`;
  }

  function clip(text, limit) {
    const size = limit === undefined ? 400 : limit;
    const value = String(text === null || text === undefined ? '' : text);
    if (value.length <= size) {
      return value;
    }

    return `${value.slice(0, size)}\n... ${value.length - size} more character(s)`;
  }

  /** A state word for the accent layer: passing, failing, or still running. */
  function stateWord(run) {
    if (!run) {
      return 'idle';
    }
    if (run.aborted) {
      return 'aborted';
    }
    if (run.tasks_passed !== null && run.tasks !== null && run.tasks_passed === run.tasks) {
      return 'all pass';
    }

    return 'partial';
  }

  UI.format = { ABSENT, present, number, integer, seconds, bytes, when, clip, stateWord };
}(window.AgentUI));
