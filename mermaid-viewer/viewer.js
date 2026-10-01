"use strict";

/**
 * Mermaid Viewer
 * Renders local .mmd files with pan, zoom, theme switching and SVG/PNG export.
 * Sources are discovered from a generated sources.json manifest (served live
 * by serve.py, or read as a static file from any other host) so new diagram
 * files appear with no manifest to edit by hand. Diagrams live in the diagrams
 * folder, and any path the manifest reports is fetched as it is given.
 *
 * A manifest is a snapshot, so a name it names is checked against the folder
 * before it is offered: renaming or deleting a diagram without restarting the
 * server leaves the old name in the file, and an entry in the picker that can
 * only fail is worse than no entry at all.
 */
(function () {
  const MANIFEST = "sources.json";
  const DIAGRAM_DIR = "diagrams";
  //: The file the viewer opens first, by base name, wherever it is sitting.
  const PREFERRED_NAME = "diagram.mmd";
  //: A trailing .md is a diagram too: it only asks an editor to preview it.
  const DIAGRAM_SUFFIX = /\.(mmd|mermaid)(\.md)?$/i;
  //: Directories never crawled when a host has no manifest to hand over.
  const SKIP_DIRECTORIES = ["vendor", "node_modules", "__pycache__"];
  const MAX_LISTING_DEPTH = 2;
  const MAX_LISTING_REQUESTS = 12;

  const MIN_SCALE = 0.1;
  const MAX_SCALE = 8;
  const ZOOM_STEP = 1.2;
  const PAN_STEP = 80;
  const FIT_PADDING = 48;
  const HINT_TIMEOUT = 6000;

  const els = {
    picker: document.getElementById("source-picker"),
    reload: document.getElementById("btn-reload"),
    zoomIn: document.getElementById("btn-zoom-in"),
    zoomOut: document.getElementById("btn-zoom-out"),
    zoomReset: document.getElementById("btn-zoom-reset"),
    fit: document.getElementById("btn-fit"),
    panUp: document.getElementById("btn-pan-up"),
    panDown: document.getElementById("btn-pan-down"),
    panLeft: document.getElementById("btn-pan-left"),
    panRight: document.getElementById("btn-pan-right"),
    center: document.getElementById("btn-center"),
    theme: document.getElementById("btn-theme"),
    exportSvg: document.getElementById("btn-export-svg"),
    exportPng: document.getElementById("btn-export-png"),
    canvas: document.getElementById("canvas"),
    diagram: document.getElementById("diagram"),
    error: document.getElementById("error"),
    errorBody: document.getElementById("error-body"),
    hint: document.getElementById("hint"),
    version: document.getElementById("mmd-version"),
    root: document.documentElement
  };

  const state = {
    source: "",
    // Every diagram the picker is offering, as viewer relative paths.
    sources: [],
    code: "",
    scale: 1,
    x: 0,
    y: 0,
    rendered: false,
    // Natural (untransformed) size of the rendered diagram, in SVG user units.
    // Measured once per render so viewport maths never reads a stale transform.
    natural: { width: 0, height: 0 }
  };

  let frameId = 0;
  let scaleLabel = "";

  /* ---------------------------------------------------------------- status */

  function setError(message) {
    els.errorBody.textContent = message || "";
    els.error.hidden = !message;
  }

  function fadeHint() {
    els.hint.style.transition = "opacity 0.4s ease";
    els.hint.style.opacity = "0";
  }

  /* -------------------------------------------------------------- transform */

  // Coalesce every transform change into one write per animation frame so a
  // fast drag never queues more work than the compositor can drain.
  function scheduleTransform() {
    if (frameId) {
      return;
    }
    frameId = window.requestAnimationFrame(function () {
      frameId = 0;
      applyTransform();
    });
  }

  function applyTransform() {
    els.diagram.style.transform =
      "translate3d(" + state.x + "px, " + state.y + "px, 0) scale(" + state.scale + ")";

    const label = Math.round(state.scale * 100) + "%";
    if (label !== scaleLabel) {
      scaleLabel = label;
      els.zoomReset.textContent = label;
    }
  }

  function canvasRect() {
    return els.canvas.getBoundingClientRect();
  }

  // Read a numeric SVG attribute, ignoring percentage values such as "100%".
  function numericAttr(element, name) {
    const raw = element.getAttribute(name);
    if (!raw || raw.indexOf("%") !== -1) {
      return 0;
    }
    const value = parseFloat(raw);
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  // Grow the diagram box so a label that was displaced to clear a collision is
  // still inside the frame, and therefore still inside an export.
  function mergeBounds(box, bounds) {
    if (!bounds) {
      return box;
    }
    const minX = Math.min(box.x, bounds.minX);
    const minY = Math.min(box.y, bounds.minY);
    const maxX = Math.max(box.x + box.width, bounds.maxX);
    const maxY = Math.max(box.y + box.height, bounds.maxY);
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
  }

  function sameBox(viewBox, box) {
    return (
      Math.abs(viewBox.x - box.x) < 0.5 &&
      Math.abs(viewBox.y - box.y) < 0.5 &&
      Math.abs(viewBox.width - box.width) < 0.5 &&
      Math.abs(viewBox.height - box.height) < 0.5
    );
  }

  /**
   * Freeze the rendered SVG at its intrinsic size and return that size.
   * Mermaid sometimes hands back width="100%" (and for some diagram types an
   * inline max-width), which would make the wrapper resize with the viewport and
   * fight the pan/zoom transform. Pinning explicit width/height attributes keeps
   * the natural size stable regardless of the current scale.
   *
   * @param {{minX: number, minY: number, maxX: number, maxY: number}|null}
   *   [labelBounds] union of the placed edge label boxes, in SVG user units
   */
  function measureDiagram(labelBounds) {
    const svg = els.diagram.querySelector("svg");
    if (!svg) {
      return { width: 0, height: 0 };
    }

    const viewBox = svg.viewBox && svg.viewBox.baseVal;
    let width = numericAttr(svg, "width");
    let height = numericAttr(svg, "height");

    if ((!width || !height) && viewBox && viewBox.width > 0 && viewBox.height > 0) {
      width = viewBox.width;
      height = viewBox.height;
    }
    if (!width || !height) {
      try {
        const box = svg.getBBox();
        if (box.width > 0 && box.height > 0) {
          width = box.width;
          height = box.height;
        }
      } catch (error) {
        return { width: 0, height: 0 };
      }
    }
    if (!width || !height) {
      return { width: 0, height: 0 };
    }

    const current =
      viewBox && viewBox.width > 0 && viewBox.height > 0
        ? { x: viewBox.x, y: viewBox.y, width: viewBox.width, height: viewBox.height }
        : { x: 0, y: 0, width: width, height: height };
    const box = mergeBounds(current, labelBounds);

    if (!viewBox || !sameBox(viewBox, box)) {
      svg.setAttribute("viewBox", box.x + " " + box.y + " " + box.width + " " + box.height);
    }
    svg.setAttribute("width", box.width);
    svg.setAttribute("height", box.height);
    svg.style.maxWidth = "none";

    return { width: box.width, height: box.height };
  }

  /**
   * Run the edge label layout pass and return the union of the placed label
   * boxes, or null when there is nothing to lay out. This is a presentation
   * improvement on top of a successful render, so it can never fail the render.
   */
  function tidyEdgeLabels() {
    const svg = els.diagram.querySelector("svg");
    if (!svg || !window.MermaidViewerLabels) {
      return null;
    }
    try {
      return window.MermaidViewerLabels.tidy(svg, {
        background: cssVar("--panel", "#ffffff"),
        outline: "1px solid " + cssVar("--ink", "#000000")
      }).bounds;
    } catch (error) {
      return null;
    }
  }

  function setScale(next) {
    state.scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
  }

  // Zoom about a point expressed in viewport coordinates, keeping whatever is
  // under the cursor fixed on screen.
  function zoomAt(clientX, clientY, factor) {
    const previous = state.scale;
    setScale(previous * factor);
    if (state.scale === previous) {
      return;
    }
    const rect = canvasRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const ratio = state.scale / previous;
    state.x = px - (px - state.x) * ratio;
    state.y = py - (py - state.y) * ratio;
    scheduleTransform();
  }

  function zoomCenter(factor) {
    const rect = canvasRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  function panBy(dx, dy) {
    state.x += dx;
    state.y += dy;
    scheduleTransform();
  }

  function centerDiagram() {
    const size = state.natural;
    if (!size.width || !size.height) {
      return;
    }
    const rect = canvasRect();
    state.x = (rect.width - size.width * state.scale) / 2;
    state.y = (rect.height - size.height * state.scale) / 2;
    scheduleTransform();
  }

  function fit() {
    const size = state.natural;
    if (!size.width || !size.height) {
      return;
    }
    const rect = canvasRect();
    const available = Math.max(1, rect.width - FIT_PADDING);
    const availableHeight = Math.max(1, rect.height - FIT_PADDING);
    const scale = Math.min(available / size.width, availableHeight / size.height);
    setScale(scale);
    centerDiagram();
  }

  function resetView() {
    setScale(1);
    centerDiagram();
  }

  /* ------------------------------------------------------------ pan and zoom */

  function bindPanZoom() {
    const pointers = new Map();
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;
    let pinchDistance = 0;

    function pointerDistance() {
      const values = Array.from(pointers.values());
      return Math.hypot(values[0].x - values[1].x, values[0].y - values[1].y);
    }

    els.canvas.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 && event.pointerType === "mouse") {
        return;
      }
      els.canvas.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

      if (pointers.size === 1) {
        dragging = true;
        startX = event.clientX;
        startY = event.clientY;
        originX = state.x;
        originY = state.y;
        els.canvas.classList.add("is-panning");
      } else if (pointers.size === 2) {
        dragging = false;
        pinchDistance = pointerDistance();
      }
    });

    els.canvas.addEventListener("pointermove", function (event) {
      if (!pointers.has(event.pointerId)) {
        return;
      }
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

      if (pointers.size >= 2) {
        const distance = pointerDistance();
        if (pinchDistance > 0 && distance > 0) {
          const values = Array.from(pointers.values());
          zoomAt((values[0].x + values[1].x) / 2, (values[0].y + values[1].y) / 2, distance / pinchDistance);
        }
        pinchDistance = distance;
        return;
      }

      if (dragging) {
        state.x = originX + (event.clientX - startX);
        state.y = originY + (event.clientY - startY);
        scheduleTransform();
      }
    });

    function release(event) {
      pointers.delete(event.pointerId);
      if (pointers.size < 2) {
        pinchDistance = 0;
      }
      if (pointers.size === 0) {
        dragging = false;
        els.canvas.classList.remove("is-panning");
      }
    }

    els.canvas.addEventListener("pointerup", release);
    els.canvas.addEventListener("pointercancel", release);

    els.canvas.addEventListener(
      "wheel",
      function (event) {
        event.preventDefault();
        // Trackpads emit many small deltas; scale the factor so the zoom feels
        // proportional rather than a fixed jump per event.
        const factor = Math.pow(1.0015, -event.deltaY);
        zoomAt(event.clientX, event.clientY, factor);
      },
      { passive: false }
    );

    els.canvas.addEventListener("dblclick", fit);
  }

  /* ------------------------------------------------------------- mermaid io */

  function currentTheme() {
    return els.root.getAttribute("data-theme") === "dark" ? "dark" : "default";
  }

  // Browsers refuse to read neighbouring files from a file:// page, so the
  // generic "Failed to fetch" would be actively misleading here.
  function fileProtocolHint() {
    return "This page was opened straight from the file system, where the browser blocks reading local diagram files. Start the viewer with ./serve.sh and open the http address it prints.";
  }

  function configureMermaid() {
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: currentTheme(),
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
      flowchart: { htmlLabels: true, curve: "basis", useMaxWidth: false },
      maxTextSize: 500000
    });
  }

  async function render() {
    setError("");
    if (!state.code.trim()) {
      state.rendered = false;
      state.natural = { width: 0, height: 0 };
      els.diagram.innerHTML = "";
      return;
    }
    configureMermaid();
    const renderId = "mmd-" + Date.now().toString(36);
    try {
      const result = await window.mermaid.render(renderId, state.code);
      els.diagram.innerHTML = result.svg;
      state.natural = measureDiagram(tidyEdgeLabels());
      state.rendered = true;
      window.requestAnimationFrame(fit);
    } catch (error) {
      state.rendered = false;
      state.natural = { width: 0, height: 0 };
      els.diagram.innerHTML = "";
      setError((error && (error.message || error.str)) || "Unknown mermaid error.");
    }
  }

  function populatePicker(sources) {
    els.picker.innerHTML = "";
    sources.forEach(function (name) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      els.picker.appendChild(option);
    });
  }

  // A diagram that cannot be read is taken out of the picker. A file renamed or
  // deleted while the viewer was open would otherwise keep offering a choice
  // that can only fail the same way again.
  function dropSource(name) {
    state.sources = state.sources.filter(function (item) {
      return item !== name;
    });
    populatePicker(state.sources);
    els.picker.value = state.sources.indexOf(state.source) === -1 ? "" : state.source;
  }

  function failLoad(name, error) {
    const message = error.message || String(error);
    if (window.location.protocol === "file:") {
      setError(fileProtocolHint());
      return;
    }
    if ((error.status === 404 || error.status === 410) && state.sources.indexOf(name) !== -1) {
      dropSource(name);
      setError(message + "\n\nThat file is not in the folder any more, so it was removed from the source list.");
      return;
    }
    setError(message);
  }

  async function loadSource(name, options) {
    const fresh = Boolean(options && options.fresh);
    state.source = name || defaultSource();
    if (!state.source) {
      state.code = "";
      state.rendered = false;
      state.natural = { width: 0, height: 0 };
      els.diagram.innerHTML = "";
      setError(
        window.location.protocol === "file:"
          ? fileProtocolHint()
          : "No diagram files were found beside the viewer. Put a .mmd or .mermaid file in the diagrams folder, then press Reload."
      );
      return;
    }
    els.picker.value = state.source;
    try {
      // A buster as well as no-store: Reload must read the file as it is now,
      // past anything between the browser and the disk that would cache it.
      const url = fresh ? state.source + cacheBuster() : state.source;
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) {
        const error = new Error("HTTP " + response.status + " while loading " + state.source);
        error.status = response.status;
        throw error;
      }
      state.code = await response.text();
      state.scale = 1;
      state.x = 0;
      state.y = 0;
      applyTransform();
      await render();
    } catch (error) {
      state.code = "";
      failLoad(state.source, error);
    }
  }

  /* ------------------------------------------------------ source discovery */

  function baseName(path) {
    return path.substring(path.lastIndexOf("/") + 1);
  }

  // One normal form for a manifest entry or a link: no leading ./, no doubled
  // separators, no backslashes.
  function normalizeName(value) {
    if (typeof value !== "string") {
      return "";
    }
    let name = value.trim().replace(/\\/g, "/");
    while (name.indexOf("./") === 0) {
      name = name.substring(2);
    }
    return name.replace(/\/{2,}/g, "/");
  }

  // A manifest is a file next to the viewer, so treat its contents as data and
  // accept only a name that stays inside the folder and is a diagram.
  function isDiagramName(value) {
    const name = normalizeName(value);
    if (!name || name.length > 240 || !DIAGRAM_SUFFIX.test(name)) {
      return false;
    }
    if (name.indexOf("..") !== -1 || name.charAt(0) === "/") {
      return false;
    }
    return name.split("/").every(function (segment) {
      return Boolean(segment) && segment !== ".";
    });
  }

  function sortNames(names) {
    return names.slice().sort(function (a, b) {
      return a.localeCompare(b, undefined, { sensitivity: "base" });
    });
  }

  function cacheBuster() {
    return "?v=" + Date.now().toString(36);
  }

  // The manifest may be served dynamically (serve.py) or as a generated file
  // sitting next to the diagrams, so bust the cache explicitly: a stale copy
  // would hide diagrams that were added after the file was last written.
  async function sourcesFromManifest() {
    const response = await fetch(MANIFEST + cacheBuster(), { cache: "no-store" });
    if (!response.ok) {
      return [];
    }
    const parsed = await response.json();
    return Array.isArray(parsed) ? parsed.filter(isDiagramName).map(normalizeName) : [];
  }

  function decodePath(href) {
    try {
      return decodeURIComponent(href);
    } catch (error) {
      return href;
    }
  }

  // Read one directory listing, take every diagram link out of it, and queue
  // each subdirectory so the diagrams folder is walked as well.
  async function crawlListing(dir, depth, found, queue) {
    const response = await fetch("./" + dir + cacheBuster(), { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const type = response.headers.get("content-type") || "";
    if (type.indexOf("text/html") === -1) {
      return;
    }
    const html = await response.text();
    // Most static servers answer a directory request with the folder's own
    // index.html, which here means this very page. Parsing it yields nothing,
    // so detect it and report no listing rather than pretending we looked.
    if (html.indexOf('id="source-picker"') !== -1) {
      return;
    }
    new DOMParser()
      .parseFromString(html, "text/html")
      .querySelectorAll("a[href]")
      .forEach(function (anchor) {
        const href = (anchor.getAttribute("href") || "").split("?")[0].split("#")[0];
        if (!href || href.indexOf("://") !== -1 || href.indexOf("//") === 0) {
          return;
        }
        const candidate = normalizeName(dir + decodePath(href));
        if (candidate.charAt(candidate.length - 1) === "/") {
          const folder = candidate.slice(0, -1);
          if (
            depth < MAX_LISTING_DEPTH &&
            folder.charAt(0) !== "." &&
            SKIP_DIRECTORIES.indexOf(baseName(folder)) === -1
          ) {
            queue.push({ dir: candidate, depth: depth + 1 });
          }
        } else if (isDiagramName(candidate)) {
          found.add(candidate);
        }
      });
  }

  // Fallback for a static host that has no manifest but does expose a listing.
  // The viewer folder is crawled first and the diagrams folder is crawled even
  // if that fails, because a folder holding an index.html answers a request for
  // it with the page rather than with a listing.
  async function sourcesFromDirectory() {
    const found = new Set();
    const queue = [
      { dir: "", depth: 0 },
      { dir: DIAGRAM_DIR + "/", depth: 1 }
    ];
    const visited = new Set();
    let requests = 0;
    while (queue.length && requests < MAX_LISTING_REQUESTS) {
      const next = queue.shift();
      if (visited.has(next.dir)) {
        continue;
      }
      visited.add(next.dir);
      requests += 1;
      await crawlListing(next.dir, next.depth, found, queue);
    }
    return Array.from(found);
  }

  // Ask a file whether it is still there. A HEAD is cheap and answers exactly
  // the question a stale manifest cannot.
  async function exists(name) {
    try {
      const response = await fetch(name, { method: "HEAD", cache: "no-store" });
      // 404 and 410 are the two answers that mean gone. Anything else - a 403,
      // a 405 from a host that does not implement HEAD, a network failure - is
      // not evidence of absence, so the name is kept.
      return response.status !== 404 && response.status !== 410;
    } catch (error) {
      return true;
    }
  }

  // Keep only the names something actually answers for. The answers come back
  // together rather than one after another, so a folder of diagrams costs one
  // round trip.
  async function keepExisting(names) {
    const answers = await Promise.all(names.map(exists));
    return names.filter(function (name, index) {
      return answers[index];
    });
  }

  // Union every strategy that answers: whichever mechanism a host happens to
  // support, the picker still lists all the diagrams in the folder.
  async function discoverSources() {
    const found = new Set();
    const strategies = [sourcesFromManifest, sourcesFromDirectory];
    for (let index = 0; index < strategies.length; index += 1) {
      try {
        (await strategies[index]()).forEach(function (name) {
          found.add(name);
        });
      } catch (error) {
        /* strategy unavailable on this host, try the next one */
      }
    }
    const candidates = sortNames(Array.from(found));
    if (!candidates.length) {
      // Nothing could enumerate the folder. Offer the conventional path rather
      // than an empty picker, because a host that serves files but cannot list
      // them probably has a diagram there; a failed load corrects the list.
      return [DIAGRAM_DIR + "/" + PREFERRED_NAME];
    }
    // An empty answer here means every candidate was reported gone, and the
    // picker says so rather than offering files that cannot be read.
    return keepExisting(candidates);
  }

  // The file the viewer opens when nothing was asked for: the diagram named
  // diagram.mmd wherever it sits, otherwise the first one on offer. An empty
  // string means there is nothing to open.
  function defaultSource() {
    const preferred = state.sources.filter(function (name) {
      return baseName(name).toLowerCase() === PREFERRED_NAME;
    })[0];
    return preferred || state.sources[0] || "";
  }

  // A ?src= link may name the file as the manifest writes it or by base name
  // alone, because the base name is what a person types.
  function requestedSource(value) {
    if (!value) {
      return "";
    }
    const wanted = normalizeName(value).toLowerCase();
    const exact = state.sources.filter(function (name) {
      return name.toLowerCase() === wanted;
    })[0];
    if (exact) {
      return exact;
    }
    return (
      state.sources.filter(function (name) {
        return baseName(name).toLowerCase() === wanted;
      })[0] || ""
    );
  }

  /* ---------------------------------------------------------------- export */

  function safeBBox(svg) {
    try {
      const box = svg.getBBox();
      if (box && box.width && box.height) {
        return { x: box.x, y: box.y, width: box.width, height: box.height };
      }
    } catch (error) {
      return null;
    }
    return null;
  }

  // Prefer the frozen viewBox so exports match exactly what the viewer shows.
  function exportBox(svg) {
    const viewBox = svg.viewBox && svg.viewBox.baseVal;
    if (viewBox && viewBox.width > 0 && viewBox.height > 0) {
      return { x: viewBox.x, y: viewBox.y, width: viewBox.width, height: viewBox.height };
    }
    if (state.natural.width > 0 && state.natural.height > 0) {
      return { x: 0, y: 0, width: state.natural.width, height: state.natural.height };
    }
    return safeBBox(svg);
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function serializeSvg() {
    const svg = els.diagram.querySelector("svg");
    if (!svg) {
      return "";
    }
    const clone = svg.cloneNode(true);
    const size = exportBox(svg);
    if (size) {
      clone.setAttribute("viewBox", size.x + " " + size.y + " " + size.width + " " + size.height);
      clone.setAttribute("width", size.width);
      clone.setAttribute("height", size.height);
      clone.style.maxWidth = "none";

      const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      background.setAttribute("x", String(size.x));
      background.setAttribute("y", String(size.y));
      background.setAttribute("width", String(size.width));
      background.setAttribute("height", String(size.height));
      background.setAttribute("fill", cssVar("--bg", "#ffffff"));
      clone.insertBefore(background, clone.firstChild);
    }
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");

    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
      new XMLSerializer().serializeToString(clone);
  }

  // An export is named after the diagram, not after the folder it came from,
  // and without either suffix.
  function fileBase() {
    return baseName(state.source).replace(DIAGRAM_SUFFIX, "") || "diagram";
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  function exportSvg() {
    const markup = serializeSvg();
    if (!markup) {
      return;
    }
    download(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }), fileBase() + ".svg");
  }

  function exportPng() {
    const markup = serializeSvg();
    const svg = els.diagram.querySelector("svg");
    if (!markup || !svg) {
      return;
    }
    const box = exportBox(svg) || { x: 0, y: 0, width: 1200, height: 800 };
    const pixelRatio = 2;
    const image = new Image();
    image.onload = function () {
      const canvas = document.createElement("canvas");
      canvas.width = Math.ceil(box.width * pixelRatio);
      canvas.height = Math.ceil(box.height * pixelRatio);
      const context = canvas.getContext("2d");
      context.fillStyle = cssVar("--bg", "#ffffff");
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (blob) {
        if (blob) {
          download(blob, fileBase() + ".png");
        }
      }, "image/png");
    };
    image.onerror = function () {
      setError("PNG export failed. Use the SVG export instead.");
    };
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
  }

  /* ----------------------------------------------------------------- theme */

  function applyTheme(theme) {
    els.root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("mermaid-viewer-theme", theme);
    } catch (error) {
      /* storage unavailable, ignore */
    }
    render();
  }

  function initTheme() {
    let theme = null;
    try {
      theme = localStorage.getItem("mermaid-viewer-theme");
    } catch (error) {
      theme = null;
    }
    if (!theme) {
      theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    els.root.setAttribute("data-theme", theme);
  }

  /* --------------------------------------------------------------- wiring */

  // Reload does what its title says: it re-reads the folder as well as the file,
  // so a diagram added, renamed or deleted while the viewer was open appears in
  // the picker without restarting the server.
  async function refreshAndReload() {
    const previous = state.source;
    const names = await discoverSources();
    if (names.length) {
      state.sources = names;
      populatePicker(names);
    }
    if (state.sources.indexOf(previous) !== -1) {
      await loadSource(previous, { fresh: true });
      return;
    }
    await loadSource(defaultSource(), { fresh: true });
  }

  function bindEvents() {
    els.picker.addEventListener("change", function () {
      loadSource(els.picker.value);
    });
    els.reload.addEventListener("click", function () {
      refreshAndReload();
    });
    els.zoomIn.addEventListener("click", function () {
      zoomCenter(ZOOM_STEP);
    });
    els.zoomOut.addEventListener("click", function () {
      zoomCenter(1 / ZOOM_STEP);
    });
    els.zoomReset.addEventListener("click", resetView);
    els.fit.addEventListener("click", fit);
    els.panUp.addEventListener("click", function () {
      panBy(0, PAN_STEP);
    });
    els.panDown.addEventListener("click", function () {
      panBy(0, -PAN_STEP);
    });
    els.panLeft.addEventListener("click", function () {
      panBy(PAN_STEP, 0);
    });
    els.panRight.addEventListener("click", function () {
      panBy(-PAN_STEP, 0);
    });
    els.center.addEventListener("click", centerDiagram);
    els.theme.addEventListener("click", function () {
      applyTheme(els.root.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
    els.exportSvg.addEventListener("click", exportSvg);
    els.exportPng.addEventListener("click", exportPng);

    window.addEventListener("keydown", function (event) {
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      if (event.target && /input|select|textarea/i.test(event.target.tagName)) {
        return;
      }
      switch (event.key) {
        case "0":
          fit();
          break;
        case "+":
        case "=":
          zoomCenter(ZOOM_STEP);
          break;
        case "-":
        case "_":
          zoomCenter(1 / ZOOM_STEP);
          break;
        case "ArrowUp":
          panBy(0, PAN_STEP);
          break;
        case "ArrowDown":
          panBy(0, -PAN_STEP);
          break;
        case "ArrowLeft":
          panBy(PAN_STEP, 0);
          break;
        case "ArrowRight":
          panBy(-PAN_STEP, 0);
          break;
        default:
          return;
      }
      event.preventDefault();
    });

    window.addEventListener("resize", function () {
      if (state.rendered) {
        scheduleTransform();
      }
    });
  }

  async function init() {
    if (!window.mermaid) {
      setError("Mermaid library not found at vendor/mermaid.min.js.");
      return;
    }
    els.version.textContent = "mermaid " + (window.mermaid.version || "11");
    initTheme();
    bindEvents();
    bindPanZoom();

    state.sources = await discoverSources();
    populatePicker(state.sources);

    const requested = requestedSource(new URLSearchParams(window.location.search).get("src"));
    await loadSource(requested || defaultSource());

    window.setTimeout(fadeHint, HINT_TIMEOUT);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
