## How to frame a LangGraph "shared state" object

### The core idea

State is one object threaded through the whole graph. Every node gets the current
state, returns a partial update, and LangGraph merges that update back in (via a
reducer) before handing state to the next node. The state schema *is* the interface
between agents — it's design, not implementation detail.

### How to frame one: read/write per node, not "what data exists"

The mistake is starting from "what data do I have" (company profile, filings,
charges, policy clauses...) and dumping it all into one flat schema. Instead, go
node by node and ask two questions: **what does this node need already decided,
and what does it add?**

For the RM Copilot graph:

| Node | Reads | Writes |
|---|---|---|
| Research Agent | `company_number` | `profile`, `filings`, `charges`, `officers` |
| Signal Agent | `filings`, `charges`, `officers` | `signals` (growth, late filings, rival charges...) |
| Policy Agent | `signals` | `qualifies`, `policy_citation`, `evidence_gap` |
| Supervisor (routing) | `evidence_gap`, `retry_count` | routes back to Research, or forward |
| Brief Agent | `profile`, `signals`, `policy_citation` | `brief_text`, `citations` |

That table *is* the state schema, almost verbatim. Once it's written down, the
fields fall into four natural categories:

1. **Invariant input** — set once, never changed (`company_number`). Every node can
   read it, no node should write it.
2. **Accumulating evidence** — grows as nodes run (`filings`, `signals`,
   `citations`). Needs a reducer that appends/merges rather than overwrites,
   especially if Research runs twice after a retry loop.
3. **Control-flow fields** — not domain data at all, just routing signals
   (`evidence_gap: bool`, `retry_count: int`). What the Supervisor's conditional
   edges branch on. Keep these separate from domain fields mentally, even though
   they live in the same object — this is the distinction people most often blur.
4. **Terminal output** — the thing being built up to hand off (`brief_text`). Only
   the last node writes it.

### A concrete schema

```python
from typing import Annotated, TypedDict
import operator

class RMCopilotState(TypedDict):
    # invariant input
    company_number: str

    # accumulating evidence — list reducer so a Research retry appends, not clobbers
    filings: Annotated[list[dict], operator.add]
    signals: Annotated[list[dict], operator.add]
    citations: Annotated[list[str], operator.add]

    # single-value evidence, latest write wins (default reducer)
    profile: dict
    policy_citation: str | None
    qualifies: bool | None

    # control flow — what the Supervisor's conditional edge reads
    evidence_gap: bool
    retry_count: int

    # terminal output
    brief_text: str | None
```

The `Annotated[list[dict], operator.add]` part is the detail worth internalizing:
without a reducer, LangGraph's default behavior is "last write overwrites the key
entirely." If two nodes (or a retry) both write `filings`, data is silently lost.
That's *the* LangGraph gotcha, and worth being able to explain unprompted in an
interview — it's the detail that separates "I read the docs" from "I built one and
got bitten."

### The exercise

Do the read/write table for real, for each of the five nodes, before writing code.
Once every row is filled in, two things should be obvious: which fields need
`operator.add` (anything more than one node touches over the run), and whether the
Supervisor's routing logic is cleanly separated from domain data. If a control-flow
field and a domain field are tangled together, that's usually where the design gets
messy later.

---
*Source: written after the interview-prep conversation on framing LangGraph shared
state (prep plan Days 3 and 6-7). Meant to feed the Build sprint's state design and
the Day-12 README architecture section.*
