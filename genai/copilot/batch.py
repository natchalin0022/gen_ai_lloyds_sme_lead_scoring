"""Run the graph over a lead list: screen every lead, then summarise the top prospects.

Shared by AGENTIC_AI_ASSISTANT.ipynb and the web app (webapp/), so both run the same logic:

    prefetch(numbers)                  Companies House records into the MCP cache, one call at a time
    screen_and_summarise(graph, ...)   pass 1: every lead, draft_brief=False (decision + code-written brief)
                                       pass 2: the N highest-ranked leads that PASSED screening — decision
                                       PROCEED or REFER, not an existing customer — draft_brief=True
                                       (+ model-written summary)

Every company's final state is saved to <out_dir>/states/<company>.json and its brief to
<out_dir>/briefs/<rank>_<company>.md. A saved state is reused when it covers what's asked for
(see `reusable`), so a re-run resumes instead of re-paying.

Observability: the whole batch is ONE LangSmith trace ("rm-copilot run · <list>"). Every company's graph
run nests inside it, and every Claude call inside those (llm.parse), each carrying its tokens and cost,
so the trace's total is the batch's cost. Its URL is returned in `info["trace_url"]`.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

from langsmith import trace

from . import llm
from .brief import rerender
from .supervisor import live_group_charges

GENAI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GENAI))                                   # for mcp_ch
from mcp_ch import tools as ch_tools  # noqa: E402

Progress = Callable[[str, int, int], None]      # (stage, done, total)


def policy_versions(policy_dir: Path = GENAI / "policies") -> dict[str, str]:
    """{'CON': 'v1.1', ...} from each policy document's header line."""
    return {p.stem.split("_")[0]: re.search(r"v\d+\.\d+", p.read_text()).group()
            for p in sorted(policy_dir.glob("*.md"))}


# ----------------------------------------------------------- the MCP cache ----
FETCH = (ch_tools.get_company_profile, ch_tools.get_filing_history, ch_tools.get_charges, ch_tools.get_officers)


def prefetch(numbers: list[str], on_progress: Progress | None = None) -> dict:
    """Fetch every record the research node will ask for, one call at a time at the API's pace.

    The research node calls four tools at once in four MCP server processes, each pacing itself
    independently; across a batch that could exceed Companies House's 600 calls per 5 minutes. Filling
    the cache first, sequentially, means every MCP call in the batch is answered from disk.
    """
    t0, failed = time.time(), {}
    for i, n in enumerate(numbers, 1):
        for f in FETCH:
            try:
                f(n)
            except Exception as e:                      # recorded, not raised: research records it too
                failed[f"{n} {f.__name__}"] = repr(e)[:120]
        if on_progress:
            on_progress("fetch", i, len(numbers))
    return {"seconds": round(time.time() - t0, 1), "failed": failed}


# ------------------------------------------------------------- one company ----
def reusable(saved: dict, draft_brief: bool, policy: dict) -> bool:
    """A saved state covers a request if it finished, under the same policy, and — when a summary is
    asked for — has one written by the current summary model (older runs used Opus for everything)."""
    run = saved.get("_run", {})
    if "error" in saved or run.get("policy") != policy:
        return False
    if not draft_brief:                                  # screening: any finished run has the decision
        return True
    return run.get("draft_brief", True) and run.get("summary_model", "claude-opus-5") == llm.BRIEF_MODEL


def _current(st: dict, path: Path, out_dir: Path, rank: int, lead: dict | None, force_write: bool = False) -> dict:
    """A reused state with its brief rebuilt by the current code (free: no model call), saved if it changed —
    so a resumed run never serves a brief in an older format."""
    if "error" not in st and st.get("brief"):
        new = rerender(st, lead)
        if new != st["brief"] or force_write:
            st = {**st, "brief": new, **({"lead": lead} if lead else {})}
            path.write_text(json.dumps(st, indent=1, default=str))
            (out_dir / "briefs" / f"{rank:03d}_{st['company_number']}.md").write_text(new)
    return st


SUMMARISE = {"PROCEED", "REFER"}      # DECLINE and INSUFFICIENT EVIDENCE never use up a summary


def worth_summarising(st: dict) -> bool:
    """Chosen AFTER screening: finished, not an existing customer, decision PROCEED or REFER."""
    return ("error" not in st and not live_group_charges(st)
            and st.get("outcome", {}).get("decision") in SUMMARISE)


async def assess(graph, n: str, rank: int, out_dir: Path, as_of: str, draft_brief: bool,
                 sem: asyncio.Semaphore, policy: dict, lead_list: str = "",
                 force: bool = False, fresh: set | None = None, lead: dict | None = None) -> dict:
    path = out_dir / "states" / f"{n}.json"
    fresh = set() if fresh is None else fresh
    saved = json.loads(path.read_text()) if path.exists() else None
    if saved and (not force or n in fresh) and reusable(saved, draft_brief, policy):
        return _current(saved, path, out_dir, rank, lead)
    async with sem:
        token, t0 = llm.CURRENT.set(n), time.time()
        try:
            st = await graph.ainvoke(
                {"company_number": n, "as_of": as_of, "draft_brief": draft_brief,
                 **({"lead": lead} if lead else {})},
                config={"run_name": f"rm-copilot {n}",
                        "metadata": {"company_number": n, "lead_rank": rank, "lead_list": lead_list,
                                     "draft_brief": draft_brief},
                        "tags": ["agentic-assistant", f"leads-{lead_list}", "summary" if draft_brief else "screen"]})
        except Exception as e:                                       # one bad company never stops the batch
            st = {"company_number": n, "error": repr(e)[:300]}
        finally:
            llm.CURRENT.reset(token)
        st["_run"] = {"seconds": round(time.time() - t0, 1), "policy": policy, "draft_brief": draft_brief,
                      "summary_model": llm.BRIEF_MODEL if draft_brief else None,
                      "usage": [u for u in llm.USAGE if u["company"] == n]}
    if "error" in st and draft_brief and saved and reusable(saved, False, policy):
        # a failed summary never erases a finished screening: keep the decision, note the failure
        # (the summary is retried on the next run, because the saved state still has none)
        saved["_run"]["summary_error"] = st["error"]
        saved["_run"]["usage"] = saved["_run"].get("usage", []) + st["_run"]["usage"]   # billed calls still count
        st = _current(saved, path, out_dir, rank, lead, force_write=True)
    fresh.add(n)
    path.write_text(json.dumps(st, indent=1, default=str))
    if st.get("brief"):
        (out_dir / "briefs" / f"{rank:03d}_{n}.md").write_text(st["brief"])
    return st


# ------------------------------------------------------------- the batch ----
async def screen_and_summarise(graph, leads: list[tuple[str, int]], out_dir: Path, as_of: str, top_n: int,
                               concurrency: int = 4, force: bool = False, lead_list: str = "",
                               on_progress: Progress | None = None, context: dict[str, dict] | None = None,
                               info: dict | None = None) -> tuple[dict[str, dict], list[str]]:
    """`leads` is [(company_number, rank)] in rank order; `context` optionally gives each company's scoring
    facts ({"rank", "of", "score", "why"}) for the brief's "why this lead". Returns (states by company,
    the summarised ones); `info`, if given, receives the trace URL and this batch's cost."""
    for d in ("states", "briefs"):
        (out_dir / d).mkdir(parents=True, exist_ok=True)
    policy, sem, fresh = policy_versions(), asyncio.Semaphore(concurrency), set()
    rank, context, first_call = dict(leads), context or {}, len(llm.USAGE)

    async def run(stage: str, numbers: list[str], draft_brief: bool) -> dict[str, dict]:
        done, out = 0, {}

        async def one(n):
            nonlocal done
            out[n] = await assess(graph, n, rank[n], out_dir, as_of, draft_brief, sem, policy,
                                  lead_list, force, fresh, context.get(n))
            done += 1
            if on_progress:
                on_progress(stage, done, len(numbers))
        await asyncio.gather(*(one(n) for n in numbers))
        return out

    async with trace(f"rm-copilot run · {lead_list or out_dir.name}", run_type="chain",
                     inputs={"companies": len(leads), "summaries_for_top": top_n, "as_of": as_of},
                     metadata={"lead_list": lead_list, "policy": policy, "summary_model": llm.BRIEF_MODEL},
                     tags=["agentic-assistant", "batch"]) as root:
        states = await run("screen", [n for n, _ in leads], draft_brief=False)         # pass 1
        top = [n for n, _ in leads if worth_summarising(states[n])][:top_n]      # in the score's order
        states.update(await run("summarise", top, draft_brief=True))                  # pass 2
        calls = llm.USAGE[first_call:]                    # the calls THIS batch made (reused states made none)
        cost = round(sum(u["cost_usd"] or 0 for u in calls), 4)
        root.set(outputs={"screened": len(states), "summarised": len(top), "model_calls": len(calls),
                          "errors": sum("error" in s for s in states.values()),
                          "summary_failures": sum(bool(s.get("_run", {}).get("summary_error")) for s in states.values()),
                          "cost_usd": cost})
    if info is not None:
        try:
            url = root.get_url()
        except Exception:                                  # tracing off, or LangSmith unreachable
            url = None
        info.update(trace_url=url, cost_usd=cost, model_calls=len(calls))
    return states, top
