"""Holding a conversation with a local language model through llama.cpp.

There is one deliberate departure from the script this replaces, and it is the
reason this module exists rather than being copied.

That script read its throughput out of llama.cpp's own log lines by redirecting
file descriptor 2 into a pipe and scraping it with regular expressions. It worked,
and it was the wrong mechanism: the redirect swallowed every other thing the
runtime wrote to stderr, including the messages that explain a failure, and the
numbers it produced disappeared whenever the log format moved. So the numbers
here are measured instead. The clock is read at the first token and at the last,
and the token count comes from the model's own tokeniser applied to the text it
actually produced. That is a smaller claim than llama.cpp's internal counters and
it is one this module can stand behind: the time is the time, and the tokens are
the tokens.

Two more decisions worth naming.

The constructor's arguments are filtered against the installed library's own
signature. llama-cpp-python adds and removes keyword arguments between releases,
and an argument that is not there is a session that will not start for a reason
that has nothing to do with the model. An argument that was dropped is reported
rather than quietly left out.

The context is the smaller of what was asked for and what the file declares. A
model whose metadata says 4096 is not asked for 8192 and then refused, and a
window that had to be reduced is said out loud, because a reduced window is a
shorter conversation.
"""

from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tools.imgmodels.sizes import human_bytes

from . import textui, theme
from .modes import Mode
from .registry import Entry
from .transcript import Report, Turn, write

#: How much of the context a conversation may occupy. The rest is left for the
#: answer, because a prompt that fills the window exactly has no room to be
#: replied to.
CONTEXT_SHARE = 0.6

#: How much of the context one answer may take, whatever is left.
ANSWER_SHARE = 0.5

#: Passed only when the accelerator is in use: flash attention is a Metal and
#: CUDA kernel, and asking for it on a CPU build is at best ignored.
FLASH_ATTENTION = True

#: Pinned memory stops the weights being paged out mid-conversation, which is
#: what stalls a long answer. It is a reservation rather than a copy, and on a
#: machine with room to spare it costs nothing.
#:
#: On a machine without, it is the most direct way to freeze one. `mlock` asks
#: the kernel to hold every byte of the file resident and unswappable, so a model
#: that is too large for the machine stops being a slow session and becomes an
#: operating system with nothing left to schedule a window with. It is therefore
#: decided per model against the memory actually free, rather than switched on
#: for every model on every machine.
MLOCK_SHARE = 0.6

#: The batch size for reading a prompt. Larger reads a prompt faster and needs
#: more transient memory, and this is the value the machine this was written on
#: is fastest at.
BATCH = 512


class TextError(RuntimeError):
    """llama.cpp is not installed, or the file will not load."""


def library() -> tuple[bool, str]:
    """Whether llama.cpp is importable here, and why not when it is not."""
    try:
        import llama_cpp  # noqa: F401
    except ImportError as error:
        return False, str(error)
    return True, ""


def require_library() -> None:
    present, reason = library()
    if present:
        return
    raise TextError(
        "the language model runtime is not installed in this interpreter\n"
        f"  {reason}\n"
        "  the install menu builds it: run the tool and choose this platform, "
        "or run `run.py install`"
    )


def offload_layers(device: str) -> int:
    """How many layers to hand to the accelerator.

    Negative means every layer, which is what both Metal and CUDA want. A machine
    with no accelerator gets zero, because the flag is otherwise a request to
    offload onto nothing.
    """
    return 0 if device == "cpu" else -1


def context_for(entry: Entry, declared: int | None, ceiling: int) -> tuple[int, str]:
    """The window to open, and a note when it had to be reduced.

    Three numbers and only one of them wins. The model's entry in `models.toml`
    is what that model should be opened at here; the setting is a machine wide
    ceiling, which is why zero means "no ceiling" rather than "4096", since a
    global default would otherwise silently overrule every model's own window.
    The file's own declaration is the last word, because asking for more than the
    model has is a load that fails.
    """
    wanted = entry.context or 4096
    reduced_by_setting = bool(ceiling) and ceiling < wanted
    if reduced_by_setting:
        wanted = ceiling

    if declared and wanted > declared:
        return declared, (
            f"asked for {wanted:,} of context and {entry.label} declares "
            f"{declared:,}, so the declared window was used"
        )
    if reduced_by_setting:
        return wanted, (
            f"the context ceiling is {ceiling:,}, below the {entry.context:,} "
            f"{entry.label} is registered for, so {wanted:,} was opened"
        )
    return wanted, ""


def free_memory() -> int | None:
    """What the machine could hand to a new process, or nothing if unmeasured.

    `available` rather than `free`, because the page cache a weight file is
    already sitting in counts towards what can still be had, while `free` on
    macOS reports almost none of it while the file is warm. A measurement that
    fails is reported as missing rather than allowed to fail a session.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 - a number that cannot be read is not an error here
        return None


def residency(path: Path, free: int | None) -> tuple[bool, str]:
    """Whether to pin this file in RAM, and why not when it is not pinned.

    The share is taken of what is free rather than of what is installed, because
    the number that decides whether the machine stays responsive is the memory
    that is actually there to take.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False, ""
    if free is None:
        return False, (
            "how much memory is free could not be measured, so the weights are "
            "left pageable rather than pinned"
        )
    if size <= free * MLOCK_SHARE:
        return True, ""
    return False, (
        f"{human_bytes(size)} of weights against {human_bytes(free)} free: pinning "
        "all of it would leave the machine nothing to run on, so the weights are "
        "left pageable and the file is read through the page cache instead"
    )


def read_header(path: Path):
    """The file's own metadata, or nothing when it cannot be read."""
    try:
        from tools import gguf
    except ImportError:
        return None
    try:
        return gguf.read_header(path)
    except Exception:  # noqa: BLE001 - a file that cannot be read fails at load, with a better message
        return None


@dataclass
class Loaded:
    """A model that is open, with what was read about it on the way in."""

    entry: Entry
    path: Path
    llm: object
    header: object | None
    context: int
    device: str
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        header = self.header
        return {
            "quantisation": header.quantisation() if header else "",
            "architecture": header.architecture() if header else "",
            "parameters": header.parameter_count() if header else None,
            "file_bytes": self.path.stat().st_size if self.path.is_file() else None,
        }

    def describe(self) -> str:
        """One line about the open model, for the screen."""
        facts = self.summary()
        parts = [self.entry.label, f"context {self.context:,}"]
        if facts["quantisation"]:
            parts.append(facts["quantisation"])
        if facts["parameters"]:
            parts.append(f"{facts['parameters'] / 1e9:.2f}B parameters")
        return " · ".join(parts)


def load(entry: Entry, path: Path, device: str, context: int, threads: int) -> Loaded:
    """Open one weight file, reporting every argument the build would not take."""
    require_library()
    from llama_cpp import Llama

    header = read_header(path)
    declared = header.context_length() if header else None
    window, note = context_for(entry, declared, context)
    notes = [note] if note else []

    pinned, pinned_note = residency(path, free_memory())
    if pinned_note:
        notes.append(pinned_note)

    wanted: dict = {
        "model_path": str(path),
        "n_ctx": window,
        "n_gpu_layers": offload_layers(device),
        "n_batch": BATCH,
        "use_mlock": pinned,
        "verbose": False,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
    }
    if device == "mps":
        # The GPU does the arithmetic, so spare CPU threads only add contention.
        wanted["n_threads"] = 1
        wanted["flash_attn"] = FLASH_ATTENTION
    elif device == "cuda":
        wanted["n_gpu_layers"] = -1
        wanted["flash_attn"] = FLASH_ATTENTION
        if threads:
            wanted["n_threads"] = threads
    elif threads:
        wanted["n_threads"] = threads

    accepted = _accepted(wanted, Llama)
    refused = sorted(set(wanted) - set(accepted))
    if refused:
        notes.append(
            f"this llama.cpp build does not take {', '.join(refused)}, so those were "
            "left at the library's own defaults"
        )

    try:
        llm = Llama(**accepted)
    except Exception as error:  # noqa: BLE001 - the message is the report
        raise TextError(f"{path.name} would not load: {error}") from error

    return Loaded(
        entry=entry,
        path=path,
        llm=llm,
        header=header,
        context=window,
        device=device,
        notes=notes,
    )


def _accepted(wanted: dict, constructor) -> dict:
    """Keep only the keyword arguments this build of the library declares.

    A library that does not expose a signature is trusted with everything,
    because dropping arguments on a guess would break a build that works.
    """
    try:
        parameters = inspect.signature(constructor.__init__).parameters
    except (TypeError, ValueError):
        return dict(wanted)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return dict(wanted)
    return {name: value for name, value in wanted.items() if name in parameters}


def count_tokens(llm, text: str) -> int | None:
    """Tokens in a piece of text, by the model's own tokeniser.

    `None` means it could not be counted, and that is recorded rather than
    estimated: a rate derived from a character count is worse than no rate,
    because it reads like a measurement.
    """
    if not text:
        return 0
    for attempt in ({"add_bos": False}, {}):
        try:
            return len(llm.tokenize(text.encode("utf-8"), **attempt))
        except TypeError:
            continue
        except Exception:  # noqa: BLE001 - a tokeniser that refuses is reported as unmeasured
            return None
    return None


@dataclass
class Reply:
    """One answer, with the numbers that make it comparable."""

    text: str
    seconds: float
    first_token: float | None
    tokens: int | None


@dataclass
class Session:
    """A conversation with one model, and the record of it for the transcript."""

    loaded: Loaded
    mode: Mode
    stream: bool = True
    #: A ceiling on one answer, when a caller wants a shorter one than the window
    #: would allow. Comparing six models on one question is a good reason.
    answer_limit: int = 0
    messages: list[dict] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)
    prompt_tokens: int | None = 0
    generated_tokens: int | None = 0
    first_token: float | None = None
    elapsed: float = 0.0
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = [{"role": "system", "content": self.mode.system}]

    def ask(self, prompt: str) -> Reply:
        """Send one message and read the answer, streaming it as it arrives."""
        self.messages.append({"role": "user", "content": prompt})
        self.turns.append(Turn(role="user", content=prompt))
        self._make_room()

        reply = self._generate()
        self.messages.append({"role": "assistant", "content": reply.text})
        self.turns.append(
            Turn(
                role="assistant",
                content=reply.text,
                seconds=reply.seconds,
                tokens=reply.tokens,
            )
        )

        self.elapsed += reply.seconds
        if reply.tokens is not None:
            self.generated_tokens = (self.generated_tokens or 0) + reply.tokens
        if self.first_token is None:
            self.first_token = reply.first_token
        return reply

    def _generate(self) -> Reply:
        started = time.time()
        first: float | None = None
        pieces: list[str] = []

        stream = self.loaded.llm.create_chat_completion(
            messages=self.messages,
            max_tokens=self.answer_room(),
            stream=True,
            temperature=self.mode.temperature,
            repeat_penalty=1.1,
            top_k=40,
        )
        for part in stream:
            choices = part.get("choices") or []
            if not choices:
                continue
            chunk = (choices[0].get("delta") or {}).get("content") or ""
            if not chunk:
                continue
            if first is None:
                first = time.time() - started
            pieces.append(chunk)
            if self.stream:
                textui.stream_text(chunk)

        seconds = time.time() - started
        text = "".join(pieces)
        return Reply(
            text=text,
            seconds=seconds,
            first_token=first,
            tokens=count_tokens(self.loaded.llm, text),
        )

    def answer_room(self) -> int:
        """A ceiling on one answer, so a single turn cannot fill the window."""
        return self.answer_limit or max(64, int(self.loaded.context * ANSWER_SHARE))

    def sent_tokens(self) -> int | None:
        """Tokens in what would be sent, by the model's own tokeniser."""
        joined = "\n".join(str(message.get("content", "")) for message in self.messages)
        return count_tokens(self.loaded.llm, joined)

    def _make_room(self) -> None:
        """Drop the oldest turns while the conversation is too long to send.

        The system prompt is kept because it is the mode and the newest message
        is kept because it is the question. What goes is the middle, oldest
        first, which is the part a conversation can lose and still continue.
        """
        limit = int(self.loaded.context * CONTEXT_SHARE)
        measured = self.sent_tokens()
        if measured is None:
            self.notes.append(
                "the tokeniser could not count this conversation, so the window "
                "was not trimmed and a long one may be refused by the runtime"
            )
            return

        dropped = 0
        while measured > limit and len(self.messages) > 2:
            del self.messages[1]
            dropped += 1
            narrowed = self.sent_tokens()
            if narrowed is None:
                break
            measured = narrowed

        if dropped:
            self.notes.append(
                f"{dropped} earlier message(s) were dropped to stay inside the "
                f"{self.loaded.context:,} token window"
            )
        self.prompt_tokens = measured

    def report(self, folder: Path, root: Path, command: str, runtime: str) -> Path:
        """Write the transcript of this conversation and return where it went."""
        facts = self.loaded.summary()
        document = Report(
            key=self.loaded.entry.key,
            label=self.loaded.entry.label,
            model_path=self.loaded.path,
            file_bytes=facts["file_bytes"],
            quantisation=facts["quantisation"],
            architecture=facts["architecture"],
            parameters=facts["parameters"],
            context=self.loaded.context,
            runtime=runtime,
            device=self.loaded.device,
            mode_label=self.mode.label,
            temperature=self.mode.temperature,
            started=self.started,
            seconds=self.elapsed,
            turns=list(self.turns),
            prompt_tokens=self.prompt_tokens,
            generated_tokens=self.generated_tokens,
            time_to_first_token=self.first_token,
            command=command,
            notes=[*self.loaded.notes, *self.notes],
            root=root,
        )
        return write(document, folder)


def voice(loaded: Loaded, palette=None) -> str:
    """A one line description with the muted accent applied, for a prompt."""
    palette = palette or theme.active()
    return palette.paint(loaded.describe(), "muted")


def threads_available() -> int:
    """How many CPUs are present, without importing anything heavy."""
    try:
        return os.cpu_count() or 0
    except Exception:  # noqa: BLE001
        return 0
