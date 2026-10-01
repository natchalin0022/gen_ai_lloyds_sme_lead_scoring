"""Test 9 — snapshots: the two saved companies must keep producing exactly what they produced.

Any code change that shifts a code-decided result for 36EL or Tesco fails here. Model-decided
parts are excluded (collateral) or stubbed (policy reading), because their wording varies by run.
"""
import json

import pytest
import requests

from builders import policy_of, signals_of
from conftest import GENAI
from copilot.signals import deterministic_signals

STATE = GENAI / "production" / "state"
SAVED = {n: json.loads((STATE / f"after_signal_{n}.json").read_text()) for n in ("10812571", "00445790")}


@pytest.mark.parametrize("number", SAVED)
def test_signals_match_notebook_02(number):
    saved = SAVED[number]
    assert deterministic_signals(saved) == {k: v for k, v in saved["signals"].items() if k != "collateral"}


@pytest.mark.parametrize("number", SAVED)
def test_tools_reproduce_the_saved_records_from_cache(number, monkeypatch):
    """Also guards the tools.py fixes: neither company's records may change."""
    def offline(*a, **kw):
        raise AssertionError("not in the disk cache — this test must not call Companies House")
    monkeypatch.setattr(requests, "get", offline)
    from mcp_ch import tools
    saved = SAVED[number]
    assert tools.get_company_profile(number) == saved["profile"]
    assert tools.get_filing_history(number) == saved["filings"]
    assert tools.get_charges(number) == saved["charges"]
    assert tools.get_officers(number) == saved["officers"]


# notebook 03's printed result for 36EL — every clause decided in code, no model call
TRACE_36EL = {
    "CON-01": "applies · code", "CON-02": "applies · code", "CON-03": "applies · code",
    "CON-04": "applies · code", "CON-05": "not_applicable · code", "CON-06": "applies · code",
    "CON-07": "not_applicable · code", "CON-08": "routed → brief",
    "EVD-01": "not_applicable · code", "EVD-02": "routed → brief", "EVD-03": "routed → brief",
    "EVD-04": "routed → brief", "EVD-05": "routed → supervisor", "EVD-06": "routed → brief",
    "EVD-07": "routed → this node (precedence)",
    "SEC-01": "applies · code", "SEC-02": "not_applicable · code", "SEC-03": "not_applicable · code",
    "SEC-04": "not_applicable · code", "SEC-05": "not_applicable · code", "SEC-06": "applies · code",
    "SEC-07": "applies · code", "SEC-08": "applies · code",
}


def test_36el_policy_matches_notebook_03(no_model):
    r = policy_of(SAVED["10812571"])
    assert r["outcome"] == {"decision": "DECLINE", "clauses": ["CON-02"], "conditions": ["CON-04"]}   # policy v1.1
    assert r["qualifies"] is False and r["evidence_gap"] == []
    assert r["policy_trace"] == TRACE_36EL


def test_tesco_policy_matches_notebook_03(model_says_cannot_tell):
    r = policy_of(SAVED["00445790"])
    assert r["outcome"] == {"decision": "REFER", "clauses": ["SEC-01"], "conditions": []}
    assert r["qualifies"] is None
    assert {g.split(" ")[0] for g in r["evidence_gap"]} == {"CON-01", "CON-03", "SEC-07", "SEC-08"}
    assert {(q["clause_id"], q["target"][:18]) for q in model_says_cannot_tell} == {
        ("SEC-07", "created 2009-11-04"), ("SEC-08", "created 2009-11-04")}     # only the two live 2009 charges
    assert len(model_says_cannot_tell) == 4
