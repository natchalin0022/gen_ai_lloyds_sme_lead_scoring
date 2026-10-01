"""Test 1 — the data contract: do real Companies House records look the way the code assumes?

Scans the charge JSON the CH pipeline saved (API/CompaniesHouse/company_info_json, ~105k companies).
This is the check that found the flag bug: the API never sends a flag as false, it leaves it out.

By default every 10th file is scanned (a few seconds); DATA_SCAN_EVERY=1 scans all of them.
"""
import json
import os
from collections import Counter
from datetime import date

import pytest

from conftest import ROOT
from copilot.refs import charge_refs
from copilot.signals import charge_signals
from mcp_ch.tools import FLAGS, charge_item

CHARGE_DIR = ROOT / "API" / "CompaniesHouse" / "company_info_json"
EVERY = int(os.environ.get("DATA_SCAN_EVERY", "10"))
RAW_STATUSES = {"outstanding", "fully-satisfied", "part-satisfied", "satisfied"}
STATUSES = {"outstanding", "fully-satisfied", "part-satisfied"}


def companies():
    files = sorted(CHARGE_DIR.glob("*_charges.json"))[::EVERY]
    if not files:
        pytest.skip(f"no charge JSON under {CHARGE_DIR}")
    for f in files:
        d = json.loads(f.read_text())
        items = d.get("items", []) if isinstance(d, dict) else d
        if items:
            yield f.name.split("_")[0], items


@pytest.fixture(scope="module")
def sample():
    return list(companies())


def test_the_api_never_sends_a_flag_as_false(sample):
    """If this ever fails, CH has started sending explicit false and _flag() in tools.py needs revisiting."""
    seen = Counter(repr((c.get("particulars") or {})[k])
                   for _, items in sample for c in items for k in FLAGS if k in (c.get("particulars") or {}))
    assert set(seen) <= {"True"}, seen


def test_every_raw_status_is_one_we_handle(sample):
    seen = Counter(c.get("status") for _, items in sample for c in items)
    assert set(seen) <= RAW_STATUSES, seen


def test_converted_charges_keep_the_promised_shape(sample):
    bad = []
    for number, items in sample:
        for c in items:
            out = charge_item(c)
            ok = (out["status"] in STATUSES
                  and out["lender_group"] in {"own", "third_party", None}
                  and (out["lender_group"] is None) == (not out["persons_entitled"])
                  and all(out[k] in ({True, False} if out["charge_code"] else {True, None}) for k in FLAGS))
            if not ok:
                bad.append((number, c.get("charge_code"), out["status"], out["lender_group"]))
    assert not bad, bad[:5]


def test_signals_run_on_every_real_company_and_refs_are_unique(sample):
    as_of = date(2026, 9, 23)
    for number, items in sample:
        converted = [charge_item(c) for c in items]
        refs = charge_refs(converted)
        assert len(set(refs)) == len(refs), number
        s = charge_signals({"items": converted}, as_of)          # must not raise on real data
        assert set(s["live"]) <= set(refs), number
