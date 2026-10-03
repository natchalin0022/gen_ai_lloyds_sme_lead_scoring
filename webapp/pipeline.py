"""Client run: company numbers in → the lead-scoring notebooks → ranked list → the RM Copilot agent.

    run = new_run(numbers)          a folder client/output/web_<timestamp>/ holding input.csv
    score(run)                      1_CompaniesHouse (cells tagged client-path: Stages 0, 1, 3, 4b)
                                    → 2_GDELT (Part 5: postcode → region) → 5_score (all of it)
    screen(run, top_n)              the agent: screen every scored lead, summarise the top prospects

The notebooks run as they are, headless, so the website uses the model's own feature code: there is
no second copy to drift from the training table. Each executed notebook is saved in the run's logs/
folder, the audit record of how that list was made. Nothing here queries BigQuery: the GDELT media
index is read from its saved file (see MEDIA_INDEX's latest week in the run's manifest).

Isolation: runs read and write a working copy of the company tables (client/live_data/, through
LLOYDS_LIVE_DATA in paths.py), never the tracked snapshot the model was trained on. One run at a time.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import fcntl
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import nbformat
import pandas as pd
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "genai"))
import paths  # noqa: E402  (imported WITHOUT LLOYDS_LIVE_DATA: these are the tracked master paths)

LIVE = ROOT / "client" / "live_data"
RUNS = paths.CLIENT_OUTPUT
KERNEL = "lloyds-genai"
COMPANY_NUMBER = re.compile(r"^(\d{8}|[A-Z]{2}\d{6})$")     # 01234567, SC123456, NI…, OC…

STAGES = [   # (label, notebook, cell tag or None for every cell)
    ("Companies House pull", "1_CompaniesHouse.ipynb", "client-path"),
    ("Region from postcode", "2_GDELT.ipynb", "client-path"),
    ("Score and rank", "5_score.ipynb", None),
]

Progress = Callable[[str, int, int], None]


class PipelineError(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


@dataclass
class Run:
    dir: Path

    @property
    def info(self) -> dict:
        return json.loads((self.dir / "run.json").read_text())

    def update(self, **kw) -> None:
        (self.dir / "run.json").write_text(json.dumps({**self.info, **kw}, indent=1, default=str))

    @property
    def leads(self) -> pd.DataFrame:
        return pd.read_csv(self.dir / "leads.csv", dtype={"com_num": str})

    @property
    def not_scored(self) -> pd.DataFrame:
        f = self.dir / "not_found.csv"
        return pd.read_csv(f, dtype={"com_num": str}) if f.exists() else pd.DataFrame(columns=["com_num", "reason"])


# ----------------------------------------------------------------- input ----
def normalise(raw: list[str]) -> tuple[list[str], list[str]]:
    """(valid unique company numbers in input order, rejected entries). Leading zeros are restored,
    as 5_score does: Excel strips them, and UK company numbers are always 8 characters."""
    good, bad, seen = [], [], set()
    for x in raw:
        n = str(x).strip().upper()
        if not n or n.lower() == "nan":
            continue
        n = n.zfill(8) if n.isdigit() else n
        if not COMPANY_NUMBER.match(n):
            bad.append(str(x).strip())
        elif n not in seen:
            seen.add(n)
            good.append(n)
    return good, bad


def new_run(numbers: list[str], asof: str | None = None) -> Run:
    """A run folder with the input list. `asof` (YYYY-MM-DD) reproduces a past list; default today."""
    d = RUNS / f"web_{dt.datetime.now():%Y-%m-%d_%H%M%S}"
    d.mkdir(parents=True)
    pd.DataFrame({"company_number": numbers}).to_csv(d / "input.csv", index=False)
    (d / "run.json").write_text(json.dumps({
        "created": dt.datetime.now().isoformat(timespec="seconds"), "companies": len(numbers),
        "asof": asof or dt.date.today().isoformat(), "stages": {}}, indent=1))
    return Run(d)


# ------------------------------------------------------------- live data ----
def ensure_live_data() -> None:
    """First use: copy the tracked tables into client/live_data/. Later runs build on that copy."""
    data = LIVE / "company_data"
    if (data / "companies.csv.gz").exists():
        return
    data.mkdir(parents=True, exist_ok=True)
    (LIVE / "company_info_json").mkdir(exist_ok=True)
    for src in (paths.COMPANIES_CSV, paths.CHARGES_CSV):
        shutil.copy2(src, data / src.name)


class _Lock:
    """One run at a time: the pipeline rewrites shared tables. Released if the process dies."""
    def __enter__(self):
        RUNS.mkdir(parents=True, exist_ok=True)
        self.f = open(RUNS / ".web_run.lock", "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.f.close()
            raise Busy("another client run is in progress")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ------------------------------------------------------------- notebooks ----
def _error_text(nb) -> str:
    for c in nb.cells:
        for o in c.get("outputs", []):
            if o.get("output_type") == "error":
                return f"{o['ename']}: {o['evalue']}"[:1500]
    return "unknown error"


def run_notebook(name: str, tag: str | None, env: dict, log: Path) -> None:
    """Execute a notebook (only cells carrying `tag`, if given) with `env` set in its kernel, and
    save the executed copy to `log` whether it succeeds or not."""
    nb = nbformat.read(ROOT / name, as_version=4)
    if tag:
        nb.cells = [c for c in nb.cells if tag in c.get("metadata", {}).get("tags", [])]
        if not nb.cells:
            raise PipelineError(f"{name} has no cells tagged {tag!r}")
    setup = "import os\nos.environ.update(" + json.dumps(env, indent=1) + ")"
    nb.cells.insert(0, nbformat.v4.new_code_cell(setup))
    try:
        NotebookClient(nb, kernel_name=KERNEL, timeout=3600, resources={"metadata": {"path": str(ROOT)}}).execute()
    except CellExecutionError:
        raise PipelineError(f"{name} failed — {_error_text(nb)}")
    finally:
        log.parent.mkdir(exist_ok=True)
        nbformat.write(nb, log)


def score(run: Run, on_progress: Progress | None = None) -> Run:
    """The lead-scoring pipeline for this run's companies: pull what isn't held, then score."""
    info = run.info
    env = {"LLOYDS_LIVE_DATA": str(LIVE), "CLIENT_RUN_FILE": str(run.dir / "input.csv"),
           "CLIENT_RUN_DIR": str(run.dir), "CLIENT_RUN_ASOF": info["asof"],
           "CLIENT_RUN_TOP_K": str(info["companies"])}
    with _Lock():
        ensure_live_data()
        stages = {}
        for i, (label, name, tag) in enumerate(STAGES):
            if on_progress:
                on_progress(label, i, len(STAGES))
            t0 = time.time()
            try:
                run_notebook(name, tag, env, run.dir / "logs" / name)
            except PipelineError as e:
                stages[label] = {"status": "failed", "error": str(e)}
                run.update(stages=stages)
                raise
            stages[label] = {"status": "done", "seconds": round(time.time() - t0, 1)}
            run.update(stages=stages)
        if on_progress:
            on_progress("done", len(STAGES), len(STAGES))
    return run


# ------------------------------------------------------------- run facts ----
def held_numbers() -> set[str]:
    """Company numbers the pipeline already holds (the live copy once it exists): no pull needed."""
    table = LIVE / "company_data" / "companies.csv.gz"
    table = table if table.exists() else paths.COMPANIES_CSV
    return set(pd.read_csv(table, dtype=str, usecols=["com_num"])["com_num"])


def data_facts() -> dict:
    """What a list is scored against: the model, the media index's latest week, the policy, the models."""
    from copilot import batch, llm
    manifest = json.loads(paths.MODEL_MANIFEST.read_text())
    media = pd.read_parquet(paths.MEDIA_INDEX, columns=["week"])["week"].max()
    return {"model": f"{manifest['model']} · trained {manifest['trained_at'][:10]}",
            "media_week": pd.Timestamp(media).date().isoformat(),
            "policy": ", ".join(f"{d} {v}" for d, v in batch.policy_versions().items()),
            "reader": llm.MODEL, "writer": llm.BRIEF_MODEL}


def runs() -> list[Run]:
    return [Run(d) for d in sorted(RUNS.glob("web_*"), reverse=True) if (d / "run.json").exists()]


def results(run: Run) -> pd.DataFrame:
    """One row per scored lead: the pipeline's score next to the agent's decision."""
    from copilot.brief import at_a_glance, rerender
    from copilot.policy import CLAUSE, level
    from copilot.supervisor import live_group_charges
    from copilot import llm

    def cost(u):
        price = next((v for k, v in llm.PRICE_PER_MTOK.items() if u["model"].startswith(k)), (0, 0))
        return (u["input_tokens"] * price[0] + u["output_tokens"] * price[1]) / 1e6

    rows, leads = [], run.leads.sort_values("rank")
    for l in leads.itertuples():
        f = run.dir / "agent" / "states" / f"{l.com_num}.json"
        st = json.loads(f.read_text()) if f.exists() else {}
        o, err = st.get("outcome") or {}, st.get("error")
        applicable = [a["clause_id"] for a in st.get("applicable", [])]
        if not st:
            decision = "Not screened"
        elif err:
            decision = "Error"
        elif live_group_charges(st):
            decision = "Existing customer"
        else:
            decision = o["decision"] + (", gaps open" if st.get("qualifies") is None else "")
        rows.append({
            "rank": l.rank, "company_number": l.com_num, "name": l.name, "sector": l.sector, "region": l.region,
            "lead_score": round(l.gated, 3), "model_score": round(l.score, 4), "decision": decision,
            "before_offer": ", ".join(o.get("conditions", [])),
            "refer_reasons": ", ".join(c for c in applicable if level(CLAUSE[c]["outcome"]) == "REFER"),
            "gaps": ", ".join(sorted({g.split(" ")[0] for g in st.get("evidence_gap", [])})),
            "summary": bool(st.get("brief")) and st.get("_run", {}).get("draft_brief", True),
            "why": l.why if isinstance(getattr(l, "why", None), str) else "",
            "glance": at_a_glance(st, {"rank": l.rank, "of": len(leads), "score": l.score,
                                       "why": getattr(l, "why", "") if isinstance(getattr(l, "why", None), str) else ""})
                      if st and not err else [],
            # rebuilt with the current code, so a run made before a format change reads the same as a new one
            "brief": rerender(st, {"rank": l.rank, "of": len(leads), "score": l.score,
                                   "why": getattr(l, "why", "") if isinstance(getattr(l, "why", None), str) else ""})
                     if st and not err and st.get("brief") else "",
            "error": err or "",
            "summary_error": st.get("_run", {}).get("summary_error") or "",
            "cost_usd": sum(cost(u) for u in st.get("_run", {}).get("usage", [])),
        })
    return pd.DataFrame(rows)


def state(run: Run, company_number: str) -> dict:
    return json.loads((run.dir / "agent" / "states" / f"{company_number}.json").read_text())


def questions(run: Run, company_number: str | None = None) -> list[dict]:
    """Every question asked in this run (or about one company), oldest first — from agent/questions.jsonl."""
    f = run.dir / "agent" / "questions.jsonl"
    rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []
    return [r for r in rows if company_number is None or r["company"] == company_number]


def questions_markdown(entries: list[dict]) -> str:
    """The Q&A as a section to append under a brief."""
    out = ["## Questions asked", ""]
    for q in entries:
        out += [f"**Q ({q['at'][:16].replace('T', ' ')}):** {q['question']}  ",
                (f"**A:** {q['answer']}  \n*Sources: {', '.join(q['sources'])}*" if q.get("ok")
                 else f"*No answer shown: {q.get('why_not', '')}*"), ""]
    return "\n".join(out)


def lead_facts(run: Run, company_number: str) -> dict | None:
    """How the scoring model (5_score) saw this lead, from leads.csv, in words the Q&A model can cite."""
    leads = run.leads
    row = leads[leads["com_num"] == company_number]
    if row.empty:
        return None
    l = row.iloc[0]
    num = lambda v, d=3: None if pd.isna(v) else round(float(v), d)
    return {"rank": int(l["rank"]), "of": len(leads), "model_score": num(l["score"]),
            "why_ranked": l["why"] if isinstance(l.get("why"), str) else "",
            "sector": l["sector"], "region": l["region"], "accounts_type": l["account_type"],
            "age_years": num(l.get("age_years"), 1),
            "charges_from_other_lenders": None if pd.isna(l.get("nonlloyds_charges")) else int(l["nonlloyds_charges"]),
            "sector_region_news_volume_z": num(l.get("vol_z")),
            "rank_on_model_score_alone": None if pd.isna(l.get("rank_model_only")) else int(l["rank_model_only"]),
            "places_moved_by_news_volume": None if pd.isna(l.get("move")) else int(l["move"])}


def ask(run: Run, company_number: str, question: str) -> dict:
    """"Ask about this lead" (copilot/ask.py: RAG over the policy + this company's record). Every question
    and answer is logged to agent/questions.jsonl in the run folder, with what it cost."""
    from copilot import llm
    from copilot.ask import ask as rag_ask

    llm.VERBOSE = False
    first, token = len(llm.USAGE), llm.CURRENT.set(company_number)
    try:
        out = asyncio.run(rag_ask(question, state(run, company_number), lead_facts(run, company_number)))
    finally:
        llm.CURRENT.reset(token)
    out["cost_usd"] = round(sum(u["cost_usd"] or 0 for u in llm.USAGE[first:]), 4)
    with open(run.dir / "agent" / "questions.jsonl", "a") as f:
        f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "company": company_number,
                            "question": question, **out}, default=str) + "\n")
    return out


# ------------------------------------------------------------------ agent ----
def screen(run: Run, top_n: int = 20, concurrency: int = 4, on_progress: Progress | None = None) -> dict:
    """The RM Copilot over this run's scored leads. Returns {company: final state}."""
    from copilot import batch, llm
    from copilot.graph import build
    from copilot.research import open_tools

    llm.VERBOSE = False
    leads = run.leads.sort_values("rank")
    pairs = [(n, int(r)) for n, r in zip(leads["com_num"], leads["rank"])]
    context = {l.com_num: {"rank": int(l.rank), "of": len(leads), "score": round(float(l.score), 3),
                           "why": getattr(l, "why", "") if isinstance(getattr(l, "why", ""), str) else ""}
               for l in leads.itertuples()}                   # the scoring model's view, for "why this lead"
    t0 = time.time()
    fetched = batch.prefetch([n for n, _ in pairs], on_progress)

    info = {}

    async def go():
        async with open_tools(ROOT / "genai") as tools:          # one MCP server process for the whole batch
            return await batch.screen_and_summarise(build(tools), pairs, run.dir / "agent",
                                                    dt.date.today().isoformat(), top_n, concurrency,
                                                    lead_list=run.dir.name, on_progress=on_progress,
                                                    context=context, info=info)
    states, top = asyncio.run(go())
    before = run.info.get("agent", {})
    run.update(agent={"seconds": round(time.time() - t0, 1), "summarised": top, "top_n": top_n,
                      "fetch_failures": len(fetched["failed"]),
                      "errors": sum("error" in s for s in states.values()),
                      "summary_model": llm.BRIEF_MODEL, "policy": batch.policy_versions(),
                      # one entry per attempt on this run (a retry adds one): its LangSmith trace and cost
                      "attempts": before.get("attempts", []) + [{
                          "at": dt.datetime.now().isoformat(timespec="seconds"), "trace_url": info.get("trace_url"),
                          "cost_usd": info.get("cost_usd"), "model_calls": info.get("model_calls")}]})
    return states
