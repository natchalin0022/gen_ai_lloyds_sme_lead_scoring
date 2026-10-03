"""Ask about a lead — retrieval-augmented Q&A over the lending policy and one company's record.

    await ask(question, state)  ->  {"answer", "sources", "ok", "why_not", "context", "retrieved"}

RAG explains; code decides. The answer explains the decision on record and never changes it: the
decision was made by the clause rules in policy.py, which check every clause (06_rag.ipynb showed a
retriever can rank the clause that fired 5th, which is fine for an explanation, not for a decision).

    1. RETRIEVE  policy_store.retrieve_facets: one short query for the question, plus one per cluster of
                 this company's facts (charges / accounts / evidence). The 06_rag finding: a single
                 blended query averages into a blurry vector and retrieves badly; short ones don't.
    2. GROUND    the context the model may use = the clauses that FIRED for this company (by id, always,
                 so they're never missed) + the clauses RETRIEVED + this company's RECORD: the brief's fact
                 sheet plus what an RM may ask that the brief doesn't need (registered office, directors,
                 every filing kept, the decision, how the scoring model ranked the lead).
    3. GENERATE  one Sonnet call, structured output: an answer plus the sources it relied on.
    4. CHECK     code verifies every cited source is in the context, as EVD-02 does for the brief: a clause
                 id, a record key, or a field that exists inside one (profile.date_of_creation). An answer
                 citing something it wasn't given, or nothing at all, is not shown. A question the record
                 can't answer gets the model's note of what is missing, marked as such.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langsmith import trace
from pydantic import BaseModel, Field

from . import llm
from .brief import fact_sheet
from .policy import CLAUSE
from .refs import filing_ref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # genai/, for policy_store
from policy_store import retrieve_facets  # noqa: E402

K_PER_FACET = 3
MAX_DISTANCE = 0.6          # weaker hits are dropped rather than handed to the model (policy_store's guidance)


# ------------------------------------------------------------ 1. retrieve ----
def facets(question: str, state: dict) -> dict[str, str]:
    """Short queries: the question itself, and one per cluster of this company's facts."""
    s = state.get("signals") or {}
    ch, f, co = s.get("charges") or {}, s.get("filings") or {}, s.get("company") or {}
    overdue = bool(f.get("accounts_on_record") and co.get("next_accounts_due")
                   and co["next_accounts_due"] < s.get("as_of", ""))                         # CON-06 (v1.2)
    said = lambda pairs: "; ".join(text for present, text in pairs if present)
    q = {"question": question}
    if ch.get("live"):
        q["charges"] = said([(True, "outstanding charge held by another lender"),
                             (ch.get("negative_pledge"), "the charge contains a negative pledge"),
                             (ch.get("floating_live"), "floating charge over the whole undertaking"),
                             (ch.get("same_lender_within_30d"), "several charges to one lender at once")])
    elif ch:
        q["charges"] = "all registered charges are fully satisfied" if ch.get("total") else "no charges ever registered"
    accounts = said([(f.get("late_3y"), "accounts filed late, a pattern of late filing"),
                     (f.get("latest_micro_or_exempt"), "micro-entity accounts do not disclose turnover or profit"),
                     (overdue, "next accounts overdue and not filed, financial information out of date")])
    if accounts:
        q["accounts"] = accounts
    if state.get("evidence_gap"):
        q["evidence"] = "evidence missing: the record cannot settle a clause"
    return q


def retrieve(question: str, state: dict) -> list[dict]:
    return retrieve_facets(facets(question, state), k=K_PER_FACET, max_distance=MAX_DISTANCE)


# -------------------------------------------------------------- 2. ground ----
def record(state: dict, lead: dict | None = None) -> dict:
    """This company's record, keyed so every part can be cited. The brief's fact sheet, widened to what an
    RM may reasonably ask about: the full profile, the directors by name, every filing the research kept,
    the decision, and the scoring model's view of the lead (`lead`, else the one saved with the state)."""
    facts = fact_sheet(state)
    if p := state.get("profile"):
        facts["profile"] = p                                     # adds registered office, insolvency history
    if o := state.get("officers"):
        facts["officers"] = {**facts.get("officers", {}),
                             "people": [{k: x.get(k) for k in ("name", "role", "appointed_on", "resigned_on")}
                                        for x in o.get("items", [])]}
    for f in (state.get("filings") or {}).get("items", []):     # the fact sheet keeps accounts/insolvency only
        facts.setdefault(filing_ref(f), {k: f.get(k) for k in ("date", "category", "description", "days_late")})
    facts["decision"] = state.get("outcome")
    if lead := lead or state.get("lead"):
        facts["lead"] = lead
    return facts


def grounding(question: str, state: dict, hits: list[dict], lead: dict | None = None) -> tuple[list[dict], dict]:
    """(the clauses the model may use, the company's record). Fired clauses first, then retrieved ones."""
    fired = [a["clause_id"] for a in state.get("applicable", [])]
    why = {c: "applies to this company" for c in fired}
    for h in hits:
        why.setdefault(h["clause_id"], f"retrieved for the {h['facet']} (distance {h['distance']})")
    clauses = [{"id": c, "title": CLAUSE[c]["title"], "outcome": CLAUSE[c]["outcome"],
                "why_included": why[c], "text": CLAUSE[c]["text"]} for c in why if c in CLAUSE]
    return clauses, record(state, lead)


# ------------------------------------------------------------ 3. generate ----
class Answer(BaseModel):
    answer: str = Field(description="2 to 5 plain-English sentences for the relationship manager.")
    sources: list[str] = Field(description="Every clause id (SEC-07), record key (profile) or field inside one "
                                           "(profile.date_of_creation) relied on, copied exactly.")
    answerable: bool = Field(description="False if the clauses and record given do not answer the question.")


SYSTEM = (
    "You answer a bank relationship manager's question about one UK company. You are given the lending-policy "
    "clauses that apply to it or were retrieved for the question, and the company's record: its Companies House "
    "profile, directors, filings and charges, the decision the bank's rules made, and how the lead-scoring model "
    "ranked it. The RM may ask about any of it (its age, directors, filing history, why it ranks where it does), "
    "not only about the policy. Use only what is given: no outside knowledge about lenders, companies, markets "
    "or law. The decision has already been made by the bank's rules: explain it, never change it or suggest a "
    "different one. List in sources every clause id, record key or field inside one (profile.date_of_creation) "
    "you relied on, copied exactly. If what is given does not answer the question, set answerable to false and "
    "say in one or two sentences what the record does not show."
)


# --------------------------------------------------------------- the call ----
async def ask(question: str, state: dict, lead: dict | None = None) -> dict:
    async with trace("rm-copilot ask", run_type="chain",
                     inputs={"question": question, "company": state.get("company_number")},
                     tags=["ask", "rag"]) as run:
        async with trace("policy retrieval", run_type="retriever", inputs={"facets": facets(question, state)}) as r:
            hits = retrieve(question, state)
            r.set(outputs={"documents": [{"type": "Document", "page_content": h["text"],
                                          "metadata": {k: h[k] for k in ("clause_id", "distance", "facet")}}
                                         for h in hits]})
        clauses, facts = grounding(question, state, hits, lead)
        try:
            resp = await llm.parse(
                "ask", len(clauses), "clause(s)",
                model=llm.BRIEF_MODEL,
                max_tokens=2000,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(
                    {"question": question, "clauses": clauses, "record": facts},
                    indent=1, default=str)}],
                output_format=Answer,
                **llm.FALLBACK,
            )
            out = check(resp, clauses, facts)
        except Exception as e:           # the retrieval still stands; only the written answer is missing
            out = {"answer": "", "sources": [], "ok": False, "status": "failed",
                   "why_not": f"the model call failed: {str(e)[:300]}"}
        out.update(context=[{k: c[k] for k in ("id", "title", "why_included")} for c in clauses],
                   record=[k for k in facts if k not in CLAUSE],
                   retrieved=[{k: h[k] for k in ("clause_id", "distance", "facet")} for h in hits])
        run.set(outputs={k: out[k] for k in ("answer", "sources", "ok", "status", "why_not")})
    return out


# --------------------------------------------------------------- 4. check ----
def given(source: str, clause_ids: set[str], facts: dict) -> bool:
    """A clause id or record key that was given, or a field that exists inside one: `profile.date_of_creation`
    passes, `profile.turnover` doesn't. Keys can hold dots themselves (a pre-2013 charge's ref ends in its
    lender's name, e.g. "P.L.C."), so the longest key the source starts with is the one walked."""
    if source in clause_ids or source in facts:
        return True
    parts = source.split(".")
    for i in range(len(parts) - 1, 0, -1):
        node = facts.get(".".join(parts[:i]))
        if node is None:
            continue
        for p in parts[i:]:
            if isinstance(node, dict) and p in node:
                node = node[p]
            elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
                node = node[int(p)]
            else:
                return False
        return True
    return False


def check(resp, clauses: list[dict], facts: dict) -> dict:
    """Show the answer only if it relied on something it was given, and on nothing it wasn't.

    status: "answered"; "not_in_record" (the model said what is given doesn't answer it — its note of what's
    missing is kept, to show as such); "blocked" (it cited something it wasn't given, or nothing)."""
    a = resp.parsed_output if resp.stop_reason != "refusal" else None
    if a is None:
        return {"answer": "", "sources": [], "ok": False, "status": "blocked", "why_not": "no answer from the model"}
    unknown = [s for s in a.sources if not given(s, {c["id"] for c in clauses}, facts)]
    why_not = (f"it cited sources it wasn't given: {', '.join(unknown)}" if unknown else
               "the policy and this company's record don't answer that" if not a.answerable else
               "it cited no sources" if not a.sources else "")
    status = ("answered" if not why_not else
              "not_in_record" if not a.answerable and not unknown else "blocked")
    return {"answer": a.answer, "sources": a.sources, "ok": not why_not, "status": status, "why_not": why_not}
