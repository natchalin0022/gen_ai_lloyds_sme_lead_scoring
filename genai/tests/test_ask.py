"""copilot/ask.py — "Ask about this lead" (RAG). Retrieval is real (local embeddings); the model is stubbed.

What code guarantees, whatever the model says:
  - the query is split into short facets built from the company's own facts (the 06_rag finding);
  - the clauses that fired for the company are always in the context, retrieved or not;
  - the RM can ask about the company itself (age, directors, rank), not only the policy;
  - an answer citing a source it wasn't given, or none, is never shown; a field inside a given record
    key counts as given only if it exists.
"""
import asyncio
from types import SimpleNamespace

from builders import accounts, charge, charges, days_before, filings, make_state, policy_of, profile, signals_of
from copilot import ask as ask_mod
from copilot.ask import Answer, ask, check, facets, grounding, record, retrieve

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


def test_the_record_covers_what_an_rm_asks_about_the_company_itself(no_model):
    st = company()
    st["filings"]["items"].append({"date": "2015-01-01", "category": "incorporation", "type": "NEWINC",
                                   "description": "incorporation company", "days_late": None})
    facts = record(st, lead={"rank": 3, "of": 40, "model_score": 0.7})
    assert facts["profile"]["date_of_creation"] == "2015-01-01" and "registered_office" in facts["profile"]
    assert facts["officers"]["people"][0]["name"] == "DOE, Jane"
    assert "NEWINC filed 2015-01-01" in facts                         # not only accounts filings
    assert facts["decision"]["decision"] == "REFER" and facts["lead"]["rank"] == 3
    assert record({**st, "lead": {"rank": 9}})["lead"]["rank"] == 9  # else the lead saved with the state


def reply(**kw):
    return SimpleNamespace(stop_reason="end_turn", parsed_output=Answer(**kw))


def test_an_answer_is_shown_only_if_every_source_was_given():
    clauses, facts = [{"id": "SEC-07"}], {"profile": {}}
    assert check(reply(answer="…", sources=["SEC-07", "profile"], answerable=True), clauses, facts)["ok"]
    bad = check(reply(answer="…", sources=["SEC-07", "SEC-99"], answerable=True), clauses, facts)
    assert not bad["ok"] and "SEC-99" in bad["why_not"]
    assert not check(reply(answer="…", sources=[], answerable=True), clauses, facts)["ok"]
    assert not check(reply(answer="…", sources=["SEC-07"], answerable=False), clauses, facts)["ok"]


def test_a_field_inside_a_record_key_is_a_source_only_if_it_exists():
    """The bug this fixes: "How old is it?" was answered correctly, citing profile.date_of_creation, and
    blocked because only the top-level key `profile` counted as given."""
    clauses = [{"id": "SEC-02"}]
    facts = {"profile": {"date_of_creation": "2023-03-17"}, "officers": {"people": [{"name": "DOE, Jane"}]},
             "2009-11-04 Lloyds Bank P.L.C.": {"status": "outstanding"}}      # a pre-2013 ref with dots in it
    ok = lambda *src: check(reply(answer="…", sources=list(src), answerable=True), clauses, facts)["ok"]
    assert ok("profile.date_of_creation") and ok("officers.people.0.name")
    assert ok("2009-11-04 Lloyds Bank P.L.C.") and ok("2009-11-04 Lloyds Bank P.L.C..status")
    assert not ok("profile.turnover") and not ok("officers.people.3.name") and not ok("SEC-02.text")


def test_a_question_the_record_cant_answer_says_so_rather_than_being_blocked():
    clauses, facts = [{"id": "CON-04"}], {"profile": {}}
    out = check(reply(answer="Total-exemption accounts don't disclose turnover.", sources=["CON-04"],
                      answerable=False), clauses, facts)
    assert out["status"] == "not_in_record" and not out["ok"] and "turnover" in out["answer"]
    made_up = check(reply(answer="…", sources=["accounts.turnover"], answerable=False), clauses, facts)
    assert made_up["status"] == "blocked"                         # citing what it wasn't given still blocks


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
    assert '"record"' in seen["messages"][0]["content"] and '"decision": "REFER"' in seen["messages"][0]["content"]
    assert {"profile", "officers", "decision"} <= set(out["record"]) and "SEC-07" not in out["record"]


def test_the_accounts_facet_follows_con06_v12(no_model):
    """An overdue company's accounts query asks about overdue accounts (v1.2), and retrieval finds CON-06."""
    st = make_state(profile=profile(next_accounts_due=days_before(10)))
    st = {**st, "signals": signals_of(st)}
    st = {**st, **policy_of(st)}
    assert "overdue" in facets("Why is this a REFER?", st)["accounts"]
    assert ("CON-06", "accounts") in {(h["clause_id"], h["facet"]) for h in retrieve("Why is this a REFER?", st)}
