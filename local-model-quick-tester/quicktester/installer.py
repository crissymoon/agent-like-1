"""Running a build plan, one step at a time, stopping at the first failure.

Three decisions, each of which is the difference between an installer a person
trusts and one they stop reading.

Nothing is run without being shown first. The plan is printed in full, as the
commands that will be typed, and a person says yes. `--apply` overrides that for
a script, and even then the plan is printed, so a log of the run says what was
attempted rather than only what succeeded.

A step that fails stops the plan. The next step's success depends on the previous
one's, and carrying on past a failed wheel install produces a half built
environment and a longer error later.

A plan for another platform is not run at all. It is correct for the machine it
names and wrong for this one, and the useful thing to do is to print it and say
so. Preparing instructions for a second machine is a real thing to want, so the
print is the feature rather than a refusal.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import platforms


@dataclass
class Outcome:
    """What a plan did, so the caller can report it rather than guess."""

    plan: platforms.Plan
    ran: int = 0
    skipped: int = 0
    failed: int = 0
    already: int = 0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        if self.reason:
            return self.reason
        parts = [f"{self.ran} step(s) run"]
        if self.already:
            parts.append(f"{self.already} already in place")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.failed:
            parts.append(f"{self.failed} failed")
        return ", ".join(parts)


def prepare(plan: platforms.Plan, host: str | None = None) -> tuple[platforms.Plan, dict[str, str]]:
    """Fill in the plan's placeholders for the machine it is for."""
    table = platforms.values(plan.venv, plan.target.host if host is None else host)
    steps = tuple(platforms.substitute(step, table) for step in plan.steps)
    return platforms.Plan(
        target=plan.target,
        steps=steps,
        venv=plan.venv,
        stage=plan.stage,
        warnings=list(plan.warnings),
    ), table


def already_done(step: platforms.Step, venv: Path) -> bool:
    """Whether a step has nothing left to do.

    Only one step can be answered from the filesystem, and it is the first: an
    environment that exists does not need making again. Every other step is a pip
    install, and asking pip what is installed to decide costs more than running
    it, since pip is already the thing that knows.
    """
    if "-m" in step.argv and "venv" in step.argv:
        return platforms.venv_python(venv, platforms.current_host()).is_file()
    return False


def run(
    plan: platforms.Plan,
    emit,
    apply: bool = False,
    host: str | None = None,
) -> Outcome:
    """Show a plan, then run it when allowed to.

    `emit` is called with each line as it is produced, so a build streams the way
    a generation does and a long step does not look like a hang.
    """
    prepared, _ = prepare(plan, host)
    outcome = Outcome(plan=prepared)

    if not platforms.matches(prepared.target):
        outcome.reason = (
            f"not run: this plan is for {prepared.target.host} and this machine is "
            f"{platforms.current_host()}"
        )
        return outcome

    for step in prepared.steps:
        if already_done(step, prepared.venv):
            outcome.already += 1
            emit(f"  already there   {step.label}")
            continue
        if not apply:
            outcome.skipped += 1
            continue

        emit(f"  running         {step.label}")
        emit(f"                  {step.display()}")
        try:
            completed = subprocess.run(
                list(step.argv),
                env=platforms.environment(step),
                check=False,
            )
        except FileNotFoundError as error:
            outcome.failed += 1
            outcome.reason = f"{step.label}: {error}"
            return outcome
        except KeyboardInterrupt:
            outcome.failed += 1
            outcome.reason = f"{step.label}: interrupted"
            return outcome

        if completed.returncode != 0:
            outcome.failed += 1
            outcome.reason = (
                f"{step.label} exited {completed.returncode}; the steps after it were "
                "not attempted, because they depend on this one"
            )
            return outcome
        outcome.ran += 1

    return outcome


def describe(plan: platforms.Plan) -> list[str]:
    """A plan as the lines a person reads before saying yes."""
    lines = [
        f"platform        {plan.target.label} ({plan.target.key})",
        f"for             {plan.target.host}",
        f"features        {plan.target.summary}",
        f"environment     {plan.venv}",
        f"covers          {'the language models and the image models' if plan.stage == platforms.STAGE_ALL else 'the language models'}",
    ]
    if plan.target.note:
        lines.append(f"note            {plan.target.note}")
    return lines
