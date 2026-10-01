"""The model client and settings shared by every node that calls Claude."""
from __future__ import annotations

import contextvars

import anthropic
from langsmith import trace

MODEL = "claude-opus-5"           # reading steps that can feed a verdict: collateral text, pre-2013 charges
BRIEF_MODEL = "claude-sonnet-5"   # the brief's summary: writing from a checked fact sheet, never a verdict

# USD per million tokens (input, output), list prices from platform.claude.com/docs/en/about-claude/pricing,
# checked 2026-09-30. Both models use the same tokenizer, so their token counts are comparable.
PRICE_PER_MTOK = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0)}

# If a safety classifier declines a request, the API re-runs it on another model instead of
# returning an empty refusal.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

# Token accounting. Every call appends one row to USAGE, tagged with the company being assessed.
# A batch runner sets CURRENT per company; asyncio tasks copy the context, so concurrent companies
# never mix their rows. VERBOSE=False silences the per-call print for batch runs.
CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("company", default=None)
USAGE: list[dict] = []
VERBOSE = True

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    """Created on first use, so importing the package doesn't need ANTHROPIC_API_KEY."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def _price(model: str) -> tuple[float, float] | None:
    return next((v for k, v in PRICE_PER_MTOK.items() if model.startswith(k)), None)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = _price(model)
    return (input_tokens * price[0] + output_tokens * price[1]) / 1e6 if price else None


async def parse(label: str, n: int, what: str, **kwargs):
    """Every Claude call goes through here: made, recorded in USAGE, and traced in LangSmith as an LLM
    run carrying its tokens AND its cost, so LangSmith's totals are right even for a model missing
    from its own price list. Inside a batch, the run nests under that batch's single trace."""
    model = kwargs["model"]
    async with trace(f"claude · {label}", run_type="llm",
                     inputs={"system": kwargs.get("system"), "messages": kwargs.get("messages")},
                     metadata={"ls_provider": "anthropic", "ls_model_name": model, "company": CURRENT.get()},
                     tags=[label]) as run:
        resp = await client().beta.messages.parse(**kwargs)
        i, o, price = resp.usage.input_tokens, resp.usage.output_tokens, _price(resp.model)
        usage = {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}
        if price:                                  # a model not in PRICE_PER_MTOK is left for LangSmith to price
            usage |= {"input_cost": i * price[0] / 1e6, "output_cost": o * price[1] / 1e6,
                      "total_cost": (i * price[0] + o * price[1]) / 1e6}
        run.set(outputs={"parsed": resp.parsed_output.model_dump() if resp.parsed_output else None,
                         "stop_reason": resp.stop_reason, "model": resp.model},
                usage_metadata=usage)
    record(label, resp, n, what)
    return resp


def usage_line(label: str, resp, n: int, what: str) -> str:
    return (f"{label}: 1 call · {n} {what} · {resp.model} · "
            f"{resp.usage.input_tokens} in / {resp.usage.output_tokens} out tokens")


def record(label: str, resp, n: int, what: str) -> None:
    """Log one model call: into USAGE always, to stdout when VERBOSE."""
    USAGE.append({"company": CURRENT.get(), "node": label, "model": resp.model,
                  "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                  "cost_usd": cost_usd(resp.model, resp.usage.input_tokens, resp.usage.output_tokens)})
    if VERBOSE:
        print(usage_line(label, resp, n, what))
