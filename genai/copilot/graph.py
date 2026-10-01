"""The full RM Copilot graph (notebook 04 §2 and §8), importable.

    START → research → signal → policy → supervisor ─┬→ brief → END
                ↑                                     ├→ END        (existing Lloyds customer)
                └────────── retry (max 2) ────────────┘

`build(tools)` takes the MCP tool dict, so a caller can pass tools that fail on purpose (notebook 04
§11–12) or tools loaded once and shared across a batch.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END

from .brief import brief
from .policy import policy
from .research import make_research
from .signals import signal
from .supervisor import supervisor


class CopilotState(TypedDict, total=False):
    # invariant input
    company_number: str
    as_of: str
    draft_brief: bool          # default True; False = screen only: every clause decided, no model-written summary
    lead: dict                 # optional: the scoring model's view ({"rank", "of", "score", "why"}) for the brief
    # evidence — overwritten on every pass (each research pass is a complete re-fetch)
    profile: dict
    filings: dict
    charges: dict
    officers: dict
    research_errors: list[str]
    signals: dict
    applicable: list[dict]
    outcome: dict
    qualifies: bool | None
    evidence_gap: list[str]
    policy_trace: dict
    # control flow — supervisor
    research_attempts: int
    route: str
    supervisor_log: Annotated[list[str], operator.add]     # appends: the history of routing decisions
    # terminal output — brief
    brief: str
    citations: list[str]
    brief_dropped: list[dict]
    brief_kept: list[dict]         # the summary statements that passed EVD-02 (for re-rendering the brief)


def build(tools: dict):
    g = StateGraph(CopilotState)
    g.add_node("research", make_research(tools))
    g.add_node("signal", signal)
    g.add_node("policy", policy)
    g.add_node("supervisor", supervisor)
    g.add_node("brief", brief)
    g.add_edge(START, "research")
    g.add_edge("research", "signal")
    g.add_edge("signal", "policy")
    g.add_edge("policy", "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["route"],
                            {"research": "research", "brief": "brief", "end": END})
    g.add_edge("brief", END)
    return g.compile()
