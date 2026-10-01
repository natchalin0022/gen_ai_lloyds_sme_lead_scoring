"""Brief node (notebook 04 §4–7) — the RM-facing write-up.

Code writes everything that must be exact: the title, the EVD-05 thin-evidence heading and gaps, the
decision line naming its clause (EVD-06), the conditions to meet before an offer (PROCEED WITH CONDITION
clauses, e.g. CON-04), a three-point "at a glance" (why this lead, where it stands, the next step), and
the table of every applicable clause (EVD-07). None of that needs the model, so every lead gets it. The model
writes only the summary: 4–7 statements, each citing fact-sheet keys; any statement citing nothing or
an unknown key is deleted (EVD-02) and kept in `brief_dropped`. With `draft_brief` False (screen only), the
model isn't called: the brief is the code-written parts alone, which carry the whole decision. A charge's collateral reaches the fact
sheet only if signal's quote check passed (EVD-04).
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from . import llm
from .policy import CLAUSE, CONDITION, level
from .refs import charge_refs, filing_ref
from .supervisor import live_group_charges

THIN = "Thin evidence — RM verification required"          # EVD-05, verbatim


def fact_sheet(state: dict) -> dict[str, dict]:
    """Everything the model may say, keyed by the same references every node uses."""
    s, facts = state["signals"], {}
    if p := state.get("profile"):
        facts["profile"] = {k: p[k] for k in ("company_name", "company_number", "company_status", "type",
                                              "date_of_creation", "sic_codes", "accounts_type",
                                              "last_accounts_made_up_to", "next_accounts_due", "accounts_overdue")}
    if s.get("officers"):
        facts["officers"] = s["officers"]
    if ch := state.get("charges"):
        for ref, c in zip(charge_refs(ch["items"]), ch["items"]):
            fact = {k: c[k] for k in ("created_on", "status", "persons_entitled", "lender_group",
                                      "contains_fixed_charge", "contains_floating_charge", "contains_negative_pledge")}
            col = s["collateral"].get(ref)
            if col and col["verified"] and col["collateral"] != "not_stated":          # EVD-04
                fact["collateral"] = {"type": col["collateral"], "from_particulars": col["quote"]}
            facts[ref] = fact
    if fl := state.get("filings"):
        for f in fl["items"]:
            if f["category"] in ("accounts", "liquidation", "insolvency"):
                facts[filing_ref(f)] = {k: f[k] for k in ("date", "category", "description", "days_late")}
    if s.get("filings"):
        facts["computed"] = {"as_of": s["as_of"],
                             "months_since_accounts_made_up": s["filings"]["months_since_made_up"],
                             "months_since_incorporation": s["filings"]["months_since_incorporation"]}
    for a in state["applicable"]:
        facts[a["clause_id"]] = {"outcome": a["outcome"], "evidence": a["evidence"],
                                 "clause": CLAUSE[a["clause_id"]]["text"]}
    return facts


class Statement(BaseModel):
    text: str = Field(description="One or two plain-English sentences for the RM.")
    sources: list[str] = Field(description="Keys of the fact sheet this statement relies on, copied exactly.")


class Draft(BaseModel):
    statements: list[Statement]


BRIEF_SYSTEM = (
    "You write the summary of a brief for a bank relationship manager (RM) about one UK company, using a fact "
    "sheet. The decision and a table of the applicable policy clauses are added to the brief separately. Your job "
    "is 4 to 7 short statements telling the RM what matters: who the company is, its existing secured borrowing, "
    "its filing conduct, and what the applicable clauses mean for an approach.\n"
    "Rules:\n"
    "- Use only the fact sheet. No knowledge about lenders, sectors or markets, however accurate.\n"
    "- Every statement lists in sources the fact-sheet keys it relies on, copied exactly. A statement whose "
    "sources are not keys of the fact sheet will be deleted.\n"
    "- Say what a charge is secured on only if that charge's fact has a collateral entry.\n"
    "- When a statement relies on a policy clause, include the clause ID in its sources.\n"
    "- A clause whose outcome is PROCEED WITH CONDITION is not a concern: present it as something to obtain "
    "before an offer, not as a reason for caution."
)


def validate(draft: Draft | None, facts: dict) -> tuple[list[dict], list[dict]]:
    """EVD-02 in code: a statement survives only if every source is a key of the fact sheet."""
    kept, dropped = [], []
    for st in (draft.statements if draft else []):
        unknown = [x for x in st.sources if x not in facts]
        item = {"text": st.text, "sources": st.sources}
        if not st.sources or unknown:
            dropped.append({**item, "why": f"unknown source(s) {unknown}" if unknown else "no source"})
        else:
            kept.append(item)
    return kept, dropped


def _cell(x) -> str:
    return str(x).replace("|", "\\|")




# --------------------------------------------------- at a glance (code) ----
def _short(cid: str) -> str:
    return CLAUSE[cid]["title"].split("—", 1)[-1].strip() if cid in CLAUSE else cid


def _why(lead: dict | None) -> str | None:
    if not lead:
        return None
    s = f"Ranked #{lead['rank']} of {lead['of']} by the lead-scoring model (score {float(lead['score']):.2f})"
    return s + (f": {lead['why']}." if lead.get("why") else ".")


def _where(state: dict) -> str:
    p, s = state.get("profile") or {}, state.get("signals") or {}
    f, ch = s.get("filings"), s.get("charges")
    age = f and f.get("months_since_incorporation")
    parts = [f"{(p.get('company_status') or 'status unknown').capitalize()}, incorporated "
             f"{p.get('date_of_creation') or 'date unknown'}" + (f" ({int(age // 12)} years)" if age else "")]
    if f and f["latest_accounts"]:
        la = f["latest_accounts"]
        kind = re.search(r"accounts type ([a-z \-]+?)(?: \(|$)", la["description"])
        d = la["days_late"]
        when = "" if d is None else " on time" if d <= 0 else f" {d} day{'s' if d > 1 else ''} late"
        late = len(f["late_3y"])
        parts.append(f"latest accounts{f' ({kind.group(1)})' if kind else ''} to {f['last_made_up_to']} filed{when}"
                     + (f", {late} late filing{'s' if late > 1 else ''} in the last 3 years" if late else ""))
    elif f:
        parts.append("no accounts filed yet")
    if ch is None:
        parts.append("charge register not retrieved")
    elif not ch["total"]:
        parts.append("no secured borrowing on record")
    else:
        items = dict(zip(charge_refs(state["charges"]["items"]), state["charges"]["items"]))
        lenders = list(dict.fromkeys(items[r]["persons_entitled"][0] for r in ch["live"]
                                     if items[r]["persons_entitled"]))
        named = ", ".join(lenders[:2]) + (f" and {len(lenders) - 2} more" if len(lenders) > 2 else "")
        parts.append(f"{len(ch['live'])} live charge{'s' if len(ch['live']) != 1 else ''}"
                     + (f" ({named})" if named else "")
                     + (f", {len(ch['satisfied'])} repaid" if ch["satisfied"] else "")
                     if ch["live"] else f"all {ch['total']} charges repaid")
        if ch.get("own_group") and not live_group_charges(state):
            parts.append("a former Lloyds borrower: every Lloyds charge is repaid")
    return "; ".join(parts) + "."


def _next(state: dict) -> str:
    o = state.get("outcome") or {}
    own = live_group_charges(state)
    if own:
        return f"Existing Lloyds customer (live charge {', '.join(own)}): pass to the relationship team, not a new lead."
    d = o.get("decision")
    before = "" if d == "DECLINE" or not o.get("conditions") else " Meet the condition above before any offer."
    refer = [_short(a["clause_id"]) for a in state.get("applicable", []) if level(a["outcome"]) == "REFER"]
    unchecked = sorted({_short(g.split(" ")[0]) for g in state.get("evidence_gap", []) if g[:3] in ("SEC", "CON")})
    check = f" The record couldn't show: {', '.join(unchecked).lower()} — check these first." if unchecked else ""
    if d == "DECLINE":
        return f"Do not approach: {', '.join(_short(c) for c in o['clauses']).lower()}."
    if d == "INSUFFICIENT EVIDENCE":
        return "Not enough public evidence to decide: research before any approach." + check
    if d == "REFER":
        return f"Approach, with a credit referral for: {', '.join(refer).lower()}." + check + before
    return "Approach: nothing in the public record counts against it." + check + before


def at_a_glance(state: dict, lead: dict | None = None) -> list[str]:
    """Three points for the RM, written by code from the record: why this lead (when a scored list sent
    it), where the company stands, and what to do next."""
    lead = lead or state.get("lead")
    points = [("Why this lead", _why(lead)), ("Where it stands", _where(state)), ("Next step", _next(state))]
    return [f"**{name}** — {text}" for name, text in points if text]


def render(state: dict, kept: list[dict], drafted: bool = True) -> str:
    """The brief. `drafted` False (screened only): no Summary section — "At a glance" covers it."""
    p, o, gaps = state.get("profile") or {}, state["outcome"], state["evidence_gap"]
    open_gaps = bool(gaps) and state["qualifies"] is None
    lines = [f"# RM brief — {p.get('company_name', '?')} ({state['company_number']})",
             f"*as of {state['signals']['as_of']} · research passes: {state.get('research_attempts', 0) + 1}*", ""]
    if open_gaps:                                                                    # EVD-05
        lines += [f"## ⚠ {THIN}", "", *[f"- {_cell(g)}" for g in gaps], ""]
    verdict = {True: "yes", False: "no", None: "not until the gaps are closed"}[state["qualifies"]]
    titles = [CLAUSE[c]["title"] if c in CLAUSE else c for c in o["clauses"]]
    lines += [f"**Decision: {o['decision']}** — set by {'; '.join(titles) or 'no clause'}  "]   # EVD-06
    if o.get("conditions") and o["decision"] != "DECLINE":                   # nothing to offer on a DECLINE
        lines += [f"**Condition:** {'; '.join(f'{CONDITION[c]} ({c})' for c in o['conditions'])}  "]
    lines += [f"**Qualifies as a lead:** {verdict}", ""]
    lines += ["## At a glance", ""] + [f"{i}. {pt}" for i, pt in enumerate(at_a_glance(state), 1)] + [""]
    if drafted:
        lines += [f"## Summary (written by {llm.BRIEF_MODEL})", ""]
        lines += [f"- {st['text']} `[{'; '.join(st['sources'])}]`" for st in kept] + [""]
    lines += ["## Policy clauses that apply", "", "| clause | outcome | evidence |", "|---|---|---|"]    # EVD-07
    lines += [f"| {_cell(a['title'])} | {_cell(a['outcome'])} | {_cell('; '.join(a['evidence']))} |"
              for a in state["applicable"]]
    if gaps and not open_gaps:
        lines += ["", "## Evidence gaps (they cannot change this decision)", "", *[f"- {_cell(g)}" for g in gaps]]
    return "\n".join(lines)


async def brief(state: dict) -> dict:
    if not state.get("draft_brief", True):                                    # screen only — no model call
        return {"brief": render(state, [], drafted=False),
                "citations": sorted(a["clause_id"] for a in state["applicable"]), "brief_dropped": []}
    facts = fact_sheet(state)
    resp = await llm.parse("brief", len(facts), "facts",
        model=llm.BRIEF_MODEL,
        max_tokens=16000,
        system=BRIEF_SYSTEM,
        messages=[{"role": "user", "content": json.dumps({"decision": state["outcome"], "facts": facts}, indent=1)}],
        output_format=Draft,
        **llm.FALLBACK,
    )
    ok = resp.stop_reason != "refusal" and resp.parsed_output is not None
    kept, dropped = validate(resp.parsed_output if ok else None, facts)
    cites = sorted({x for st in kept for x in st["sources"]} | {a["clause_id"] for a in state["applicable"]})
    # the kept statements are saved too, so the brief can be re-rendered when the code-written parts change
    return {"brief": render(state, kept), "citations": cites, "brief_dropped": dropped, "brief_kept": kept}


def rerender(state: dict, lead: dict | None = None) -> str:
    """A saved state's brief, rebuilt with the current code — no model call. The summary statements are
    reused when the state kept them; a state that has none renders screened-only."""
    st = {**state, **({"lead": lead} if lead else {})}
    kept, drafted = st.get("brief_kept"), bool(st.get("_run", {}).get("draft_brief", True))
    if drafted and kept is None:            # summarised before statements were saved: keep the saved text
        return state.get("brief", "")
    return render(st, kept or [], drafted=drafted and bool(kept))
