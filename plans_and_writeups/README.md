# plans_and_writeups

Public article view. A static page that lists every exported write-up and links to the rendered article pages. This folder is the published copy: it holds no editor code, only the viewer and the files the editor writes here.

## Layout

```
plans_and_writeups/
  index.html            viewer: article grid, search, tags, theme toggle, recents
  exports.json          index of exported articles, read by index.html
  track.php             optional analytics endpoint, writes engage/track.json
  run.sh                local PHP server with automatic port selection
  pages/
    *.html              rendered articles, one per export
    *.md                markdown source copied alongside each export
    imgs/               images referenced by the articles
    assets/page-layout.js
  engage/track.json     created at runtime by track.php, not version controlled
```

`engage/` is host state rather than content. `track.php` creates it on the first
visit and appends to it on every one after that, so it is held out of git the way
the other run outputs are: a tracked file that every page view rewrites leaves
the checkout dirty, and the release review reads the commit, so it refuses a
checkout that holds anything else.

`index.html` reads `exports.json`, then links each card to `<prefix>/<id>.html`, where `<prefix>` is `/pages` locally and `/docs/pages` on the live host. `id` is the page basename without `.html`, and the export path writes it that way, so a row and its file agree by construction.

## View it locally

```bash
cd ~/Documents/gemma/plans_and_writeups
./run.sh
```

`run.sh` finds an open port starting at 8181, starts PHP on `127.0.0.1`, prints the URL, opens a browser when run from a terminal, and stops on Ctrl+C. It exits with a message if PHP is missing, if a pinned `PORT` is taken, or if the bind fails, in which case it also prints PHP's own reason.

| variable | default | meaning |
| --- | --- | --- |
| `PORT` | auto | use this exact port, fail if it is taken |
| `BASE_PORT` | `8181` | where the scan for a free port starts |
| `PORT_LIMIT` | `40` | how many ports to try |
| `HOST` | `127.0.0.1` | bind address; `0.0.0.0` exposes it on the LAN |
| `OPEN` | `1` | set `0` to skip opening a browser |

Examples:

```bash
PORT=9000 ./run.sh          # exact port
OPEN=0 ./run.sh             # no browser
HOST=0.0.0.0 ./run.sh       # reachable from other devices, browse via localhost
```

The viewer needs a server rather than `file://`, because it loads `exports.json` with `fetch` and browsers block that for local files. PHP is needed for `run.sh` and for `track.php`; if PHP is unavailable, any static server rooted at this folder works and article rendering is unaffected, only visit tracking stops.

Always browse through `http://localhost:<port>` or `http://127.0.0.1:<port>`. Reaching the server by any other name switches the viewer to the live path `/docs/pages`, which does not exist locally, so cards will 404.

## How content gets here

Content is produced by the MD Editor in `~/Documents/agent-wrkn/md_editor`, not in this repo. The editor writes to this folder through its export destination `Documents/gemma`, defined once in the editor's `destinations.json`.

Export HTML with that destination selected writes:

1. `pages/<timestamp>_<slug>.html`, the rendered article.
2. `pages/<timestamp>_<slug>.md`, a copy of the markdown source.
3. An upserted row in `exports.json`.

Sync Imgs with that destination selected copies only the images the articles
point at into `pages/imgs/`, never the editor's whole `imgs/` folder: the
references come from the open document plus every `pages/*.md` and
`pages/*.html` already here. A destination with pruning on also has copies
nothing references removed, so this folder holds exactly the images its articles
use.

## The two readers

Each export answers to two readers, and each one reads a different file:

| file | reader | how a reference is resolved |
| --- | --- | --- |
| `pages/<name>.html` | the local viewer, which serves this folder | from the site root, `/imgs/a.webp` |
| `pages/<name>.md` | GitHub, which renders the repository | from the file, `imgs/a.webp` |

The markdown is copied out of the editor as it was typed, which is addressed for
the server, so the export hands the copy to `~/Documents/gemma/tools/github_md.py`,
named by the destination's `md_converter` setting. It rewrites only the markdown:
a root relative image becomes relative to the page, a link to an exported page
becomes a link to the markdown it was generated from, and a reference that is
already right is left alone. The page itself is never touched.

Run it by hand after adding articles another way:

```
python3 tools/github_md.py --check     # report, change nothing, non-zero if it would rewrite
python3 tools/github_md.py --write     # rewrite every markdown under pages/
```

`exports.json` is owned by the export path. It is upserted by `id` or `filename`, so re-exporting the same source updates the existing row and preserves `created` and `tags`. The file is validated before anything is written, and a page is never written without a matching row. Edit it by hand only if you accept that the next export of that same article will overwrite your row.

A row looks like this:

| field | meaning |
| --- | --- |
| `id` | stable key and the upsert key; also the article URL, so `pages/<id>.html` must exist |
| `title` | card heading |
| `description` | card subtitle, often the source path |
| `filename` | file under `pages/`, normally `<id>.html` |
| `source_md` | markdown source copied into `pages/` |
| `created` | `YYYY-MM-DD HH:MM`, the grid sorts newest first |
| `tags` | array of strings, used by the viewer filters |

## Publishing

This is the public mirror. To publish, copy this folder to the live host's docs path and make sure `index.html`, `exports.json`, `pages/`, and `track.php` all land together; on the live host the viewer expects articles under `/docs/pages`. The editor holds the deployment tooling (`deploy_pages.py`, which snapshots only changed files) and is not part of this repo.

## Tracking

`index.html` posts a `visit` per page load and a `click` per article click to `track.php`, which appends to `engage/track.json` and trims to the newest 10000 entries. `track.php` answers `204` on success, `405` for a non-POST method, and `400` for an unknown type. Delete `engage/track.json` to reset the log. Tracking failures never block rendering.

## Troubleshooting

**Grid shows "No Articles Yet".** `exports.json` is missing, empty, or not reachable. Confirm the server is running, then check that `exports.json` is an array and the JSON parses.

**A card 404s.** The row exists but `pages/<id>.html` does not. Re-export from the editor, or remove the row.

**A hand-added page is not reachable.** Its row must have `id` equal to the file name without `.html`, and the file must sit in `pages/`.

**Images are broken in an article.** Run Sync Imgs in the editor with this destination selected. It copies the images the article references into `pages/imgs/`. A name it cannot find in the editor's `imgs/` folder is reported as `MISS` in the sync output.

**A new export is not listed.** Reload the page. The viewer requests `exports.json` with a cache-busting query, so a stale cache is not usually the cause; if it persists, confirm the export actually reached this folder.

**Port already in use.** `run.sh` skips busy ports automatically. If you pinned `PORT`, the message names the process holding it.
