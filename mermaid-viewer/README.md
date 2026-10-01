# mermaid viewer

A single page that draws the `.mmd` files in `diagrams/`. The Mermaid build it
draws with is vendored beside it, so the page reaches nothing on the network and
the same source renders the same way on any machine that opens it.

GitHub draws these files too, with its own build and its own page, and the two do
not always agree. The section below is what to read when a diagram looks wrong on
the repository page, because that difference is in the renderer rather than in
the diagram.

## Running it

From the repository root:

```bash
./mermaid-viewer/serve.sh          # http://127.0.0.1:8765/index.html
./mermaid-viewer/serve.sh 9000     # or any other port
```

Or from this folder, `./serve.sh`. Ctrl+C stops it.

Opening `index.html` straight from the file system is not enough, and the page
says so: a browser refuses to hand a page loaded over `file://` any neighbouring
file, so there is nothing for the picker to list and no diagram to read. Serve the
folder and open the address the server prints.

## What it does

The picker at the top selects a diagram. A file can also be named in the link, as
`?src=db-h-scaling.mmd` or with the folder, as `?src=diagrams/db-h-scaling.mmd`.

| input | what happens |
|---|---|
| drag | pans, one pixel of pointer movement for one pixel of diagram |
| wheel, or a trackpad pinch | zooms about the pointer |
| double-click | fits the diagram to the window |
| the panel, bottom right | zoom out, zoom in, fit, centre, and four arrow buttons that pan one step |
| arrow keys | pan |
| `0` | fit; `+` and `-` zoom |
| `SVG`, `PNG` | downloads the diagram as drawn, with the label corrections below applied to it |
| `Theme` | switches the page and the diagram between light and dark |

The readout beside the zoom buttons is a button: it returns the diagram to 100%
and centres it.

## When a diagram does not show correctly

Read it here first. Two things are going on.

**GitHub renders with a build that is not this one.** Every diagram in this
folder is drawn on the repository page by GitHub's own Mermaid build, at its own
version, at whatever width that page gives it. Nothing in this repository selects
that build or sets that width, so the two pictures can differ in where a node
lands and how wide a label is allowed to be.

**This viewer adds one pass that GitHub does not run.** Mermaid puts every edge
label at the midpoint of its edge and paints all of them into a single layer in
edge order, so two edges that run through the same corridor produce labels that
sit on top of each other, and the layer is painted before the nodes, so a node box
can cover a label as well. `labels.js` corrects both after each render: the label
layer is raised above the nodes, each label gets a solid backing so an edge line
never runs through its text, and labels that overlap are moved the shortest
distance that clears them. It travels with the export, so a downloaded SVG or PNG
carries the corrected layout.

`diagrams/db-h-scaling.mmd` is the file where this shows. Four of its edges run
through two corridors, so without the pass its two pairs of edge labels land on
top of one another. In this viewer they are separated and all four are readable.
On the repository page they are not, because that page runs its own build and
there is no hook to hang a correction on. The source is correct as written and is
not going to be bent to suit a renderer this repository does not control.

If a page has to show the corrected picture, the viewer is the way to make it:
`SVG` or `PNG` writes the diagram as this viewer draws it, and that file can be
committed and linked like any other image. The `.mmd` source stays the source.

## Browsers

Chromium based browsers are what this has been tested in: Chrome, Edge, Brave,
Chromium, and the headless Chromium build the repository's own checks draw
diagrams with. Pointer events, `requestAnimationFrame`, CSS custom properties and
a canvas the export draws into are the features it needs, and all of them are in
every browser above.

Firefox and Safari have not been run against it, so neither is claimed. The part
most likely to differ is the export: a flowchart's labels are HTML inside a
`foreignObject`, and both PNG and SVG export serialise the rendered diagram,
which is where engines are least alike. If the picture matters, open it in a
Chromium based browser.

## The diagrams

| file | what it is |
|---|---|
| `diagrams/overall-flow.mmd` | The agent loop end to end, from an inbound prompt through the vector registry and the sandbox to the released function. |
| `diagrams/db-h-scaling.mmd` | Horizontally scaled execution nodes over a content addressable store, drawn with styled subgraphs. This is the one whose labels are separated here and overlap on GitHub. |
| `diagrams/database-orchestration.mmd` | The same work as a sequence diagram, so it is read in time order rather than as a graph. |

## How the picker is fed

The picker is not written by hand, so a diagram dropped into `diagrams/` appears
in it without an edit. `serve.py` lists this folder and every subfolder and serves
the result as `/sources.json` on each request. It also writes the same list to
`sources.json` on disk, which is what makes the viewer work behind a plain static
host that cannot compute a listing: `python3 -m http.server`, nginx, or anything
else that serves the folder. `sources.json` is generated. Editing it by hand
achieves nothing, because the next request rewrites it.

A name ending in `.mmd`, `.mermaid`, `.mmd.md` or `.mermaid.md` is a diagram. A
name that appears in a listing but not on disk is dropped rather than listed, and
a diagram that fails to load is removed from the picker with the reason shown.
Where neither a manifest nor a listing answers, the picker is empty and the page
names the two things that would fill it, rather than guessing at a file it has
never seen.

## The files

| file | what it is |
|---|---|
| `index.html` | The page: toolbar, stage, error panel, and the viewport controls. |
| `styles.css` | The theme, built on the desktop shell's tokens: square corners, hairline borders, one tint and one accent, dark derived from light. |
| `viewer.js` | Source discovery, the render, pan and zoom, and the two exports. The wrapper transform is the only source of the diagram's position. |
| `labels.js` | The post-render label pass described above. It is a no-op for a diagram type that has no edge labels to separate. |
| `serve.py` | The local server and the manifest. |
| `serve.sh` | Runs `serve.py` on the port given, or 8765. |
| `sources.json` | The generated manifest. Do not edit it. |
| `vendor/mermaid.min.js` | Unmodified upstream Mermaid 11.17.2, with the URL and the sha256 it was checked against recorded in a comment in `index.html`. |
