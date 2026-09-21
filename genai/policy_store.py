"""Clause-level retriever over policies/*.md — extracted from 06_rag.ipynb.

Public API
    build_store(policy_dir=None)             -> chromadb Collection (built once, cached)
    retrieve(query, k=8, doc=None, ...)      -> list[dict]   nearest clauses to ONE query
    retrieve_facets(facets, k=3, ...)        -> list[dict]   union over several short queries

Two findings from the notebook are baked in:
  * one clause per chunk, split on '### ' — never a character-count splitter;
  * a single blended "describe the company" query retrieves badly; ask one short
    query per fact cluster and union the results (`retrieve_facets`).

The store is built lazily on first use and then reused — never rebuild inside a
graph node, or every company re-embeds the whole corpus.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

POLICY_DIR = Path(__file__).resolve().parent / "policies"
EMBED_MODEL = "all-MiniLM-L6-v2"
REFERENCE_DOCS = {"REF"}          # reference data, not rules — excluded from rule lookups by default


# ---------------------------------------------------------------- chunking ----
def load_clauses(policy_dir: str | Path | None = None) -> list[dict]:
    """One chunk per clause, keyed on '### ' headings. Skips each file's preamble."""
    policy_dir = Path(policy_dir) if policy_dir else POLICY_DIR
    clauses = []
    for path in sorted(glob.glob(os.path.join(policy_dir, "*.md"))):
        raw = open(path, encoding="utf-8").read()
        doc = os.path.basename(path).split("_")[0]           # SEC / CON / EVD / REF
        for block in re.split(r"\n(?=### )", raw):
            if not block.startswith("### "):
                continue
            head = block.split("\n", 1)[0][4:].strip()
            m = re.search(r"\*\*Outcome:\s*([^*]+)\*\*", block)
            clauses.append({
                "clause_id": head.split(" ")[0],
                "doc":       doc,
                "title":     head,
                "outcome":   m.group(1).strip() if m else "",
                "text":      re.sub(r"\n---\s*$", "", block).strip(),
            })
    ids = [c["clause_id"] for c in clauses]
    assert len(set(ids)) == len(ids),               "duplicate clause ids"
    assert all(c["text"].strip() for c in clauses), "empty chunk"
    assert all(c["outcome"] for c in clauses),      "clause with no outcome"
    return clauses


# ------------------------------------------------------------------ store ----
_COLL = None      # module-level cache: built once per process


def build_store(policy_dir: str | Path | None = None, name: str = "policy"):
    """Build (or rebuild) the in-memory store from the policy files. Cached after first call.

    Rebuilds only if the clause count changed — so editing a policy file and calling
    again picks the change up, while repeated calls from a node are free.
    """
    global _COLL
    clauses = load_clauses(policy_dir)
    if _COLL is not None and _COLL.count() == len(clauses):
        return _COLL

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    client = chromadb.Client()
    try:
        client.delete_collection(name)
    except Exception:
        pass
    coll = client.create_collection(name, embedding_function=ef,
                                    metadata={"hnsw:space": "cosine"})
    coll.add(
        ids=[c["clause_id"] for c in clauses],
        documents=[c["text"] for c in clauses],
        metadatas=[{"clause_id": c["clause_id"], "doc": c["doc"],
                    "title": c["title"], "outcome": c["outcome"]} for c in clauses],
    )
    _COLL = coll
    return coll


def get_clause(clause_id: str) -> dict | None:
    """Fetch one clause by id (for citation checks) — no embedding involved."""
    r = build_store().get(ids=[clause_id])
    if not r["ids"]:
        return None
    return {**r["metadatas"][0], "text": r["documents"][0]}


# -------------------------------------------------------------- retrieval ----
def retrieve(query: str, k: int = 8, doc: str | None = None,
             include_reference: bool = False, max_distance: float | None = None) -> list[dict]:
    """Nearest clauses to ONE short query, closest first.

    doc               restrict to a single document: "SEC" / "CON" / "EVD"
    include_reference include REF-* entries (off by default: they are data, not rules)
    max_distance      drop hits weaker than this cosine distance (~0.6 is a sensible cut)
    """
    coll = build_store()
    if doc:
        where = {"doc": doc}
    elif not include_reference:
        where = {"doc": {"$nin": sorted(REFERENCE_DOCS)}}
    else:
        where = None
    res = coll.query(query_texts=[query], n_results=min(k, coll.count()), where=where)
    hits = [{"clause_id": m["clause_id"], "doc": m["doc"], "outcome": m["outcome"],
             "distance": round(d, 3), "text": t}
            for m, d, t in zip(res["metadatas"][0], res["distances"][0], res["documents"][0])]
    if max_distance is not None:
        hits = [h for h in hits if h["distance"] <= max_distance]
    return hits


def retrieve_facets(facets: dict[str, str], k: int = 3, **kw) -> list[dict]:
    """One query per fact cluster, results unioned; each hit records which facet found it.

    facets = {"charges": "...", "accounts": "...", "evidence": "..."}
    A clause found by several facets keeps its best (smallest) distance.
    """
    found: dict[str, dict] = {}
    for facet, q in facets.items():
        for h in retrieve(q, k=k, **kw):
            cid = h["clause_id"]
            if cid not in found or h["distance"] < found[cid]["distance"]:
                found[cid] = {**h, "facet": facet}
    return sorted(found.values(), key=lambda h: h["distance"])


# ------------------------------------------------------------- self-test ----
if __name__ == "__main__":
    coll = build_store()
    print(f"store: {coll.count()} clauses from {POLICY_DIR}")

    TESTS = [
        ("SEC-01", "Company has two outstanding charges held by Interbay Funding Limited, neither satisfied"),
        ("CON-01", "Accounts for the period ending 2024-06-30 were filed on 2025-12-17, months after the statutory deadline"),
        ("CON-04", "The company files micro-entity accounts which do not disclose turnover or profit"),
        ("SEC-02", "No charges have ever been registered against this company"),
        ("EVD-04", "The brief wants to state what the charge is secured against but particulars were not retrieved"),
        ("CON-05", "Company incorporated in 2017. No accounts filing appears anywhere in the record."),
        ("SEC-06", "Two charges created on the same day in favour of the same lender"),
        ("CON-07", "A liquidation filing appears in the company's history"),
    ]
    ranks = []
    for expect, q in TESTS:
        ids = [h["clause_id"] for h in retrieve(q, k=8)]
        rank = ids.index(expect) + 1 if expect in ids else None
        ranks.append(rank)
        print(f'{"OK " if rank == 1 else "   "} expect {expect:8} rank {str(rank) if rank else ">8":4}  top3: {", ".join(ids[:3])}')
    hit = lambda n: sum(1 for r in ranks if r and r <= n) / len(ranks)
    print(f"\nrecall@1 {hit(1):.2f}   recall@3 {hit(3):.2f}   recall@5 {hit(5):.2f}   recall@8 {hit(8):.2f}")

    print("\nfaceted:")
    for h in retrieve_facets({
        "charges":  "two charges outstanding in favour of a third-party lender, neither satisfied",
        "accounts": "micro-entity accounts filed 8 months after the statutory deadline",
        "evidence": "is there enough evidence to make a recommendation",
    }):
        print(f'  {h["clause_id"]:8} d={h["distance"]:.3f}  via {h["facet"]:9} {h["outcome"][:30]}')

    print("\nlookup by id:", get_clause("SEC-01")["title"])
