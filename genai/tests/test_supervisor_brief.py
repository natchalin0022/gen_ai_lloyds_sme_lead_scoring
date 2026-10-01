"""The supervisor's routes and the brief's code-side checks — no model calls.

Supervisor: one test per row of the routing table in copilot/supervisor.py.
Brief: the parts code guarantees — dropping unsourced statements (EVD-02), keeping unverified
collateral off the fact sheet (EVD-04), and the thin-evidence heading (EVD-05).
"""
import asyncio

from builders import accounts, charge, charges, days_before, filings, make_state, policy_of, signals_of
from copilot.brief import Draft, Statement, THIN, brief, fact_sheet, render, validate
from copilot.signals import signal
from copilot.supervisor import supervisor


def assessed(no_model_state: dict, **extra) -> dict:
    """State as the supervisor sees it: records + signals + policy result (+ overrides)."""
    s = {**no_model_state, "signals": signals_of(no_model_state)}
    return {**s, **policy_of(no_model_state), **extra}


LLOYDS = dict(persons_entitled=["Lloyds Bank PLC"], lender_group="own")


# ---------------------------------------------------------------- supervisor ----
def test_live_lloyds_charge_ends_the_run_without_a_brief(no_model):
    r = supervisor(assessed(make_state(charges=charges(charge(**LLOYDS)))))
    assert r["route"] == "end" and "existing Lloyds customer" in r["supervisor_log"][0]


def test_lapsed_lloyds_customer_is_still_a_prospect(no_model):
    r = supervisor(assessed(make_state(charges=charges(charge(status="fully-satisfied", **LLOYDS)))))
    assert r["route"] == "brief"


def test_no_gap_goes_to_brief(no_model):
    assert supervisor(assessed(make_state()))["route"] == "brief"


def test_decline_goes_to_brief_even_with_a_failed_fetch(no_model):
    st = assessed(make_state(filings=filings(accounts(days_before(100), 200)), charges=None),
                  research_errors=["get_charges: ConnectionError"])
    assert st["outcome"]["decision"] == "DECLINE"
    assert supervisor(st)["route"] == "brief"


def test_failed_fetch_is_retried_then_stops_at_two(no_model):
    st = assessed(make_state(charges=None), research_errors=["get_charges: ConnectionError"])
    first = supervisor(st)
    assert first["route"] == "research" and first["research_attempts"] == 1
    assert supervisor({**st, "research_attempts": 2})["route"] == "brief"


def test_gaps_without_a_failed_fetch_are_not_retried(no_model):
    st = assessed(make_state(filings=filings(accounts(days_before(100), 0), kept=500)))   # truncated window
    assert st["evidence_gap"] and not st.get("research_errors")
    assert supervisor(st)["route"] == "brief"


# --------------------------------------------------------------------- brief ----
def test_statements_citing_nothing_real_are_dropped():
    facts = {"profile": {}, "CON-02": {}}
    draft = Draft(statements=[Statement(text="ok", sources=["profile"]),
                              Statement(text="made up", sources=["profile", "SEC-99"]),
                              Statement(text="no source", sources=[])])
    kept, dropped = validate(draft, facts)
    assert [s["text"] for s in kept] == ["ok"]
    assert {d["text"] for d in dropped} == {"made up", "no source"}


def test_unverified_collateral_never_reaches_the_fact_sheet(no_model):
    c = charge(particulars="freehold land")
    st = assessed(make_state(charges=charges(c)))
    ref = c["charge_code"]
    st["signals"]["collateral"] = {ref: {"collateral": "property", "quote": "not in text", "verified": False}}
    assert "collateral" not in fact_sheet(st)[ref]
    st["signals"]["collateral"][ref]["verified"] = True
    assert fact_sheet(st)[ref]["collateral"]["type"] == "property"


def test_condition_is_shown_before_an_offer_but_not_on_a_decline(no_model):
    micro = "accounts with accounts type micro entity"
    proceed = assessed(make_state(filings=filings(accounts(days_before(100), 0, micro))))
    assert "**Condition:** management accounts" in render(proceed, [])
    decline = assessed(make_state(filings=filings(accounts(days_before(100), 200, micro))))    # CON-02
    assert decline["outcome"]["conditions"] == ["CON-04"] and "**Condition:**" not in render(decline, [])


def test_screen_only_makes_no_model_call_and_keeps_the_whole_decision(no_model, monkeypatch):
    def refuse():
        raise AssertionError("the model was called in screen-only mode")
    monkeypatch.setattr("copilot.llm.client", refuse)
    micro = "accounts with accounts type micro entity"
    st = make_state(filings=filings(accounts(days_before(100), 45, micro)),
                    charges=charges(charge(particulars="freehold land")), draft_brief=False)
    st = {**st, **asyncio.run(signal(st))}                      # collateral skipped, not classified
    assert st["signals"]["collateral"] == {}
    st = {**st, **policy_of(st)}
    out = asyncio.run(brief(st))["brief"]
    assert "## Summary" not in out and "**Decision: REFER**" in out and "**Condition:**" in out
    assert "## At a glance" in out and "**Next step** — Approach, with a credit referral" in out


def test_thin_evidence_heading_only_when_gaps_leave_the_decision_open(no_model):
    open_gap = assessed(make_state(filings=filings(accounts(days_before(100), 0), kept=500)))
    assert open_gap["qualifies"] is None and THIN in render(open_gap, [])
    clean = assessed(make_state())
    assert clean["qualifies"] is True and THIN not in render(clean, [])
