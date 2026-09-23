"""Checkbox 4: connect the MCP server to a LangGraph agent and prove it reaches the LIVE API.

Run:   python -m mcp_ch.agent_check [company_number]        (from genai/)

Proof of "live", not cached: we count cache files before and after. A company the
cache has never seen must add files — that can only happen if the server went to
Companies House over HTTP.
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
CACHE = Path(__file__).resolve().parent / "cache"

SYSTEM = (
    "You are a credit analyst. Use the Companies House tools to answer. Use only what "
    "the tools return; do not add knowledge about lenders or sectors. Report 'days_late' "
    "and 'lender_group' exactly as given — they are computed by the server."
)


async def main(company_number: str = "00445790"):          # Tesco plc — big, not in cache
    before = len(list(CACHE.glob("*.json")))

    client = MultiServerMCPClient({
        "companies_house": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "mcp_ch.server"],
            "cwd": str(Path(__file__).resolve().parents[1]),     # genai/, so `-m mcp_ch.server` resolves
        }
    })
    tools = await client.get_tools()                           # list_tools() -> LangChain tool objects
    print(f"adapter loaded {len(tools)} tools from the MCP server: {[t.name for t in tools]}\n")

    agent = create_agent(model="claude-opus-5", tools=tools, system_prompt=SYSTEM)
    result = await agent.ainvoke({"messages": [{"role": "user", "content":
        f"For company {company_number}: what is it, is it active, does it have outstanding "
        f"charges, and were its latest accounts filed late? Two or three sentences."}]})

    total = 0
    for m in result["messages"]:
        if m.type == "ai":
            total += (m.usage_metadata or {}).get("total_tokens", 0)
            for tc in m.tool_calls:
                print(f"AI    → CALL {tc['name']}({tc['args']})")
        elif m.type == "tool":
            print(f"TOOL  ← {m.name}: {' '.join(m.text.split())[:110]}…")
    print(f"\nANSWER: {result['messages'][-1].text}\n")

    after = len(list(CACHE.glob("*.json")))
    print(f"tokens: {total:,} | cache files {before} → {after} "
          f"({'LIVE API reached' if after > before else 'served from cache'})")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
