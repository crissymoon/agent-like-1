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

## Layout

```
XcaliburLiteMCP/
├── run_xcalibur.sh          launcher: deps, llama-server, dashboard, TUI
├── sh_runner.sh             the only script the agent may trigger
├── cli.py                   the TUI, and the single tool it exposes
├── config.py                paths, model, palette, budgets (env overridable)
├── theme.py                 the Rich theme built from the palette
├── core/
│   ├── adapter.py           find, list and read a file into prompt text
│   ├── file_chunker.py      overlapping windows so the context never overflows
│   └── surgical_patcher.py  exact and whitespace tolerant edits, shadow, diff
├── security/
│   ├── command_guard.py     the deny list, the verb list, redirect confinement
│   ├── guarded_run.py       scan, then the only process start for a model command
│   └── sh_runner.py         validate a request, stage it, queue it
├── engine/
│   ├── llama_server.py      supervises llama-server
│   ├── model_client.py      OpenAI compatible chat, tool calls, edit authoring
│   ├── context.py           resolves referenced paths, attaches chunked context
│   ├── orchestrator.py      request to chunked context to staged job
│   └── prompts.py           every model facing string, in one place
├── dashboard/
│   ├── app.py               Flask: queue, merge view, approve, test, rework
│   ├── jobs.py              atomic JSON job store with a claim lock
│   ├── worker.py            one thread, so the queue is serial
│   ├── testing.py           sandbox copy, overlay a shadow, guarded run
│   ├── templates/           base, index, job
│   └── static/              app.css (desktop tokens), job.js (CodeMirror 6)
└── workspace/
    ├── original_copy/       pristine snapshot of every touched file
    ├── shadow_copy/         timestamped proposed versions
    ├── jobs/                persisted job records
    └── tests/               throwaway environments for a test run
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
./sh_runner.sh --scan "pytest -q" # ask the guard about one command and exit
./sh_runner.sh --request req.json # stage one request from a file
```

Overridable settings live in `.env.example`. The ones used most are
`XCALIBUR_MODEL`, `XCALIBUR_TARGET_ROOT`, `XCALIBUR_DASHBOARD_PORT`,
`XCALIBUR_CTX`, `XCALIBUR_NGL` and the context budgets.
`XCALIBUR_TARGET_ROOT` defaults to the parent of this package, which is the
directory the agent reads and may propose changes against.

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
- Nothing reaches the live tree until a person approves it. Every proposal is
  paired with a pristine snapshot and a unified diff.
