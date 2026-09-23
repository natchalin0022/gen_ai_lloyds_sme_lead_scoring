"""Policy node (notebook 03) — check every clause in the corpus against the signals.

Code decides what the record settles; the model reads only what needs reading (and any clause
with no rule); what the record doesn't contain becomes an evidence gap. Outcome by EVD-07.

One change from the notebook: if a whole record is missing (research failed or found nothing),
the clauses that need it become evidence gaps instead of raising a KeyError.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .refs import charge_refs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # genai/, for policy_store
from policy_store import load_clauses  # noqa: E402

CLAUSES = load_clauses()
CLAUSE = {c["clause_id"]: c for c in CLAUSES}

ROUTED = {
    "CON-08": "brief",
    "EVD-02": "brief", "EVD-03": "brief", "EVD-04": "brief", "EVD-06": "brief",
    "EVD-05": "supervisor",
    "EVD-07": "this node (precedence)",
}

# which signal sections each document's rules read — a missing section makes them undeterminable
NEEDS = {"SEC": ("charges",), "CON": ("filings", "company")}


# ----------------------------------------------------------- rule helpers ----
def charge_records(charges: dict | None) -> dict[str, dict]:
    if not charges:
        return {}
    keep = ("status", "created_on", "persons_entitled", "contains_fixed_charge",
            "contains_floating_charge", "contains_negative_pledge", "particulars")
    items = charges["items"]
    return {r: {"charge": r, **{k: c[k] for k in keep}} for r, c in zip(charge_refs(items), items)}


def decided(applies: bool, evidence=None) -> dict:
    return {"verdict": "applies" if applies else "not_applicable",
            "evidence": list(evidence or []) if applies else [], "by": "code"}


def unknown(why: str) -> dict:
    return {"verdict": "not_determinable", "why": why, "by": "code"}


def read(targets: dict[str, dict]) -> dict:
    return {"verdict": "needs_reading", "targets": targets}


def _listed(section: str, key: str):
    return lambda s, recs: decided(bool(s[section][key]), s[section][key])


def _flag_rule(key: str):
    def rule(s, recs):
        if s["charges"][key]:
            return decided(True, s["charges"][key])
        unrecorded = s["charges"]["flags_not_recorded_live"]
        return read({r: recs[r] for r in unrecorded}) if unrecorded else decided(False)
    return rule


def sec_06(s, recs):
    groups = s["charges"]["same_lender_within_30d"]
    return decided(bool(groups), [f"{', '.join(g['charges'])} ({g['lender']})" for g in groups])


def con_01(s, recs):
    f, w = s["filings"], s["filings"]["worst_days_late_3y"]
    if w and w["days_late"] > 30:
        return decided(True, [f"{w['ref']} — {w['days_late']} days late"])
    if f["window_truncated"]:
        return unknown("filing window truncated; late accounts earlier in the 3 years may be missing")
    return decided(False)


def con_02(s, recs):
    la = s["filings"]["latest_accounts"]
    late = bool(la and la["days_late"] is not None and la["days_late"] > 180)
    return decided(late, la and [f"{la['ref']} — {la['days_late']} days late"])


def con_03(s, recs):
    f = s["filings"]
    if len(f["late_3y"]) >= 2:
        return decided(True, f["late_3y"])
    if f["window_truncated"]:
        return unknown("filing window truncated; only part of the 3 years was retrieved")
    return decided(False)


def con_04(s, recs):
    f = s["filings"]
    return decided(f["latest_micro_or_exempt"],
                   f["latest_accounts"] and [f"{f['latest_accounts']['ref']} — {f['latest_accounts']['description']}"])


def con_05(s, recs):
    f = s["filings"]
    if f["accounts_on_record"] > 0:
        return decided(False)
    if f["window_truncated"]:
        return unknown("no accounts in the retrieved window, but the window was truncated")
    if f["months_since_incorporation"] is None:
        return unknown("incorporation date missing")
    m = f["months_since_incorporation"]
    return decided(m > 21, [f"no accounts filing; incorporated {m} months before as_of"])


def con_06(s, recs):
    f = s["filings"]
    m = f["months_since_made_up"]
    return decided(m is not None and m > 18, [f"accounts made up to {f['last_made_up_to']} — {m} months before as_of"])


def con_07(s, recs):
    ev = s["filings"]["insolvency_filings"] + (
        ["profile: has_insolvency_history"] if s["company"]["has_insolvency_history"] else [])
    return decided(bool(ev), ev)


RULES = {
    "SEC-01": _listed("charges", "outstanding_third_party"),
    "SEC-02": lambda s, recs: decided(s["charges"]["clean_position"],
                                      s["charges"]["satisfied"] or ["no charges registered"]),
    "SEC-03": _listed("charges", "satisfied"),
    "SEC-04": _listed("charges", "part_satisfied"),
    "SEC-05": _listed("charges", "third_party_last_180d"),
    "SEC-06": sec_06,
    "SEC-07": _flag_rule("negative_pledge"),
    "SEC-08": _flag_rule("floating_live"),
    "CON-01": con_01, "CON-02": con_02, "CON-03": con_03, "CON-04": con_04,
    "CON-05": con_05, "CON-06": con_06, "CON-07": con_07,
    "EVD-01": lambda s, recs: decided(bool(s["missing"]), s["missing"]),
}


# ---------------------------------------------------------------- model ----
class Judgement(BaseModel):
    id: str
    verdict: Literal["applies", "not_applicable", "not_determinable"]
    quotes: list[str] = Field(description="Words copied exactly from single values in the record that show the "
                                          "verdict, one entry per value. Empty if not_determinable.")
    reason: str = Field(description="One sentence, citing only the record.")


class Judgements(BaseModel):
    judgements: list[Judgement]


SYSTEM = (
    "You check whether a lending-policy clause applies, using only the record given with each question. "
    "Answer applies only if the record shows the clause's condition is met, and not_applicable only if the "
    "record shows it is not met. If the record is silent, incomplete, or points to a document you do not have "
    "(for example 'see image for full details'), answer not_determinable and say what is missing. "
    "Do not assume, and do not use knowledge about lenders, companies or what such charges usually contain. "
    "For applies and not_applicable, give quotes: each one copied exactly from a single value in the record, "
    "with no field names, JSON punctuation or '...'. Use several quotes when the evidence is in several values."
)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _flatten(x) -> str:
    if isinstance(x, dict):
        return " ".join(_flatten(v) for v in x.values())
    if isinstance(x, list):
        return " ".join(_flatten(v) for v in x)
    return "" if x is None else str(x)


async def judge(questions: list[dict]) -> dict[str, dict]:
    if not questions:
        return {}
    payload = [{"id": q["id"], "clause": q["clause_text"], "record": q["record"]} for q in questions]
    resp = await llm.client().beta.messages.parse(
        model=llm.MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
        output_format=Judgements,
        **llm.FALLBACK,
    )
    print(llm.usage_line("policy", resp, len(questions), "question(s)"))

    ok = resp.stop_reason != "refusal" and resp.parsed_output is not None
    got = {j.id: j for j in resp.parsed_output.judgements} if ok else {}
    out = {}
    for q in questions:
        j = got.get(q["id"])
        if j is None:
            out[q["id"]] = {"verdict": "not_determinable", "quotes": [], "reason": "no answer from the model"}
            continue
        flat = _norm(_flatten(q["record"]))
        missing = [x for x in j.quotes if _norm(x) not in flat]
        if j.verdict != "not_determinable" and (not j.quotes or missing):
            out[q["id"]] = {"verdict": "not_determinable", "quotes": j.quotes,
                            "reason": f"quote not found in the record: {missing or 'none given'} "
                                      f"(model said {j.verdict}: {j.reason})"}
        else:
            out[q["id"]] = {"verdict": j.verdict, "quotes": j.quotes, "reason": j.reason}
    return out


# ----------------------------------------------------------- precedence ----
RANK = {"PROCEED": 0, "REFER": 1, "INSUFFICIENT EVIDENCE": 2, "DECLINE": 3}      # EVD-07


def level(outcome_text: str) -> str | None:
    t = outcome_text.upper()
    return next((k for k in sorted(RANK, key=len, reverse=True) if t.startswith(k)), None)


def decide(applicable: list[dict], gaps: list[str]) -> tuple[dict, bool | None]:
    levels = [level(a["outcome"]) for a in applicable if level(a["outcome"])]
    decision = max(levels, key=RANK.get, default="PROCEED")
    outcome = {"decision": decision,
               "clauses": [a["clause_id"] for a in applicable if level(a["outcome"]) == decision]}
    qualifies = False if decision == "DECLINE" else (None if gaps else True)
    return outcome, qualifies


# ------------------------------------------------------------------ node ----
async def evaluate(signals: dict, charges: dict | None, clauses: list[dict]) -> dict:
    recs = charge_records(charges)
    if signals.get("charges"):
        assert set(signals["charges"]["live"]) <= set(recs), "charge refs out of sync with signals"

    results, questions = {}, []
    for c in clauses:
        cid = c["clause_id"]
        if cid in ROUTED:
            results[cid] = {"verdict": f"routed → {ROUTED[cid]}"}
            continue
        absent = [sec for sec in NEEDS.get(c["doc"], ()) if not signals.get(sec)]
        if cid in RULES and absent:
            r = unknown(f"{' and '.join(absent)} record missing")
        elif cid in RULES:
            r = RULES[cid](signals, recs)
        else:
            print(f"(!) {cid} has no rule in code — the model judges it from the clause text")
            r = read({"company": {"signals": signals, "charges": list(recs.values())}})
        if r["verdict"] == "needs_reading":
            for target, record in r["targets"].items():
                questions.append({"id": f"q{len(questions) + 1}", "clause_id": cid, "target": target,
                                  "clause_text": c["text"], "record": record})
        results[cid] = r

    answers = await judge(questions)
    for cid in {q["clause_id"] for q in questions}:
        qa = [(q, answers[q["id"]]) for q in questions if q["clause_id"] == cid]
        hits = [f"{q['target']}: " + " + ".join(f"«{x}»" for x in a["quotes"])
                for q, a in qa if a["verdict"] == "applies"]
        unk = [f"{q['target']}: {a['reason']}" for q, a in qa if a["verdict"] == "not_determinable"]
        results[cid] = ({"verdict": "applies", "evidence": hits, "by": "model"} if hits else
                        {"verdict": "not_determinable", "why": " | ".join(unk), "by": "model"} if unk else
                        {"verdict": "not_applicable", "evidence": [], "by": "model"})

    applicable, gaps, trace = [], [], {}
    for c in clauses:
        cid, r = c["clause_id"], results[c["clause_id"]]
        trace[cid] = r["verdict"] + (f" · {r['by']}" if "by" in r else "")
        if r["verdict"] == "applies":
            applicable.append({"clause_id": cid, "title": c["title"], "outcome": c["outcome"],
                               "evidence": r["evidence"], "decided_by": r["by"]})
        elif r["verdict"] == "not_determinable":
            gaps.append(f"{cid} ({level(c['outcome']) or c['outcome']}) not determinable — {r['why']}")
    gaps += [f"EVD-01 missing: {m}" for m in signals["missing"]]

    outcome, qualifies = decide(applicable, gaps)
    return {"applicable": applicable, "outcome": outcome, "qualifies": qualifies,
            "evidence_gap": gaps, "policy_trace": trace}


async def policy(state: dict) -> dict:
    return await evaluate(state["signals"], state.get("charges"), CLAUSES)
