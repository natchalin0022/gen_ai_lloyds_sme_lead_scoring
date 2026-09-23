"""The four Companies House tools — plain Python, no MCP.

Each returns a SMALL, model-ready dict: only the fields a credit analyst uses, with
codes decoded into text and facts (lender_group, days_late) resolved in code. The raw
API responses are 5-10x bigger and full of links and template keys the model would
have to guess at; shrinking them here is what keeps the agent's context under control.

Kept separate from server.py so they can be imported and tested directly, and so the
graph's deterministic `fetch` node can call them without a protocol round-trip.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from . import ch_client as ch

# lender_groups.py lives one level up (genai/); share the single definition
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lender_groups import lender_group  # noqa: E402


# ------------------------------------------------------------- 1. profile ----
def get_company_profile(company_number: str) -> dict | None:
    """Core facts: name, status, incorporation date, SIC codes, accounts due/overdue.

    Source of `date_of_creation` (needed for age and the first-accounts deadline) and of
    the CURRENT accounts-overdue flag, which is the cheapest filing-conduct signal.
    """
    d = ch.get(f"/company/{ch.norm(company_number)}")
    if d is None:
        return None
    acc = d.get("accounts", {})
    return {
        "company_number":   d.get("company_number"),
        "company_name":     d.get("company_name"),
        "company_status":   d.get("company_status"),            # active / liquidation / dissolved …
        "type":             d.get("type"),                      # ltd / plc / llp …
        "date_of_creation": d.get("date_of_creation"),
        "sic_codes":        d.get("sic_codes", []),
        "registered_office": ", ".join(v for k, v in (d.get("registered_office_address") or {}).items()
                                       if v and k != "country"),
        "accounts_type":    acc.get("last_accounts", {}).get("type"),          # micro-entity / small / full …
        "last_accounts_made_up_to": acc.get("last_accounts", {}).get("made_up_to"),
        "next_accounts_due": acc.get("next_accounts", {}).get("due_on"),
        "accounts_overdue": acc.get("next_accounts", {}).get("overdue", False),
        "has_charges":      d.get("has_charges", False),
        "has_insolvency_history": d.get("has_insolvency_history", False),
    }


# ------------------------------------------------------ 2. filing history ----
# categories that carry lending signal; the rest is administrative noise
SIGNAL_CATEGORIES = {"accounts", "mortgage", "officers", "capital", "liquidation",
                     "incorporation", "insolvency", "gazette", "dissolution"}


def _describe(f: dict) -> str:
    """CH returns `description` as a template key ('accounts-with-accounts-type-full')
    and the variable parts in `description_values`. Decode to readable text."""
    values = f.get("description_values") or {}
    if "description" in values:                      # legacy template carries real text
        text = values["description"]
    else:
        text = (f.get("description") or "").replace("-", " ").strip()
    extras = [f"{k.replace('_', ' ')}: {v}" for k, v in values.items() if k != "description"]
    return text + (f" ({'; '.join(extras)})" if extras else "")


def _days_late(f: dict, date_of_creation: str | None) -> int | None:
    """Statutory lateness for an accounts filing, in code so the model never estimates it.

    Deadline = period end + 9 months (private company). First accounts: 21 months from
    incorporation if that is later. Positive = filed after the deadline.
    """
    if f.get("category") != "accounts":
        return None
    mu = (f.get("description_values") or {}).get("made_up_date")
    filed = f.get("date")
    if not (mu and filed):
        return None
    y, m, d = map(int, mu.split("-"))
    m += 9
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    deadline = date(y, m, min(d, 28))
    if date_of_creation:
        inc = date.fromisoformat(date_of_creation)
        first_deadline = inc + timedelta(days=int(21 * 30.44))
        if inc <= date.fromisoformat(mu) <= inc + timedelta(days=548):   # first accounts period
            deadline = max(deadline, first_deadline)
    return (date.fromisoformat(filed) - deadline).days


def get_filing_history(company_number: str, signal_only: bool = True,
                       max_items: int = 60) -> dict | None:
    """Filed documents, newest first, decoded and trimmed to the lending-signal categories.

    Returns {"total": n, "kept": k, "items": [...]} so the caller can see how much was filtered.
    """
    n = ch.norm(company_number)
    raw = ch.get_paged(f"/company/{n}/filing-history")
    if raw is None:
        return None
    profile = ch.get(f"/company/{n}") or {}
    doc = profile.get("date_of_creation")
    items = [f for f in raw if not signal_only or f.get("category") in SIGNAL_CATEGORIES]
    out = []
    for f in items[:max_items]:
        out.append({
            "date":        f.get("date"),
            "category":    f.get("category"),
            "type":        f.get("type"),                    # AA, MR01, AP01 …
            "description": _describe(f),
            "days_late":   _days_late(f, doc),
        })
    return {"total": len(raw), "kept": len(items), "items": out}


# -------------------------------------------------------------- 3. charges ----
def get_charges(company_number: str) -> dict:
    """Registered charges (secured lending), each tagged with lender_group in code."""
    raw = ch.get_paged(f"/company/{ch.norm(company_number)}/charges")
    items = []
    for c in raw:
        lenders = [p.get("name") for p in c.get("persons_entitled", [])]
        part = c.get("particulars") or {}
        items.append({
            "charge_code":     c.get("charge_code"),
            "created_on":      c.get("created_on"),
            "delivered_on":    c.get("delivered_on"),
            "satisfied_on":    c.get("satisfied_on"),
            "status":          c.get("status"),               # outstanding / fully-satisfied / part-satisfied
            "persons_entitled": lenders,
            "lender_group":    lender_group(lenders),         # own / third_party — resolved here, not by the model
            "contains_fixed_charge":    part.get("contains_fixed_charge"),
            "contains_floating_charge": part.get("contains_floating_charge"),
            "contains_negative_pledge": part.get("contains_negative_pledge"),
            "particulars":     part.get("description"),      # kept: SEC-07/08 and EVD-04 need it
        })
    return {"total": len(items),
            "outstanding": sum(i["status"] == "outstanding" for i in items),
            "items": items}


# ------------------------------------------------------------- 4. officers ----
def get_officers(company_number: str, active_only: bool = True) -> dict | None:
    """Directors and secretaries. Names and roles only — no DOB, nationality or address."""
    raw = ch.get_paged(f"/company/{ch.norm(company_number)}/officers")
    if raw is None:
        return None
    items = [{
        "name":         o.get("name"),
        "role":         o.get("officer_role"),
        "appointed_on": o.get("appointed_on"),
        "resigned_on":  o.get("resigned_on"),
    } for o in raw if not active_only or not o.get("resigned_on")]
    return {"total": len(raw), "active": sum(1 for o in raw if not o.get("resigned_on")), "items": items}
