"""copilot/ask.py — "Ask about this lead" (RAG). Retrieval is real (local embeddings); the model is stubbed.

What code guarantees, whatever the model says:
  - the query is split into short facets built from the company's own facts (the 06_rag finding);
  - the clauses that fired for the company are always in the context, retrieved or not;
  - an answer citing a source it wasn't given, or none, is never shown.
"""
import asyncio
from types import SimpleNamespace

from builders import accounts, charge, charges, days_before, filings, make_state, policy_of, signals_of
from copilot import ask as ask_mod
from copilot.ask import Answer, ask, check, facets, grounding, retrieve

MICRO = "accounts with accounts type micro entity"


def company():
    """Live third-party charge with a negative pledge, micro-entity accounts: REFER (SEC-01, SEC-07)."""
    st = make_state(charges=charges(charge(contains_negative_pledge=True)),
                    filings=filings(accounts(days_before(100), 0, MICRO)))
    st = {**st, "signals": signals_of(st)}
    return {**st, **policy_of(st)}


def test_facets_are_short_queries_from_the_companys_own_facts(no_model):
    q = facets("Why is this a REFER?", company())
    assert q["question"] == "Why is this a REFER?"
    assert "negative pledge" in q["charges"] and "micro-entity" in q["accounts"]


def test_the_charges_facet_retrieves_the_negative_pledge_clause(no_model):
    hits = retrieve("Why is this a REFER?", company())
    assert ("SEC-07", "charges") in {(h["clause_id"], h["facet"]) for h in hits}


def test_clauses_that_fired_are_in_the_context_even_if_retrieval_misses_them(no_model):
    st = company()
    clauses, facts = grounding("anything", st, hits=[])                   # retrieval found nothing
    assert {a["clause_id"] for a in st["applicable"]} <= {c["id"] for c in clauses}
    assert "profile" in facts


def reply(**kw):
    return SimpleNamespace(stop_reason="end_turn", parsed_output=Answer(**kw))


def test_an_answer_is_shown_only_if_every_source_was_given():
    clauses, facts = [{"id": "SEC-07"}], {"profile": {}}
    assert check(reply(answer="…", sources=["SEC-07", "profile"], answerable=True), clauses, facts)["ok"]
    bad = check(reply(answer="…", sources=["SEC-07", "SEC-99"], answerable=True), clauses, facts)
    assert not bad["ok"] and "SEC-99" in bad["why_not"]
    assert not check(reply(answer="…", sources=[], answerable=True), clauses, facts)["ok"]
    assert not check(reply(answer="…", sources=["SEC-07"], answerable=False), clauses, facts)["ok"]


def test_ask_end_to_end_with_a_stand_in_model(no_model, monkeypatch):
    seen = {}

    async def model(label, n, what, **kw):
        seen.update(kw)
        return reply(answer="A negative pledge means the existing lender must consent.", sources=["SEC-07"],
                     answerable=True)
    monkeypatch.setattr(ask_mod.llm, "parse", model)
    out = asyncio.run(ask("What does the negative pledge mean for us?", company()))
    assert out["ok"] and out["sources"] == ["SEC-07"]
    assert seen["model"] == ask_mod.llm.BRIEF_MODEL                        # Sonnet writes, as for summaries
    assert any(r["clause_id"] == "SEC-07" for r in out["retrieved"])
