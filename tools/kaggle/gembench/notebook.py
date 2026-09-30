"""The notebook a hosted run is carried out in, written from the modules.

The notebook is not a second implementation of anything. Every module this
package holds is embedded in it as a cell, in dependency order, and the cells
are the module sources themselves rather than a paraphrase: a rule that is
changed here and not there cannot drift, because there is only one file.

The prose is the steps. A reader who opens the notebook is walked through what
a task is, how it is decided, how the numbers are computed, and how the two
runtimes are set beside each other, in that order, with the cell that does the
work immediately after the paragraph that explains it.

Nothing here names a machine, a memory limit, a weight file or a model. What
the notebook measures is a solver against a suite, and both are data. The step
that joins the two sides reads a results directory at a declared path rather
than searching for one, because a search over a mounted input tree is a walk
over an image volume as often as it is a find.

A run that cannot be carried off the machine it ran on is not evidence, so the
last three steps are the run leaving: the numbers as pictures, the same rows as
one dataset with the failures read as work on the harness, and everything the
run wrote in one zip with a link to it.
"""

from __future__ import annotations

from pathlib import Path

#: The directory a published local-results set is read from when the notebook
#: runs. It is a contract with the caller, not a search: the step that uses it
#: says so, and an absent directory is reported as the fact it is.
LOCAL_RESULTS_DIR = "/kaggle/input/agent-benchmark-local"

#: Where the notebook writes its own run, and the name of the side it writes
#: as. The name is the whole point of the exercise, so it is stated once.
WORKING_DIR = "/kaggle/working/benchmark"
KAGGLE_SIDE = "kaggle"
LOCAL_SIDE = "local"

#: The modules embedded in the notebook, in the order they can be executed.
#: The order is a dependency order and is checked rather than assumed.
MODULE_ORDER = (
    "sandbox",
    "suite",
    "scoring",
    "reference",
    "runner",
    "compare",
    "parity",
    "charts",
    "dataset",
    "bundle",
    "report",
)

MARKDOWN_MARKER = "# %% [markdown]"
CODE_MARKER = "# %%"

#: The delimiter a module's source is held in. A module source may not contain
#: it, which is why the builder refuses rather than producing a notebook that
#: would fail on the platform instead of here.
DELIMITER = "'''"


def modules_dir() -> Path:
    return Path(__file__).resolve().parent


def read_module(name: str) -> str:
    return (modules_dir() / f"{name}.py").read_text(encoding="utf-8")


def module_findings() -> list[str]:
    """Reasons an embedded cell would not survive the notebook's own format."""
    findings: list[str] = []
    for name in MODULE_ORDER:
        source = read_module(name)
        if DELIMITER in source:
            findings.append(f"{name}.py contains {DELIMITER}, which would end the embedding")
        for number, line in enumerate(source.splitlines(), start=1):
            if line.strip().startswith("# %%"):
                findings.append(f"{name}.py line {number} is a cell marker and would split the cell")
    return findings


def _md(lines: list[str]) -> str:
    """A markdown cell, written the way the percent format holds one."""
    body = [f"# {line}".rstrip() for line in lines]
    return MARKDOWN_MARKER + "\n" + "\n".join(body) + "\n"


def _code(lines: list[str]) -> str:
    return CODE_MARKER + "\n" + "\n".join(lines) + "\n"


def _embed(name: str) -> str:
    """One module, held in a raw string so the source arrives verbatim."""
    header = [
        f"# gembench/{name}.py",
        "#",
        "# The module is held as text and executed by the loader cell, so the code",
        "# that runs on the machine and the code that runs here are one file.",
        "",
        f"MODULES[{name!r}] = r{DELIMITER}",
    ]
    return "\n".join(header + [read_module(name).rstrip("\n"), DELIMITER, ""])


def source() -> str:
    """The whole notebook as a python source with cell markers."""
    parts: list[str] = []

    parts.append(_md([
        "# Agent Benchmark: the same suite on two runtimes",
        "",
        "This notebook runs a fixed, mechanical benchmark of an agent harness and",
        "writes its results in the same shape the same benchmark writes on a",
        "machine. The point is not the score. The point is that the two sides can",
        "be set beside each other and agree, which is what makes a hosted number",
        "comparable with a local one instead of merely reported.",
        "",
        "The steps, in order:",
        "",
        "1. Say what is being measured and on what.",
        "2. The workspace a task runs in, and why a task is decidable.",
        "3. The suite: nine tasks, each with a budget and a mechanical verifier.",
        "4. The arithmetic: five dimensions blended into one composite.",
        "5. The solvers: two deterministic profiles, so a result is reproducible.",
        "6. Carry the suite out and write the run down.",
        "7. Compare two runtimes from their written runs.",
        "8. Run the benchmark here, on a hosted machine.",
        "9. Set the local side beside the hosted side.",
        "10. Take the two files away and read them anywhere.",
        "11. The runs as pictures.",
        "12. The runs as one dataset, with the failures read as work.",
        "13. Everything the run wrote, in one file, with a link to it.",
        "",
        "Everything below is deterministic. There is no network call, no model and",
        "no clock in the scoring path, so a rerun of this notebook reproduces the",
        "same table, the same pictures and the same dataset.",
    ]))

    parts.append(_md([
        "## 0. What is being measured",
        "",
        "An agent harness is measured here on two things a program can check:",
        "whether the workspace ended in the state the request asked for, and how",
        "the turns taken getting there were spent.",
        "",
        "The first is decided by reading the filesystem after the run and nothing",
        "else. A model's account of what it did is not evidence, so it is never",
        "read by a verifier. The one exception is the stopping rule, where the",
        "honest outcome is a refusal, and a refusal is an answer.",
    ]))

    parts.append(_code([
        "import platform, sys, time, os",
        "",
        "print('python     :', sys.version.split()[0])",
        "print('machine    :', platform.machine(), platform.system())",
        "print('processors :', os.cpu_count())",
        "print('suite      : 9 tasks over 9 capabilities, mechanical verifiers')",
        "print('scoring    : 5 dimensions, weighted, one composite')",
        "print('output     : charts, one dataset, and one zip with a download link')",
    ]))

    parts.append(_md([
        "## 1. The workspace a task runs in",
        "",
        "Every task is given an empty directory, may touch only that directory, and",
        "is judged by looking at it afterwards. A path that would leave the",
        "directory is refused rather than tidied, because a task about file layout",
        "is worth nothing if the layout can point anywhere.",
        "",
        "One directory per task also means a task cannot pass on a file an earlier",
        "task left behind.",
    ]))

    parts.append(_md([
        "## Embedding",
        "",
        "The modules below are the benchmark. Each is held as text in its own cell",
        "and executed by the loader at the end, in dependency order, so the same",
        "source runs on a machine and here.",
    ]))

    parts.append(_md([
        "### The run's own settings",
        "",
        "Where this run writes, what it calls itself, and where the other side is",
        "read from when it is attached. All three are declared once, at the top,",
        "because a path repeated in three cells is a path that will disagree with",
        "itself.",
    ]))

    parts.append(_code([
        "import os",
        "",
        "# The side this notebook is: the name a column carries in the joined table.",
        f"KAGGLE_SIDE = {KAGGLE_SIDE!r}",
        f"LOCAL_SIDE = {LOCAL_SIDE!r}",
        "",
        "# Where the runs are written, and where the other side is read from.",
        "# Both are overridable from the environment so the notebook can be exercised",
        "# on a machine before it is published.",
        f"WORKING_DIR = os.environ.get('GEMBENCH_WORKING', {WORKING_DIR!r})",
        f"LOCAL_RESULTS_DIR = os.environ.get('GEMBENCH_LOCAL', {LOCAL_RESULTS_DIR!r})",
        "",
        "# The modules carried below, in the order they can be executed.",
        "MODULE_ORDER = (",
        *[f"    {name!r}," for name in MODULE_ORDER],
        ")",
        "",
        "MODULES = {}",
    ]))

    for name in MODULE_ORDER:
        parts.append(_embed(name))

    parts.append(_md([
        "### The loader",
        "",
        "Each held source is compiled and executed into its own module, registered",
        "under `gembench`, so the modules import each other exactly as they do on a",
        "machine. This is the step that makes the same file run in two places.",
    ]))

    parts.append(_code([
        "import types",
        "",
        "_package = types.ModuleType('gembench')",
        "_package.__path__ = []",
        "sys.modules['gembench'] = _package",
        "",
        "for _name in MODULE_ORDER:",
        "    _module = types.ModuleType('gembench.' + _name)",
        "    _module.__package__ = 'gembench'",
        "    sys.modules['gembench.' + _name] = _module",
        "    exec(compile(MODULES[_name], 'gembench/' + _name + '.py', 'exec'), _module.__dict__)",
        "    setattr(_package, _name, _module)",
        "",
        "from gembench import suite, scoring, reference, runner, compare, parity",
        "from gembench import charts, dataset, bundle, report",
        "",
        "print('loaded   :', ', '.join('gembench.' + name for name in MODULE_ORDER))",
        "print('isolated :', all(m in MODULE_ORDER for m in MODULES))",
    ]))

    parts.append(_md([
        "## 2. The suite",
        "",
        "Nine tasks. Six measure instruction following, tool use, composition,",
        "recovery, structured output and a conditional action. Three measure what a",
        "solver does when the request is noisy, when the workspace is a mess, and",
        "when the honest answer is to refuse rather than to keep trying.",
        "",
        "Each task carries a step budget and a verifier. A task is included only if",
        "a program can decide whether it finished.",
    ]))

    parts.append(_code([
        "print(f\"{'task':<26} {'capability':<24} {'budget':>6}  checks\")",
        "for task in suite.suite('all'):",
        "    print(f\"{task['id']:<26} {task['capability']:<24} {task['budget']:>6}  verifier attached\")",
        "print()",
        "print('suites:', ', '.join(suite.suite_names()))",
        "for name in suite.suite_names():",
        "    print(f'  {name:<8} {len(suite.suite(name))} task(s)')",
    ]))

    parts.append(_md([
        "## 3. The arithmetic",
        "",
        "One trajectory becomes five numbers. The composite is a blend rather than",
        "a pass or a fail, because for a small solver the interesting result is",
        "usually not that it failed but how it failed: a solver that plans correctly",
        "and cannot hold the output format has a different problem, and a different",
        "fix, from one that holds the format and never reaches the right state.",
        "",
        "Recovery is averaged only over the tasks that actually produced an error to",
        "recover from. Averaging it over every task would let a solver that never",
        "needed to recover dilute a total failure to recover.",
    ]))

    parts.append(_code([
        "weights = scoring.WEIGHTS",
        "for name in scoring.DIMENSIONS:",
        "    print(f'  {name:<22} {weights[name]:.2f}')",
        "print(f'  {\"sum\":<22} {sum(weights.values()):.2f}')",
        "print(f'  reported out of {scoring.SCORE_SCALE:.0f}')",
    ]))

    parts.append(_md([
        "## 4. The solvers",
        "",
        "A benchmark whose only input is a language model cannot be checked: the",
        "same suite gives a different answer twice, and a broken verifier looks",
        "exactly like a weak solver. Two deterministic profiles separate the two.",
        "",
        "* `reference` carries out every task correctly. It is the calibration: a",
        "  suite a correct solver cannot pass is a broken suite, not a hard one.",
        "* `naive` reads the request and does not check the result. It trusts a file",
        "  name it was told might be wrong, does arithmetic in its head, emits a",
        "  nearly-JSON object, treats tidying as deleting, and invents a value for",
        "  an input that is not there rather than saying it is not there.",
        "",
        "A live model is a third profile in the same shape, which is the whole",
        "extension point.",
    ]))

    parts.append(_code([
        "print('profiles:', ', '.join(reference.profile_names()))",
        "",
        "rows = []",
        "for profile in reference.profile_names():",
        "    result = runner.run(profile, 'all', profile + '-solver', profile + '-solver')",
        "    rows.append((profile, result))",
        "    print(f\"  {profile:<10} passed {result['summary']['tasks_passed']}/9\")",
    ]))

    parts.append(_md([
        "## 5. The run, written down",
        "",
        "The calibration first: if the reference profile does not pass every task,",
        "the fault is in the suite and not in the solver, and it is reported as a",
        "fault.",
    ]))

    parts.append(_code([
        "findings = runner.check(rows[0][1])",
        "if findings:",
        "    for item in findings:",
        "        print('[error]', item)",
        "else:",
        "    print('the reference profile passed every task, so the suite is answerable')",
    ]))

    parts.append(_md([
        "## 6. Comparing two runtimes",
        "",
        "The comparison reads written runs, not live ones. That is what makes it",
        "usable at all: the two sides rarely run at the same time, and a comparison",
        "that needed both live would be a screenshot rather than a result.",
        "",
        "Where two columns share a solver label they are expected to agree. Where",
        "they differ, the difference is the finding.",
    ]))

    parts.append(_md([
        "## 7. Run the benchmark here",
        "",
        "The same call as on a machine, writing the same three things per run: a",
        "flat CSV, a manifest, and the printed table.",
    ]))

    parts.append(_code([
        "from pathlib import Path",
        "",
        "side_dir = Path(WORKING_DIR) / KAGGLE_SIDE",
        "written = []",
        "for profile, result in rows:",
        "    files = runner.write_run(side_dir / result['run_id'], result,",
        "                             extra={'side': KAGGLE_SIDE, 'runtime': platform.platform()})",
        "    written.append(files)",
        "    print(f\"{profile:<10} -> {files['tasks']}\")",
        "print()",
        "print(runner.render(rows[0][1]), end='')",
    ]))

    parts.append(_md([
        "## 8. The hosted side, on its own",
        "",
        "Before the two sides are joined, the hosted side is read back from the",
        "files it just wrote. A run that cannot be read back is not evidence.",
    ]))

    parts.append(_code([
        "table, problems = compare.collect({KAGGLE_SIDE: side_dir})",
        "document = compare.build(table)",
        "document['findings'].extend(problems)",
        "print(compare.render({'columns': document['columns'], 'tasks': document['tasks'],",
        "                     'matrix': document['matrix'], 'sides': document['sides'],",
        "                     'findings': document['findings'], 'disagreements': document['disagreements']}), end='')",
    ]))

    parts.append(_md([
        "## 9. The two sides beside each other",
        "",
        "The local side is read from a results directory published as a dataset and",
        "attached to this notebook at `" + LOCAL_RESULTS_DIR + "`. It holds the same flat",
        "CSV the run just wrote, produced by the same code on a different machine.",
        "",
        "If the directory is not attached, the step says so and prints the command",
        "that produces it, rather than reporting a comparison it could not make. The",
        "table is printed here; it is written, with everything else, in the last step.",
    ]))

    parts.append(_code([
        "from pathlib import Path",
        "",
        "# The sides this run knows about. The hosted side is always there; the local",
        "# side is added only when the directory is attached, so the list is the",
        "# description of what was actually measured.",
        "sides = {KAGGLE_SIDE: side_dir}",
        "",
        "local_dir = Path(LOCAL_RESULTS_DIR)",
        "if compare.run_directories(local_dir):",
        "    sides[LOCAL_SIDE] = local_dir",
        "    both, more = compare.collect(sides)",
        "    joined = compare.build(both)",
        "    joined['findings'].extend(more)",
        "    print(compare.render(joined), end='')",
        "else:",
        "    print('no local results at', local_dir)",
        "    print()",
        "    print('To make them, run the same benchmark on the machine that holds the models:')",
        "    print()",
        "    print('  python3 tools/kaggle/gembench/runner.py --profile reference --suite all \\\\')",
        "    print('      --out results/benchmark/local')",
        "    print('  python3 tools/kaggle/gembench/runner.py --profile naive --suite all \\\\')",
        "    print('      --out results/benchmark/local')",
        "    print()",
        "    print('then publish results/benchmark/local as a dataset and attach it here.')",
    ]))

    parts.append(_md([
        "## 10. The same reading on a machine",
        "",
        "Everything this notebook writes is read by the same code that wrote it, on",
        "any machine, with no rerun. On the machine that ran the local side, the",
        "hosted run is pulled into a directory and the whole reading is rebuilt from",
        "the files:",
        "",
        "```bash",
        "python3 tools/kaggle/gembench/compare.py \\",
        "    --side local=results/benchmark/local \\",
        "    --side kaggle=results/benchmark/kaggle \\",
        "    --out results/benchmark",
        "```",
        "",
        "The exit code of the comparison is non-zero when the two sides did not",
        "measure the same task set, which is a finding about the runs rather than",
        "about the solvers. One call writes the whole set this notebook ends with:",
        "",
        "```bash",
        "python3 tools/kaggle/gembench/report.py --side local=results/benchmark/local --side kaggle=results/benchmark/kaggle --out results/benchmark",
        "```",
        "",
        "It writes the comparison, the pictures, the dataset and the zip.",
    ]))

    parts.append(_md([
        "## 11. The runs as pictures",
        "",
        "A table of nine rows is read one cell at a time. The same numbers as bars are",
        "read at once, and the reading a benchmark exists for is which task a solver",
        "fails and whether the failure is the format, the plan, or the ending state.",
        "",
        "The charts are SVG written by the benchmark itself rather than by a plotting",
        "library. Two consequences: the notebook gains no dependency, and the file",
        "that leaves it is a vector document that opens in a browser and in a pull",
        "request. Every rectangle is drawn with a zero corner radius, which is stated",
        "in the source as a constant rather than left to a style sheet, because a",
        "rounded bar reads as a decoration rather than a measurement.",
        "",
        "The matrix is the chart that needs both sides: one column per side and solver",
        "label, one row per task, and a cell that says whether the end state was",
        "reached rather than how near it was.",
    ]))

    parts.append(_code([
        "here = Path(WORKING_DIR) / KAGGLE_SIDE",
        "runs_here = dataset.read_sides({KAGGLE_SIDE: here})",
        "",
        "figures = charts.write(Path(WORKING_DIR) / 'figures', runs_here)",
        "for name, path in sorted(figures.items()):",
        "    print(f'{name:<12} {path}')",
        "print()",
        "print('border radius:', charts.BORDER_RADIUS, 'every rectangle is square by construction')",
        "print()",
        "charts.show(figures)",
    ]))

    parts.append(_md([
        "## 12. The runs as one dataset, and the failures as work",
        "",
        "The numbers are written twice over. Once as a flat table with the run's own",
        "counters left in their own columns, so a row can be filtered and joined by",
        "something other than the composite. Once as records that keep the counters",
        "and the checks as objects, because a counter is not a feature until someone",
        "says it is.",
        "",
        "On top of the rows sits the part that names the work. Every failed row is",
        "read against a small table of signals, each of which is a measurable fact",
        "about the row rather than an opinion about it, and each of which names the",
        "part of the harness it points at: the output contract, the tool catalog,",
        "the argument schema, the retry rule, the stop condition, the plan, or the",
        "verifier. The mapping is deliberately short. It says where to look, not what",
        "to change, and it is stated as data, so a reader can disagree with one entry",
        "without touching the arithmetic.",
        "",
        "The figure that orders the work is the composite a solver did not get, over",
        "the rows a signal fired on. A dimension averaged over the suite hides the",
        "four tasks a solver missed while scoring near a hundred on the five it",
        "passed, and the four tasks are the work.",
    ]))

    parts.append(_code([
        "reading_here = dataset.learning(runs_here)",
        "print(dataset.render(reading_here), end='')",
        "print()",
        "print('signals:', ', '.join(entry['name'] for entry in dataset.SIGNALS))",
        "print('areas  :', ', '.join(reading_here['priority']))",
    ]))

    parts.append(_md([
        "## 13. Everything the run wrote, in one file, with a link",
        "",
        "A hosted session ends and its files with it. A run whose result cannot be",
        "carried off the machine it ran on is not evidence, so the last step writes",
        "every file into one zip and offers a link to it: the runs, the comparison,",
        "the pictures, the dataset, and a manifest naming each member with its size",
        "and a digest, so a reader can tell what they hold without unpacking it.",
        "",
        "The zip is written beside the run and the link points at it. Nothing here",
        "reaches the network.",
    ]))

    parts.append(_code([
        "record = report.build(sides, Path(WORKING_DIR))",
        "print(report.render(record), end='')",
        "print()",
        "charts.show(record['charts'])",
        "print()",
        "print('download:', bundle.show(record['bundle']['path'], 'benchmark-bundle.zip'))",
    ]))

    parts.append(_md([
        "## Notes on what this does not say",
        "",
        "* These are fixture solvers, not models. The table measures the suite and",
        "  the arithmetic, and it is the calibration a model's run is read against.",
        "* The dataset is the runs and nothing else: no host, no path outside the run,",
        "  no clock. A row carries the solver label and the side, which is what lets a",
        "  run made later be set beside this one.",
        "* A signal is a reading of counters the loop wrote, so it can be recomputed",
        "  from the CSV by anyone who doubts it. The reading is a place to look, not a",
        "  verdict.",
        "* Latency is measured here and on the machine, and is not comparable across",
        "  them: it is reported as what it is, a property of the runtime.",
        "* Token counts are zero because no model was called. A live profile fills",
        "  them the same way it fills everything else.",
        "* A difference between two sides on the same solver label is a finding about",
        "  portability, and the comparison names the task and both readings.",
    ]))

    parts.append(_code([
        "print('runs written:'); [print(' ', f) for f in written]",
        "print('charts     :', len(record['charts']))",
        "print('dataset    :', record['dataset']['descriptor'])",
        "print('bundle     :', record['bundle']['path'], record['bundle']['size'], 'bytes')",
        "print('done')",
    ]))

    return "\n".join(parts).rstrip("\n") + "\n"
