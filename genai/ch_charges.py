"""Pull charge (secured-lending) filings for ONE company from Companies House.

Usage:  .venv/bin/python genai/ch_charges.py 10812571
Auth :  CH_API in .env (HTTP basic auth, key as username, blank password).
Docs :  https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/chargelist
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE = "https://api.company-information.service.gov.uk"
AUTH = (os.environ["CH_API"], "")          # key is the username, password is empty


def get_charges(company_number: str) -> list[dict]:
    """Return every charge registered against a company (all pages). [] if none."""
    number = company_number.strip().zfill(8)  # CH numbers are 8 chars, zero-padded
    items, start = [], 0
    while True:
        r = requests.get(f"{BASE}/company/{number}/charges",
                         params={"items_per_page": 100, "start_index": start},
                         auth=AUTH, timeout=30)
        if r.status_code == 404:            # company has no charges (or doesn't exist)
            return items
        r.raise_for_status()
        page = r.json()
        items.extend(page.get("items", []))
        start += len(page.get("items", []))
        if start >= page.get("total_count", 0) or not page.get("items"):
            return items


def get_charge(company_number: str, charge_id: str) -> dict:
    """Return ONE charge in full, by the id found in the list's `links.self`."""
    number = company_number.strip().zfill(8)
    r = requests.get(f"{BASE}/company/{number}/charges/{charge_id}", auth=AUTH, timeout=30)
    r.raise_for_status()
    return r.json()


def summarise(charge: dict) -> dict:
    """The handful of fields the lending model actually uses."""
    return {
        "charge_code":     charge.get("charge_code"),
        "created_on":      charge.get("created_on"),
        "delivered_on":    charge.get("delivered_on"),
        "satisfied_on":    charge.get("satisfied_on"),
        "status":          charge.get("status"),                 # outstanding / fully-satisfied / part-satisfied
        "classification":  charge.get("classification", {}).get("description"),
        "persons_entitled": [p.get("name") for p in charge.get("persons_entitled", [])],  # the LENDER(s)
        "charge_id":       charge.get("links", {}).get("self", "").rsplit("/", 1)[-1],
    }


if __name__ == "__main__":
    number = sys.argv[1] if len(sys.argv) > 1 else "10812571"
    charges = get_charges(number)
    print(f"company {number}: {len(charges)} charge(s)\n")
    for c in charges:
        s = summarise(c)
        print(f"  {s['created_on']}  {s['status']:16s}  lender: {', '.join(s['persons_entitled']) or '-'}")
    if charges:
        one = get_charge(number, summarise(charges[0])["charge_id"])
        print(f"\nfull record for the most recent charge ({one.get('charge_code')}):")
        for k in ("created_on", "status", "classification", "persons_entitled", "particulars", "secured_details"):
            print(f"  {k:17s} {one.get(k)}")
