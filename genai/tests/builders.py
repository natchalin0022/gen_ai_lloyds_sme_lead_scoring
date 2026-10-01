"""Small builders for fake records, shaped exactly like the mcp_ch tools' output.

Every builder has sensible defaults, so a test only states what it is about:
    make_state(charges=charges(charge(status="part-satisfied")))
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from copilot.policy import policy
from copilot.signals import deterministic_signals

AS_OF = "2026-09-23"


def days_before(n: int, as_of: str = AS_OF) -> str:
    return (date.fromisoformat(as_of) - timedelta(days=n)).isoformat()


def profile(**kw) -> dict:
    return {"company_number": "00000001", "company_name": "TEST LTD", "company_status": "active",
            "type": "ltd", "date_of_creation": "2015-01-01", "sic_codes": ["62020"], "registered_office": "",
            "accounts_type": "small", "last_accounts_made_up_to": days_before(200),
            "next_accounts_due": "2027-01-01", "accounts_overdue": False, "has_charges": True,
            "has_insolvency_history": False, **kw}


def accounts(filed: str, days_late: int | None, description: str = "accounts with accounts type small") -> dict:
    return {"date": filed, "category": "accounts", "type": "AA", "description": description, "days_late": days_late}


def filings(*items: dict, kept: int | None = None) -> dict:
    return {"total": len(items), "kept": len(items) if kept is None else kept, "items": list(items)}


_codes = iter(range(10_000, 10**9))


def charge(**kw) -> dict:
    """A post-2013 charge (has a charge_code) with every flag recorded, unless overridden."""
    return {"charge_code": f"0000000{next(_codes)}", "created_on": "2020-01-01", "delivered_on": None,
            "satisfied_on": None, "status": "outstanding", "persons_entitled": ["Barclays Bank UK PLC"],
            "lender_group": "third_party", "contains_fixed_charge": True, "contains_floating_charge": False,
            "contains_negative_pledge": False, "particulars": None, **kw}


def pre2013_charge(**kw) -> dict:
    """A charge registered before April 2013: no charge_code, flags not recorded."""
    return charge(charge_code=None, created_on="2009-11-04", contains_fixed_charge=None,
                  contains_floating_charge=None, contains_negative_pledge=None, **kw)


def charges(*items: dict) -> dict:
    return {"total": len(items), "outstanding": sum(c["status"] == "outstanding" for c in items),
            "items": list(items)}


def officers() -> dict:
    return {"total": 1, "active": 1,
            "items": [{"name": "DOE, Jane", "role": "director", "appointed_on": "2015-01-01", "resigned_on": None}]}


def make_state(**over) -> dict:
    """A clean company: no charges, one on-time accounts filing. Override any record."""
    base = {"company_number": "00000001", "as_of": AS_OF, "profile": profile(),
            "filings": filings(accounts(days_before(100), -30)), "charges": charges(), "officers": officers()}
    return {**base, **over}


def signals_of(state: dict) -> dict:
    """The signal node without its model call (collateral left empty)."""
    return {**deterministic_signals(state), "collateral": {}}


def policy_of(state: dict) -> dict:
    """Run the real policy node. Pair with the `no_model` or `model_says_cannot_tell` fixture."""
    return asyncio.run(policy({**state, "signals": signals_of(state)}))


def verdict(result: dict, clause_id: str) -> str:
    return result["policy_trace"][clause_id].split(" · ")[0]
