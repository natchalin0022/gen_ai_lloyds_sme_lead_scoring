"""Ask about a lead — retrieval-augmented Q&A over the lending policy and one company's record.

    await ask(question, state)  ->  {"answer", "sources", "ok", "why_not", "context", "retrieved"}

RAG explains; code decides. The answer explains the decision on record and never changes it: the
decision was made by the clause rules in policy.py, which check every clause (06_rag.ipynb showed a
retriever can rank the clause that fired 5th, which is fine for an explanation, not for a decision).

    1. RETRIEVE  policy_store.retrieve_facets: one short query for the question, plus one per cluster of
                 this company's facts (charges / accounts / evidence). The 06_rag finding: a single
                 blended query averages into a blurry vector and retrieves badly; short ones don't.
    2. GROUND    the context the model may use = the clauses that FIRED for this company (by id, always,
                 so they're never missed) + the clauses RETRIEVED + this company's fact sheet
                 (brief.fact_sheet, the same facts the brief uses).
    3. GENERATE  one Sonnet call, structured output: an answer plus the ids it relied on.
    4. CHECK     code verifies every cited id is in the context, as EVD-02 does for the brief. An answer
                 citing something it wasn't given, or nothing at all, is not shown.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # genai/, for policy_store
from policy_store import retrieve_facets  # noqa: E402

K_PER_FACET = 3
MAX_DISTANCE = 0.6          # weaker hits are dropped rather than handed to the model (policy_store's guidance)


# ------------------------------------------------------------ 1. retrieve ----
def facets(question: str, state: dict) -> dict[str, str]:
    """Short queries: the question itself, and one per cluster of this company's facts."""
    s = state.get("signals") or {}
    ch, f = s.get("charges") or {}, s.get("filings") or {}
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
                     ((f.get("months_since_made_up") or 0) > 18, "accounts made up more than 18 months ago")])
    if accounts:
        q["accounts"] = accounts
    if state.get("evidence_gap"):
        q["evidence"] = "evidence missing: the record cannot settle a clause"
    return q


def retrieve(question: str, state: dict) -> list[dict]:
    return retrieve_facets(facets(question, state), k=K_PER_FACET, max_distance=MAX_DISTANCE)


# -------------------------------------------------------------- 2. ground ----
def grounding(question: str, state: dict, hits: list[dict]) -> tuple[list[dict], dict]:
    """(the clauses the model may use, the company's facts). Fired clauses first, then retrieved ones."""
    fired = [a["clause_id"] for a in state.get("applicable", [])]
    why = {c: "applies to this company" for c in fired}
    for h in hits:
        why.setdefault(h["clause_id"], f"retrieved for the {h['facet']} (distance {h['distance']})")
    clauses = [{"id": c, "title": CLAUSE[c]["title"], "outcome": CLAUSE[c]["outcome"],
                "why_included": why[c], "text": CLAUSE[c]["text"]} for c in why if c in CLAUSE]
    return clauses, fact_sheet(state)


# ------------------------------------------------------------ 3. generate ----
class Answer(BaseModel):
    answer: str = Field(description="2 to 5 plain-English sentences for the relationship manager.")
    sources: list[str] = Field(description="Clause ids (e.g. SEC-07) and fact keys relied on, copied exactly.")
    answerable: bool = Field(description="False if the clauses and facts given do not answer the question.")


SYSTEM = (
    "You answer a bank relationship manager's question about one UK company, using only the lending-policy "
    "clauses and the company facts given. The decision has already been made by the bank's rules: explain it, "
    "never change it or suggest a different one. List in sources every clause id and fact key you relied on, "
    "copied exactly. If the clauses and facts do not answer the question, set answerable to false and say what "
    "is missing. Use no outside knowledge about lenders, companies, markets or law."
)


# --------------------------------------------------------------- the call ----
async def ask(question: str, state: dict) -> dict:
    async with trace("rm-copilot ask", run_type="chain",
                     inputs={"question": question, "company": state.get("company_number")},
                     tags=["ask", "rag"]) as run:
        async with trace("policy retrieval", run_type="retriever", inputs={"facets": facets(question, state)}) as r:
            hits = retrieve(question, state)
            r.set(outputs={"documents": [{"type": "Document", "page_content": h["text"],
                                          "metadata": {k: h[k] for k in ("clause_id", "distance", "facet")}}
                                         for h in hits]})
        clauses, facts = grounding(question, state, hits)
        try:
            resp = await llm.parse(
                "ask", len(clauses), "clause(s)",
                model=llm.BRIEF_MODEL,
                max_tokens=2000,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(
                    {"question": question, "decision": state.get("outcome"), "clauses": clauses, "facts": facts},
                    indent=1, default=str)}],
                output_format=Answer,
                **llm.FALLBACK,
            )
            out = check(resp, clauses, facts)
        except Exception as e:           # the retrieval still stands; only the written answer is missing
            out = {"answer": "", "sources": [], "ok": False, "why_not": f"the model call failed: {str(e)[:300]}"}
        out.update(context=[{k: c[k] for k in ("id", "title", "why_included")} for c in clauses],
                   retrieved=[{k: h[k] for k in ("clause_id", "distance", "facet")} for h in hits])
        run.set(outputs={k: out[k] for k in ("answer", "sources", "ok", "why_not")})
    return out


# --------------------------------------------------------------- 4. check ----
def check(resp, clauses: list[dict], facts: dict) -> dict:
    """Show the answer only if it relied on something it was given, and on nothing it wasn't."""
    a = resp.parsed_output if resp.stop_reason != "refusal" else None
    if a is None:
        return {"answer": "", "sources": [], "ok": False, "why_not": "no answer from the model"}
    allowed = {c["id"] for c in clauses} | set(facts)
    unknown = [s for s in a.sources if s not in allowed]
    why_not = ("the policy and this company's record don't answer that" if not a.answerable else
               f"it cited sources it wasn't given: {', '.join(unknown)}" if unknown else
               "it cited no sources" if not a.sources else "")
    return {"answer": a.answer, "sources": a.sources, "ok": not why_not, "why_not": why_not}
