"""What this machine can hold, and where the offload boundary falls.

There are two budgets and they are not the same one. Device memory is what the
graph can occupy on the accelerator; host memory is what has to be resident on
the machine, because offloading swaps components *out* of the device and into
host memory rather than into nothing. A plan that compares the whole model
against the accelerator alone will call a model runnable and then let the
machine page for an hour.

The component sizes come from the Hub's own file metadata, so the plan is made
before anything is fetched. That ordering is the point: the answer to "will this
fit" should arrive before the download, not after it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .catalog import ModelProfile
from .sizes import human_bytes

#: How much of a budget is treated as usable. A denoising step needs room for
#: activations beyond the weights, and a plan that fills a device exactly does
#: not run.
BUDGET_FRACTION = 0.85

OFFLOAD_CHOICES = ("auto", "none", "model", "sequential")

#: What the plan concluded. `fits` means no offload is needed; `exceeds-host`
#: means even the smallest arrangement needs more host memory than is free.
FITS = "fits"
OFFLOAD_MODEL = "offload-model"
OFFLOAD_SEQUENTIAL = "offload-sequential"
EXCEEDS_HOST = "exceeds-host"


@dataclass(frozen=True)
class Weights:
    """The bytes that have to be found a home, and where each part comes from."""

    transformer: Path
    transformer_bytes: int
    components: dict[str, int]
    components_known: bool
    encoder: Path | None = None

    @property
    def support_bytes(self) -> int:
        return sum(self.components.values())

    @property
    def total_bytes(self) -> int:
        return self.transformer_bytes + self.support_bytes

    def largest(self) -> tuple[str, int]:
        """The biggest single component, counting the transformer as one."""
        name, size = "transformer", self.transformer_bytes
        for component, bytes_ in self.components.items():
            if bytes_ > size:
                name, size = component, bytes_
        return name, size

    def resident_floor(self) -> int:
        """The least host memory a streaming arrangement can work in.

        Weights are streamed from host memory, so the transformer and the one
        largest component are what must be held at once, whatever the offload
        setting is. Below this the machine swaps rather than runs.
        """
        return self.transformer_bytes + self.largest()[1]


def component_sizes(repo: str, components: tuple[str, ...]) -> tuple[dict[str, int], bool]:
    """Bytes per component of a base repository, from the Hub's own metadata.

    The transformer subfolder is deliberately left out of the caller's list: the
    quantised file replaces it, so downloading it would be wasted bytes.
    """
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=True)
    except Exception:
        return {}, False

    sizes: dict[str, int] = {}
    for sibling in info.siblings:
        name = sibling.rfilename
        component = name.split("/")[0]
        if component in components:
            sizes[component] = sizes.get(component, 0) + (sibling.size or 0)
    return sizes, True


def read_weights(profile: ModelProfile, path: Path, encoder: Path | None = None) -> Weights:
    """Measure the weight file and ask the Hub for the parts it does not carry.

    A text encoder file, when one is given, replaces the repository's own encoder
    in the accounting. The size that matters is the one that will be held, and the
    full precision encoder the file stands in for is precisely the thing this
    machine is trying not to hold.
    """
    sizes, known = component_sizes(profile.repo, profile.components)
    if encoder is not None:
        sizes["text_encoder"] = encoder.stat().st_size
    return Weights(
        transformer=path,
        transformer_bytes=path.stat().st_size,
        components=sizes,
        components_known=known,
        encoder=encoder,
    )


def host_available() -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.virtual_memory().available)


@dataclass
class Plan:
    """A device, a model, and the arrangement that puts one on the other."""

    device: str
    device_budget: int | None
    host_available: int | None
    weights: Weights
    profile: ModelProfile
    verdict: str
    offload: str
    warnings: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        largest_name, largest = self.weights.largest()
        device_room = self.device_room()
        if self.verdict == FITS:
            return f"{human_bytes(self.weights.total_bytes)} of weights fit in {human_bytes(device_room)}"
        if self.verdict == OFFLOAD_MODEL:
            return (
                f"{largest_name} is the largest part at {human_bytes(largest)}, "
                f"so one component is resident on the device at a time"
            )
        if self.verdict == OFFLOAD_SEQUENTIAL:
            return (
                f"{largest_name} alone is {human_bytes(largest)}, above the "
                f"{human_bytes(device_room)} budget, so submodules are streamed "
                f"through the device one at a time"
            )
        return (
            f"{largest_name} plus the transformer is {human_bytes(self.weights.resident_floor())}, "
            f"which is more host memory than the {human_bytes(self.host_available)} this machine has free"
        )

    def device_room(self) -> int | None:
        if self.device_budget is None:
            return None
        return int(self.device_budget * BUDGET_FRACTION)

    def host_room(self) -> int | None:
        if self.host_available is None:
            return None
        return int(self.host_available * BUDGET_FRACTION)

    def render(self, indent: str = "  ") -> str:
        weights = self.weights
        lines = [
            f"model             {self.profile.label} ({self.profile.key})",
            f"device            {self.device}",
            f"device budget     {human_bytes(self.device_budget)}",
            f"host available    {human_bytes(self.host_available)}",
            f"transformer       {weights.transformer.name} ({human_bytes(weights.transformer_bytes)})",
            f"base components   "
            f"{human_bytes(weights.support_bytes) if weights.components_known else 'unknown'} "
            f"from {self.profile.repo}",
            f"total to hold     "
            f"{human_bytes(weights.total_bytes) if weights.components_known else 'unknown'}",
        ]
        if weights.encoder is not None:
            held = weights.components.get("text_encoder")
            lines.append(
                f"text encoder      {weights.encoder.name} "
                f"({human_bytes(held) if held else 'unknown'}), held packed in place of "
                f"{self.profile.repo}'s own"
            )
        if weights.components_known:
            for name, size in sorted(weights.components.items(), key=lambda item: -item[1]):
                lines.append(f"{indent}{name:<14}{human_bytes(size)}")
        lines.append(f"offload           {self.offload}")
        lines.append(f"reason            {self.note}")
        for warning in self.warnings:
            lines.append(f"warning           {warning}")
        return "\n".join(lines)


def choose_device(requested: str | None) -> str:
    """The device asked for, or the best one this machine offers."""
    if requested:
        return requested
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def device_budget(name: str) -> int | None:
    if name == "mps":
        import torch

        return int(torch.mps.recommended_max_memory())
    if name == "cuda":
        import torch

        return int(torch.cuda.mem_get_info()[0])
    return None


def plan_run(profile: ModelProfile, weights: Weights, requested: str | None = None) -> Plan:
    """Decide the arrangement, and say plainly when there is not one.

    The host check is made whatever the device says, because offloading is not
    free: it moves work between two memories rather than removing it.
    """
    device = choose_device(requested)
    budget = device_budget(device)
    host = host_available()
    plan = Plan(
        device=device,
        device_budget=budget,
        host_available=host,
        weights=weights,
        profile=profile,
        verdict=FITS,
        offload="none",
    )

    room = plan.device_room()
    known = weights.components_known

    if device == "cpu" or room is None:
        # Everything lives in host memory, so the whole model has to fit at once.
        plan.offload = "none"
        plan.verdict = FITS
        if known and host is not None and weights.total_bytes > int(host * BUDGET_FRACTION):
            plan.verdict = EXCEEDS_HOST
    elif weights.total_bytes <= room:
        plan.offload = "none"
        plan.verdict = FITS
    elif weights.largest()[1] <= room:
        plan.offload = "model"
        plan.verdict = OFFLOAD_MODEL
    else:
        plan.offload = "sequential"
        plan.verdict = OFFLOAD_SEQUENTIAL

    floor = weights.resident_floor()
    if plan.verdict != FITS and known and host is not None and floor > int(host * BUDGET_FRACTION):
        # Only when the arrangement keeps weights in host memory. An arrangement
        # that fits on the device streams through the host one tensor at a time
        # rather than living there, so the floor does not describe it, and saying
        # otherwise would refuse a run that would have worked.
        plan.verdict = EXCEEDS_HOST
        plan.offload = "sequential"
        plan.warnings.append(
            f"even the smallest arrangement needs {human_bytes(floor)} resident and this "
            f"machine has {human_bytes(host)} free, so it will page to disk; expect minutes "
            f"per step rather than seconds"
        )
    elif plan.offload == "sequential":
        plan.warnings.append(
            "weights stream from host memory one submodule at a time, which is correct "
            "but slow"
        )
    if not known:
        plan.warnings.append(
            "component sizes unavailable, so the arrangement is unverified and the "
            "largest component may be underestimated"
        )
    return plan


def apply_override(plan: Plan, choice: str) -> Plan:
    """Honour an explicit offload request, refusing only what cannot work.

    The warnings stand whatever the request is: choosing an arrangement does not
    change how much memory the machine has, and the note says so.
    """
    if choice == "auto":
        return plan
    if plan.device == "cpu":
        if choice == "none":
            return plan
        raise SystemExit("offloading needs an accelerator: this machine has none")

    plan.offload = choice
    plan.verdict = {
        "none": FITS,
        "model": OFFLOAD_MODEL,
        "sequential": OFFLOAD_SEQUENTIAL,
    }[choice]
    plan.warnings.append(
        f"offload set to {choice} by request rather than by the plan for this machine"
    )
    return plan
