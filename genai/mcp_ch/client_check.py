"""Prove the server works over the protocol: start it as a subprocess, list its tools,
call two of them. This is what a LangGraph `fetch` node does, minus the graph.

Run:   python -m mcp_ch.client_check            (from genai/)
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(company_number: str = "10812571"):
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_ch.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"server exposes {len(tools.tools)} tools:")
            for t in tools.tools:
                d = t.model_dump(by_alias=True)                 # version-proof: 1.x camelCase, 2.x snake_case
                schema = d.get("inputSchema") or d.get("input_schema")
                print(f"  {t.name:22s} args={list(schema['properties'])} required={schema.get('required', [])}")

            for name in ("get_company_profile", "get_charges"):
                res = await session.call_tool(name, {"company_number": company_number})
                d = res.model_dump(by_alias=True)
                payload = d.get("structuredContent") or d.get("structured_content") or json.loads(res.content[0].text)
                print(f"\n{name}({company_number}) -> {json.dumps(payload, indent=1)[:500]}")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
