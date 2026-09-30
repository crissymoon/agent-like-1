# The capability ladder

This document describes how the harness measures a model and what it is entitled
to claim afterwards. It states the method and not the figures. The tasks are
declared in `lib/AgentTask.php` and mirrored in `tools/kaggle/gembench/suite.py`,
the scoring weights in `lib/AgentScoring.php` and in
`tools/kaggle/gembench/scoring.py`, and `README.md` is generated from those
artefacts. The task table, the weight table and the recorded results are therefore
read from the files that own them rather than repeated here, where they would
quietly stop being true.

## What a rung is

A rung is one task, declared as a goal, a workspace and a step budget. A rung
enters the ladder only if a program can decide whether it was finished. The
verifier reads the end state of the workspace: which files exist, what they
contain, and what is left behind that should not be. A solver's account of its
own work is never the evidence, because the failure this ladder is built to
catch is a run that describes success while the workspace says otherwise.

One rung reads the closing answer instead of the workspace, and it is the
exception that shows why the rule is stated the way it is. Its named input does
not exist, so there is no correct output file to produce and the pass condition
is that the solver stopped and said what was missing. A rung whose correct
behaviour is a refusal has to read the refusal, which is why the loop hands that
verifier the closing answer and whether the run ended by stopping or by spending
its budget.

## The two bands

The tasks are grouped into two named suites, and the split is not cosmetic.

The first band is the set that every recorded run measured. It is fixed on
purpose: a suite that grows silently makes an earlier result unreadable, because
a score computed over ten tasks and a score computed over six are not the same
measurement wearing a different number. Work added later therefore does not
enter the band that was already measured, and every comparison refuses to join
two runs whose task sets differ. A run against a moved target is reported rather
than read as a change in the model.

The second band tests what the first band does not. It covers an instruction
delivered in the noisy form a person actually types, a workspace that has to be
reorganised without disturbing the parts of it the request did not mention, and
a request whose honest answer is a refusal. The last of the three matters most,
because the failure it catches, inventing the contents of an input that is not
there, is indistinguishable from success when only the output is looked at.

## What a rung costs

A rung's step budget is part of its definition rather than a setting on the loop.
The efficiency dimension compares the steps a solver took with the budget its own
task declared, so a task given more room is not scored as though it had been
given less, and a budget changed for one task does not silently reprice every
other one. A budget is a claim about how much work the task should take, which is
why it is written beside the task and not in a configuration file.

## How a run is scored

A result is a weighted blend rather than a pass or a fail, because for a model
this size the useful fact is usually how it failed. Task success carries the most
weight and is the only pass-or-fail term. The remaining dimensions are computed
from counters the loop recorded, so every figure in a result can be recomputed
from the run's own CSV, and a disagreement between a summary and its rows is
visible rather than hidden.

Recovery is averaged only over the tasks that actually met an error. A solver that
never erred is neither rewarded nor punished for a problem it never had, which is
the difference between measuring resilience and measuring the absence of
provocation.

## The controls a result carries

A score is not readable on its own. Every run records the conditions it was
produced under: the tool protocol in force, whether the loop guard was on,
whether tool calls were checked against the schema before they ran, which sandbox
policy was applied, and which decoder, if any, constrained the engine. Those
values are written into the run's manifest, and a comparison refuses to join two
runs whose recorded controls differ.

The principle behind that is simple and worth stating. A number that travels
without its conditions is not evidence, it is an assertion.

## Reproducing a rung on both sides

The same task definitions run in two environments. The portable package runs on a
machine and on a hosted notebook, both from one source, and running it in both is
what separates a difference in the model from a difference in the environment.

```bash
# the suite on this machine, without an engine
python3 tools/kaggle/gembench/runner.py --profile reference --suite all \
    --out results/benchmark/local

# the same suite behind the local engine, one weight file at a time, then one table
./benchmark-models.sh --suite all
php benchmark.php --compare --dir results/benchmark

# one recorded run against a recorded baseline, reported per capability
php ladder.php --base results/agent/baseline --trial results/agent/guarded

# and the joined reading of both sides, as tables and figures
python3 tools/kaggle/gembench/compare.py \
    --side local=results/benchmark/local --side kaggle=results/benchmark/kaggle \
    --out results/benchmark
```

Each of these exits non-zero when the inputs did not measure the same task set. A
comparison across different suites measures the suites, and reporting it as a
result about a model is the specific error the exit status exists to prevent.

## What a run leaves behind

A run does not end at a table on a screen. Every finished run writes the
following, and the closing steps of the portable benchmark read all of it into
one archive:

* `figures/*.svg`, the per-task composite, the five dimensions, the per-capability
  reading, the run in one line and the pass matrix across sides. They are written
  as SVG by the benchmark itself, so there is no plotting dependency and a figure
  opens in a browser or in a pull request. Every rectangle is drawn with a zero
  corner radius, which is a constant in the source rather than a style sheet.
* `dataset/dataset.csv` and `dataset/dataset.jsonl`, one row per side, solver and
  task, with the loop's own counters kept as columns and the same rows as records
  for a training script.
* `dataset/signals.json` and `dataset/learning.json`, the signals a failed row is
  read against, each naming the part of the harness it points at, and the work
  ordered by the composite it accounts for.
* `benchmark-bundle.zip` beside `bundle.json`, all of the above in one file with a
  size and a digest per member.

```bash
python3 tools/kaggle/gembench/report.py \
    --side local=results/benchmark/local --side kaggle=results/benchmark/kaggle \
    --out results/benchmark
```

## What the ladder does not claim

The boundaries are part of the method, and stating them is what keeps a result
from being read as more than it is.

* A rung does not separate the model from the harness. A pass means the pair
  finished the task, and the loop, the tool schema, the jail and the prompt are
  all part of that pair. Nothing here attributes a result to a weight file alone.
* The portable runs measure fixture solvers, not a language model. A correct
  solver and a careless one are both deterministic, so the pair calibrates the
  arithmetic and the task definitions and says nothing about a model's ability.
  A live model is another entry in the same shape and is measured by the same
  code, which is the point of keeping the fixtures in the suite.
* A rung decides an end state, not a route. Two solvers can arrive at the same
  workspace differently and both pass, so a rung is evidence that a task was
  finished and not evidence about how well it was reasoned about.
* One condition is measured at a time. A recorded run carries the engine build,
  the sampling settings, the seed and the controls it ran under, and it supports
  a claim about that combination rather than about the model in general.
* A composite is comparable within one suite across runs that measured the same
  task set. It is not a general capability score and it does not transfer to a
  different ladder.
