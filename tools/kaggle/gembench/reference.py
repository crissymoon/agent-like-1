"""Deterministic solvers, so the harness can be measured without a model.

A benchmark whose only input is a language model cannot be checked: the same
suite on the same machine gives a different answer twice, and a broken verifier
looks exactly like a weak solver. These profiles are the fixture that separates
the two. They take no model, no network and no clock, so two runs of the same
profile agree figure for figure, and the only thing that can move between them
is a change to the suite or the arithmetic.

Two profiles are shipped, and the second is the interesting one.

  `reference` carries out each task correctly. It proves the scoring path end to
  end and it is the calibration: a suite a correct solver cannot pass is a
  broken suite, not a hard one.

  `naive` is what a solver that reads the request and does not check looks like.
  It trusts a file name it was told might be wrong, does arithmetic in its head,
  emits a nearly-JSON object, treats tidying as deleting, and invents a value
  for an input that is not there rather than saying it is not there. Every one
  of those is a recorded behaviour rather than an invention of this file.

A live model is a third profile in the same shape: a mapping from a task id to
what the solver did. That is the whole extension point, and it is data.
"""

from __future__ import annotations

try:  # the package on a machine
    from .sandbox import Sandbox
except ImportError:  # a notebook cell, where the modules are loaded one after another
    from gembench.sandbox import Sandbox  # noqa: F401

#: The counters a behaviour may set, and the defaults it may leave out.
_DEFAULT_COUNTERS = (
    "tool_calls",
    "successful_tool_calls",
    "invalid_actions",
    "unstructured_tool_calls",
    "unknown_tools",
    "invalid_args",
    "tool_errors",
    "redundant_calls",
    "repeated_failed_turns",
    "recovered",
)


def _took(
    steps: int,
    *,
    finished: bool = True,
    answer: str = "",
    had_error: bool = False,
    recovered: bool = False,
    **counters: int,
) -> dict:
    """The record of what a solver did on one task.

    `answer` is the solver's closing message, which only the stopping-rule task
    reads. `had_error` says an error was met at all, and `recovered` whether the
    solver got itself out of it; both are recorded because a solver that avoided
    the error and one that repaired it are the same behaviour being asked for.
    """
    body = {key: int(counters.get(key, 0)) for key in _DEFAULT_COUNTERS}
    body["recovered"] = 1 if recovered else 0
    return {
        "steps_used": steps,
        "finished": finished,
        "answer": answer,
        "had_error": had_error,
        "recovered": recovered,
        "counters": body,
    }


# ---------------------------------------------------------------------------
# The reference solver: every task carried out correctly.
# ---------------------------------------------------------------------------


def _reference_create(sandbox: Sandbox) -> dict:
    sandbox.write("release.txt", "name=atlas\nversion=3\n")
    return _took(2, tool_calls=1, successful_tool_calls=1)


def _reference_count(sandbox: Sandbox) -> dict:
    sandbox.write("count.txt", "4")
    return _took(3, tool_calls=2, successful_tool_calls=2)


def _reference_sum(sandbox: Sandbox) -> dict:
    sandbox.write("total.txt", "42")
    return _took(4, tool_calls=3, successful_tool_calls=3)


def _reference_recover(sandbox: Sandbox) -> dict:
    sandbox.write("output.txt", sandbox.read("service.yml"))
    return _took(4, tool_calls=3, successful_tool_calls=3)


def _reference_extract(sandbox: Sandbox) -> dict:
    sandbox.write("parsed.json", '{"alpha": 1, "beta": 2, "gamma": 3}')
    return _took(3, tool_calls=2, successful_tool_calls=2)


def _reference_prune(sandbox: Sandbox) -> dict:
    for name in list(sandbox.names()):
        if name.endswith(".tmp"):
            sandbox.remove(name)
    return _took(4, tool_calls=4, successful_tool_calls=4)


def _reference_typos(sandbox: Sandbox) -> dict:
    lines = len(sandbox.read("notes.md").splitlines())
    sandbox.write("lines.txt", str(lines))
    return _took(3, tool_calls=2, successful_tool_calls=2)


def _reference_organize(sandbox: Sandbox) -> dict:
    for name in list(sandbox.names()):
        if name.endswith(".txt"):
            sandbox.move(name, f"docs/{name}")
    return _took(5, tool_calls=5, successful_tool_calls=5)


def _reference_refuse(sandbox: Sandbox) -> dict:
    # It looks, finds nothing, and says so. The error is real and the recovery
    # is the correct refusal, which is what this task scores.
    return _took(
        2,
        answer="I cannot find deployment.json in the workspace root, so I cannot read its region field.",
        had_error=True,
        recovered=True,
        tool_calls=1,
        successful_tool_calls=0,
        tool_errors=1,
    )


# ---------------------------------------------------------------------------
# The naive solver: reads the request, does not check the result.
# ---------------------------------------------------------------------------


def _naive_create(sandbox: Sandbox) -> dict:
    sandbox.write("release.txt", "name=atlas\nversion=3\n")
    return _took(2, tool_calls=1, successful_tool_calls=1)


def _naive_count(sandbox: Sandbox) -> dict:
    sandbox.write("count.txt", "4")
    return _took(3, tool_calls=2, successful_tool_calls=2)


def _naive_sum(sandbox: Sandbox) -> dict:
    # Writes the expression rather than its value. The verifier reads the file,
    # so the failure is a wrong end state and not a wrong plan.
    sandbox.write("total.txt", "17+25")
    return _took(2, tool_calls=1, successful_tool_calls=1)


def _naive_recover(sandbox: Sandbox) -> dict:
    # Trusts the name it was told might be wrong, asks for it, gets nothing, and
    # stops without trying the name that is actually there.
    return _took(1, had_error=True, recovered=False, invalid_actions=1)


def _naive_extract(sandbox: Sandbox) -> dict:
    sandbox.write("parsed.json", "alpha: 1\nbeta: 2\ngamma: 3")
    return _took(2, tool_calls=1, successful_tool_calls=1)


def _naive_prune(sandbox: Sandbox) -> dict:
    # Names the files one at a time without the required argument, is told which
    # argument is missing, and repeats the same call until the budget runs out.
    return _took(
        6,
        had_error=True,
        recovered=False,
        tool_calls=1,
        successful_tool_calls=0,
        invalid_actions=5,
        invalid_args=1,
        repeated_failed_turns=4,
    )


def _naive_typos(sandbox: Sandbox) -> dict:
    sandbox.write("lines.txt", str(len(sandbox.read("notes.md").splitlines())))
    return _took(3, tool_calls=2, successful_tool_calls=2)


def _naive_organize(sandbox: Sandbox) -> dict:
    # Reads "tidy the workspace" and deletes. The keep checks refuse it.
    for name in list(sandbox.names()):
        if name.endswith(".txt"):
            sandbox.remove(name)
    return _took(4, tool_calls=3, successful_tool_calls=3)


def _naive_refuse(sandbox: Sandbox) -> dict:
    # Invents a value for an input that is not there. This is the behaviour the
    # task exists to catch, and it is why the answer matters and not the files.
    sandbox.write("region.txt", "us-east-1")
    return _took(
        6,
        finished=False,
        had_error=True,
        recovered=False,
        tool_calls=4,
        successful_tool_calls=2,
        invalid_args=1,
    )


#: Profile name to the behaviour of every task. A profile is the extension
#: point: a live model is another mapping in this shape.
PROFILES: dict[str, dict[str, callable]] = {
    "reference": {
        "create_exact_file": _reference_create,
        "count_by_extension": _reference_count,
        "sum_two_files": _reference_sum,
        "recover_misnamed_file": _reference_recover,
        "extract_field_map": _reference_extract,
        "prune_by_extension": _reference_prune,
        "follow_typos": _reference_typos,
        "organize_directory": _reference_organize,
        "refuse_impossible": _reference_refuse,
    },
    "naive": {
        "create_exact_file": _naive_create,
        "count_by_extension": _naive_count,
        "sum_two_files": _naive_sum,
        "recover_misnamed_file": _naive_recover,
        "extract_field_map": _naive_extract,
        "prune_by_extension": _naive_prune,
        "follow_typos": _naive_typos,
        "organize_directory": _naive_organize,
        "refuse_impossible": _naive_refuse,
    },
}


#: Live solvers, registered rather than shipped. The two profiles above are
#: fixtures and are always present; a live one is added here at run time by the
#: module that knows how to reach it. The registry is empty on import, so a
#: caller that embeds this module and has no network - the hosted notebook - sees
#: exactly the two fixtures and nothing else.
_LIVE: dict[str, dict[str, callable]] = {}


def register_profile(name: str, table: dict[str, callable]) -> None:
    """Make a live solver's behaviour table readable under *name*.

    A name that shadows a fixture is refused rather than allowed to replace it:
    a run recorded under `reference` must always mean the deterministic fixture,
    or a result would stop being reproducible the moment someone registered over
    it.
    """
    if name in PROFILES:
        raise ValueError(f"{name} is a fixture profile and cannot be replaced")
    if not table:
        raise ValueError(f"profile {name} has no behaviours to register")
    _LIVE[name] = dict(table)


def unregister_profile(name: str) -> None:
    """Drop a live solver, so a process can register a fresh client under it."""
    _LIVE.pop(name, None)


def profile_names() -> list[str]:
    return sorted(set(PROFILES) | set(_LIVE))


def behaviour(profile: str, task_id: str) -> callable:
    """The behaviour a profile has for a task, refused by name when absent."""
    table = PROFILES.get(profile) or _LIVE.get(profile)
    if table is None:
        raise ValueError(
            f"unknown profile: {profile} (known: {', '.join(profile_names())})"
        )
    if task_id not in table:
        raise ValueError(f"profile {profile} has no behaviour for {task_id}")
    return table[task_id]
