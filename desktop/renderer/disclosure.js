'use strict';

/**
 * A card that folds, and the memory that keeps it folded.
 *
 * The reason this is a module rather than a `details` element written into a
 * view is the renderer's own rule. A screen is rebuilt from the store on every
 * event, and a streamed turn arrives as dozens of fragments, so a view that
 * created its own fold would recreate it on each fragment and the fold would
 * spring open under the reader's hand several times a second. The state of a
 * fold therefore cannot live in the element. It lives in a registry outside the
 * render, keyed by a name the view declares, and the element is drawn from the
 * registry every time it is built.
 *
 * Two properties are worth stating because both are easy to lose.
 *
 * The first is that a fold must be real rather than visual. The body carries the
 * `hidden` attribute and the stylesheet restates `[hidden]` as `display: none`,
 * because the attribute is a user agent style at the lowest specificity and any
 * `display` rule of ours would beat it. A body that is merely transparent still
 * holds its height, and a folded card that still occupies the screen has not
 * made anything fit.
 *
 * The second is that folding must not change any width. The head keeps its
 * place and the body keeps its box, so a reader who folds a card does not get a
 * reflow of the screen behind the finger that folded it.
 *
 * The registry is deliberately free of the document, so it can be read by a
 * check that runs without a window. `registry` is the memory; `card` is the
 * element built from it.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  /**
   * The memory of which folds are shut.
   *
   * A key is declared with the state it should have when it is first drawn, and
   * the declaration is only honoured the first time. Every later build reads
   * what is remembered instead, which is what lets a reader fold a card and have
   * it stay folded across the redraws that follow. A key that was never declared
   * has no state at all rather than a false one, because "folded" and "never
   * seen" are different facts and a summary that ran them together would report
   * a fold that nobody made.
   */
  function registry() {
    const open = new Map();

    return {
      define(key, preferred) {
        if (!open.has(key)) {
          open.set(key, Boolean(preferred));
        }

        return open.get(key);
      },
      isOpen(key) {
        return open.has(key) ? open.get(key) : null;
      },
      toggle(key) {
        const next = !open.get(key);
        open.set(key, next);

        return next;
      },
      set(key, value) {
        open.set(key, Boolean(value));

        return open.get(key);
      },
      keys() {
        return Array.from(open.keys());
      },
      snapshot() {
        return Object.fromEntries(open);
      }
    };
  }

  /** One registry for the whole window: the folds are a property of the reader. */
  const store = registry();

  function bodyId(key) {
    return `disclosure-${String(key).replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`;
  }

  /** Draw an element into the state it should be in. The one place that shapes it. */
  function paint(node, open) {
    const head = node.querySelector('.disclosure-head');
    const body = node.querySelector('.disclosure-body');
    const mark = node.querySelector('.disclosure-mark');
    node.dataset.open = open ? 'true' : 'false';
    if (head !== null) {
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (mark !== null) {
      mark.textContent = open ? '[-]' : '[+]';
    }
    if (body !== null) {
      if (open) {
        body.removeAttribute('hidden');
      } else {
        body.setAttribute('hidden', 'hidden');
      }
    }

    return node;
  }

  /**
   * A folding card.
   *
   * The head is a button rather than a click handler on a div, so the fold is
   * reachable by keyboard and announced by a screen reader. It carries
   * `aria-expanded` and points at the body it controls, and the body carries the
   * id that pointer names.
   *
   * A caller that already has a head of its own, as the transcript does with a
   * row of chips, passes it in `head` and the title is left unused. The head is
   * still wrapped in the button, because the button is what makes the fold a
   * control rather than a decoration.
   */
  function card(key, options, children) {
    const dom = UI.dom;
    const settings = options || {};
    const open = store.define(key, settings.open);
    const id = bodyId(key);
    const mark = dom.el('span', { class: 'disclosure-mark', 'aria-hidden': 'true' });
    const body = dom.el('div', { class: 'disclosure-body', id: `${id}-body` }, children);
    const head = dom.el('button', {
      class: 'disclosure-head',
      type: 'button',
      'aria-expanded': open ? 'true' : 'false',
      'aria-controls': `${id}-body`
    }, [
      mark,
      settings.head
        ? settings.head
        : dom.el('span', { class: 'disclosure-title', text: settings.title }),
      settings.head || !settings.summary
        ? null
        : dom.el('span', { class: 'muted mono disclosure-summary', text: settings.summary })
    ]);
    const node = dom.el('section', {
      class: settings.sectionClass || `card disclosure${settings.paper ? ' card-paper' : ''}`,
      dataset: { key, open: open ? 'true' : 'false' }
    }, [head, body]);

    paint(node, open);
    head.addEventListener('click', () => paint(node, store.toggle(key)));

    return node;
  }

  /** Fold or unfold one drawn card, and remember it. Used by the window and by checks. */
  function apply(node, open) {
    if (node === null || node === undefined) {
      return false;
    }
    store.set(node.dataset.key, open);

    return paint(node, open === true) !== null;
  }

  /**
   * What the drawn folds are, as a reading rather than an opinion.
   *
   * The height of each body is read from the painted box, so a body that is
   * still occupying space while claiming to be folded is reported as the height
   * it has rather than as the state it declares.
   */
  function reading(root) {
    const scope = root || document;
    const nodes = Array.from(scope.querySelectorAll('.disclosure'));

    return {
      total: nodes.length,
      open: nodes.filter((node) => node.dataset.open === 'true').length,
      nodes: nodes.map((node) => {
        const head = node.querySelector('.disclosure-head');
        const body = node.querySelector('.disclosure-body');

        return {
          key: node.dataset.key,
          open: node.dataset.open === 'true',
          expanded: head === null ? null : head.getAttribute('aria-expanded'),
          height: body === null ? 0 : Math.round(body.getBoundingClientRect().height)
        };
      })
    };
  }

  UI.disclosure = { registry, card, apply, reading, store, snapshot: () => store.snapshot() };
}(window.AgentUI));
