"""The web app's pipeline connection must reproduce the notebooks' own lead list exactly.

Feeds the 14 Sep sample's 100 companies through webapp/pipeline.py at the same ASOF and compares
with client/output/2026-09-14/leads.csv, produced by running 5_score.ipynb by hand. Every company
is already held, so this makes no API calls; it takes about a minute (three notebook kernels).

    RUN_PIPELINE_TESTS=1 .venv/bin/python -m pytest webapp/tests -q
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webapp"))

pytestmark = pytest.mark.skipif(not os.environ.get("RUN_PIPELINE_TESTS"),
                                reason="slow: set RUN_PIPELINE_TESTS=1")

REFERENCE = ROOT / "client" / "output" / "2026-09-14"


def test_web_run_reproduces_the_14_sep_lead_list(tmp_path, monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "RUNS", tmp_path)            # keep test runs out of client/output/
    numbers = pd.read_csv(ROOT / "client" / "input" / "sample copy.csv", dtype=str)["company_number"]
    good, bad = pipeline.normalise(list(numbers))
    assert len(good) == 100 and not bad

    run = pipeline.score(pipeline.new_run(good, asof="2026-09-14"))
    got, want = run.leads, pd.read_csv(REFERENCE / "leads.csv", dtype={"com_num": str})

    assert list(got["com_num"]) == list(want["com_num"])                # same companies, same order
    assert list(got["rank"]) == list(want["rank"])
    for col in ("score", "r_score", "gated", "age_years", "nonlloyds_charges"):
        assert np.allclose(got[col], want[col], rtol=0, atol=1e-12), col
    assert list(got.columns[:len(want.columns)]) == list(want.columns)
    assert list(got.columns[len(want.columns):]) == ["why"]              # added since: the reasons, in words
    assert got["why"].str.len().gt(0).all()
    assert run.not_scored.empty
    assert all(s["status"] == "done" for s in run.info["stages"].values())
