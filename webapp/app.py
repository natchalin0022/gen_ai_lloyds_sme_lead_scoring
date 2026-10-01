"""RM Lead Copilot — company numbers in, a ranked and policy-screened lead list out.

    .venv/bin/streamlit run webapp/app.py --server.address 127.0.0.1

Local only: the page holds the Companies House and Anthropic keys (from .env), and every run with
summaries spends Anthropic credit. All work is done by webapp/pipeline.py; this file is the page.
"""
from __future__ import annotations

import io
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402

load_dotenv(pipeline.ROOT / ".env")

MAX_COMPANIES = 300
COST_PER_SUMMARY = 0.025      # Sonnet summary + Opus collateral reading, from the 2026-09 runs
SECONDS_PER_NEW = 8           # pipeline pull + agent fetch, per company not yet held

DECISION_COLOUR = {"PROCEED": "#0a7d0a", "REFER": "#a86800", "DECLINE": "#c02f2f",
                   "INSUFFICIENT EVIDENCE": "#4a3aa7", "Existing customer": "#6b6a65", "Error": "#c02f2f"}

st.set_page_config(page_title="RM Lead Copilot", layout="wide")


@st.cache_data(ttl=600, show_spinner=False)
def held() -> set[str]:
    return pipeline.held_numbers()


@st.cache_data(ttl=600, show_spinner=False)
def facts() -> dict:
    return pipeline.data_facts()


def parse(text: str, upload) -> list[str]:
    raw = [t for t in re.split(r"[\s,;]+", text or "") if t]
    if upload is not None:
        df = pd.read_csv(upload, dtype=str)
        col = next((c for c in df.columns if c.lower().strip() in ("company_number", "com_num", "company number")),
                   df.columns[0])
        raw += df[col].dropna().tolist()
    return raw


def colour(v: str) -> str:
    return f"color: {DECISION_COLOUR.get(v.split(',')[0], '#0b0b0b')}; font-weight: 600"


# ---------------------------------------------------------------- sidebar ----
with st.sidebar:
    st.header("Settings")
    top_n = st.number_input("Model-written summaries for the top N leads that pass screening", 0, 50, 20,
                            help="Every lead is screened first and gets a code-written brief (decision, "
                                 "conditions, clauses). Then the N highest-ranked leads whose decision is "
                                 "PROCEED or REFER (never a DECLINE or an existing customer) also get a "
                                 f"plain-English summary from {facts()['writer']}. That summary is the only "
                                 "part that costs money.")
    st.divider()
    st.subheader("Previous runs")
    past = pipeline.runs()
    labels = {f"{r.dir.name[4:]} · {r.info['companies']} companies": r for r in past}
    pick = st.selectbox("Open a run", ["—"] + list(labels), label_visibility="collapsed")
    if pick != "—" and st.button("Open", width="stretch"):
        st.session_state.run_dir = str(labels[pick].dir)
    st.divider()
    f = facts()
    st.caption(f"**Scoring model** {f['model']}  \n**News index** latest week {f['media_week']}  \n"
               f"**Lending policy** {f['policy']}  \n**Models** {f['reader']} reads · {f['writer']} writes")

# ----------------------------------------------------------------- input ----
st.title("RM Lead Copilot")
st.write("Paste or upload UK company numbers. The lead-scoring pipeline pulls what it doesn't hold from "
         "Companies House and ranks them; the agent then checks every lead against the lending policy and "
         "writes a brief for each.")

c1, c2 = st.columns([3, 2])
text = c1.text_area("Company numbers", height=150, placeholder="12591963\nSC349083, 10941988 …",
                    help="One per line, or separated by commas or spaces. Leading zeros are restored.")
upload = c2.file_uploader("…or a CSV with a `company_number` column", type="csv")

good, bad = pipeline.normalise(parse(text, upload))
new = [n for n in good if n not in held()]
if good or bad:
    m = st.columns(4)
    m[0].metric("Companies", len(good))
    m[1].metric("Already held", len(good) - len(new))
    m[2].metric("To pull", len(new))
    m[3].metric("Rejected", len(bad))
    if bad:
        st.warning("Not UK company numbers, ignored: " + ", ".join(bad[:20]) + (" …" if len(bad) > 20 else ""))
    est_min = max(1, round((len(new) * SECONDS_PER_NEW + 60) / 60))
    st.caption(f"Estimated time about {est_min} min · estimated cost up to "
               f"${min(top_n, len(good)) * COST_PER_SUMMARY:.2f} (summaries only; screening is almost free)")

too_many = len(good) > MAX_COMPANIES
if too_many:
    st.error(f"Up to {MAX_COMPANIES} companies per run.")

if st.button("Run", type="primary", disabled=not good or too_many):
    with st.status("Running the pipeline…", expanded=True) as status:
        bar = st.progress(0.0)
        span = {"score": (0.0, 0.35), "fetch": (0.35, 0.55), "screen": (0.55, 0.8), "summarise": (0.8, 1.0)}

        def progress(phase: str):
            lo, hi = span[phase]
            return lambda stage, i, n: bar.progress(lo + (hi - lo) * i / max(n, 1),
                                                    text=f"{stage} · {i}/{n}" if phase != "score" else stage)

        t0 = time.time()
        try:
            run = pipeline.new_run(good)
            st.write(f"Scoring {len(good)} companies with the lead-scoring notebooks…")
            pipeline.score(run, on_progress=progress("score"))
            n_leads = len(run.leads)
            st.write(f"{n_leads} scored, {len(run.not_scored)} not scored. Screening with the agent…")
            screen_progress = {k: progress(k) for k in ("fetch", "screen", "summarise")}
            pipeline.screen(run, top_n=top_n, on_progress=lambda s, i, n: screen_progress[s](s, i, n))
            bar.progress(1.0, text="done")
            status.update(label=f"Done in {time.time() - t0:.0f} s", state="complete", expanded=False)
            st.session_state.run_dir = str(run.dir)
            held.clear()
        except pipeline.Busy:
            status.update(label="Another run is in progress — try again when it finishes.", state="error")
        except pipeline.PipelineError as e:
            status.update(label="The pipeline stopped", state="error")
            st.error(f"{e}\n\nThe executed notebooks are saved in `{run.dir.relative_to(pipeline.ROOT)}/logs/`.")

# --------------------------------------------------------------- results ----
if "run_dir" not in st.session_state:
    st.stop()

run = pipeline.Run(Path(st.session_state.run_dir))
info = run.info
if not (run.dir / "leads.csv").exists():
    st.info("This run did not finish scoring.")
    st.stop()

res = pipeline.results(run)
st.divider()
st.subheader(f"Results · {run.dir.name[4:]}")
st.caption(f"{info['companies']} companies submitted · features as of {info['asof']} · "
           f"policy {', '.join(f'{d} {v}' for d, v in info.get('agent', {}).get('policy', {}).items())}")

base = res["decision"].str.split(",").str[0]
m = st.columns(6)
m[0].metric("Scored", len(res))
m[1].metric("Not scored", len(run.not_scored))
m[2].metric("Existing customers", int((base == "Existing customer").sum()))
m[3].metric("PROCEED", int((base == "PROCEED").sum()))
m[4].metric("REFER", int((base == "REFER").sum()))
m[5].metric("DECLINE", int((base == "DECLINE").sum()))

failed = False
for col, what in (("error", "could not be screened"),
                  ("summary_error", "have no model-written summary (their 3-point summary is still there)")):
    bad_rows = res[res[col] != ""]
    if len(bad_rows):
        failed, msg, k = True, bad_rows[col].iloc[0], len(bad_rows)
        why = ("the Anthropic credit balance is too low. Top up, then retry below." if "credit balance" in msg
               else msg[:200])
        st.error(f"{k} compan{'y' if k == 1 else 'ies'} {what} — {why}")
if failed and st.button("Retry the failed parts",
                        help="Re-runs the agent on this same run: finished screenings and summaries are reused, "
                             "so only what failed is redone (and paid for)."):
    with st.status("Retrying…", expanded=True) as status:
        bar = st.progress(0.0)
        try:
            pipeline.screen(run, top_n=info.get("agent", {}).get("top_n", top_n),
                            on_progress=lambda stg, i, n: bar.progress(i / max(n, 1), text=f"{stg} · {i}/{n}"))
            status.update(label="Retried", state="complete", expanded=False)
        except pipeline.Busy:
            status.update(label="Another run is in progress — try again when it finishes.", state="error")
    st.rerun()

rm_only = st.toggle("RM list only (hide existing customers and declines)", value=True)
view = res[~base.isin(["Existing customer", "DECLINE", "Error"])] if rm_only else res
cols = ["rank", "name", "model_score", "decision", "before_offer", "refer_reasons", "gaps", "summary",
        "sector", "region", "company_number"]   # what an RM acts on first; why-this-lead is in each brief
st.dataframe(
    view[cols].style.map(colour, subset=["decision"]),
    hide_index=True, width="stretch",
    column_config={
        "rank": st.column_config.NumberColumn("Rank", width="small"),
        "name": st.column_config.TextColumn("Company"),
        "company_number": st.column_config.TextColumn("Number", width="small"),
        "sector": st.column_config.TextColumn("Sector"),
        "region": st.column_config.TextColumn("Region"),
        "model_score": st.column_config.ProgressColumn(
            "Model score", min_value=0, max_value=1, format="%.3f",
            help="The scoring model's value: a ranking value, not a probability. Rank also blends in "
                 "sector × region news activity (15%)."),
        "decision": st.column_config.TextColumn("Decision"),
        "before_offer": st.column_config.TextColumn("Before an offer", help="Conditions: documents to obtain"),
        "refer_reasons": st.column_config.TextColumn("Refer reasons", help="Clauses a person must judge"),
        "gaps": st.column_config.TextColumn("Couldn't check", help="Clauses the public record can't settle"),
        "summary": st.column_config.CheckboxColumn("Model summary", width="small",
                                                   help=f"A plain-English summary from {facts()['writer']} "
                                                        "(top N leads that pass screening)"),
    })

# ---- one brief
with_brief = view[view["brief"] != ""]
if len(with_brief):
    order = with_brief.sort_values(["summary", "rank"], ascending=[False, True])
    def with_glance(r) -> str:
        """Briefs from before the 3-point summary existed get it inserted, from the same code."""
        if "## At a glance" in r.brief or not r.glance:
            return r.brief
        block = "## At a glance\n\n" + "\n".join(f"{i}. {pt}" for i, pt in enumerate(r.glance, 1)) + "\n\n"
        return r.brief.replace("## Summary", block + "## Summary", 1)

    opts = {f"#{r.rank} · {r.name} · {r.decision}" + (" · model summary" if r.summary else ""): with_glance(r)
            for r in order.itertuples()}
    number = dict(zip(opts, order["company_number"]))
    choice = st.selectbox("Read a brief", list(opts))
    failed_summary = order.set_index(pd.Index(list(opts)))["summary_error"].get(choice, "")
    if failed_summary:
        st.caption("A model-written summary was requested for this lead but failed — "
                   + ("the Anthropic credit balance is too low. " if "credit balance" in failed_summary else "")
                   + "Retry above once that's fixed; the brief below is complete without it.")
    with st.container(border=True):
        st.markdown(re.sub(r"^(#{1,2}) ", lambda m: "#" * (len(m.group(1)) + 2) + " ", opts[choice],
                           flags=re.M))                       # the brief's headings, two levels smaller

    # ---- "Ask about this lead": RAG over the lending policy + this company's record (copilot/ask.py)
    with st.form(f"ask_{number[choice]}", clear_on_submit=False):
        question = st.text_input("Ask about this lead", placeholder="Why is this a REFER?  ·  What does the "
                                 "negative pledge mean for us?  ·  What would it take to PROCEED?")
        asked = st.form_submit_button("Ask", help=f"Answered by {facts()['writer']} from the policy clauses that "
                                      "apply here plus those retrieved for your question — about $0.01")
    if asked and question.strip():
        with st.spinner("Retrieving policy clauses and answering…"):
            a = pipeline.ask(run, number[choice], question.strip())
        if "credit balance" in a["why_not"]:
            a["why_not"] = "the Anthropic credit balance is too low (the retrieval below still ran)"
        if a["ok"]:
            st.success(a["answer"])
            st.caption("Sources: " + ", ".join(a["sources"]) + f" · cost ${a.get('cost_usd', 0):.3f}")
        else:
            st.warning(f"No answer shown: {a['why_not']}.")
        if a.get("context"):
            with st.expander(f"What the answer was allowed to use ({len(a['context'])} clauses)"):
                st.dataframe(pd.DataFrame(a["context"]), hide_index=True, width="stretch")
                st.caption("Clauses that apply to this company are always included; the rest were retrieved "
                           "by meaning, one short query per topic.")

# ---- the rest
with st.expander(f"Not scored ({len(run.not_scored)})"):
    st.dataframe(run.not_scored.rename(columns={"com_num": "company_number"}), hide_index=True,
                 width="stretch")

# ---- cost and tracing
attempts = info.get("agent", {}).get("attempts", [])
cost = sum(a["cost_usd"] or 0 for a in attempts) if attempts else res["cost_usd"].sum()
calls = sum(a["model_calls"] or 0 for a in attempts) if attempts else None
st.subheader("Cost and tracing")
k1, k2, k3 = st.columns([1, 1, 2])
k1.metric("Model cost of this run", f"${cost:.2f}",
          help="Every Claude call this run made, priced at list prices by the model that answered. Calls the API "
               "rejected (e.g. for credit) are not billed, so they cost $0.")
k2.metric("Model calls", "—" if calls is None else calls)
with k3:
    traces = [a for a in attempts if a.get("trace_url")]
    if traces:
        st.write("The whole run is **one LangSmith trace**: every company inside it, every Claude call inside "
                 "those, with the total cost on top.")
        for i, a in enumerate(traces, 1):
            st.link_button(f"Open in LangSmith{f' (attempt {i})' if len(traces) > 1 else ''} · "
                           f"${a['cost_usd'] or 0:.2f}", a["trace_url"])
    else:
        st.caption("No LangSmith trace for this run (made before tracing was grouped per run, or tracing is off).")

d1, d2, d3 = st.columns(3)
export = res.drop(columns=["brief"]).assign(glance=res["glance"].map(" | ".join))
d1.download_button("Download the list (CSV)", export.to_csv(index=False),
                   file_name=f"{run.dir.name}_leads.csv", mime="text/csv")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for r in res[res["brief"] != ""].itertuples():              # the current rendering, as shown on the page
        z.writestr(f"{r.rank:03d}_{r.company_number}.md", r.brief)
d2.download_button("Download all briefs (ZIP)", buf.getvalue(), file_name=f"{run.dir.name}_briefs.zip",
                   mime="application/zip")
d3.caption(f"Run folder `{run.dir.relative_to(pipeline.ROOT)}`")
