"use strict";

/**
 * Mermaid Viewer
 * Renders local .mmd files listed in sources.json with pan, zoom,
 * theme switching and SVG/PNG export. No network access required.
 */
(function () {
  const MANIFEST = "sources.json";
  const DEFAULT_SOURCE = "diagram.mmd";
  const MIN_SCALE = 0.1;
  const MAX_SCALE = 8;

  const els = {
    picker: document.getElementById("source-picker"),
    reload: document.getElementById("btn-reload"),
    zoomIn: document.getElementById("btn-zoom-in"),
    zoomOut: document.getElementById("btn-zoom-out"),
    zoomReset: document.getElementById("btn-zoom-reset"),
    fit: document.getElementById("btn-fit"),
    theme: document.getElementById("btn-theme"),
    exportSvg: document.getElementById("btn-export-svg"),
    exportPng: document.getElementById("btn-export-png"),
    stage: document.getElementById("stage"),
    canvas: document.getElementById("canvas"),
    diagram: document.getElementById("diagram"),
    error: document.getElementById("error"),
    errorBody: document.getElementById("error-body"),
    hint: document.getElementById("hint"),
    version: document.getElementById("mmd-version"),
    root: document.documentElement
  };

  const state = {
    source: DEFAULT_SOURCE,
    code: "",
    svg: "",
    scale: 1,
    x: 0,
    y: 0,
    rendered: false
  };

  function setError(message) {
    els.errorBody.textContent = message || "";
    els.error.hidden = !message;
  }

  function currentTheme() {
    return els.root.getAttribute("data-theme") === "dark" ? "dark" : "default";
  }

  function configureMermaid() {
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: currentTheme(),
      fontFamily: "Helvetica, Arial, sans-serif",
      flowchart: { htmlLabels: true, curve: "basis", useMaxWidth: false },
      maxTextSize: 500000
    });
  }

  function applyTransform() {
    els.diagram.style.transform =
      "translate(" + state.x + "px, " + state.y + "px) scale(" + state.scale + ")";
    els.zoomReset.textContent = Math.round(state.scale * 100) + "%";
  }

  function zoomAt(clientX, clientY, factor) {
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, state.scale * factor));
    if (next === state.scale) {
      return;
    }
    const rect = els.canvas.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const ratio = next / state.scale;
    state.x = px - (px - state.x) * ratio;
    state.y = py - (py - state.y) * ratio;
    state.scale = next;
    applyTransform();
  }

  function zoomCenter(factor) {
    const rect = els.canvas.getBoundingClientRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  function diagramSize() {
    const svg = els.diagram.querySelector("svg");
    if (!svg) {
      return { width: 0, height: 0 };
    }
    const box = svg.getBoundingClientRect();
    return { width: box.width / state.scale, height: box.height / state.scale };
  }

  function fit() {
    const size = diagramSize();
    if (!size.width || !size.height) {
      return;
    }
    const rect = els.canvas.getBoundingClientRect();
    const padding = 48;
    const scale = Math.min(
      (rect.width - padding) / size.width,
      (rect.height - padding) / size.height
    );
    state.scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
    state.x = (rect.width - size.width * state.scale) / 2;
    state.y = (rect.height - size.height * state.scale) / 2;
    applyTransform();
  }

  function bindPanZoom() {
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;
    const pointers = new Map();
    let pinchDistance = 0;

    els.canvas.addEventListener("pointerdown", function (event) {
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
          const [a, b] = Array.from(pointers.values());
          zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, distance / pinchDistance);
        }
        pinchDistance = distance;
        return;
      }

      if (dragging) {
        state.x = originX + (event.clientX - startX);
        state.y = originY + (event.clientY - startY);
        applyTransform();
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

    function pointerDistance() {
      const [a, b] = Array.from(pointers.values());
      return Math.hypot(a.x - b.x, a.y - b.y);
    }

    els.canvas.addEventListener(
      "wheel",
      function (event) {
        event.preventDefault();
        const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
        zoomAt(event.clientX, event.clientY, factor);
      },
      { passive: false }
    );

    els.canvas.addEventListener("dblclick", fit);
  }

  function serializeSvg() {
    const svg = els.diagram.querySelector("svg");
    if (!svg) {
      return "";
    }
    const clone = svg.cloneNode(true);
    const size = safeBBox(svg);
    if (size) {
      clone.setAttribute("viewBox", size.x + " " + size.y + " " + size.width + " " + size.height);
      clone.setAttribute("width", size.width);
      clone.setAttribute("height", size.height);
      clone.setAttribute("style", "max-width:none");

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

  function safeBBox(svg) {
    try {
      const box = svg.getBBox();
      if (box && box.width && box.height) {
        return box;
      }
    } catch (error) {
      return null;
    }
    return null;
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function fileBase() {
    return state.source.replace(/\.mmd$|\.mermaid$|\.txt$/i, "") || "diagram";
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
    const box = safeBBox(svg) || { x: 0, y: 0, width: 1200, height: 800 };
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

  async function render() {
    setError("");
    if (!state.code.trim()) {
      state.rendered = false;
      els.diagram.innerHTML = "";
      return;
    }
    configureMermaid();
    const renderId = "mmd-" + Date.now().toString(36);
    try {
      const result = await window.mermaid.render(renderId, state.code);
      els.svg = result.svg;
      els.diagram.innerHTML = result.svg;
      state.rendered = true;
      requestAnimationFrame(fit);
    } catch (error) {
      state.rendered = false;
      els.diagram.innerHTML = "";
      setError((error && (error.message || error.str)) || "Unknown mermaid error.");
    }
  }

  async function loadSource(name) {
    state.source = name || DEFAULT_SOURCE;
    els.picker.value = state.source;
    try {
      const response = await fetch(state.source, { cache: "no-store" });
      if (!response.ok) {
        throw new Error("HTTP " + response.status + " while loading " + state.source);
      }
      state.code = await response.text();
      els.diagram.style.transform = "none";
      state.scale = 1;
      state.x = 0;
      state.y = 0;
      await render();
    } catch (error) {
      state.code = "";
      setError(error.message || String(error));
    }
  }

  async function populatePicker() {
    let sources = [DEFAULT_SOURCE];
    try {
      const response = await fetch(MANIFEST, { cache: "no-store" });
      if (response.ok) {
        const parsed = await response.json();
        if (Array.isArray(parsed) && parsed.length) {
          sources = parsed;
        }
      }
    } catch (error) {
      sources = [DEFAULT_SOURCE];
    }
    els.picker.innerHTML = "";
    sources.forEach(function (name) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      els.picker.appendChild(option);
    });
    return sources;
  }

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

  function bindEvents() {
    els.picker.addEventListener("change", function () {
      loadSource(els.picker.value);
    });
    els.reload.addEventListener("click", function () {
      loadSource(state.source);
    });
    els.zoomIn.addEventListener("click", function () {
      zoomCenter(1.2);
    });
    els.zoomOut.addEventListener("click", function () {
      zoomCenter(1 / 1.2);
    });
    els.zoomReset.addEventListener("click", function () {
      state.scale = 1;
      state.x = 0;
      state.y = 0;
      applyTransform();
    });
    els.fit.addEventListener("click", fit);
    els.theme.addEventListener("click", function () {
      applyTheme(els.root.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
    els.exportSvg.addEventListener("click", exportSvg);
    els.exportPng.addEventListener("click", exportPng);

    window.addEventListener("keydown", function (event) {
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      if (event.key === "0") {
        fit();
      } else if (event.key === "+" || event.key === "=") {
        zoomCenter(1.2);
      } else if (event.key === "-") {
        zoomCenter(1 / 1.2);
      }
    });

    window.addEventListener("resize", function () {
      if (state.rendered) {
        applyTransform();
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
    const sources = await populatePicker();
    const requested = new URLSearchParams(window.location.search).get("src");
    const initial = requested && sources.indexOf(requested) !== -1 ? requested : sources[0];
    await loadSource(initial);
    setTimeout(function () {
      els.hint.style.opacity = "0";
    }, 6000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
