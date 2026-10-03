"""Account for every run in one RM Copilot batch trace — the count the LangSmith page doesn't show.

    python genai/trace_audit.py client/output/web_2026-09-30_144205     # a web run folder (reads run.json)
    python genai/trace_audit.py "<trace URL from the app's Open in LangSmith button>"

A batch trace has a fixed shape, so its run count is predictable:

    1 batch  +  1 graph run per company per pass  +  5 nodes per graph run  +  4 tool calls per research
    +  1 routing call per supervisor  +  however many Claude calls were made

The script sorts every run into those buckets by its position in the tree (not by name), so a run that
fits none of them — something you didn't expect to run — is listed as "other" instead of hiding in a total.
It also checks that the batch's cost equals the sum of its Claude calls.
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

ROOT = Path(__file__).resolve().parents[1]


def trace_url(arg: str) -> str:
    """A trace URL, or a run folder whose run.json holds one (the latest attempt)."""
    if arg.startswith("http"):
        return arg
    attempts = json.loads((Path(arg) / "run.json").read_text()).get("agent", {}).get("attempts", [])
    urls = [a["trace_url"] for a in attempts if a.get("trace_url")]
    if not urls:
        sys.exit(f"{arg}: no trace URL in run.json (tracing was off, or the run predates one-trace-per-batch)")
    return urls[-1]


def runs_of(url: str) -> list:
    trace = re.search(r"/trace/([0-9a-f-]{36})", url).group(1)
    project = re.search(r"/projects/p/([0-9a-f-]{36})", url)
    with warnings.catch_warnings():
        # list_runs is deprecated in favour of client.runs.query (a new API with a different result shape)
        # and is supported until 31 Jan 2027 — move to runs.query before then
        warnings.simplefilter("ignore", DeprecationWarning)
        return list(Client().list_runs(trace_id=trace, **({"project_id": project.group(1)} if project else {})))


def audit(runs: list) -> None:
    by_id = {r.id: r for r in runs}
    parent = lambda r: by_id.get(r.parent_run_id)
    root = next(r for r in runs if r.parent_run_id is None)
    graphs = [r for r in runs if r.parent_run_id == root.id]
    graph_ids = {g.id for g in graphs}
    nodes = [r for r in runs if r.parent_run_id in graph_ids]
    bucket = {root.id: "batch", **{g.id: "graph run" for g in graphs}, **{n.id: "node" for n in nodes}}
    for r in runs:
        if r.id in bucket:
            continue
        p = parent(r)
        bucket[r.id] = ("tool call" if r.run_type == "tool" else
                        "Claude call" if r.run_type == "llm" else
                        "routing call" if p is not None and p.name == "supervisor" else "other")
    n = Counter(bucket.values())

    passes = Counter("summary" if "summary" in (g.tags or []) else "screen" for g in graphs)
    print(f"{root.name}\n{len(runs)} runs in this trace\n")
    print(f"  company runs   {len(graphs):4d}   (screen {passes['screen']}, summary {passes['summary']}; "
          f"{len({g.name for g in graphs})} companies)")
    print("  by run type    " + " · ".join(f"{t} {c}" for t, c in Counter(r.run_type for r in runs).most_common()))
    print("  by name        " + " · ".join(f"{name} {c}" for name, c in
                                          Counter(r.name for r in runs if bucket[r.id] in ("node", "tool call",
                                                                                          "Claude call", "routing call"))
                                          .most_common()))

    order = ["batch", "graph run", "node", "tool call", "routing call", "Claude call", "other"]
    parts = " + ".join(f"{n[b]} {b}{'s' if n[b] != 1 and b != 'other' else ''}" for b in order if n[b])
    print(f"\n  accounting     {parts} = {sum(n.values())}  {'✓' if sum(n.values()) == len(runs) else '✗'}")
    expected = {"node": 5 * len(graphs), "routing call": len(graphs)}
    for b, want in expected.items():
        if n[b] != want:
            print(f"  ✗ expected {want} {b}s (one set per graph run), found {n[b]}")
    research = [r for r in nodes if r.name == "research"]
    if n["tool call"] != 4 * len(research):
        print(f"  ✗ expected {4 * len(research)} tool calls (4 per research pass), found {n['tool call']}")
    if len(research) > len(graphs):
        print(f"  ↻ research ran {len(research) - len(graphs)} extra time(s): the supervisor's retry loop fired")
    for r in runs:
        if bucket[r.id] == "other":
            print(f"  ? other: {r.run_type} '{r.name}' under '{parent(r).name if parent(r) else '-'}'")

    calls = [r for r in runs if bucket[r.id] == "Claude call"]
    total = sum(float(r.total_cost or 0) for r in calls)
    same = abs(total - float(root.total_cost or 0)) < 1e-6
    print(f"\n  cost           batch ${float(root.total_cost or 0):.4f} {'=' if same else '≠'} "
          f"sum of {len(calls)} Claude calls ${total:.4f}  {'✓' if same else '✗'}")
    for name, c in Counter(r.name for r in calls).most_common():
        cost = sum(float(r.total_cost or 0) for r in calls if r.name == name)
        print(f"                 {name}: {c} call(s), ${cost:.4f}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    load_dotenv(ROOT / ".env")
    audit(runs_of(trace_url(sys.argv[1])))
