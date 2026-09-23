"""Research node (notebook 01) — fetch the four Companies House records through MCP.

Two changes from the notebook:
  * the four calls run concurrently (asyncio.gather) — each opens its own server process;
  * a call that FAILS is recorded in `research_errors` and its slot left None, instead of
    crashing the graph. That is what lets the supervisor retry a network blip. A record that
    comes back None WITHOUT an error (a 404) is an answer — the resource doesn't exist — and
    retrying would return the same None.

The tools are passed in (`make_research(tools)`), so a caller can wrap one to simulate failure.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

GENAI = Path(__file__).resolve().parents[1]           # where mcp_ch/ lives

SLOTS = (("profile", "get_company_profile"), ("filings", "get_filing_history"),
         ("charges", "get_charges"), ("officers", "get_officers"))


async def load_tools(genai_dir: Path = GENAI) -> dict:
    """Start-up recipe for the stdio MCP server, then its tools keyed by name."""
    client = MultiServerMCPClient({
        "companies_house": {
            "transport": "stdio",
            "command":   sys.executable,
            "args":      ["-m", "mcp_ch.server"],
            "cwd":       str(genai_dir),
        }
    })
    return {t.name: t for t in await client.get_tools()}


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
