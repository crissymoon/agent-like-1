# XcaliburLiteMCP

A lite coding agent for one small local GGUF model. It reads a codebase through
an adapter, and it changes code through exactly one tool, `run_sh_runner`, which
stages each change into a timestamped shadow copy and waits for a person to
approve it in a browser diff. Nothing the model proposes touches the live tree
on its own.

The interactive prompt, the review dashboard and the one guarded runner all
start from `run_xcalibur.sh`. The colour tokens are the desktop application's,
read from `desktop/renderer/styles.css`, so the terminal, the dashboard and the
window read as one product.

## The incubator database

The storage and retrieval layer the agent is being built around is a separate
project: an incubator engine on the Java Vector API that holds records as JSON
Lines shards and searches them with a lane scan, with a content addressed
projection beside each shard. It is where the long term memory, the module
registry and the account and session store are meant to live.

It is under active development, and no public release is available at the
moment. Once it is complete we will make a binary for it, and then push that to
its private repository. Until then it is not part of this package: no code here
reads its files, it is supplied to a deployment rather than built with it, and
a machine without it simply reports that it is absent rather than guessing a
path.

## Layout

```
XcaliburLiteMCP/
├── run_xcalibur.sh          launcher: deps, llama-server, dashboard, TUI
├── sh_runner.sh             the only script the agent may trigger
├── nlp_smoke.sh             runs the interpretation smoke test, no model needed
├── cli.py                   the TUI, the navigator, and the one tool it exposes
├── config.py                paths, model, palette, budgets (env overridable)
├── theme.py                 the Rich theme built from the palette
├── splash.py                the banner art, its animation, and the boot steps
├── on-load.txt              the banner art, with a version placeholder
├── mcp_server.py            the same surface over MCP on stdio
├── core/
│   ├── adapter.py           find, list and read a file into prompt text
│   ├── interpreter.py       closest-neighbour file resolution and green-lighting
│   ├── workspace_shell.py   ls, cd, pwd, cat, confined to the workspace
│   ├── file_chunker.py      overlapping windows so the context never overflows
│   ├── chunk_merge.py       chunk a large file, then merge the windows back
│   ├── linters.py           pick, run and parse a checker into a defect list
│   └── surgical_patcher.py  exact and whitespace tolerant edits, shadow, diff
├── security/
│   ├── command_guard.py     the deny list, the verb list, redirect confinement
│   ├── guarded_run.py       scan, then the only process start for a model command
│   └── sh_runner.py         validate a request, stage it, queue it
├── engine/
│   ├── llama_server.py      supervises llama-server
│   ├── model_client.py      OpenAI compatible chat, streaming, tool calls
│   ├── prompt_repair.py     reshapes the model's shape drift into valid edits
│   ├── events.py            the reporter that shows reasoning and actions
│   ├── context.py           resolves referenced paths, attaches chunked context
│   ├── orchestrator.py      request to chunked context to staged, linted job
│   └── prompts.py           every model facing string, in one place
├── dashboard/
│   ├── app.py               Flask: queue, merge view, approve, test, rework
│   ├── jobs.py              atomic JSON job store with a claim lock
│   ├── worker.py            one thread, so the queue is serial
│   ├── testing.py           sandbox copy, overlay a shadow, guarded run
│   ├── templates/           base, index, job
│   └── static/              app.css (desktop tokens), job.js (CodeMirror 6)
├── tools/
│   └── nlp_smoke.py         the smoke test and benchmark behind nlp_smoke.sh
└── workspace/                       the one tree the agent reads and edits
    ├── .xcalibur/                   the harness store, hidden from every walk
    │   ├── original_copy/           pristine snapshot of every touched file
    │   ├── shadow_copy/             timestamped proposed versions
    │   ├── jobs/                    persisted job records
    │   └── tests/                   throwaway environments for a test run
    └── (your code)                  what the navigator lists
```

## Install

Python 3.10 or newer, and a `llama-server` on `PATH` from llama.cpp.

```bash
cd XcaliburLiteMCP
python3 -m pip install -r requirements.txt
```

`requirements.txt` holds the five packages the runtime imports, and nothing
else: `flask`, `requests`, `python-dotenv`, `rich`, `prompt_toolkit`.

## Run

```bash
./run_xcalibur.sh                 # server, dashboard and prompt in one command
./run_xcalibur.sh --no-open       # do not open the dashboard in a browser
./run_xcalibur.sh --no-splash     # skip the startup animation
./run_xcalibur.sh --no-server     # navigate and stage by hand, no model
./nlp_smoke.sh                    # check the interpretation layer, no model
./sh_runner.sh --scan "pytest -q" # ask the guard about one command and exit
./sh_runner.sh --request req.json # stage one request from a file
./xcalibur_mcp.sh                 # serve the same surface over MCP on stdio
./xcalibur_mcp.sh --self-check    # check the MCP handshake and reads, no model
```

Overridable settings live in `.env.example`. The ones used most are
`XCALIBUR_MODEL`, `XCALIBUR_WORKSPACE`, `XCALIBUR_DASHBOARD_PORT`,
`XCALIBUR_CTX`, `XCALIBUR_NGL` and the context budgets. `XCALIBUR_WORKSPACE`
defaults to `workspace/` beside this package, and is the only folder the
terminal navigates or the agent reads and edits; `XCALIBUR_TARGET_ROOT` is
accepted as the older name for it. The agent's own behaviour is tuned with
`XCALIBUR_MINIMAL_LINES`, `XCALIBUR_SHOW_THINKING`,
`XCALIBUR_STREAM`, `XCALIBUR_LINT`, `XCALIBUR_FUZZY_MIN` and
`XCALIBUR_REPAIR_ATTEMPTS`. The banner is tuned with `XCALIBUR_SPLASH`,
`XCALIBUR_SPLASH_REVEAL`, `XCALIBUR_SPLASH_HOLD`, `XCALIBUR_SPLASH_FPS` and
`XCALIBUR_VERSION`, and its art comes from `XCALIBUR_SPLASH_FILE`.

## The startup splash

`on-load.txt` holds the banner art with a `-{$the_version}-` placeholder, and
`splash.py` stamps `XCALIBUR_VERSION` into it, so the banner and the version the
MCP handshake reports cannot drift apart. Startup then runs under the splash:
the art is revealed top to bottom behind a travelling scan band, with a column
gradient drawn from the same palette as the dashboard, twinkling star
characters, and a pulsing moon block located by shape rather than by line
number.

Underneath the art, each boot step reports itself as it happens, with a moon
phase as the activity tick and a progress bar across the bottom:

```
  + workspace  workspace
  + local model server  http://127.0.0.1:8080
  ● review dashboard...
  ██████████████████████████  workspace ~/code
```

The four steps are workspace, model server, review dashboard, and opening the
browser. A step that fails is marked and the boot continues, so a missing model
server still leaves you at a prompt that can explain itself. When the work
finishes early the reveal is allowed to complete before the frame settles, so
the banner you keep is always whole. No terminal, `NO_COLOR`, `TERM=dumb`, or
`--no-splash` prints the banner and the step lines without animation.

## As an MCP server

`xcalibur_mcp.sh` starts `mcp_server.py`, a dependency-free JSON-RPC 2.0 server
on standard input and output, so an MCP host gets the same surface the terminal
has. Standard output carries protocol messages only; diagnostics go to standard
error.

| Tool | What it does |
| --- | --- |
| `read_file` | Reads a file under the workspace; a file over one prompt window is returned one overlapping window at a time. |
| `list_files` | Lists the files under a directory of the workspace. |
| `find_files` | Finds files whose path matches a glob. |
| `run_sh_runner` | The only write path: stages proposed edits into the shadow copy and returns a job id and a review URL. |

The reads go through the same adapter as everything else, so a path that escapes
the workspace is refused with a reason. `--tools` prints the advertised tool
list as JSON and exits; `--self-check` runs the handshake, a read, an escape
refusal and an unknown-tool refusal without loading any weights, and exits
non-zero on the first failure. A workspace that holds no file yet gets a probe
file placed inside the harness store for the read check, and removed after.

## Checking the interpretation layer before the model

`nlp_smoke.sh` runs `tools/nlp_smoke.py`, which exercises resolution, chunking
and merge, prompt repair, the navigator, the workspace boundary, linters and
context assembly against a throwaway tree. It loads no weights and takes well
under a second, so it gates a session or a benchmark before the GGUF is pulled
into memory. It exits non-zero on the first broken expectation, and prints a
per-group timing line.

```bash
$ ./nlp_smoke.sh
XcaliburLite NLP smoke test
  PASS  exact path resolves
  ...
passed 110, failed 0 in 168ms
```

## Walking the workspace

The prompt is a confined navigator. `ls`, `cd`, `pwd` and `cat` move around the
workspace, and the prompt shows the current directory. The workspace is one
folder: the navigator is rooted on it and the agent reads and edits only inside
it, so a file the terminal green-lights is a file the model can act on. The
navigator cannot leave the tree: `cd ..` above the root, `cd ~` and an absolute
path elsewhere are refused rather than clamped, so the location is always
honest. The harness store, `.xcalibur/` inside the workspace, is refused too, so
the staging area and the job records are never mistaken for the user's code.

```
~/auth > ls
~/auth > cd ../src
~/src > cat app.py
```

Typing a bare name resolves it instead of sending it: the interpreter matches
exactly first, then by basename, then by stem, then by closest neighbour, and
reports the match kind and score. A name that maps onto a real file is
green-lit and held for the next request; a name that does not is reported with
its closest candidates rather than guessed. `selected` lists the held files and
`drop` clears them.

```
~/src > app
ok src/app.py (basename, 0.95)
~/src > admin_pnl
no match unmatched; closest: src/admin_panel.py
```

## Minimal code, chunked reading

The model is asked for the smallest version of a file that fully works, and the
harness enforces it. A written file that arrives wrapped in a full HTML
document is nudged once, and if the model still does not comply the wrapper is
removed deterministically before the edit is staged. A file over the chunk
threshold is read as labelled, overlapping windows, so the model never holds the
whole of a large file. That same machinery edits large files: each window is
amended on its own, `chunk_merge` stitches the results back, trims the overlap,
and reports any line two windows both changed.

The worker drives that path itself. It first asks the model for targeted edits
and discards any edit larger than one prompt window. Then every referenced file
that is too large for one prompt and still has no staged edit is rewritten a
window at a time and merged, so a large file the model declined to match does
not end the job as an error. A job produced that way is marked `chunked`, and
the review page says so above the diff, because a merge across window seams is a
different claim from a single exact match.

## Linters and surgical repair

A staged file is checked by the right checker for its type (`ruff` or
`py_compile`, `php -l`, `node --check`, `shellcheck`, `tsc`). Findings are
attached to the edit, shown in the terminal and on the review page, and handed
to a repair pass that asks the model for the smallest edit that clears them
instead of a rewrite. Commands are built from fixed templates and run through
the guard, so no model text reaches a shell.

## Seeing what the model is doing

Every turn shows its work: the reasoning when the model emits it, streamed
tokens as they arrive, a throttled character count while a tool call is being
written, each tool call, each staged edit, and each lint result.

```
:: thinking
thinking The user wants me to create a file named auth/login.php ...
  run_sh_runner writing call...
    402 chars
-> run_sh_runner auth/login.php
  + auth/login.php create (19 diff lines)
  + staged 20261003-180916-e9d6a7 status=pending_review edits=1
    review: http://127.0.0.1:8420/job/20261003-180916-e9d6a7
```

## The model

The default weights are `gemma-agent-coding-Q4_K_M.gguf`, at the path
`<repo>/models/gemma-agent-coding-Q4_K_M.gguf`. They are served once by
`llama-server` and reused for every turn, with the whole model offloaded
(`-ngl 99`) and a 32K window, which is what keeps it fast without a container.

## The tool calls

The model is given one tool in the interactive prompt, and a second one only on
the authoring pass a rework uses. Both are OpenAI function-calling schemas. The
calls below were captured from `gemma-agent-coding-Q4_K_M.gguf` itself, one
sample per condition, and are reproduced verbatim.

### run_sh_runner

The only way the agent can change code. It stages proposed edits into the shadow
copy and returns a job id and a review URL. It never writes the live tree.

```json
{
  "type": "function",
  "function": {
    "name": "run_sh_runner",
    "description": "The only way to change code. Stage proposed edits into a shadow copy for human approval in the dashboard. Returns a job id and a review URL.",
    "parameters": {
      "type": "object",
      "properties": {
        "instruction": {"type": "string", "description": "What the change is and why."},
        "files": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Files the change touches, relative to the target root."
        },
        "edits": {
          "type": "array",
          "description": "Precise find-and-replace edits.",
          "items": {
            "type": "object",
            "properties": {
              "filepath": {"type": "string"},
              "old_str": {"type": "string", "description": "Exact text to replace, or an empty string to create the file."},
              "new_str": {"type": "string", "description": "Replacement text."},
              "regex": {"type": "boolean", "description": "Treat old_str as a regex."}
            },
            "required": ["filepath", "old_str", "new_str"]
          }
        },
        "run": {"type": "string", "description": "Optional allowed command to run for context."}
      },
      "required": ["instruction"]
    }
  }
}
```

| argument | required | what it carries |
|---|---|---|
| instruction | yes | what the change is and why, shown on the review page |
| files | no | the files the change touches, relative to the target root |
| edits | no | the find-and-replace edits, each a filepath, old_str and new_str |
| run | no | one optional command for context, held to the guard |

#### Suggested call: create a file

Asked to create `hello.py`, the model emits a non-existent file with the new
text in `new_str`. Note that it writes `old_str` as the two-character literal
`""` rather than an empty string. A target that does not yet exist is staged as
a creation regardless of `old_str`, so both forms are accepted.

```json
{"edits":[{"filepath":"hello.py","new_str":"print(\"hello world\")","old_str":"\"\"","regex":false}],"files":["hello.py"],"instruction":"Create hello.py that prints hello world.","run":"python -c \"print('hello world')\""}
```

#### Suggested call: change a line

Asked to turn `return a - b` into `return a + b`, the model sends the smallest
exact substring it can, and repeats `instruction` in its own words.

```json
{"edits":[{"filepath":"pkg/mod.py","new_str":"return a + b","old_str":"return a - b"}],"instruction":"Change return a - b to return a + b in pkg/mod.py.","run":"pkg/mod.py"}
```

### propose_edits

The authoring pass, used only when a reviewer sends a change back for rework.
The harness asks for edits against the context it attaches, and the model
returns the same edit shape without the surrounding request fields.

```json
{"edits":[{"filepath":"pkg/mod.py","new_str":"def add(a, b):\n    return a + b","old_str":"return a - b"}]}
```

### How a call is handled

`sh_runner` is the only entry point. It scans the request with the command
guard, then hands it to the orchestrator, which resolves the referenced paths,
attaches chunked context, applies each edit in memory and writes the result into
`shadow_copy/<timestamp>/`. Each edit comes back with a unified diff, a match
kind (`exact`, `normalized` for a whitespace difference, `regex`, or `create`)
and a review URL.

Before any of that, the call is reshaped by `prompt_repair`, which absorbs the
shape drift a small model produces: a payload wrapped in a code fence, `path`
for `filepath`, `content` for `new_str`, edits as a single object, a trailing
comma, or a field that swallowed the tail of the envelope. A field that cannot
be salvaged ends the turn with one corrective retry rather than a silent drop.

An `old_str` that appears more than once is refused rather than guessed, and an
empty `old_str` against a file that already exists is refused with a clear
message.

## Review in the browser

The dashboard is a Flask app on `127.0.0.1:8420`. A job is one staged change,
and the page shows a CodeMirror merge view of the shadow against the original.

- **Approve** writes the shadow over the live file.
- **Test** builds a throwaway copy of the tree, overlays the shadow, and runs a
  guarded command (`php -l` for a PHP file, `pytest` or similar otherwise).
- **Rework** attaches the reviewer's note and the prior shadow, chunks the
  context with an overlap, and asks the authoring pass for a revision, which is
  staged on top of the previous one.

One worker thread drains the queue, so a job submitted while the agent is busy
sits in `queued` and is picked up when the thread is free. Each job carries a
claim lock, so the same job is never worked twice.

The desktop application can open this page. Its Settings screen has a Review
dashboard address, held to the same rule as its other endpoints, and an **Open
the review dashboard** button that hands the address to the machine's browser.
The address is validated in the main process before it is opened, and the screen
reports which address was refused when one is.

## Safety

- The agent can trigger exactly one script, `sh_runner.sh`, and only through
  `run_sh_runner`.
- Commands are checked against a deny list and a verb allow list; `sudo`,
  `rm -rf /`, `curl ... | sh` and command substitution are refused. `php -l`,
  `pytest`, `python -m py_compile` and similar read-only checks pass.
- The program is started directly, without a shell. A command carrying a shell
  metacharacter (`|`, `&&`, `||`, `;`, `>`, `<`) is refused rather than
  interpreted, so a model string cannot become a shell injection or a redirect
  the runner does not make.
- The only process start for a model-issued command is `guarded_run`, and it runs
  after the scan.
- Paths are resolved against the workspace and refused when they leave it, so
  a request naming `../../etc/passwd` is rejected rather than rewritten. The
  navigator is bounded the same way, and cannot `cd` out of the workspace; a
  configured `XCALIBUR_NAV_ROOT` outside it is ignored rather than honoured.
- Nothing reaches the live tree until a person approves it. Every proposal is
  paired with a pristine snapshot and a unified diff.
