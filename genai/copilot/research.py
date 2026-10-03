"""Research node (notebook 01) — fetch the four Companies House records through MCP.

Two changes from the notebook:
  * the four calls run concurrently (asyncio.gather);
  * a call that FAILS is recorded in `research_errors` and its slot left None, instead of
    crashing the graph. That is what lets the supervisor retry a network blip. A record that
    comes back None WITHOUT an error (a 404) is an answer — the resource doesn't exist — and
    retrying would return the same None.

The tools are passed in (`make_research(tools)`), so a caller can wrap one to simulate failure.

Two ways to get them:
  load_tools()   the library's default: EVERY tool call starts its own `python -m mcp_ch.server` process,
                 shakes hands, makes one call and exits — ~0.9 s each, even when the answer comes from the
                 disk cache. Fine for one company in a notebook (notebook 04).
  open_tools()   one server process for a whole batch: every call goes over the same session. The batch
                 trace (2026-09-30) showed process start-up was nearly all of research's time. One process
                 also means ONE Companies House pacer (ch_client's _last_call), so companies researched at
                 the same time can't exceed the API's rate between them, as separate processes could.
"""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

GENAI = Path(__file__).resolve().parents[1]           # where mcp_ch/ lives

SLOTS = (("profile", "get_company_profile"), ("filings", "get_filing_history"),
         ("charges", "get_charges"), ("officers", "get_officers"))


def _client(genai_dir: Path) -> MultiServerMCPClient:
    """Start-up recipe for the stdio MCP server (nothing starts until a session is opened)."""
    return MultiServerMCPClient({
        "companies_house": {
            "transport": "stdio",
            "command":   sys.executable,
            "args":      ["-m", "mcp_ch.server"],
            "cwd":       str(genai_dir),
        }
    })


async def load_tools(genai_dir: Path = GENAI) -> dict:
    """The tools keyed by name; each CALL starts a fresh server process (see the module docstring)."""
    return {t.name: t for t in await _client(genai_dir).get_tools()}


@asynccontextmanager
async def open_tools(genai_dir: Path = GENAI) -> AsyncIterator[dict]:
    """The tools keyed by name, all sharing ONE server process for as long as the block is open:

        async with open_tools() as tools:
            graph = build(tools)
            ...                               # every research call in here uses the same server

    The server runs the tools one at a time (they are plain functions), which costs nothing on cache hits.
    If the process dies mid-batch, every later call fails and is recorded in research_errors (the supervisor
    retries, then briefs with the gap) — with load_tools only the one call would have failed."""
    async with _client(genai_dir).session("companies_house") as session:
        tools = await load_mcp_tools(session, server_name="companies_house")
        yield {t.name: t for t in tools}


def _unwrap(result) -> dict | None:
    """MCP returns [{'type': 'text', 'text': '{...json...}'}]; give back the dict."""
    if isinstance(result, list) and result and result[0].get("type") == "text":
        return json.loads(result[0]["text"])
    return result


def make_research(tools: dict):
    async def research(state: dict) -> dict:
        n = state["company_number"]
        results = await asyncio.gather(
            *(tools[tool].ainvoke({"company_number": n}) for _, tool in SLOTS),
            return_exceptions=True,
        )
        out, errors = {}, []
        for (slot, tool), r in zip(SLOTS, results):
            if isinstance(r, Exception):
                out[slot] = None
                errors.append(f"{tool}: {type(r).__name__}: {r}")
            else:
                out[slot] = _unwrap(r)
        return {**out, "research_errors": errors}
    return research
