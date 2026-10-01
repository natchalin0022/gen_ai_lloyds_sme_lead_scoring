"""Test 3 — properties: rules that must hold for EVERY company, checked on generated ones.

Hypothesis generates hundreds of random charge lists and filing histories per test, and when a
property fails it shrinks the example to the smallest one that still breaks it. No labels needed:
the property itself is the reference.
"""
import asyncio
from datetime import date
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from builders import AS_OF, accounts, charges, filings, make_state, signals_of
from copilot.policy import CLAUSES, RANK, level, policy
from copilot.signals import charge_signals
from lender_groups import lender_group

LENDERS = ["Barclays Bank UK PLC", "HSBC UK Bank PLC", "Lloyds Bank PLC", "Interbay Funding Limited"]
STATUSES = ["outstanding", "fully-satisfied", "part-satisfied"]
iso = lambda lo, hi: st.dates(min_value=lo, max_value=hi).map(date.isoformat)


@st.composite
def a_charge(draw) -> dict:
    post_2013 = draw(st.booleans())
    lenders = draw(st.lists(st.sampled_from(LENDERS), min_size=1, max_size=2, unique=True))
    flag = st.booleans() if post_2013 else st.sampled_from([None, None, True])
    return {
        "charge_code": "post" if post_2013 else None,          # made unique in some_charges()
        # pre-2013 dates are drawn mostly from two fixed days: charges with no charge_code are told
        # apart by date + lender, and random dates over 23 years almost never collide — an earlier
        # version of this strategy passed while charge_refs was order-dependent on 1,152 real companies.
        "created_on": draw(iso(date(2013, 4, 6), date(2026, 9, 23)) if post_2013
                           else st.sampled_from(["2009-11-04", "2005-05-13"]) | iso(date(1990, 1, 1), date(2013, 4, 5))),
        "delivered_on": None, "satisfied_on": None,
        "status": draw(st.sampled_from(STATUSES)),
        "persons_entitled": lenders, "lender_group": lender_group(lenders),
        "contains_fixed_charge": draw(flag), "contains_floating_charge": draw(flag),
        "contains_negative_pledge": draw(flag), "particulars": None,
    }


@st.composite
def some_charges(draw, max_size=8) -> list[dict]:
    items = draw(st.lists(a_charge(), max_size=max_size))
    for i, c in enumerate(items):
        if c["charge_code"]:
            c["charge_code"] = f"{i:012d}"
    return items


@st.composite
def some_filings(draw) -> list[dict]:
    n = draw(st.integers(0, 6))
    dates = draw(st.lists(iso(date(2018, 1, 1), date(2026, 9, 1)), min_size=n, max_size=n, unique=True))
    return [accounts(d, draw(st.integers(-200, 400))) for d in dates]


async def _cannot_tell(questions):
    return {q["id"]: {"verdict": "not_determinable", "quotes": [], "reason": "stub"} for q in questions}


def run_policy(state: dict) -> dict:
    with patch("copilot.policy.judge", _cannot_tell):
        return asyncio.run(policy({**state, "signals": signals_of(state)}))


def applies(result: dict) -> set[str]:
    return {a["clause_id"] for a in result["applicable"]}


def as_sets(sig: dict) -> dict:
    """Signals with list order removed, so two orderings can be compared."""
    out = {}
    for k, v in sig.items():
        if k == "same_lender_within_30d":
            out[k] = {(g["lender"], frozenset(g["charges"])) for g in v}
        elif isinstance(v, list):
            out[k] = frozenset(v)
        else:
            out[k] = v
    return out


settings.register_profile("rmcopilot", deadline=None, max_examples=150)
settings.load_profile("rmcopilot")


# ---------------------------------------------------------------- signals ----
@given(some_charges(), st.randoms())
def test_signals_do_not_depend_on_the_order_charges_arrive_in(items, rnd):
    shuffled = list(items)
    rnd.shuffle(shuffled)
    as_of = date.fromisoformat(AS_OF)
    assert as_sets(charge_signals(charges(*items), as_of)) == as_sets(charge_signals(charges(*shuffled), as_of))


@given(some_charges())
def test_signal_lists_are_consistent_with_each_other(items):
    s = charge_signals(charges(*items), date.fromisoformat(AS_OF))
    live, satisfied = set(s["live"]), set(s["satisfied"])
    assert s["clean_position"] == all(c["status"] == "fully-satisfied" for c in items)
    assert not live & satisfied
    assert set(s["outstanding_third_party"]) <= live
    assert set(s["floating_live"]) <= live
    assert set(s["flags_not_recorded_live"]) <= live


# ----------------------------------------------------------------- policy ----
@given(some_charges(), some_filings(), st.booleans())
def test_policy_result_is_internally_consistent(items, filing_list, charges_missing):
    r = run_policy(make_state(charges=None if charges_missing else charges(*items),
                              filings=filings(*filing_list)))
    decision = r["outcome"]["decision"]
    assert set(r["policy_trace"]) == {c["clause_id"] for c in CLAUSES}          # every clause checked
    assert (r["qualifies"] is False) == (decision == "DECLINE")
    assert (r["qualifies"] is None) == (bool(r["evidence_gap"]) and decision != "DECLINE")
    levels = [level(a["outcome"]) for a in r["applicable"] if level(a["outcome"])]
    assert decision == max(levels, key=RANK.get, default="PROCEED")
    assert set(r["outcome"]["clauses"]) <= applies(r)
    assert all(a["evidence"] for a in r["applicable"])                         # nothing applies without evidence


@given(some_filings(), st.integers(1, 400))
def test_filing_later_never_removes_a_lateness_finding(filing_list, extra_days):
    later = [{**f, "days_late": f["days_late"] + extra_days} for f in filing_list]
    before = applies(run_policy(make_state(filings=filings(*filing_list))))
    after = applies(run_policy(make_state(filings=filings(*later))))
    lateness = {"CON-01", "CON-02", "CON-03"}
    assert before & lateness <= after & lateness


@given(some_charges(max_size=6), a_charge())
def test_adding_a_fully_satisfied_charge_never_changes_sec01_sec04_sec08(items, extra):
    extra = {**extra, "status": "fully-satisfied", "charge_code": "999999999999"}
    before = run_policy(make_state(charges=charges(*items)))
    after = run_policy(make_state(charges=charges(*items, extra)))
    for cid in ("SEC-01", "SEC-04", "SEC-08"):
        assert before["policy_trace"][cid] == after["policy_trace"][cid], cid
