"""Which machine this is, and what it takes to build a runtime for it.

The platforms differ in one thing that matters: how `llama-cpp-python` is
obtained. On Apple silicon it is built from source with the Metal backend turned
on. On a CUDA machine it is a prebuilt wheel from a version specific index. On a
machine with neither it is a prebuilt CPU wheel. Everything after that step, the
support packages and the diffusion library, is the same on all of them.

So the platform specific part is a row of data, an index URL and an environment,
and the sequence of steps is one function. Adding a platform is a row.

Two details are worth stating because they were wrong in the script this
replaces. The Metal build takes `-DGGML_METAL=on`; the older `-DLLAMA_METAL=on`
is a variable that no longer exists, and a `-G` generator name has to be one
CMake knows. And a wheel index is version specific: a CUDA wheel built for one
CUDA major will not load against another, so the index is a setting to check
rather than a constant that is always right.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

MACOS = "macos"
WINDOWS = "windows"
LINUX = "linux"

#: The interpreter that runs the tool right now, and the one a new environment
#: will be made from. Named as a placeholder because a step is built for a target
#: that may not be the machine reading it.
BASE_PYTHON = "{base_python}"
VENV_PYTHON = "{venv_python}"
VENV_DIR = "{venv}"

#: Installed in every plan. `gguf` is what reads the header of a weight file and
#: `psutil` is what measures how much memory the machine actually has free, which
#: is the number the image models are planned against. Both are small.
SUPPORT_PACKAGES: tuple[str, ...] = ("pillow", "psutil", "gguf")

#: The diffusion stack. `torch` and `transformers` come from the ordinary index;
#: `diffusers` does not, because GGUF loading and the newest pipelines are on
#: main rather than in a release.
TORCH_PACKAGES: tuple[str, ...] = ("torch>=2.4", "transformers>=5.17", "accelerate")
DIFFUSERS_PACKAGE = "git+https://github.com/huggingface/diffusers"


@dataclass(frozen=True)
class Runtime:
    """How one platform gets its language model runtime."""

    key: str
    label: str
    host: str
    summary: str
    index: str = ""
    env: tuple[tuple[str, str], ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Step:
    """One command, as an argument list rather than a shell line.

    An argument list is passed to the process directly, so a path with a space in
    it is one argument and not three, and nothing has to be quoted for a shell
    that is not there.
    """

    label: str
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    note: str = ""

    def display(self) -> str:
        """The command as a person would type it, environment included."""
        prefix = " ".join(f"{name}={shlex.quote(value)}" for name, value in self.env)
        body = shlex.join(self.argv)
        return f"{prefix} {body}".strip()


@dataclass
class Plan:
    """A sequence of steps for one platform, ready to show or to run."""

    target: Runtime
    steps: tuple[Step, ...]
    venv: Path
    stage: str
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.target.key


#: Every platform this tool can build for. The hosts are the machines a plan is
#: for, which is not always the machine reading it.
TARGETS: tuple[Runtime, ...] = (
    Runtime(
        key="apple-silicon",
        label="Apple silicon",
        host="macos-arm64",
        summary="Metal build of llama.cpp, one command, no CUDA needed",
        env=(("CMAKE_ARGS", "-DGGML_METAL=on"),),
        note=(
            "built from source, which needs the Xcode command line tools "
            "(xcode-select --install) and takes a few minutes. The rendered build "
            "gives the GPU to llama.cpp and leaves the CPU threads low, which is "
            "what this machine is fastest at."
        ),
    ),
    Runtime(
        key="apple-intel",
        label="Apple Intel",
        host="macos-x86_64",
        summary="CPU build of llama.cpp; no GPU backend is available on this Mac",
        note=(
            "Metal is not offered on an Intel Mac. The build is unaccelerated, so "
            "expect a few tokens a second from a three billion parameter model."
        ),
    ),
    Runtime(
        key="windows-gpu",
        label="Windows with an NVIDIA GPU",
        host="windows",
        summary="prebuilt CUDA wheel of llama.cpp, which needs the matching CUDA runtime",
        index="https://abetlen.github.io/llama-cpp-python/whl/cu124",
        note=(
            "a CUDA wheel is built for one CUDA major version. cu124 matches CUDA "
            "12.x; on a machine with a different runtime this needs the index "
            "beside it changed to match, or the plan falls back to the CPU build."
        ),
    ),
    Runtime(
        key="windows-cpu",
        label="Windows, CPU only",
        host="windows",
        summary="prebuilt CPU wheel of llama.cpp, no compiler and no CUDA needed",
        index="https://abetlen.github.io/llama-cpp-python/whl/cpu",
        note="the safest plan on Windows: one wheel, nothing to build.",
    ),
    Runtime(
        key="linux-gpu",
        label="Linux with an NVIDIA GPU",
        host="linux",
        summary="prebuilt CUDA wheel of llama.cpp",
        index="https://abetlen.github.io/llama-cpp-python/whl/cu124",
        note="as on Windows, the index has to match the installed CUDA runtime.",
    ),
    Runtime(
        key="linux-cpu",
        label="Linux, CPU only",
        host="linux",
        summary="prebuilt CPU wheel of llama.cpp",
        index="https://abetlen.github.io/llama-cpp-python/whl/cpu",
    ),
)

#: The two halves a plan can cover. Splitting them matters because the language
#: models are what most sessions use, and the diffusion stack is several
#: gigabytes that a session which never draws anything should not have to fetch.
STAGE_RUNTIME = "runtime"
STAGE_ALL = "all"
STAGES = (STAGE_RUNTIME, STAGE_ALL)


def target(key: str) -> Runtime:
    for runtime in TARGETS:
        if runtime.key == key.casefold():
            return runtime
    known = ", ".join(runtime.key for runtime in TARGETS)
    raise KeyError(f"no platform '{key}'; there are {known}")


def host_family(host: str) -> str:
    return host.split("-")[0]


def current_host() -> str:
    """This machine, named the same way a target is."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return f"{MACOS}-arm64" if machine in ("arm64", "aarch64") else f"{MACOS}-x86_64"
    if system == "windows":
        return WINDOWS
    return LINUX


def matches(runtime: Runtime) -> bool:
    """Whether a plan is for the machine reading it.

    A plan for another platform is still worth showing, because a person may be
    preparing instructions for a second machine. It is not worth running, and the
    installer refuses to.
    """
    return host_family(runtime.host) == host_family(current_host())


def has_nvidia() -> bool:
    """Whether there is a CUDA runtime to build against, asked of the driver."""
    return shutil.which("nvidia-smi") is not None


def default_key() -> str:
    """The plan this machine would choose, which is what `auto` resolves to.

    Named rather than detected through a flag, because the two Windows plans are
    the same operating system and differ only in whether a GPU is there to use.
    """
    host = current_host()
    if host == "macos-arm64":
        return "apple-silicon"
    if host == "macos-x86_64":
        return "apple-intel"
    if host == "windows":
        return "windows-gpu" if has_nvidia() else "windows-cpu"
    return "linux-gpu" if has_nvidia() else "linux-cpu"


def resolve(setting: str) -> Runtime:
    """A platform from a setting, resolving `auto` to this machine's own."""
    if not setting or setting.strip().casefold() == "auto":
        return target(default_key())
    return target(setting)


def venv_python(venv: Path, host: str | None = None) -> Path:
    """Where the interpreter inside an environment lives, for a given host.

    Named off the target rather than off this machine, so a plan printed on one
    platform shows the command that will work on the other.
    """
    if host_family(host or current_host()) == WINDOWS:
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def plan(platform_key: str, venv: Path, stage: str = STAGE_RUNTIME) -> Plan:
    """The steps that build one platform, in order, with nothing run yet."""
    runtime = resolve(platform_key)
    if stage not in STAGES:
        raise KeyError(f"no stage '{stage}'; there is {', '.join(STAGES)}")

    steps: list[Step] = [
        Step(
            label="create the environment",
            argv=(BASE_PYTHON, "-m", "venv", VENV_DIR),
            note="skipped when it already exists, so the plan is safe to re-run",
        ),
        Step(
            label="bring the packaging tools up to date",
            argv=(VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"),
        ),
    ]
    steps.append(_llama_step(runtime))
    steps.append(
        Step(
            label="install the support packages",
            argv=(VENV_PYTHON, "-m", "pip", "install", *SUPPORT_PACKAGES),
            note="reads a weight file's header, measures free memory, and loads an image",
        )
    )

    warnings: list[str] = []
    if not matches(runtime):
        warnings.append(
            f"this plan is for {runtime.host} and this machine is {current_host()}: "
            "the commands are correct for the other machine and will not be run here"
        )
        warnings.append(
            "the environment path and the interpreter in the commands are this "
            "machine's; on the target, python resolves to that machine's own, and "
            "the environment is created wherever it is run from"
        )
    if runtime.index and host_family(runtime.host) == "windows" and runtime.key == "windows-gpu" and not has_nvidia():
        warnings.append("no NVIDIA driver found, so the CPU plan is the safer choice on this machine")

    if stage == STAGE_ALL:
        steps.append(
            Step(
                label="install the diffusion stack",
                argv=(VENV_PYTHON, "-m", "pip", "install", *TORCH_PACKAGES),
                note="several gigabytes; needed only for the image models",
            )
        )
        steps.append(
            Step(
                label="install the diffusion library from source",
                argv=(VENV_PYTHON, "-m", "pip", "install", DIFFUSERS_PACKAGE),
                note="GGUF loading and the newest pipelines are on main, not in a release",
            )
        )

    return Plan(target=runtime, steps=tuple(steps), venv=venv, stage=stage, warnings=warnings)


def _llama_step(runtime: Runtime) -> Step:
    """The one step that differs between platforms."""
    argv = [VENV_PYTHON, "-m", "pip", "install", "llama-cpp-python"]
    if runtime.index:
        argv += ["--extra-index-url", runtime.index]
    else:
        argv += ["--no-cache-dir"]
    return Step(label="install the language model runtime", argv=tuple(argv), env=runtime.env)


def values(venv: Path, host: str | None = None) -> dict[str, str]:
    """What the placeholders in a step stand for."""
    return {
        "base_python": sys.executable,
        "venv_python": str(venv_python(venv, host)),
        "venv": str(venv),
    }


def substitute(step: Step, table: dict[str, str]) -> Step:
    """A step with its placeholders filled in."""
    def fill(text: str) -> str:
        filled = text
        for name, value in table.items():
            filled = filled.replace("{" + name + "}", value)
        return filled

    return Step(
        label=step.label,
        argv=tuple(fill(argument) for argument in step.argv),
        env=tuple((name, fill(value)) for name, value in step.env),
        note=step.note,
    )


def environment(step: Step) -> dict[str, str]:
    """The process environment for a step, this machine's own plus the step's."""
    merged = dict(os.environ)
    merged.update(step.env)
    return merged
