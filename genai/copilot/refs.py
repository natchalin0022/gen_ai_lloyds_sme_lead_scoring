"""Record references — how every node points at a charge or a filing.

One copy on purpose: signal, policy and brief join on these strings, so two versions that
differed by one character would silently split one charge into two.
"""
from __future__ import annotations


def charge_refs(items: list[dict]) -> list[str]:
    """One stable, human-readable reference per charge, in the same order as `items`.

    Uses charge_code; charges registered before April 2013 have none, so those fall back to
    creation date + first lender, with a '#2' suffix if two would collide.
    """
    refs, seen = [], {}
    for c in items:
        lender = (c["persons_entitled"] or ["?"])[0]
        lender = lender if len(lender) <= 28 else lender[:27].rstrip() + "…"
        base = c["charge_code"] or f"created {c['created_on']} · {lender}"
        seen[base] = seen.get(base, 0) + 1
        refs.append(base if seen[base] == 1 else f"{base} #{seen[base]}")
    return refs


def filing_ref(f: dict) -> str:
    return f"{f['type']} filed {f['date']}"
