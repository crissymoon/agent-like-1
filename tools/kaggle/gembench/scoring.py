"""The arithmetic that turns one trajectory into the numbers the table shows.

The composite is a weighted blend rather than a pass or a fail, because the
interesting result for a small solver is usually not that it failed but how it
failed. A solver that plans correctly and cannot hold the output format has a
different problem, and a different fix, from one that holds the format and never
reaches the right state.

Every dimension is defined in terms of counters the loop recorded, so a reader
can recompute any of them from the run's CSV. The five weights, the scale and
the five dimension names are declared once, here, and the parity check holds
them against the runtime that owns them.
"""

from __future__ import annotations

DIMENSIONS = (
    "task_success",
    "protocol_compliance",
    "tool_validity",
    "error_recovery",
    "efficiency",
)

#: The scale the composite is reported out of.
SCORE_SCALE = 100.0

#: The blend. They sum to one, which a check asserts.
WEIGHTS = {
    "task_success": 0.45,
    "protocol_compliance": 0.20,
    "tool_validity": 0.15,
    "error_recovery": 0.10,
    "efficiency": 0.10,
}

#: The counters every trajectory carries, in the order the CSV writes them.
COUNTERS = (
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


def empty_counters() -> dict[str, int]:
    return {key: 0 for key in COUNTERS}


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def dimensions(trajectory: dict) -> dict[str, float]:
    """The five dimensions of one trajectory."""
    counters = trajectory["counters"]
    tool_calls = int(counters["tool_calls"])
    invalid_actions = int(counters["invalid_actions"])
    successful_calls = int(counters["successful_tool_calls"])
    redundant_calls = int(counters["redundant_calls"])

    # The share of intended actions the solver managed to express at all.
    expressed = tool_calls + invalid_actions
    protocol = 0.0 if expressed == 0 else tool_calls / expressed

    # The share of expressed calls that named a real action with usable
    # arguments, which is where a hallucinated action or a missing argument
    # shows up.
    tool_validity = 0.0 if tool_calls == 0 else successful_calls / tool_calls

    # A run with no error at all is credited in full: avoiding the failure and
    # recovering from it are both the behaviour being asked for, and every
    # solver faces the same tasks.
    applicable = bool(trajectory["had_error"])
    recovery = (1.0 if trajectory["recovered"] else 0.0) if applicable else 1.0

    budget = max(1, int(trajectory["budget"]))
    steps = int(trajectory["steps_used"])
    redundancy = 0.0 if tool_calls == 0 else min(1.0, redundant_calls / tool_calls)
    step_pressure = min(1.0, steps / budget)
    efficiency = max(0.0, min(1.0, 1.0 - 0.5 * redundancy - 0.5 * step_pressure))

    return {
        "task_success": 1.0 if trajectory["success"] else 0.0,
        "protocol_compliance": round(protocol, 4),
        "tool_validity": round(tool_validity, 4),
        "error_recovery": round(recovery, 4),
        "efficiency": round(efficiency, 4),
    }


def composite(values: dict[str, float]) -> float:
    total = sum(WEIGHTS[key] * float(values[key]) for key in DIMENSIONS)
    return round(SCORE_SCALE * total, 2)


def score(trajectory: dict) -> dict:
    """One trajectory's dimensions, composite and check tally."""
    values = dimensions(trajectory)
    checks = trajectory.get("checks") or []
    return {
        "dimensions": values,
        "composite": composite(values),
        "recovery_applicable": bool(trajectory["had_error"]),
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_total": len(checks),
    }


def aggregate(scored: list[dict], trajectories: list[dict]) -> dict:
    """A solver's summary across a whole suite.

    Recovery is averaged over the tasks that actually produced an error to
    recover from. Averaging it over every task would let a solver that never
    needed to recover dilute a total failure to recover into a healthy-looking
    mean, which is the one reading of this dimension that would mislead. The
    number of tasks the figure rests on is reported beside it.
    """
    means = {key: _mean([float(entry["dimensions"][key]) for entry in scored]) for key in DIMENSIONS}

    recovery_values = [
        float(entry["dimensions"]["error_recovery"])
        for entry in scored
        if entry["recovery_applicable"]
    ]
    means["error_recovery"] = 1.0 if not recovery_values else _mean(recovery_values)

    return {
        "tasks": len(scored),
        "tasks_passed": sum(1 for entry in scored if entry["dimensions"]["task_success"] > 0.5),
        "pass_rate": 0.0
        if not scored
        else round(sum(1 for entry in scored if entry["dimensions"]["task_success"] > 0.5) / len(scored), 4),
        "composite": _mean([float(entry["composite"]) for entry in scored]),
        "dimensions": means,
        "error_recovery_applicable_tasks": len(recovery_values),
        "steps_mean": _mean([float(item["steps_used"]) for item in trajectories]),
        "budget_exhausted": sum(1 for item in trajectories if item.get("budget_exhausted")),
    }


def by_capability(scored: list[dict], trajectories: list[dict]) -> dict[str, dict]:
    buckets: dict[str, dict[str, list[float]]] = {}
    for index, entry in enumerate(scored):
        capability = str(trajectories[index]["capability"])
        bucket = buckets.setdefault(capability, {"composite": [], "task_success": []})
        bucket["composite"].append(float(entry["composite"]))
        bucket["task_success"].append(float(entry["dimensions"]["task_success"]))

    return {
        capability: {
            "tasks": len(values["composite"]),
            "composite": _mean(values["composite"]),
            "task_success": _mean(values["task_success"]),
        }
        for capability, values in sorted(buckets.items())
    }
