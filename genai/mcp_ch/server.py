"""Companies House MCP server — four read-only tools over stdio.

Run:   python -m mcp_ch.server            (from genai/)
The server is deliberately thin: every tool is a one-line wrapper over tools.py. MCP's
job here is the *contract* — name, description, input schema, and a transport any
client can speak — not the logic.

Why stdio: the client (a LangGraph node, Claude Desktop, an evaluation script) starts
this process and talks over its stdin/stdout. No port, no auth layer, nothing exposed
on the network — right for a server that holds an API key.
"""
from __future__ import annotations

try:                                            # mcp 2.x renamed FastMCP -> MCPServer;
    from mcp.server.mcpserver import MCPServer  # langchain-mcp-adapters pins mcp 1.x,
except ModuleNotFoundError:                     # so support both.
    from mcp.server.fastmcp import FastMCP as MCPServer

from . import tools

server = MCPServer(
    name="companies-house",
    instructions=(
        "Read-only access to the UK Companies House public register. Company numbers "
        "are 8 characters, zero-padded (e.g. '00059225'). Responses are trimmed to the "
        "fields relevant to credit assessment; 'lender_group' and 'days_late' are "
        "computed by the server and should be used as given, not re-derived."
    ),
)

# Each decorator turns the function's name, docstring and type hints into the tool's
# schema — the same idea as LangChain's @tool, but published over the protocol so
# any MCP client discovers it with list_tools().

@server.tool()
def get_company_profile(company_number: str) -> dict | None:
    """Core facts for one company: name, status, incorporation date, SIC codes,
    accounts type and whether the next accounts are overdue. Returns null if the
    company number does not exist."""
    return tools.get_company_profile(company_number)


@server.tool()
def get_filing_history(company_number: str, signal_only: bool = True, max_items: int = 60) -> dict | None:
    """Documents the company has filed, newest first, with descriptions decoded to plain
    text. By default only lending-relevant categories are returned (accounts, mortgage,
    officers, capital, liquidation, insolvency, gazette, dissolution). Each accounts
    filing carries 'days_late' against its statutory deadline (positive = late)."""
    return tools.get_filing_history(company_number, signal_only=signal_only, max_items=max_items)


@server.tool()
def get_charges(company_number: str) -> dict:
    """Registered charges (secured borrowing): date, status (outstanding / fully-satisfied
    / part-satisfied), lender names, and 'lender_group' = 'own' (Lloyds Banking Group
    entity) or 'third_party', resolved by the server. Includes fixed/floating/negative-
    pledge flags and the particulars text. 'total' is 0 if none are registered."""
    return tools.get_charges(company_number)


@server.tool()
def get_officers(company_number: str, active_only: bool = True) -> dict | None:
    """Directors and secretaries: name, role, appointment and resignation dates. No
    personal data (date of birth, nationality, address) is returned."""
    return tools.get_officers(company_number, active_only=active_only)


if __name__ == "__main__":
    server.run(transport="stdio")
