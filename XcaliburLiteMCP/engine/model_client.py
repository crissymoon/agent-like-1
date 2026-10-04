"""
Model client.

A thin client for the local llama-server's OpenAI-compatible endpoint. It talks
to the server one of two ways: a buffered call, or a streamed call that reports
tokens as they arrive so the terminal can show the model thinking. Both return
the same response shape, so the caller does not care which was used.

Three tools exist: ``run_sh_runner`` for the interactive agent, ``propose_edits``
for the authoring pass, and a shared repair path that turns a linter's defect
list into a surgical edit.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import requests

from config import GENERATION_MAX_TOKENS, GENERATION_TEMPERATURE, REQUEST_TIMEOUT, STREAM_OUTPUT
from engine import llama_server, prompts, prompt_repair
RUN_SH_RUNNER_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "run_sh_runner",
        "description": (
            "The only way to change code. Stage proposed edits into a shadow copy "
            "for human approval in the dashboard. Returns a job id and a review URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "What the change is and why."},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files the change touches, relative to the workspace.",
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
                            "regex": {"type": "boolean", "description": "Treat old_str as a regex."},
                        },
                        "required": ["filepath", "old_str", "new_str"],
                    },
                },
                "run": {"type": "string", "description": "Optional allowed command to run for context."},
            },
            "required": ["instruction"],
        },
    },
}

PROPOSE_EDITS_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "propose_edits",
        "description": "Return the precise edits that satisfy the instruction.",
        "parameters": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string"},
                            "old_str": {"type": "string", "description": "Exact text to replace; empty to create the file."},
                            "new_str": {"type": "string"},
                            "regex": {"type": "boolean"},
                        },
                        "required": ["filepath", "old_str", "new_str"],
                    },
                }
            },
            "required": ["edits"],
        },
    },
}

DeltaHook = Callable[[str], None]
ToolHook = Callable[[str, int], None]


class ModelError(Exception):
    """Raised when the local model cannot be reached or returns nothing usable."""


class ModelClient:
    def __init__(self, model: str = "local", temperature: float = GENERATION_TEMPERATURE,
                 model_hint: str = "") -> None:
        self.model = model
        self.temperature = temperature
        self.model_hint = model_hint

    def available(self) -> bool:
        return llama_server.is_up()

    def system_prompt(self, base: str) -> str:
        """Append any per-model guidance the caller registered."""
        hint = prompts.adapt_for_model(self.model_hint or self.model)
        return f"{base}\n{hint}" if hint else base

    # -- transport ---------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = GENERATION_MAX_TOKENS,
        tool_choice: str | None = None,
        timeout: int | None = None,
        stream: bool | None = None,
        on_delta: DeltaHook | None = None,
        on_reasoning: DeltaHook | None = None,
        on_tool_delta: ToolHook | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        limit = timeout or REQUEST_TIMEOUT
        if stream is None:
            stream = STREAM_OUTPUT

        if stream and (on_delta or on_reasoning or on_tool_delta or tools is not None):
            return self._stream_chat(payload, limit, on_delta, on_reasoning, on_tool_delta)

        payload["stream"] = False
        return self._buffered_chat(payload, limit)

    def _buffered_chat(self, payload: dict, limit: int) -> dict:
        # A small model occasionally emits a malformed tool call that the server
        # rejects with a 5xx. One retry clears the transient cases. A timeout is
        # never retried: repeating a slow generation only doubles the wait.
        last_error = ""
        for attempt in range(2):
            try:
                resp = requests.post(
                    llama_server.base_url() + "/v1/chat/completions",
                    json=payload,
                    timeout=limit,
                )
            except requests.Timeout:
                raise ModelError(f"model timed out after {limit}s")
            except requests.RequestException as exc:
                last_error = f"model request failed: {exc}"
                continue
            if resp.status_code != 200:
                last_error = f"model returned HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code >= 500 and attempt == 0:
                    continue
                raise ModelError(last_error)
            return resp.json()
        raise ModelError(last_error or "model request failed")

    def _stream_chat(self, payload: dict, limit: int, on_delta: DeltaHook | None,
                     on_reasoning: DeltaHook | None,
                     on_tool_delta: ToolHook | None = None) -> dict:
        """Stream tokens, assembling the same response shape the buffered call returns."""
        payload = dict(payload)
        payload["stream"] = True
        try:
            resp = requests.post(
                llama_server.base_url() + "/v1/chat/completions",
                json=payload,
                timeout=limit,
                stream=True,
            )
        except requests.Timeout:
            raise ModelError(f"model timed out after {limit}s")
        except requests.RequestException as exc:
            raise ModelError(f"model request failed: {exc}")

        content: list[str] = []
        reasoning: list[str] = []
        tool_calls: dict[int, dict] = {}
        finish = ""

        with resp:
            if resp.status_code != 200:
                raise ModelError(f"model returned HTTP {resp.status_code}: {resp.text[:300]}")
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                if thought:
                    reasoning.append(thought)
                    if on_reasoning:
                        on_reasoning(thought)
                text = delta.get("content")
                if text:
                    content.append(text)
                    if on_delta:
                        on_delta(text)
                for call in delta.get("tool_calls") or []:
                    index = call.get("index", 0)
                    slot = tool_calls.setdefault(
                        index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                    if call.get("id"):
                        slot["id"] = call["id"]
                    function = call.get("function") or {}
                    if function.get("name"):
                        slot["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        slot["function"]["arguments"] += function["arguments"]
                    if on_tool_delta:
                        name = slot["function"]["name"] or "tool"
                        on_tool_delta(name, len(slot["function"]["arguments"]))

        message: dict = {"role": "assistant", "content": "".join(content) or None}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return {"choices": [{"message": message, "finish_reason": finish or ("tool_calls" if tool_calls else "stop")}]}

    # -- accessors ---------------------------------------------------------
    @staticmethod
    def message_of(response: dict) -> dict:
        choices = response.get("choices") or []
        if not choices:
            raise ModelError("model returned no choices")
        return choices[0].get("message", {})

    @staticmethod
    def tool_calls_of(response: dict) -> list[dict]:
        return ModelClient.message_of(response).get("tool_calls") or []

    @staticmethod
    def content_of(response: dict) -> str:
        return (ModelClient.message_of(response).get("content") or "").strip()

    @staticmethod
    def reasoning_of(response: dict) -> str:
        return (ModelClient.message_of(response).get("reasoning_content") or "").strip()

    @staticmethod
    def finish_reason_of(response: dict) -> str:
        """Why the server stopped: 'stop', 'length' (truncated), 'tool_calls'."""
        choices = response.get("choices") or []
        return choices[0].get("finish_reason", "") if choices else ""

    # -- higher level ------------------------------------------------------
    def generate_edits(self, instruction: str, context: str, note: str = "") -> list[dict]:
        return self._authoring_pass(
            self.system_prompt(prompts.AUTHOR_SYSTEM),
            prompts.author_user_message(instruction, context, note),
        )

    def generate_repairs(self, instruction: str, context: str, diagnostics: str) -> list[dict]:
        """Ask for the smallest edit that clears a defect list."""
        return self._authoring_pass(
            self.system_prompt(prompts.REPAIR_SYSTEM),
            prompts.repair_user_message(instruction, context, diagnostics),
        )

    def _authoring_pass(self, system: str, user: str) -> list[dict]:
        """
        Ask the model to author edits.

        The tool interface is required on the first pass because an auto pass on
        a small model frequently returns nothing; a plain-text JSON fallback and
        the prompt-repair coerce cover the rest.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = self.chat(messages, tools=[PROPOSE_EDITS_TOOL], tool_choice="required", stream=False)
        except ModelError:
            response = {}

        for call in self.tool_calls_of(response):
            if call.get("function", {}).get("name") in ("propose_edits", "run_sh_runner"):
                edits = prompt_repair.coerce_edits(
                    prompt_repair.extract_json(call["function"].get("arguments", ""))
                )
                if edits:
                    return edits

        content = self.content_of(response) if response else ""
        edits = prompt_repair.coerce_edits(content)
        if edits:
            return edits

        try:
            plain = self.chat(messages, tools=None, stream=False)
        except ModelError as exc:
            raise ModelError(f"model did not return edits: {exc}") from exc
        edits = prompt_repair.coerce_edits(self.content_of(plain))
        if edits:
            return edits
        raise ModelError(f"model did not return edits: {self.content_of(plain)[:300] or 'empty response'}")
