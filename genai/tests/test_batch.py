"""copilot/batch.py — what a batch run keeps and reuses. A stand-in graph, so no model or API call.

The rule under test: a failed summary (e.g. the Anthropic credit balance runs out) must never erase
a finished screening, and must be retried on the next run.
"""
import asyncio
import json

from copilot import batch


class Graph:
    """Stands in for the compiled graph: screening always works; summaries fail while `fail` is set."""
    def __init__(self, fail: bool):
        self.fail, self.calls = fail, []

    async def ainvoke(self, inp, config):
        self.calls.append(inp["draft_brief"])
        if inp["draft_brief"] and self.fail:
            raise RuntimeError("Your credit balance is too low to access the Anthropic API.")
        return {"company_number": inp["company_number"], "applicable": [], "evidence_gap": [], "qualifies": True,
                "signals": {"as_of": "2026-09-30", "charges": None},
                "outcome": {"decision": "PROCEED", "clauses": [], "conditions": ["CON-04"]},
                "brief": "summary" if inp["draft_brief"] else "code-only"}


LEADS = [("00000001", 1)]


def run(graph, out, top_n=1):
    return asyncio.run(batch.screen_and_summarise(graph, LEADS, out, "2026-09-30", top_n))[0]["00000001"]


def test_a_failed_summary_keeps_the_screened_decision(tmp_path):
    st = run(Graph(fail=True), tmp_path)
    assert "error" not in st and st["outcome"]["decision"] == "PROCEED"
    assert "credit" in st["_run"]["summary_error"]
    saved = json.loads((tmp_path / "states" / "00000001.json").read_text())
    assert "credit" in saved["_run"]["summary_error"]
    assert "## At a glance" in saved["brief"] and "## Summary" not in saved["brief"]   # screened-only brief


def test_the_next_run_reuses_the_screening_and_retries_only_the_summary(tmp_path):
    run(Graph(fail=True), tmp_path)
    g = Graph(fail=False)
    st = run(g, tmp_path)
    assert g.calls == [True]                            # screening reused; only the summary ran
    assert st["brief"] == "summary" and "summary_error" not in st["_run"]


class Mixed(Graph):
    """Screening gives each company a set decision; ranks 1 and 2 are the kind that must not get a summary."""
    DECISION = {"00000001": "DECLINE", "00000002": "PROCEED", "00000003": "INSUFFICIENT EVIDENCE",
                "00000004": "REFER", "00000005": "PROCEED"}

    async def ainvoke(self, inp, config):
        st = await super().ainvoke(inp, config)
        n = inp["company_number"]
        st["outcome"] = {**st["outcome"], "decision": self.DECISION[n]}
        if n == "00000002":                                  # an existing Lloyds customer: live own-group charge
            st["signals"]["charges"] = {"own_group": ["X"], "live": ["X"]}
        return st


def test_summaries_go_to_the_top_leads_that_passed_screening(tmp_path):
    leads = [(f"0000000{i}", i) for i in range(1, 6)]
    _, top = asyncio.run(batch.screen_and_summarise(Mixed(fail=False), leads, tmp_path, "2026-09-30", top_n=2))
    assert top == ["00000004", "00000005"]    # skips the DECLINE, the existing customer, INSUFFICIENT EVIDENCE
