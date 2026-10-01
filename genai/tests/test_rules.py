"""Test 2 — unit tests: each clause at its boundary, and the three data fixes in mcp_ch/tools.py.

These check the code does what we MEANT. They cannot tell whether what we meant is the right
reading of the policy — that needs a person (see the SEC-07 xfail at the bottom).
"""
import pytest

from builders import (accounts, charge, charges, days_before, filings, make_state, policy_of, pre2013_charge,
                      profile, verdict)
from copilot.refs import charge_refs
from mcp_ch.tools import charge_item


# ------------------------------------------------ the three data fixes ----
def raw(**kw) -> dict:
    """A charge exactly as the Companies House API sends it."""
    return {"charge_code": "108125710002", "created_on": "2017-07-10", "status": "outstanding",
            "persons_entitled": [{"name": "Interbay Funding Limited"}],
            "particulars": {"contains_negative_pledge": True, "description": "freehold land"}, **kw}


def test_absent_flag_means_false_on_a_post_2013_charge():
    c = charge_item(raw())
    assert c["contains_negative_pledge"] is True
    assert c["contains_floating_charge"] is False            # absent + charge_code -> False, not None


def test_absent_flag_means_not_recorded_on_a_pre_2013_charge():
    c = charge_item(raw(charge_code=None, particulars={"description": "see image"}))
    assert c["contains_negative_pledge"] is None
    assert c["contains_floating_charge"] is None


def test_legacy_satisfied_status_counts_as_fully_satisfied():
    assert charge_item(raw(status="satisfied"))["status"] == "fully-satisfied"


def test_a_charge_with_no_named_lender_has_no_lender_group():
    c = charge_item(raw(persons_entitled=[]))
    assert c["lender_group"] is None and c["persons_entitled"] == []
    assert charge_item(raw(persons_entitled=None))["lender_group"] is None


def test_an_unknown_lender_is_an_evidence_gap_not_a_competitor(no_model):
    r = policy_of(make_state(charges=charges(charge(persons_entitled=[], lender_group=None))))
    assert verdict(r, "SEC-01") == "not_applicable"           # not counted as a third-party charge
    assert verdict(r, "EVD-01") == "applies"                  # EVD-01: lender_group of every charge needed
    assert r["outcome"]["decision"] == "INSUFFICIENT EVIDENCE"


# --------------------------------- the bug the data scan found (regression) ----
def test_post_2013_charge_without_floating_charge_is_decided_in_code(no_model):
    """Before the fix, a missing flag meant 'not recorded', so this went to the model and became a gap."""
    r = policy_of(make_state(charges=charges(charge(contains_floating_charge=False, contains_negative_pledge=False))))
    assert verdict(r, "SEC-07") == "not_applicable"
    assert verdict(r, "SEC-08") == "not_applicable"
    assert r["evidence_gap"] == []


def test_pre_2013_live_charge_is_sent_to_the_model_and_can_become_a_gap(model_says_cannot_tell):
    r = policy_of(make_state(charges=charges(pre2013_charge())))
    assert {q["clause_id"] for q in model_says_cannot_tell} == {"SEC-07", "SEC-08"}
    assert verdict(r, "SEC-07") == verdict(r, "SEC-08") == "not_determinable"
    assert r["qualifies"] is None


# --------------------------------------------------- SEC clause edges ----
def test_sec02_clean_position_with_no_charges(no_model):
    assert verdict(policy_of(make_state()), "SEC-02") == "applies"


@pytest.mark.parametrize("age, applies", [(180, True), (181, False)])
def test_sec05_third_party_charge_within_180_days(no_model, age, applies):
    r = policy_of(make_state(charges=charges(charge(created_on=days_before(age)))))
    assert (verdict(r, "SEC-05") == "applies") is applies


@pytest.mark.parametrize("gap, applies", [(30, True), (31, False)])
def test_sec06_same_lender_within_30_days(no_model, gap, applies):
    a = charge(created_on="2020-01-01")
    b = charge(created_on=days_before(gap, "2020-01-01"), persons_entitled=[" barclays bank uk plc "])
    r = policy_of(make_state(charges=charges(a, b)))
    assert (verdict(r, "SEC-06") == "applies") is applies     # lender match ignores case and spaces


def test_sec06_needs_the_same_lender(no_model):
    a, b = charge(created_on="2020-01-01"), charge(created_on="2020-01-01", persons_entitled=["HSBC UK Bank PLC"])
    assert verdict(policy_of(make_state(charges=charges(a, b))), "SEC-06") == "not_applicable"


def test_sec08_ignores_a_fully_satisfied_floating_charge(no_model):
    r = policy_of(make_state(charges=charges(charge(status="fully-satisfied", contains_floating_charge=True))))
    assert verdict(r, "SEC-08") == "not_applicable"


# --------------------------------------------------- CON clause edges ----
@pytest.mark.parametrize("late, applies", [(30, False), (31, True)])
def test_con01_more_than_30_days_late(no_model, late, applies):
    r = policy_of(make_state(filings=filings(accounts(days_before(100), late))))
    assert (verdict(r, "CON-01") == "applies") is applies


@pytest.mark.parametrize("late, applies", [(180, False), (181, True)])
def test_con02_latest_more_than_180_days_late(no_model, late, applies):
    r = policy_of(make_state(filings=filings(accounts(days_before(100), late))))
    assert (verdict(r, "CON-02") == "applies") is applies


def test_con02_only_looks_at_the_latest_filing(no_model):
    f = filings(accounts(days_before(100), 0), accounts(days_before(400), 300))
    assert verdict(policy_of(make_state(filings=f)), "CON-02") == "not_applicable"


def test_con03_two_late_filings_inside_three_years(no_model):
    f = filings(accounts(days_before(100), 1), accounts(days_before(1000), 1))
    assert verdict(policy_of(make_state(filings=f)), "CON-03") == "applies"


def test_con03_ignores_late_filings_older_than_three_years(no_model):
    f = filings(accounts(days_before(100), 1), accounts(days_before(1100), 1))       # 3 years ≈ 1096 days
    assert verdict(policy_of(make_state(filings=f)), "CON-03") == "not_applicable"


def test_con03_is_undeterminable_when_the_filing_window_was_cut(no_model):
    r = policy_of(make_state(filings=filings(accounts(days_before(100), 0), kept=500)))
    assert verdict(r, "CON-03") == "not_determinable"
    assert verdict(r, "CON-02") == "not_applicable"           # the newest filing is never cut


@pytest.mark.parametrize("description, applies", [
    ("accounts with accounts type micro entity", True),        # tools.py turns hyphens into spaces
    ("accounts with accounts type total exemption full", True),
    ("accounts with accounts type small", False),
])
def test_con04_micro_or_total_exemption(no_model, description, applies):
    r = policy_of(make_state(filings=filings(accounts(days_before(100), 0, description))))
    assert (verdict(r, "CON-04") == "applies") is applies


MICRO = "accounts with accounts type micro entity"


def test_con04_alone_proceeds_with_its_condition(no_model):
    """Policy v1.1: undisclosed earnings attach a condition; they don't refer the company."""
    r = policy_of(make_state(filings=filings(accounts(days_before(100), 0, MICRO))))
    assert r["outcome"]["decision"] == "PROCEED" and r["qualifies"] is True
    assert r["outcome"]["conditions"] == ["CON-04"]
    assert "CON-04" not in r["outcome"]["clauses"]                 # a condition never sets the decision


def test_con04_condition_is_kept_when_another_clause_refers(no_model):
    r = policy_of(make_state(filings=filings(accounts(days_before(100), 45, MICRO))))     # CON-01: 45 days late
    assert r["outcome"] == {"decision": "REFER", "clauses": ["CON-01"], "conditions": ["CON-04"]}


@pytest.mark.parametrize("age_days, applies", [(22 * 31, True), (20 * 30, False)])
def test_con05_no_accounts_after_21_months(no_model, age_days, applies):
    r = policy_of(make_state(profile=profile(date_of_creation=days_before(age_days)), filings=filings()))
    assert (verdict(r, "CON-05") == "applies") is applies


@pytest.mark.parametrize("age_days, applies", [(round(18.1 * 30.44), True), (round(17.9 * 30.44), False)])
def test_con06_accounts_made_up_more_than_18_months_ago(no_model, age_days, applies):
    r = policy_of(make_state(profile=profile(last_accounts_made_up_to=days_before(age_days))))
    assert (verdict(r, "CON-06") == "applies") is applies


def insolvency_filing(type_: str, description: str, category: str = "insolvency") -> dict:
    return {"date": days_before(400), "category": category, "type": type_, "description": description,
            "days_late": None}


@pytest.mark.parametrize("item, applies", [
    (insolvency_filing("CAP-SS", "Solvency Statement dated 15/06/23"), False),     # found in the 100-lead run
    (insolvency_filing("AM01", "Notice of administrator's appointment"), True),
    (insolvency_filing("LIQ02", "Statement of affairs", category="liquidation"), True),
])
def test_con07_a_solvency_statement_is_not_insolvency(no_model, item, applies):
    r = policy_of(make_state(filings=filings(accounts(days_before(100), 0), item)))
    assert (verdict(r, "CON-07") == "applies") is applies


# ------------------------------------------------ precedence (EVD-07) ----
def test_decline_beats_refer_and_qualifies_is_false(no_model):
    f = filings(accounts(days_before(100), 200))              # CON-01 REFER + CON-02 DECLINE
    r = policy_of(make_state(filings=f))
    assert r["outcome"] == {"decision": "DECLINE", "clauses": ["CON-02"], "conditions": []}
    assert r["qualifies"] is False
    assert "CON-01" in {a["clause_id"] for a in r["applicable"]}   # still cited (EVD-07)


def test_refer_with_no_gap_qualifies(no_model):
    r = policy_of(make_state(charges=charges(charge())))       # SEC-01 REFER
    assert r["outcome"]["decision"] == "REFER" and r["qualifies"] is True


def test_missing_charges_record_gives_insufficient_evidence(no_model):
    r = policy_of(make_state(charges=None))
    assert r["outcome"] == {"decision": "INSUFFICIENT EVIDENCE", "clauses": ["EVD-01"], "conditions": []}
    assert all(verdict(r, f"SEC-0{i}") == "not_determinable" for i in range(1, 9))
    assert r["qualifies"] is None


# -------------------------------------------------------------- refs ----
def test_charge_refs_are_unique_when_pre_2013_charges_collide():
    a = pre2013_charge(persons_entitled=["Lloyds Bank PLC"])
    refs = charge_refs([a, dict(a)])
    assert len(set(refs)) == 2 and refs[1].endswith("#2")


def test_a_charge_keeps_its_ref_whatever_order_the_api_returns():
    """Regression: numbering by list position swapped these two refs (1,152 real companies affected)."""
    live = pre2013_charge(persons_entitled=["Lloyds Bank PLC"], status="outstanding")
    paid = pre2013_charge(persons_entitled=["Lloyds Bank PLC"], status="fully-satisfied")
    ab, ba = charge_refs([live, paid]), charge_refs([paid, live])
    assert (ab[0], ab[1]) == (ba[1], ba[0])


# ----------------------------------------------- open policy question ----
@pytest.mark.xfail(strict=True, reason="SEC-07 currently counts fully-satisfied charges (literal 'Applies when'). "
                                       "Awaiting a policy-owner decision; if SEC-07 is restricted to live "
                                       "charges this test starts passing and strict=True makes that visible.")
def test_sec07_ignores_a_fully_satisfied_charge(no_model):
    r = policy_of(make_state(charges=charges(charge(status="fully-satisfied", contains_negative_pledge=True))))
    assert verdict(r, "SEC-07") == "not_applicable"
