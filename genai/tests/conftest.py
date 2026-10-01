"""Shared setup for the RM Copilot tests.

Run from the repo root:   .venv/bin/python -m pytest genai/tests
Full data scan:           DATA_SCAN_EVERY=1 .venv/bin/python -m pytest genai/tests/test_data_contract.py

No test calls a model or the Companies House API: model calls are patched out (see `no_model`)
and API reads come from the on-disk caches.
"""
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

GENAI = Path(__file__).resolve().parents[1]
ROOT = GENAI.parent
sys.path.insert(0, str(GENAI))
load_dotenv(ROOT / ".env")                     # mcp_ch.ch_client refuses to import without CH_API
os.environ["LANGSMITH_TRACING"] = "false"      # .env turns tracing on; tests must not post traces


@pytest.fixture
def no_model(monkeypatch):
    """Fail the test if the policy node would call the model for a question code should settle."""
    async def judge(questions):
        assert not questions, f"model called for {[(q['clause_id'], q['target']) for q in questions]}"
        return {}
    monkeypatch.setattr("copilot.policy.judge", judge)


@pytest.fixture
def model_says_cannot_tell(monkeypatch):
    """Stand-in model that answers 'not_determinable' to every question, and records what it was asked."""
    asked = []

    async def judge(questions):
        asked.extend(questions)
        return {q["id"]: {"verdict": "not_determinable", "quotes": [], "reason": "stub"} for q in questions}
    monkeypatch.setattr("copilot.policy.judge", judge)
    return asked
