"""The one place a service address is turned into the address to post to.

`hybrid.py` and `prompt_agent.py` are the two live profiles, and both are handed
the same argument from the same documented command lines. The argument is
documented as a base url, and the value in every recorded run is the endpoint
completions are posted to:

    --gemma-url http://127.0.0.1:8081/v1/chat/completions

Both spellings have to mean one thing. Appending the chat path to the second
produced `.../v1/chat/completions/v1/chat/completions`, which a server answers
404, and a 404 reads in a run as a model that is missing rather than as a url
that was built twice.

The harness met this defect first and repaired it in `lib/EngineProfile::rootOf()`.
The rule below is that function's, kept identical on purpose rather than
re-derived, and `test_prompt_agent.py` reads the PHP case table back out of
`lib/AgentSelfCheck.php` and requires this module to answer every case the same
way. Two implementations that must agree are pinned to one table, not to each
other, so either side drifting fails a check rather than producing a second
opinion about the same url.

The rule is mechanical: strip a trailing OpenAI-compatible surface, which is a
version segment followed by a path, and leave anything else alone. A root url is
returned unchanged, which is what makes it safe to apply unconditionally.
"""

from __future__ import annotations

import re

#: The path a turn is posted to. It is a constant here so the address a run was
#: taken against is built in exactly one place.
CHAT_PATH = "/v1/chat/completions"

#: The engine's own readiness probe. It lives at the root and not under the
#: completions surface, which is the measured fact behind the rule: on a running
#: server `.../v1/chat/completions/health` answers 404 while `/health` on the
#: same port answers 200, so a probe built under the surface reads a healthy
#: engine as absent.
HEALTH_PATH = "/health"

#: A trailing versioned surface: a version segment then a path. The same
#: expression `EngineProfile::rootOf()` uses, so the two cannot disagree about
#: what counts as a surface.
_SURFACE = re.compile(r"/v[0-9]+[a-z]*/[A-Za-z0-9._/-]*$")


def root_of(url: str) -> str:
    """The service's root, from whichever of its urls a caller holds.

    A root is returned unchanged, which is what makes this safe to call on a
    value that may already be one.
    """
    trimmed = (url or "").strip().rstrip("/")
    if not trimmed:
        return ""
    root = _SURFACE.sub("", trimmed)
    if not root:
        return trimmed
    return root.rstrip("/")


def chat_url(url: str) -> str:
    """The completions endpoint one turn is posted to."""
    return root_of(url) + CHAT_PATH


def health_url(url: str) -> str:
    """The readiness probe, which is never under the completions surface."""
    return root_of(url) + HEALTH_PATH
