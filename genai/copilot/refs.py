"""Record references — how every node points at a charge or a filing.

One copy on purpose: signal, policy and brief join on these strings, so two versions that
differed by one character would silently split one charge into two.
"""
from __future__ import annotations

import json


def _base(c: dict) -> str:
    lender = (c["persons_entitled"] or ["?"])[0]
    lender = lender if len(lender) <= 28 else lender[:27].rstrip() + "…"
    return c["charge_code"] or f"created {c['created_on']} · {lender}"


def charge_refs(items: list[dict]) -> list[str]:
    """One stable, human-readable reference per charge, in the same order as `items`.

    Uses charge_code; charges registered before April 2013 have none, so those fall back to
    creation date + first lender, with a '#2' suffix if two would collide.

    Colliding charges are numbered in an order fixed by their CONTENT, not by where they sit in
    the list, so a charge keeps its reference whatever order the API returns them in. (Numbering
    by position made refs order-dependent on 1,152 of ~105k real companies — found by
    tests/test_properties.py.) Charges identical in content are interchangeable, so their tie
    order doesn't matter.
    """
    bases = [_base(c) for c in items]
    content = [json.dumps(c, sort_keys=True, default=str) for c in items]
    refs: list[str] = [""] * len(items)
    seen: dict[str, int] = {}
    for i in sorted(range(len(items)), key=lambda i: (bases[i], content[i])):
        seen[bases[i]] = seen.get(bases[i], 0) + 1
        refs[i] = bases[i] if seen[bases[i]] == 1 else f"{bases[i]} #{seen[bases[i]]}"
    return refs


def filing_ref(f: dict) -> str:
    return f"{f['type']} filed {f['date']}"
