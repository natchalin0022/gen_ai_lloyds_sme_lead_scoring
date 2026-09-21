"""Companies House HTTP client with a disk cache — the ONLY module that talks to the API.

Why a separate layer: the MCP server should be thin (protocol only). Everything about
*how* we reach Companies House — auth, paging, rate limiting, caching — lives here, so
it can be unit-tested without MCP and reused by the notebooks.

Cache: every GET is written to CACHE_DIR/<sha1 of path+params>.json before returning.
600 requests / 5 min disappears fast when debugging a loop; with the cache, the second
run of anything costs zero API calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE      = "https://api.company-information.service.gov.uk"
CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

_KEY = os.environ.get("CH_API")
if not _KEY:
    raise RuntimeError("CH_API not set — add it to .env")
_AUTH = (_KEY, "")                      # key as username, blank password

# --- pacing: CH allows 600 req / 5 min per key = one every 0.5 s. Stay under it. ---
_MIN_INTERVAL = 0.55
_last_call = 0.0


def norm(company_number: str) -> str:
    """CH numbers are 8 chars, zero-padded: '59225' and '00059225' are the same company."""
    return str(company_number).strip().upper().zfill(8)


def _cache_path(path: str, params: dict | None) -> Path:
    key = path + "?" + json.dumps(params or {}, sort_keys=True)
    return CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".json")


def get(path: str, params: dict | None = None, use_cache: bool = True) -> dict | None:
    """GET one endpoint. Returns parsed JSON, or None on 404 (company / resource absent).

    Raises on other HTTP errors so the caller sees a real failure, not silent None.
    """
    global _last_call
    cp = _cache_path(path, params)
    if use_cache and cp.exists():
        return json.loads(cp.read_text())

    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        r = requests.get(BASE + path, params=params, auth=_AUTH, timeout=30)
        _last_call = time.time()
        if r.status_code == 429:                                   # rate-limited: back off
            time.sleep(int(r.headers.get("Retry-After", 30)) + 1)
            continue
        if r.status_code == 404:
            data = None
            break
        r.raise_for_status()
        data = r.json()
        break
    else:
        raise RuntimeError(f"gave up on {path} after repeated 429s")

    cp.write_text(json.dumps(data))          # cache 404s too — absence is an answer
    return data


def get_paged(path: str, per_page: int = 100) -> list[dict]:
    """Follow start_index paging until total_count is reached. [] if the resource is absent."""
    items, start = [], 0
    while True:
        page = get(path, {"items_per_page": per_page, "start_index": start})
        if page is None:
            return items
        batch = page.get("items", [])
        items.extend(batch)
        start += len(batch)
        if not batch or start >= page.get("total_count", 0):
            return items
