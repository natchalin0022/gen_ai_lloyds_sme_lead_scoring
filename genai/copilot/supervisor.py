"""Supervisor node (notebook 04 §3) — decide what happens after policy.

Routes, checked in order:

| situation                                      | route    | why                                               |
|------------------------------------------------|----------|---------------------------------------------------|
| a Lloyds-group charge is still live            | end      | an existing customer is not a prospect: no brief  |
| no evidence gap                                | brief    | nothing to fetch                                  |
| decision is DECLINE                            | brief    | EVD-07: nothing missing could make it less strict |
| a fetch failed, fewer than 2 retries used      | research | a timeout or server error may not repeat          |
| a fetch failed, 2 retries used                 | brief    | EVD-05's limit                                    |
| gaps, but no fetch failed                      | brief    | the same tool returns the same record             |

The first row is new since notebook 04. It applies the lead-scoring pipeline's own rule
(3_Flat_table.ipynb): a firm with any Lloyds charge still outstanding is an existing customer and is
never a prospect; a firm whose Lloyds charges are all satisfied is a lapsed customer and may be one.
The scoring run trusts the client file's `relationship` column for this; the agent checks the charge
register itself.
"""
from __future__ import annotations

MAX_RETRIES = 2                                                     # EVD-05


def live_group_charges(state: dict) -> list[str]:
    """Refs of Lloyds-group charges that are outstanding or part-satisfied."""
    ch = (state.get("signals") or {}).get("charges") or {}
    return sorted(set(ch.get("own_group", [])) & set(ch.get("live", [])))


def supervisor(state: dict) -> dict:
    gaps, errors = state["evidence_gap"], state.get("research_errors", [])
    tries = state.get("research_attempts", 0)
    live_own = live_group_charges(state)

    if live_own:
        route, why = "end", f"existing Lloyds customer (live group charge: {', '.join(live_own)}) — not a prospect"
    elif not gaps:
        route, why = "brief", "no evidence gap"
    elif state["qualifies"] is False:
        route, why = "brief", f"{state['outcome']['decision']} — nothing missing could make it less restrictive"
    elif errors and tries < MAX_RETRIES:
        route, why = "research", f"fetch failed ({'; '.join(errors)}) — retry {tries + 1} of {MAX_RETRIES}"
    elif errors:
        route, why = "brief", f"fetch still failing after {MAX_RETRIES} retries (EVD-05)"
    else:
        route, why = "brief", f"{len(gaps)} gap(s), none from a failed fetch — a retry would return the same record"

    return {"route": route,
            "research_attempts": tries + (route == "research"),
            "supervisor_log": [f"pass {tries + 1}: {state['outcome']['decision']:21s} → {route:8s} {why}"]}
