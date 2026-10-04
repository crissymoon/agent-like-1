#!/usr/bin/env python3
"""
Direct chat mode.

``/chat`` turns the terminal into a plain conversation with the local model. The
agent contract does not apply here: no tool is offered, no edit is required, and
the answer is prose. That makes it the mode for asking a question, thinking out
loud, or drafting something in words - the things the staging loop is bad at
because it insists on a file.

Two things are kept from the agent because they are the useful half. Reasoning
is streamed while it happens, so a slow answer is shown rather than hidden. And
the answer can be copied: ``/copy`` takes the last reply, and leaving the mode
asks whether the transcript should be copied, so a chat can be pasted somewhere
without a trip through the model again.

The copy is made by whatever the platform offers - pbcopy, clip, wl-copy, xclip
or xsel - and when none of them is present the text is written to a file and the
path is printed, because a machine with no clipboard still has a filesystem. A
session that cannot copy is told so; a session that has nothing to copy is told
that instead, which is a different answer.

The module owns its own prompt session and its own transcript, so entering chat
mode leaves the agent's conversation, its tool history, and its staged jobs
untouched. Nothing here reads or writes a file in the workspace.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style

from config import CHAT_COPY_DIR, CHAT_COPY_PROMPT, CHAT_HISTORY_TURNS, CHAT_MAX_TOKENS, PALETTE
from engine import prompts
from engine.model_client import ModelClient, ModelError
from theme import console

#: Words that leave chat mode, with or without the leading slash.
LEAVE = frozenset({"end", "exit", "quit", "q", "done", "back"})
#: Answers that mean yes to the copy question.
CONFIRM_YES = frozenset({"y", "yes"})

#: The clipboard tools, in the order they are preferred. Each entry is the whole
#: command, because a tool that needs an argument is not a tool that is present.
DEFAULT_TOOLS: tuple[tuple[str, ...], ...] = (
    ("pbcopy",),
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
    ("clip",),
)

HELP = (
    "[dim]direct chat - no edits are staged and no tool is offered.[/dim]\n"
    "[bold]/copy[/bold] copy the last reply   [bold]/transcript[/bold] copy the whole chat   "
    "[bold]/clear[/bold] forget it\n"
    "[bold]/help[/bold] this list   [bold]/end[/bold] leave chat mode (also /quit, /exit)\n"
    "[dim]Anything else is sent to the model as a message.[/dim]"
)


@dataclass(frozen=True)
class CopyResult:
    """Whether a copy happened, and the sentence a person should read about it."""

    ok: bool
    message: str

    def __bool__(self) -> bool:
        return self.ok


def _pipe_to_tool(command: tuple[str, ...], text: str) -> bool:
    """Feed the text to a clipboard tool on its standard input."""
    try:
        finished = subprocess.run(
            list(command), input=text.encode("utf-8"), capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return finished.returncode == 0


class Clipboard:
    """
    Copies text with the platform's own tool, and writes a file when there is none.

    The lookup is injectable so a session can be checked without a clipboard, and
    so a caller can pin one tool rather than accept whatever is first on the path.
    """

    def __init__(self, tools: tuple[tuple[str, ...], ...] | None = None, directory=None,
                 runner=None, which=None) -> None:
        self._tools = DEFAULT_TOOLS if tools is None else tuple(tools)
        self._directory = Path(directory) if directory is not None else CHAT_COPY_DIR
        self._runner = runner or _pipe_to_tool
        self._which = which or shutil.which

    def tool(self) -> tuple[str, ...] | None:
        """The first available clipboard command, or None on a machine without one."""
        for command in self._tools:
            if self._which(command[0]):
                return command

        return None

    def copy(self, text: str) -> CopyResult:
        """Copy the text, falling back to a file rather than failing."""
        if not text or not text.strip():
            return CopyResult(False, "there was nothing to copy")
        command = self.tool()
        if command is not None and self._runner(command, text):
            return CopyResult(True, f"copied to the {command[0]} clipboard")

        return CopyResult(True, f"no clipboard tool here; wrote {self._write(text)}")

    def _write(self, text: str) -> Path:
        """
        Write a transcript beside the workspace and return where it went.

        The name carries the second, so a chat copied twice in the same second
        would otherwise overwrite the first copy. Rather than add a random token
        to every name, the existing name is appended to until it is free, which
        keeps an ordinary session at one file per copy and an exact name to read.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self._directory / f"chat-{stamp}.txt"
        counter = 2
        while path.exists():
            path = self._directory / f"chat-{stamp}-{counter}.txt"
            counter += 1
        path.write_text(text, encoding="utf-8")

        return path


class ChatTranscript:
    """The turns of one chat, and its plain-text rendering."""

    def __init__(self) -> None:
        self._turns: list[tuple[str, str]] = []

    def add(self, speaker: str, text: str) -> None:
        self._turns.append((speaker, text))

    def turns(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._turns)

    def empty(self) -> bool:
        return not self._turns

    def clear(self) -> None:
        self._turns.clear()

    def last_reply(self) -> str:
        """The most recent answer, which is what ``/copy`` takes."""
        for speaker, text in reversed(self._turns):
            if speaker == "model":
                return text

        return ""

    def render(self) -> str:
        return "\n\n".join(f"{speaker}: {text}" for speaker, text in self._turns)


class TerminalPrompter:
    """Reads a chat line with prompt_toolkit, so the input has history and editing."""

    def __init__(self, accent: str = "") -> None:
        self._accent = accent or PALETTE["communicative"]
        self._session: PromptSession = PromptSession(
            history=InMemoryHistory(),
            style=Style.from_dict({"prompt": f"{self._accent} bold"}),
        )

    def read(self, label: str) -> str:
        return self._session.prompt(
            HTML(f"<b><style fg='{self._accent}'>{label}</style></b> &gt; ")
        )


class ChatSession:
    """
    Runs the direct-chat loop until the user leaves it.

    The model is called with no tools at all, which is the whole difference
    between this and a turn: nothing can be staged, so the answer is whatever
    the model says. Every turn is kept so the model sees the conversation, and
    the oldest turns are dropped once the history is longer than the configured
    length, because a sitting that grows without bound stops answering.
    """

    def __init__(self, client, prompter, copier=None, sink=console, thinking: bool = True,
                 system: str = prompts.DIRECT_SYSTEM, history_turns: int = CHAT_HISTORY_TURNS,
                 offers_copy: bool = CHAT_COPY_PROMPT) -> None:
        self.client = client
        self.prompter = prompter
        self.copier = copier or Clipboard()
        self.sink = sink
        self.thinking = thinking
        self.system = system
        self.history_turns = max(1, history_turns)
        self.offers_copy = offers_copy
        self.transcript = ChatTranscript()

    # -- the loop ----------------------------------------------------------
    def run(self) -> None:
        self._announce()
        while True:
            try:
                line = self.prompter.read("chat")
            except (KeyboardInterrupt, EOFError):
                self.sink.print()
                break
            message = line.strip()
            if not message:
                continue
            if message.startswith("/"):
                if not self._slash(message):
                    break
                continue
            self._turn(message)
        self._offer_copy()

    def _announce(self) -> None:
        self.sink.print(
            "[header]direct chat[/header] [dim]no edits are staged; "
            "/end leaves, /copy takes the last reply[/dim]"
        )

    def _slash(self, line: str) -> bool:
        """Handle a chat command. False means leave the mode."""
        name = line[1:].strip().lower()
        if name in LEAVE:
            return False
        if name in ("copy", "yank"):
            self._copy(self.transcript.last_reply() or self.transcript.render(), "the last reply")
            return True
        if name in ("transcript", "all"):
            self._copy(self.transcript.render(), "the transcript")
            return True
        if name == "clear":
            self.transcript.clear()
            self.sink.print("[dim]chat history cleared[/dim]")
            return True
        if name in ("help", "?"):
            self.sink.print(HELP)
            return True
        self.sink.print(f"[warn]unknown chat command /{name}[/warn] [dim]/help lists them[/dim]")

        return True

    def _turn(self, message: str) -> None:
        self.transcript.add("you", message)
        state = {"content": False, "reasoning": False}
        self.sink.print("[dim]:: chat[/dim]")

        def on_reasoning(chunk: str) -> None:
            if not self.thinking:
                return
            if not state["reasoning"]:
                self.sink.print("[dim]thinking[/dim] [dim italic]", end="")
                state["reasoning"] = True
            self.sink.print(chunk, end="", markup=False, highlight=False, soft_wrap=True)

        def on_delta(chunk: str) -> None:
            if state["reasoning"]:
                self.sink.print()
                state["reasoning"] = False
            state["content"] = True
            self.sink.print(chunk, end="", markup=False, highlight=False, style="ai", soft_wrap=True)

        try:
            response = self.client.chat(
                self._messages(), tools=None, max_tokens=CHAT_MAX_TOKENS,
                on_delta=on_delta, on_reasoning=on_reasoning,
            )
        except ModelError as exc:
            self.sink.print(f"[error]{exc}[/error]")
            self.transcript.add("model", f"(no answer: {exc})")
            return

        reply = ModelClient.content_of(response)
        if not state["content"]:
            self.sink.print(reply, markup=False, highlight=False, style="ai", soft_wrap=True)
        else:
            self.sink.print()
        self.transcript.add("model", reply or "(empty reply)")

    def _messages(self) -> list[dict]:
        kept = self.transcript.turns()[-self.history_turns:]
        conversation: list[dict] = [{"role": "system", "content": self.system}]
        for speaker, text in kept:
            conversation.append({
                "role": "user" if speaker == "you" else "assistant",
                "content": text,
            })

        return conversation

    # -- copying -----------------------------------------------------------
    def _copy(self, text: str, label: str) -> CopyResult:
        result = self.copier.copy(text)
        style = "success" if result.ok else "warn"
        self.sink.print(f"[{style}]{result.message}[/{style}] [dim]({label})[/dim]")

        return result

    def _offer_copy(self) -> None:
        """Ask whether to copy the transcript, which is the offer the mode owes."""
        if not self.offers_copy or self.transcript.empty():
            return
        try:
            answer = self.prompter.read("copy the transcript? [y/N]")
        except (KeyboardInterrupt, EOFError):
            return
        if answer.strip().lower() in CONFIRM_YES:
            self._copy(self.transcript.render(), "the transcript")
        else:
            self.sink.print("[dim]not copied[/dim]")


def run(client, accent: str = "", sink=console) -> None:
    """Enter direct chat mode and stay there until the user leaves it."""
    ChatSession(client, TerminalPrompter(accent), sink=sink).run()
