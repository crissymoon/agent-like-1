"""
Model client.

A thin, non-streaming client for the local llama-server's OpenAI-compatible
endpoint. Streaming is deliberately omitted: for a small model it adds moving
parts without improving tool-call reliability. Two tools are exposed, one to
the interactive agent and one to the authoring pass used by rework.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from config import GENERATION_MAX_TOKENS, GENERATION_TEMPERATURE, REQUEST_TIMEOUT
from engine import llama_server
from engine import prompts

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
                    "description": "Files the change touches, relative to the target root.",
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


class ModelError(Exception):
    """Raised when the local model cannot be reached or returns nothing usable."""


def _extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of a blob of text."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\" and in_string:
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class ModelClient:
    def __init__(self, model: str = "local", temperature: float = GENERATION_TEMPERATURE) -> None:
        self.model = model
        self.temperature = temperature

    def available(self) -> bool:
        return llama_server.is_up()

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = GENERATION_MAX_TOKENS,
        tool_choice: str | None = None,
        timeout: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        limit = timeout or REQUEST_TIMEOUT
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
    def finish_reason_of(response: dict) -> str:
        """Why the server stopped: 'stop', 'length' (truncated), 'tool_calls'."""
        choices = response.get("choices") or []
        return choices[0].get("finish_reason", "") if choices else ""

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Run one turn and return the assistant message dict."""
        response = self.chat(messages, tools=tools)
        return self.message_of(response)

    def generate_edits(self, instruction: str, context: str, note: str = "") -> list[dict]:
        """
        Ask the model to author edits. The tool interface is required on the
        first pass because an auto pass on a small model frequently returns
        nothing; a plain-text JSON fallback covers the rest.
        """
        messages = [
            {"role": "system", "content": prompts.AUTHOR_SYSTEM},
            {"role": "user", "content": prompts.author_user_message(instruction, context, note)},
        ]
        try:
            response = self.chat(messages, tools=[PROPOSE_EDITS_TOOL], tool_choice="required")
        except ModelError:
            response = {}

        for call in self.tool_calls_of(response):
            if call.get("function", {}).get("name") == "propose_edits":
                args = call["function"].get("arguments", "")
                try:
                    parsed = json.loads(args) if isinstance(args, str) else args
                except json.JSONDecodeError:
                    continue
                edits = parsed.get("edits") if isinstance(parsed, dict) else None
                if edits:
                    return edits

        content = self.content_of(response) if response else ""
        parsed = _extract_json(content)
        if parsed and isinstance(parsed.get("edits"), list):
            return parsed["edits"]

        # Last resort: a plain request with no tools at all.
        try:
            plain = self.chat(messages, tools=None)
        except ModelError as exc:
            raise ModelError(f"model did not return edits: {exc}") from exc
        parsed = _extract_json(self.content_of(plain))
        if parsed and isinstance(parsed.get("edits"), list):
            return parsed["edits"]
        raise ModelError(f"model did not return edits: {self.content_of(plain)[:300] or 'empty response'}")
