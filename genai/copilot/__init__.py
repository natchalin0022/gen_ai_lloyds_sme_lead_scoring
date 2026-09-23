"""RM Copilot graph nodes, extracted from production/01–03 so notebook 04 can wire the full graph.

    research.py  make_research(tools)  — notebook 01 (+ concurrent calls, failed calls recorded)
    signals.py   signal                — notebook 02
    policy.py    policy, evaluate      — notebook 03 (+ guard for a missing record)
    refs.py      charge_refs, filing_ref — the ONE copy of the record references every node uses
    llm.py       the model client and settings shared by signal, policy and brief

Notebooks 01–03 keep their own code as the step-by-step build; this package is what runs.
"""
