"use strict";

/**
 * Edge-label layout for rendered mermaid diagrams.
 *
 * Mermaid places every edge label at the midpoint of its edge and draws them all
 * into a single layer, in edge order. Two edges that run through the same
 * corridor therefore produce labels that sit on top of each other, and because
 * the layer is painted in that order the later label buries the earlier one: its
 * text is no longer readable. Mermaid also paints the node layer after the label
 * layer, so a node box can hide a label outright.
 *
 * This module corrects both after a render:
 *   1. the label layer is raised above the node layer,
 *   2. each label gets a solid backing so an edge line never runs through its
 *      text,
 *   3. labels that collide are displaced by the smallest shift that clears the
 *      collision, measured in SVG user units so the result is independent of the
 *      current pan and zoom.
 *
 * Nothing here depends on layout: mermaid writes the label centre on the outer
 * group and the half-size shift on the inner one, so the geometry is read
 * straight from those attributes and the correction is written back the same
 * way. The result survives SVG and PNG export, because both serialise the live
 * DOM.
 */
(function (global) {
  const LABEL_SELECTOR = "g.edgeLabel";
  const LAYER_SELECTOR = "g.edgeLabels";
  const INNER_SELECTOR = ".label";

  // Minimum clear space kept between two labels, in SVG user units.
  const LABEL_GAP = 8;
  // A label is never displaced further than this from where mermaid put it, so a
  // pathological diagram degrades to "overlapping" rather than "label on Mars".
  const MAX_SHIFT = 240;
  // Each pass resolves one collision; a handful is enough for real diagrams.
  const MAX_PASSES = 6;

  function round(value) {
    return Math.round(value * 1000) / 1000;
  }

  function numericAttribute(element, name) {
    if (!element) {
      return 0;
    }
    const value = parseFloat(element.getAttribute(name));
    return Number.isFinite(value) ? value : 0;
  }

  // Matches "translate(12, 34)", "translate(12 34)" and mermaid's unspaced form.
  function parseTranslate(value) {
    const match = /translate\(\s*(-?[\d.]+)(?:[ ,]+(-?[\d.]+))?\s*\)/.exec(value || "");
    if (!match) {
      return null;
    }
    const x = parseFloat(match[1]);
    const y = match[2] === undefined ? 0 : parseFloat(match[2]);
    return Number.isFinite(x) && Number.isFinite(y) ? { x: x, y: y } : null;
  }

  function hasText(element) {
    return (element.textContent || "").trim().length > 0;
  }

  // Fallback for diagram types that do not use the flowchart label structure:
  // measure the rendered box and map it back into the SVG's user space.
  function measuredBox(element, svg) {
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return null;
    }
    const matrix = svg.getScreenCTM();
    if (!matrix) {
      return null;
    }
    const inverse = matrix.inverse();
    const topLeft = svg.createSVGPoint();
    topLeft.x = rect.left;
    topLeft.y = rect.top;
    const bottomRight = svg.createSVGPoint();
    bottomRight.x = rect.right;
    bottomRight.y = rect.bottom;
    const start = topLeft.matrixTransform(inverse);
    const end = bottomRight.matrixTransform(inverse);
    return {
      x: start.x,
      y: start.y,
      width: end.x - start.x,
      height: end.y - start.y
    };
  }

  function labelBox(element, svg) {
    const centre = parseTranslate(element.getAttribute("transform"));
    const inner = element.querySelector(INNER_SELECTOR);
    const shift = inner ? parseTranslate(inner.getAttribute("transform")) : null;
    const foreign = element.querySelector("foreignObject");
    const width = numericAttribute(foreign, "width");
    const height = numericAttribute(foreign, "height");

    if (centre && shift && width > 0 && height > 0) {
      return { x: centre.x + shift.x, y: centre.y + shift.y, width: width, height: height };
    }
    return measuredBox(element, svg);
  }

  // Prefer the outer group, whose transform is the label's placement, and fall
  // back to the inner group for structures that put it there instead.
  function shiftTarget(element) {
    if (parseTranslate(element.getAttribute("transform"))) {
      return element;
    }
    const inner = element.querySelector(INNER_SELECTOR);
    if (inner && parseTranslate(inner.getAttribute("transform"))) {
      return inner;
    }
    return null;
  }

  function applyShift(element, shift) {
    const target = shiftTarget(element);
    if (!target) {
      return false;
    }
    const centre = parseTranslate(target.getAttribute("transform"));
    const next = "translate(" + round(centre.x + shift.dx) + ", " + round(centre.y + shift.dy) + ")";
    if (next === target.getAttribute("transform")) {
      return false;
    }
    target.setAttribute("transform", next);
    return true;
  }

  function overlaps(a, b, gap) {
    return (
      a.x < b.x + b.width + gap &&
      b.x < a.x + a.width + gap &&
      a.y < b.y + b.height + gap &&
      b.y < a.y + a.height + gap
    );
  }

  /**
   * Smallest displacement that takes `box` clear of `obstacles`.
   *
   * Each pass picks the first obstacle still in the way and compares the four
   * ways out of it, taking the shortest. Repeating resolves chains, where moving
   * clear of one label runs into the next.
   */
  function separate(box, obstacles) {
    const work = { x: box.x, y: box.y, width: box.width, height: box.height };
    const total = { dx: 0, dy: 0 };

    for (let pass = 0; pass < MAX_PASSES; pass += 1) {
      const blocker = obstacles.find(function (other) {
        return overlaps(work, other, LABEL_GAP);
      });
      if (!blocker) {
        break;
      }

      const options = [
        { dx: 0, dy: blocker.y + blocker.height + LABEL_GAP - work.y },
        { dx: 0, dy: blocker.y - LABEL_GAP - (work.y + work.height) },
        { dx: blocker.x + blocker.width + LABEL_GAP - work.x, dy: 0 },
        { dx: blocker.x - LABEL_GAP - (work.x + work.width), dy: 0 }
      ];
      options.sort(function (a, b) {
        return Math.abs(a.dx) + Math.abs(a.dy) - (Math.abs(b.dx) + Math.abs(b.dy));
      });

      const chosen = options[0];
      if (
        Math.abs(total.dx + chosen.dx) > MAX_SHIFT ||
        Math.abs(total.dy + chosen.dy) > MAX_SHIFT
      ) {
        break;
      }

      work.x += chosen.dx;
      work.y += chosen.dy;
      total.dx += chosen.dx;
      total.dy += chosen.dy;
    }

    return total;
  }

  function raiseLabelLayer(svg) {
    const layer = svg.querySelector(LAYER_SELECTOR);
    if (!layer || !layer.parentNode) {
      return false;
    }
    const parent = layer.parentNode;
    if (parent.lastElementChild === layer) {
      return false;
    }
    parent.appendChild(layer);
    return true;
  }

  // The backing sits on the innermost HTML element mermaid already gives a
  // background, so the text stays legible where an edge line crosses it. An
  // outline is used rather than a border because it does not change layout.
  function backingElement(element) {
    return (
      element.querySelector("div.labelBkg") ||
      element.querySelector("foreignObject > div") ||
      element.querySelector("foreignObject > *")
    );
  }

  function styleBacking(element, background, outline) {
    const backing = backingElement(element);
    if (!backing || !backing.style) {
      return false;
    }
    if (background) {
      backing.style.background = background;
    }
    if (outline) {
      backing.style.outline = outline;
    }
    return true;
  }

  function boundsOf(boxes) {
    if (!boxes.length) {
      return null;
    }
    return boxes.reduce(
      function (bounds, box) {
        return {
          minX: Math.min(bounds.minX, box.x),
          minY: Math.min(bounds.minY, box.y),
          maxX: Math.max(bounds.maxX, box.x + box.width),
          maxY: Math.max(bounds.maxY, box.y + box.height)
        };
      },
      { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity }
    );
  }

  /**
   * Layout every edge label in a freshly injected diagram.
   *
   * @param {SVGSVGElement} svg rendered diagram root
   * @param {{background?: string, outline?: string}} [options] label chip styling
   * @returns {{total: number, moved: number, raised: boolean, bounds: object|null}}
   *   counts for callers that want to report, and the union of the placed label
   *   boxes so the viewBox can be grown to keep a displaced label in frame.
   */
  function tidy(svg, options) {
    const settings = options || {};
    const result = { total: 0, moved: 0, raised: raiseLabelLayer(svg), bounds: null };
    if (!svg) {
      return result;
    }

    const labels = Array.from(svg.querySelectorAll(LABEL_SELECTOR)).filter(hasText);
    result.total = labels.length;

    labels.forEach(function (element) {
      styleBacking(element, settings.background, settings.outline);
    });

    const placed = [];
    labels.forEach(function (element) {
      const box = labelBox(element, svg);
      if (!box) {
        return;
      }
      const shift = separate(box, placed);
      if ((shift.dx || shift.dy) && applyShift(element, shift)) {
        box.x += shift.dx;
        box.y += shift.dy;
        result.moved += 1;
      }
      placed.push(box);
    });

    result.bounds = boundsOf(placed);
    return result;
  }

  global.MermaidViewerLabels = {
    tidy: tidy,
    // Exposed for tests and for callers that need the same geometry.
    labelBox: labelBox,
    labelSelector: LABEL_SELECTOR
  };
})(window);
