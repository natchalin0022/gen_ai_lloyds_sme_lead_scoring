"""The model client and settings shared by every node that calls Claude."""
from __future__ import annotations

import anthropic

MODEL = "claude-opus-5"

# If a safety classifier declines a request, the API re-runs it on another model instead of
# returning an empty refusal.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    """Created on first use, so importing the package doesn't need ANTHROPIC_API_KEY."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def usage_line(label: str, resp, n: int, what: str) -> str:
    return (f"{label}: 1 call · {n} {what} · {resp.model} · "
            f"{resp.usage.input_tokens} in / {resp.usage.output_tokens} out tokens")
