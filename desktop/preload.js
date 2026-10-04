'use strict';

/**
 * The bridge, and the whole of it.
 *
 * Nine calls cross into the main process and nothing else does. No node module
 * is exposed, no path is passed without the main process resolving it, and the
 * renderer cannot start a process or open a file. The event stream arrives as a
 * subscription rather than as a request, because the stream is push by nature
 * and a window that polled it would be a window that can miss a turn.
 */

const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('agentBridge', {
  info: () => ipcRenderer.invoke('app:info'),
  settings: {
    get: () => ipcRenderer.invoke('settings:get'),
    set: (patch) => ipcRenderer.invoke('settings:set', patch)
  },
  engine: () => ipcRenderer.invoke('engine:status'),
  // Opens the lite harness's review dashboard in the machine's browser. The main
  // process holds the address to the endpoint rule and does the opening, so the
  // window names the intent rather than a location.
  dashboard: {
    open: () => ipcRenderer.invoke('dashboard:open')
  },
  selfCheck: () => ipcRenderer.invoke('harness:selfcheck'),
  describe: () => ipcRenderer.invoke('harness:describe'),
  runs: {
    list: () => ipcRenderer.invoke('runs:list'),
    replay: (dir) => ipcRenderer.invoke('runs:replay', dir)
  },
  run: {
    start: (overrides) => ipcRenderer.invoke('run:start', overrides),
    cancel: () => ipcRenderer.invoke('run:cancel')
  },
  project: {
    pick: () => ipcRenderer.invoke('project:pick'),
    stage: (target) => ipcRenderer.invoke('project:stage', target)
  },
  reveal: (target) => ipcRenderer.invoke('shell:reveal', target),
  // A dropped file is a browser File object, and its path is not readable from
  // the renderer. This is the supported way to ask for it, and it is the only
  // thing the window is told about a path.
  pathFor: (file) => webUtils.getPathForFile(file),
  onEvent: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('agent:event', listener);

    return () => ipcRenderer.removeListener('agent:event', listener);
  },
  onLog: (handler) => {
    const listener = (_event, line) => handler(line);
    ipcRenderer.on('harness:line', listener);

    return () => ipcRenderer.removeListener('harness:line', listener);
  },
  onRunStarted: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('run:started', listener);

    return () => ipcRenderer.removeListener('run:started', listener);
  },
  onRunEnded: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('run:ended', listener);

    return () => ipcRenderer.removeListener('run:ended', listener);
  },
  onSettingsChanged: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('settings:changed', listener);

    return () => ipcRenderer.removeListener('settings:changed', listener);
  }
});
