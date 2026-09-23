"""Pull filing history for ONE company from Companies House.

Usage:  .venv/bin/python genai/ch_filing_history.py 10812571
Auth :  CH_API in .env (HTTP basic auth, key as username, blank password).
Docs :  https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/filinghistorylist
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE = "https://api.company-information.service.gov.uk"
AUTH = (os.environ["CH_API"], "")          # key is the username, password is empty

# categories that carry lending signal; the rest (address, change-of-name,
# annual-return, resolution, miscellaneous) is mostly administrative noise.
SIGNAL_CATEGORIES = {"accounts", "mortgage", "officers", "capital",
                     "liquidation", "incorporation"}


def get_filing_history(company_number: str) -> list[dict]:
    """Return every filing for a company (all pages), newest first. [] if none."""
    number = company_number.strip().zfill(8)  # CH numbers are 8 chars, zero-padded
    items, start = [], 0
    while True:
        r = requests.get(f"{BASE}/company/{number}/filing-history",
                         params={"items_per_page": 100, "start_index": start},
                         auth=AUTH, timeout=30)
        if r.status_code == 404:            # no filing history (or company doesn't exist)
            break
        r.raise_for_status()
        page = r.json()
        batch = page.get("items", [])
        items.extend(batch)
        start += len(batch)
        if start >= page.get("total_count", 0) or not batch:
            break
    return sorted(items, key=lambda f: f.get("date", ""), reverse=True)


def describe(filing: dict) -> str:
    """Readable text for one filing.

    CH returns `description` as a TEMPLATE KEY ("accounts-with-accounts-type-full")
    and puts the variable parts in `description_values`. Handing the raw key to a
    model is poor input, so de-slugify it and fold the values back in.
    """
    values = filing.get("description_values") or {}
    if "description" in values:             # the 'legacy' template carries real text here
        text = values["description"]
    else:
        text = (filing.get("description") or "").replace("-", " ").strip()
    extras = [f"{k.replace('_', ' ')}: {v}"
              for k, v in values.items() if k != "description"]
    return f"{text} ({'; '.join(extras)})" if extras else text


def summarise(filing: dict) -> dict:
    """The handful of fields the lending model actually uses."""
    return {
        "date":        filing.get("date"),
        "category":    filing.get("category"),     # accounts / mortgage / officers / ...
        "subcategory": filing.get("subcategory"),
        "type":        filing.get("type"),
        "description": describe(filing),
        "paper_filed": filing.get("paper_filed", False),
    }


def filter_filings(filings: list[dict], since: str | None = None,
                   categories: set[str] | None = None) -> list[dict]:
    """Narrow to the filings worth sending to a model.

    Deterministic work belongs here rather than in the prompt: when an eval case
    fails you want to know whether the filter dropped the evidence or the model
    missed it. Two judgement calls in one place can't be told apart.
    """
    cats = SIGNAL_CATEGORIES if categories is None else categories
    return [f for f in filings
            if f.get("category") in cats
            and (since is None or (f.get("date") or "") >= since)]


if __name__ == "__main__":
    number = sys.argv[1] if len(sys.argv) > 1 else "10812571"
    filings = get_filing_history(number)
    kept = filter_filings(filings, since="2015-01-01")
    print(f"company {number}: {len(filings)} filing(s), {len(kept)} in signal categories\n")
    for f in kept[:20]:
        s = summarise(f)
        print(f"  {s['date']}  {s['category']:14s}  {s['description'][:80]}")

    counts = {}
    for f in filings:
        counts[f.get("category")] = counts.get(f.get("category"), 0) + 1
    print("\ncategory counts across all filings:")
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {str(cat):24s} {n}")
