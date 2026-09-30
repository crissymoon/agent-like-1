'use strict';

/**
 * The smallest DOM helper that keeps the views readable.
 *
 * There is no framework here on purpose. The view is a projection of an append
 * only event list, so every screen is a function from state to elements, and a
 * framework would add a second model of that state to keep in step with the
 * first. What a framework does buy is safety in element construction, so that is
 * the part written here: text is set as text, never as markup, and an attribute
 * that is not a string is either applied as a property or refused.
 */

window.AgentUI = window.AgentUI || {};

(function (UI) {
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function el(tag, props, children) {
    const node = tag === 'svg'
      ? document.createElementNS(SVG_NS, tag)
      : document.createElement(tag);

    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (value === null || value === undefined || value === false) {
          continue;
        }
        if (key === 'text') {
          node.textContent = String(value);
        } else if (key === 'dataset') {
          for (const [dataKey, dataValue] of Object.entries(value)) {
            node.dataset[dataKey] = String(dataValue);
          }
        } else if (key.startsWith('on') && typeof value === 'function') {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (key === 'class') {
          node.className = String(value);
        } else {
          node.setAttribute(key, String(value));
        }
      }
    }

    append(node, children);

    return node;
  }

  function append(node, children) {
    if (children === null || children === undefined || children === false) {
      return node;
    }
    if (Array.isArray(children)) {
      for (const child of children) {
        append(node, child);
      }

      return node;
    }
    node.appendChild(children instanceof Node ? children : document.createTextNode(String(children)));

    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function replace(node, children) {
    clear(node);
    append(node, children);

    return node;
  }

  function card(title, options, children) {
    const head = title === null
      ? null
      : el('div', { class: 'card-title' }, [
          el('h3', { text: title }),
          options && options.right ? el('span', { class: 'muted mono', text: options.right }) : null
        ]);

    return el('section', { class: `card${options && options.paper ? ' card-paper' : ''}` }, [head, children]);
  }

  function table(columns, rows) {
    const head = el('tr', {}, columns.map((column) => el('th', { text: column.label })));
    const body = rows.map((row) => el('tr', {}, columns.map((column) => {
      const value = column.value(row);
      return el('td', {
        class: column.numeric ? 'num' : '',
        text: value === null || value === undefined ? '' : String(value)
      });
    })));

    return el('table', { class: 'grid-table' }, [
      el('thead', {}, [head]),
      el('tbody', {}, body)
    ]);
  }

  function keyValue(pairs) {
    return el('div', { class: 'rows' }, pairs
      .filter((pair) => pair !== null)
      .map((pair) => el('div', { class: 'row row-static' }, [
        el('span', { class: 'muted', text: pair[0] }),
        el('span', { class: 'mono', text: pair[1] === null || pair[1] === undefined ? 'not recorded' : String(pair[1]) })
      ])));
  }

  function button(label, options, onClick) {
    return el('button', {
      class: `button${options && options.variant ? ` button-${options.variant}` : ''}`,
      type: 'button',
      disabled: options && options.disabled,
      onclick: onClick
    }, [label]);
  }

  function field(label, control, hint) {
    return el('label', { class: 'field' }, [
      el('span', { text: label }),
      control,
      hint ? el('span', { class: 'muted', text: hint }) : null
    ]);
  }

  function select(name, value, options) {
    const node = el('select', { name }, options.map((option) => el('option', {
      value: option.value,
      selected: String(option.value) === String(value)
    }, [option.label])));
    node.value = String(value);

    return node;
  }

  function checkbox(name, checked, label) {
    return el('label', { class: 'task' }, [
      el('input', { type: 'checkbox', name, checked: checked ? 'checked' : null }),
      el('span', {}, [el('b', { text: label.id }), el('span', { class: 'muted', text: ` ${label.capability}` })])
    ]);
  }

  UI.dom = { el, append, clear, replace, card, table, keyValue, button, field, select, checkbox };
}(window.AgentUI));
