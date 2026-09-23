"""Signal node (notebook 02) — turn the four records into facts the policy clauses test.

Split in two so the deterministic part can be checked against notebook 02's saved state
without a model call:
    deterministic_signals(state)  everything except collateral — counting and date arithmetic
    signal(state)                 the node: the above + the one model call (collateral)
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .refs import charge_refs, filing_ref

LIVE = {"outstanding", "part-satisfied"}          # not yet fully redeemed
MICRO_OR_EXEMPT = re.compile(r"micro[\s-]?entity|total[\s-]?exemption", re.I)
THREE_YEARS = timedelta(days=round(3 * 365.25))


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _months(earlier: date, later: date) -> float:
    return round((later - earlier).days / 30.44, 1)


# ------------------------------------------------------------- charges ----
def _same_lender_within_30d(refs: list[str], items: list[dict]) -> list[dict]:
    """SEC-06: runs of 2+ charges to one lender, each created within 30 days of the previous one."""
    lenders = {p.strip().lower() for c in items for p in c["persons_entitled"]}
    groups = []
    for lender in sorted(lenders):
        dated = sorted((_d(c["created_on"]), r) for r, c in zip(refs, items)
                       if c["created_on"] and lender in {p.strip().lower() for p in c["persons_entitled"]})
        run = dated[:1]
        for prev, cur in zip(dated, dated[1:]):
            if (cur[0] - prev[0]).days <= 30:
                run.append(cur)
                continue
            if len(run) >= 2:
                groups.append({"lender": lender, "charges": [r for _, r in run]})
            run = [cur]
        if len(run) >= 2:
            groups.append({"lender": lender, "charges": [r for _, r in run]})
    return groups


def charge_signals(charges: dict, as_of: date) -> dict:
    items = charges["items"]
    refs = charge_refs(items)
    where = lambda test: [r for r, c in zip(refs, items) if test(c)]
    age = lambda c: (as_of - _d(c["created_on"])).days if c["created_on"] else None
    return {
        "total": len(items),
        "live": where(lambda c: c["status"] in LIVE),
        "clean_position":          all(c["status"] == "fully-satisfied" for c in items),   # SEC-02
        "satisfied":               where(lambda c: c["status"] == "fully-satisfied"),       # SEC-03
        "outstanding_third_party": where(lambda c: c["status"] == "outstanding"
                                                   and c["lender_group"] == "third_party"),  # SEC-01
        "part_satisfied":          where(lambda c: c["status"] == "part-satisfied"),         # SEC-04
        "third_party_last_180d":   where(lambda c: c["lender_group"] == "third_party"
                                                   and age(c) is not None and 0 <= age(c) <= 180),  # SEC-05
        "same_lender_within_30d":  _same_lender_within_30d(refs, items),                      # SEC-06
        "negative_pledge":         where(lambda c: c["contains_negative_pledge"] is True),    # SEC-07
        "floating_live":           where(lambda c: c["contains_floating_charge"] is True
                                                   and c["status"] != "fully-satisfied"),     # SEC-08
        "flags_not_recorded_live": where(lambda c: c["status"] in LIVE
                                                   and (c["contains_negative_pledge"] is None
                                                        or c["contains_floating_charge"] is None)),
        "own_group":               where(lambda c: c["lender_group"] == "own"),
    }


# ------------------------------------------------------------- filings ----
def filing_signals(filings: dict, profile: dict, as_of: date) -> dict:
    items = filings["items"]
    accounts = sorted((f for f in items if f["category"] == "accounts"),
                      key=lambda f: f["date"], reverse=True)
    recent = [f for f in accounts if timedelta(0) <= as_of - _d(f["date"]) <= THREE_YEARS]
    latest = accounts[0] if accounts else None
    worst = max((f for f in recent if f["days_late"] is not None),
                key=lambda f: f["days_late"], default=None)
    inc = _d(profile.get("date_of_creation"))
    made_up = _d(profile.get("last_accounts_made_up_to"))
    return {
        "accounts_on_record": len(accounts),                                       # CON-05
        "latest_accounts": latest and {"ref": filing_ref(latest),
                                       "days_late": latest["days_late"],           # CON-02
                                       "description": latest["description"]},
        "latest_micro_or_exempt": bool(latest and MICRO_OR_EXEMPT.search(latest["description"])),  # CON-04
        "worst_days_late_3y": worst and {"ref": filing_ref(worst),
                                         "days_late": worst["days_late"]},         # CON-01
        "late_3y": [filing_ref(f) for f in recent if (f["days_late"] or 0) > 0],   # CON-03
        "months_since_incorporation": inc and _months(inc, as_of),                 # CON-05
        "last_made_up_to": profile.get("last_accounts_made_up_to"),
        "months_since_made_up": made_up and _months(made_up, as_of),               # CON-06
        "insolvency_filings": [filing_ref(f) for f in items
                               if f["category"] in ("liquidation", "insolvency")], # CON-07
        "window_truncated": filings["kept"] > len(items),
    }


# ------------------------------------------------------------ officers ----
def officer_signals(officers: dict, as_of: date) -> dict:
    new = [o for o in officers["items"]
           if o["appointed_on"] and 0 <= (as_of - _d(o["appointed_on"])).days <= 365]
    return {
        "active": officers["active"],
        "total_ever": officers["total"],
        "appointed_last_12m": [f"{o['role']} appointed {o['appointed_on']}" for o in new],
    }


def missing_inputs(state: dict) -> list[str]:
    gaps = [k for k in ("profile", "filings", "charges", "officers") if state.get(k) is None]
    if state.get("profile") is not None and not state["profile"].get("date_of_creation"):
        gaps.append("profile.date_of_creation")
    if state.get("charges") is not None:
        items = state["charges"]["items"]
        gaps += [f"charge {r}: status or lender_group"
                 for r, c in zip(charge_refs(items), items) if not (c["status"] and c["lender_group"])]
    return gaps


def deterministic_signals(state: dict) -> dict:
    as_of = _d(state.get("as_of")) or date.today()
    p, f, c, o = (state.get(k) for k in ("profile", "filings", "charges", "officers"))
    return {
        "as_of":    as_of.isoformat(),
        "company":  p and {"status": p["company_status"],
                           "accounts_type": p["accounts_type"],
                           "next_accounts_due": p["next_accounts_due"],
                           "accounts_overdue": p["accounts_overdue"],
                           "has_insolvency_history": p["has_insolvency_history"]},
        "charges":  c and charge_signals(c, as_of),
        "filings":  f and filing_signals(f, p or {}, as_of),
        "officers": o and officer_signals(o, as_of),
        "missing":  missing_inputs(state),
    }


# ------------------------------------------------- the one model call ----
Collateral = Literal["property", "cash_deposit", "shares", "receivables", "vehicles_plant_equipment",
                     "intellectual_property", "all_assets", "other", "not_stated"]


class Label(BaseModel):
    id: str
    collateral: Collateral
    quote: str = Field(description="The words in the text that name the asset, copied exactly. Empty if not_stated.")


class Labels(BaseModel):
    labels: list[Label]


SYSTEM = (
    "You classify the free-text particulars of UK Companies House charges by the asset they secure. "
    "Use only the text you are given. Do not use knowledge about lenders, companies, or what such "
    "charges usually cover. If the text does not say what is secured (for example it only says "
    "'see image for full details'), answer not_stated. For quote, copy the words from the text that "
    "name the asset, exactly as written."
)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


async def classify_collateral(charges: dict) -> dict:
    items, refs = charges["items"], charge_refs(charges["items"])
    texts: dict[str, list[str]] = {}
    for r, c in zip(refs, items):
        if c["status"] in LIVE and c["particulars"]:
            texts.setdefault(c["particulars"], []).append(r)
    if not texts:
        return {}

    batch = [{"id": f"t{i}", "text": t} for i, t in enumerate(texts, 1)]
    resp = await llm.client().beta.messages.parse(
        model=llm.MODEL,
        max_tokens=4096,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(batch, indent=1)}],
        output_format=Labels,
        output_config={"effort": "low"},
        **llm.FALLBACK,
    )
    print(llm.usage_line("signal/collateral", resp, len(batch), "text(s)"))

    ok = resp.stop_reason != "refusal" and resp.parsed_output is not None
    got = {lab.id: lab for lab in resp.parsed_output.labels} if ok else {}
    out = {}
    for b, charge_list in zip(batch, texts.values()):
        lab = got.get(b["id"])
        entry = ({"collateral": "unclassified", "quote": None, "verified": False} if lab is None else
                 {"collateral": lab.collateral,
                  "quote": lab.quote,
                  "verified": lab.collateral == "not_stated"
                              or (bool(lab.quote) and _norm(lab.quote) in _norm(b["text"]))})
        for r in charge_list:
            out[r] = entry
    return out


async def signal(state: dict) -> dict:
    s = deterministic_signals(state)
    c = state.get("charges")
    s["collateral"] = await classify_collateral(c) if c else {}
    return {"signals": s}
