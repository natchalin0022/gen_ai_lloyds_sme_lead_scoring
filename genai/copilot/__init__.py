"""RM Copilot graph nodes, extracted from production/01–04 so the whole graph is importable.

    graph.py       build(tools), CopilotState   — the full graph (notebook 04)
    research.py    make_research(tools)         — notebook 01 (+ concurrent calls, failed calls recorded)
    signals.py     signal                       — notebook 02
    policy.py      policy, evaluate             — notebook 03 (+ guard for a missing record)
    supervisor.py  supervisor                   — notebook 04 (+ existing-customer check → end)
    brief.py       brief                        — notebook 04
    refs.py        charge_refs, filing_ref      — the ONE copy of the record references every node uses
    llm.py         client, record, USAGE        — models (Opus 5 reads, Sonnet 5 summarises), prices,
                                                   per-company token accounting

Notebooks 01–04 keep their own code as the step-by-step build; this package is what runs
(AGENTIC_AI_ASSISTANT.ipynb, tests/).
"""
