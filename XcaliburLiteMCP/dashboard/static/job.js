/*
 * The review surface for one job.
 *
 * CodeMirror's merge view draws the original and the proposed file side by side
 * with the changed lines marked, which is the comparison a reviewer needs before
 * approving. The module is loaded from an import map so nothing is bundled.
 */
import { EditorView, basicSetup } from "codemirror";
import { MergeView } from "@codemirror/merge";

const raw = document.getElementById("xc-edits");
const edits = raw ? JSON.parse(raw.textContent) : [];

const theme = EditorView.theme({
  "&": {
    fontSize: "12px",
    border: "1px solid var(--ink)",
    backgroundColor: "var(--paper)",
    color: "var(--ink)",
  },
  ".cm-scroller": { fontFamily: "var(--mono)" },
  ".cm-gutters": {
    backgroundColor: "var(--tint-deep)",
    color: "var(--ink)",
    border: "none",
    borderRight: "1px solid var(--ink)",
  },
  ".cm-changedLine": { backgroundColor: "rgba(0, 225, 255, 0.16)" },
  ".cm-changedText, .cm-insertedLine .cm-changedText": {
    background: "rgba(0, 255, 30, 0.55)",
  },
  ".cm-deletedChunk .cm-changedText": { background: "rgba(255, 0, 98, 0.45)" },
  ".cm-deletedLine": { backgroundColor: "rgba(255, 0, 98, 0.14)" },
});

edits.forEach((edit, index) => {
  const host = document.getElementById("merge-" + index);
  if (!host) {
    return;
  }
  new MergeView({
    a: { doc: edit.original, extensions: [basicSetup, EditorView.editable.of(false), theme] },
    b: { doc: edit.modified, extensions: [basicSetup, EditorView.editable.of(false), theme] },
    parent: host,
    highlightChanges: true,
    gutter: true,
    collapseUnchanged: { margin: 4, minSize: 6 },
  });
});
