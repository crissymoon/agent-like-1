"""The action grammar, as the harness generates it.

The harness can ask the inference engine to constrain the sampler so that only a
schema-valid action can be emitted, and it states the constraint in two dialects
because there are two. This is the first: a GBNF grammar string in the `grammar`
request field, which is llama.cpp's own sampler constraint and therefore the
dialect of the engine a quantised model is served by.

The grammar is a port of `ActionSchema::gbnf()`, which is itself generated from
`ToolRegistry::specs()`. It is ported rather than described because a second
hand-written statement of a protocol is the defect this whole study is about:
the harness's own comment says so, and cites a replay that measured a constrained
decoder refusing three turns in four on tasks that had been scoring at parity,
because the schema it was given permitted fewer layouts than the reader accepts.
The same mistake is available here, so `test_prompt_agent.py` renders the
grammar with the harness's PHP and compares the two strings character for
character rather than trusting this file.

The grammar fixes the outer key order, the key vocabulary and the value type,
and it does not fix which arguments belong to which tool, because a context-free
grammar cannot correlate two token positions. That correlation stays where the
harness keeps it: in the registry and in dispatch, which refuse a malformed
argument with a message naming it.

The function takes the tool names rather than importing a table, so this module
depends on nothing and the caller decides what the vocabulary is.
"""

from __future__ import annotations

__all__ = ["gbnf", "action_names"]


def action_names(tools: list[str]) -> list[str]:
    """The names the grammar's `tool-name` rule offers.

    `finish` is not one of them: it is its own layout, and a grammar that listed
    it twice would let a model emit a call to a tool that is not a tool.
    """
    return [name for name in tools if name != "finish"]


def _literal(text: str) -> str:
    """A GBNF string literal that emits the bare characters of *text*."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _json_literal(text: str) -> str:
    """A GBNF string literal that emits *text* as a quoted JSON string.

    The difference is one pair of escaped quotes, and it is the difference
    between a grammar that can produce an action and one that cannot. The tool
    name has to arrive inside JSON quotes, so the grammar has to carry the quote
    characters as part of what it emits rather than as part of its own syntax.
    """
    return '"\\"' + text.replace("\\", "\\\\") + '\\""'


def gbnf(names: list[str], quoted: bool = True) -> str:
    """The action protocol as a llama.cpp grammar.

    The four layouts the reader accepts are the four the grammar permits. A
    grammar that permitted fewer would refuse turns the harness dispatches, and
    one that permitted more would let through an object the harness then refuses,
    which is a worse failure because it is only visible at the far end of a run.

    *quoted* selects the `tool-name` rule, and it is the one place this port and
    the harness disagreed. A rule built from a GBNF literal emits the bare
    characters between its marks, so the sampler was free to write

        {"action":"tool","tool":list_files,"args":{"path":"."}}

    with the tool name bare. That is not JSON, `json_decode` refuses it, and a
    sweep measured what it costs: five quants over nine tasks, 0 of 9 passed with
    `protocol_compliance` 0.000 under this decoder, against 5 to 8 of 9 with no
    constraint at all. `quoted=True` emits the name inside the JSON quotes where
    the reader looks for it, and it is what `ActionSchema::gbnf()` renders now.

    `quoted=False` is kept because the defect is the study's subject: it renders
    the grammar the harness shipped before the fix, which is the condition that
    zero was measured under, and it is the comparison `test_prompt_agent.py`
    pins so the repair cannot quietly revert.
    """
    rule = _json_literal if quoted else _literal
    lines = [
        "# The action protocol of the agent harness, generated from ToolRegistry::specs().",
        "# The four layouts the harness reads, with the outer key order and the value type",
        "# fixed. A grammar cannot correlate a tool name with that tool's argument set, so the",
        "# required arguments are still enforced by ActionSchema::checkContent and by dispatch.",
        "root ::= ws ( canonical | named | flattened | finish ) ws",
        'canonical ::= "{" ws "\\"action\\"" ws ":" ws "\\"tool\\"" ws "," ws "\\"tool\\"" ws ":" ws tool-name ws "," ws "\\"args\\"" ws ":" ws args ws "}"',
        'named ::= "{" ws "\\"action\\"" ws ":" ws tool-name ws "," ws "\\"args\\"" ws ":" ws args ws "}"',
        'flattened ::= "{" ws "\\"action\\"" ws ":" ws tool-name ( ws "," ws pair )+ ws "}"',
        'finish ::= "{" ws "\\"action\\"" ws ":" ws "\\"finish\\"" ws "," ws "\\"answer\\"" ws ":" ws json-string ws "}"',
        "tool-name ::= " + " | ".join(rule(name) for name in names),
        'args ::= "{" ws "}" | "{" ws pair-list ws "}"',
        'pair-list ::= pair ( ws "," ws pair )*',
        'pair ::= "\\"" key-chars "\\"" ws ":" ws json-string',
        "key-chars ::= [A-Za-z0-9_-]+",
        'json-string ::= "\\"" ( [^"\\\\] | "\\\\" ( ["\\\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] ) )* "\\""',
        "ws ::= [ \\t\\n\\r]*",
    ]
    return "\n".join(lines) + "\n"
