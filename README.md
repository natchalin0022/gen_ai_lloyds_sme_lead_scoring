# Lloyds SME Lead-Scoring

Final group project, University of Bristol MSc Data Science.

A **lead-scoring model** that ranks UK SME companies by how likely they are to need new
lending, so Lloyds relationship managers (RMs) know who to approach first. Scope is
**lending only**. The output is a ranked list of **non-customers** who look like customers.

On top of the model sits the **RM Copilot** (`genai/`, built solo after the group project): a
LangGraph agent that takes one company from the lead list, checks its Companies House record
against a 23-clause lending policy, and writes a brief in which every sentence cites its source.
See *RM Copilot — the agent layer* below.

Two public data sources, no proprietary bank data:

| pillar | source | role |
|---|---|---|
| structured | **Companies House** API | age, sector, region, accounts, charge history — the workhorse |
| unstructured | **GDELT** via BigQuery | sector × region news volume, used as a soft re-rank |

---

## The two pipelines

The same five notebooks serve two different jobs. Run them in numbered order.

### Training side — build and evaluate the model

Run occasionally: once to build, then quarterly to retrain.

```
company_raw/*.csv                bulk Companies House company lists
      │
      ▼
1_CompaniesHouse.ipynb           Stage 1  profile pull (9 fields per company)
                                 Stage 2  flag the SME population
                                 Stage 3  charges + PSC
                                 Stage 4b charges_history.csv  ← the LABEL source
      │
      ▼
2_GDELT.ipynb                    Part 5   postcode → region   (required: a model feature)
                                 Part 3/4 sector × region media index (for the re-rank)
      │
      ▼
3_Flat_table.ipynb               features at ASOF=2021-01-01 + forward label
                                 →  API/flat_pot.csv.gz
      │
      ▼
4_model.ipynb                    LogReg (primary) · XGBoost · MLP (comparators)
                                 grid search, Precision@K, SHAP
                                 refit on ALL rows  →  Model/model.joblib + manifest
```

### Client side — score a real list

Run per engagement. **One pass through each notebook, in order.**

```
client/input/*.csv               what the client sends (see "Client input" below)
      │
      ▼
1_CompaniesHouse.ipynb           Stage 0  ingest client lists → company_raw/
                                 Stage 1  pull anyone not already held
                                 Stage 3  charges + PSC for the new SMEs
                                 Stage 4b rebuild charges_history.csv
      │
      ▼
2_GDELT.ipynb                    Part 5   region for the new companies
      │
      ▼
5_score.ipynb                    §1 read + repair      §5 exclude customers
                                 §2 coverage check     §6 soft re-rank
                                 §3 features as-of TODAY  §7 reason codes
                                 §4 score              §8 write   §9 checks
      │
      ▼
client/output/<date>/            leads.csv · not_found.csv · run_manifest.txt
```

**Stage 0 is what makes this linear.** It reads the same `client/input/*.csv` files that
notebook 5 will, and queues their company numbers for the pull — so by the time you reach
notebook 5, every company is already held and it runs once.

Notebook 3 is **not** in the client path: it builds the *training* table (past `ASOF` + a
label). Notebook 5 builds the *scoring* table (today, no label) using identical feature code.

---

## RM Copilot — the agent layer (`genai/`)

The model ranks *who* to call. The copilot answers the RM's next question: *could we lend to
them, and why?* It takes one company from the lead list, reads its Companies House record,
checks it against a written 23-clause lending policy, and returns a brief in which every
sentence cites the record it came from.

```
leads.csv (5_score)
      │  one company number
      ▼
research      4 Companies House records through an MCP server, fetched concurrently
      │                     (profile · filing history · charges · officers)
      ▼
signal        dates and counts in code; Claude reads collateral from free-text charge particulars
      ▼
policy        all 23 clauses checked; code decides what the record settles, Claude reads the rest
      ▼
supervisor ─┬→ research   a fetch failed: retry, at most twice (EVD-05)
            ├→ end        a Lloyds charge is still live: existing customer, no brief
            └→ brief      decision + clause table written by code, summary written by Claude
```

Built with **LangGraph** (`copilot/graph.py`), **Claude** through the Anthropic SDK with
structured outputs (Pydantic), and **LangSmith** tracing at graph, node, tool and model-call
level, with the token cost of every Claude call attached.

The policy (`policies/*.md`) is **written for this project**, not Lloyds' own credit policy:
8 security clauses (`SEC-`), 8 filing-conduct clauses (`CON-`), 7 evidence rules (`EVD-`). Each
has an ID, an outcome and a testable *Applies when* condition, so a brief can cite the exact
rule behind its decision.

### Code decides, the model explains

The design rule throughout. A model is used only where something must be *read*, and code
checks its output before anything downstream uses it.

| step | done by | check |
|---|---|---|
| the decision (PROCEED / REFER / DECLINE / INSUFFICIENT EVIDENCE) | code: EVD-07 precedence over the clauses that applied | snapshot tests |
| clause rules on charges and filings | code | unit + property tests |
| collateral named in a charge's particulars | Claude Opus | its quote must appear in the text, or the collateral never reaches the brief (EVD-04) |
| a clause with no rule, or one that needs reading | Claude Opus | each verdict must quote the record word for word, or it becomes *not determinable* |
| the brief's 4–7 sentence summary | Claude Sonnet | each sentence must cite a fact-sheet key; one citing nothing, or an unknown key, is deleted (EVD-02) |
| answers to *Ask about this lead* | Claude Sonnet + retrieval | an answer citing a source it was not given is not shown |

With `draft_brief=False` the brief is the code-written parts alone, which carry the whole
decision. A batch uses that to screen every lead, then pays for a summary only on the top
prospects that passed.

### The MCP server (`mcp_ch/`)

Four read-only tools over stdio: `get_company_profile`, `get_filing_history`, `get_charges`,
`get_officers`. The server is a thin contract; `tools.py` does the work.

- **Rate limit.** Companies House allows 600 requests per 5 minutes. Calls are paced, and every
  response (404s included) is disk-cached, so a re-run costs no API calls.
- **Resolved in code, not inferred by the model.** `lender_group` (Lloyds Banking Group or third
  party, from the same pattern list that labels the training data — `lender_groups.py`),
  `days_late` against the statutory deadline, and filing descriptions decoded from Companies
  House's template codes.
- **Trimmed.** Only the fields a credit assessment uses reach the model's context. No personal
  data (date of birth, nationality, address) is returned.

A batch opens **one** server process (`research.open_tools()`). A trace showed that starting a
process per call was nearly all of research's time, and one process also means one rate-limit
pacer shared by every company in flight.

### Retrieval (`policy_store.py`, `copilot/ask.py`)

The policy is chunked **one clause per chunk** (split on `### `, 23 chunks) and embedded with
`all-MiniLM-L6-v2` into ChromaDB. On eight questions with a known answer: recall@1 0.75,
recall@3 1.00 (`06_rag.ipynb`).

**One blended query retrieves badly.** Describing a whole company in one query averages into a
vector that sits between clusters: SEC-01, the clause that should obviously fire, fell to about
5th. One short query per fact cluster (charges / accounts / evidence), unioned, puts it back at
the top of its facet. `retrieve_facets` does exactly that.

**Retrieval explains; it never decides.** A retriever that can rank the right clause 5th is fine
for an explanation and wrong for a decision, so `policy.py` checks every clause and does not use
retrieval. Retrieval powers *Ask about this lead*: the clauses that fired for this company are
always in the context, retrieved clauses are added, and the answer may cite only what it was given.

### Running it

| entry point | what it does |
|---|---|
| `AGENTIC_AI_ASSISTANT.ipynb` | screen the latest lead list, summarise the top prospects |
| `webapp/app.py` | the same, as a local web page — see below |
| `production/01–04_*.ipynb` | the step-by-step build, one notebook per node |
| `00–06_*.ipynb` | the learning path: Claude API → LangChain → LangGraph → RAG |
| `trace_audit.py` | accounts for every run in a batch's LangSmith trace; checks the batch cost equals the sum of its Claude calls |

Batch runs (`copilot/batch.py`) save every company's final state and brief, and reuse them on a
re-run, so finished work is never paid for twice. A failed summary (say the API credit runs out)
never erases a finished screening; it is retried on the next run.

### Web app (`webapp/`)

```bash
.venv/bin/streamlit run webapp/app.py --server.address 127.0.0.1
```

Company numbers in, a ranked and policy-screened lead list out. `webapp/pipeline.py` runs
notebooks 1, 2 and 5 **headless, as they are** (nbclient), then the copilot. There is no second
copy of the feature code to drift from the training table, so *The one rule to preserve* holds
by construction. Each executed notebook is saved in the run's `logs/` folder as its audit record.

- **Local only.** The page holds the Companies House and Anthropic keys; keep it on `127.0.0.1`.
- **Costs money.** About $0.025 per summarised lead (from the September 2026 runs). Up to 300
  companies per run.
- **Isolated.** Runs read and write a working copy of the company tables (`client/live_data/`),
  never the tracked snapshot the model was trained on. One run at a time.

### Tests

```bash
.venv/bin/python -m pytest genai/tests -q
RUN_PIPELINE_TESTS=1 .venv/bin/python -m pytest webapp/tests -q
```

The first runs ~80 tests in about 20 seconds, with no Companies House or Claude calls (the first
run downloads the embedding model from Hugging Face). The second takes about a minute: it runs
three notebook kernels.

| file | checks |
|---|---|
| `test_rules.py` | every clause at its boundary, and the three data fixes below |
| `test_properties.py` | Hypothesis: rules that must hold for *every* company, on generated charge lists and filing histories |
| `test_data_contract.py` | real Companies House charge JSON looks the way the code assumes |
| `test_snapshots.py` | two saved companies keep producing exactly the same code-decided result |
| `test_supervisor_brief.py` | one test per routing row; unsourced statements dropped (EVD-02) |
| `test_ask.py` | the retrieval guarantees above |
| `test_batch.py` | a failed summary never erases a finished screening |
| `webapp/tests/test_pipeline.py` | the web app reproduces the notebooks' own lead list exactly |

**Checking the data, not just the code, found the worst bug.** The Companies House API never
sends a charge flag as `false`; it leaves the key out. Read as "not recorded", that would have
raised a false evidence gap on every one of the 147,119 post-2013 charges with no floating-charge
flag. Only pre-2013 charges (no `charge_code`, no tick-boxes on the form) are genuinely
unrecorded. The other two fixes, each found the same way: `satisfied` merged into
`fully-satisfied` (17 charges), and a charge with no named lender treated as an evidence gap
rather than a competitor (201 charges). Counts are over the 399,767 cached charges.

One test is a **strict `xfail`**: SEC-07 (negative pledge) currently counts fully-satisfied
charges, which is the literal reading of the clause but probably not the intended one. That is a
policy owner's decision, not a coding one. If the clause changes, the test starts passing and
`strict=True` makes that visible.

### Limits

- **A screening aid, not a credit decision.** It says which policy clauses a public record
  triggers. It cannot see financials beyond what is filed, or anything a bank holds privately.
- **The policy is illustrative.** Plausible SME secured-lending rules, written to be testable;
  not Lloyds' policy.
- **Public record only.** Where the record leaves a gap the policy needs filled, the brief opens
  with *Thin evidence — RM verification required* and lists the gaps (EVD-05) instead of guessing.

---

## Setup

Python 3.12+ (developed on 3.13). Use a dedicated environment so the notebooks and the
`pip` you install with are the same interpreter — a mismatch is the usual cause of
`InconsistentVersionWarning` / `AttributeError` when `5_score.ipynb` loads `model.joblib`.

```bash
conda create -y -p ./.venv -c conda-forge python=3.13 pip llvm-openmp
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m ipykernel install --user --name lloyds-genai --display-name "Python 3.13 (Lloyds .venv)"
```

`llvm-openmp` is the OpenMP runtime xgboost needs on macOS (`brew install libomp` is the
system-wide alternative). Then pick the **Python 3.13 (Lloyds .venv)** kernel in Jupyter /
VS Code. To confirm the notebook is on the right interpreter, run this in a cell:

```python
import sys, sklearn; print(sys.executable, sklearn.__version__)   # expect .venv/... and 1.8.0
```

Create `.env` in this folder with your Companies House API keys (one per line, no quotes).
More keys = proportionally faster; the client paces each one to stay under the rate limit.

```
CH_API=your_key_here
CH_API_2=your_second_key
```

The RM Copilot and the web app also need a Claude API key. LangSmith tracing is optional;
without it the copilot runs the same, just untraced.

```
ANTHROPIC_API_KEY=your_key_here
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_key_here
LANGSMITH_PROJECT=rm-copilot
```

GDELT Parts 3–4 additionally need a Google Cloud project with BigQuery billing enabled
(`gcloud auth application-default login`). **Parts 3–4 are optional** — skip them and you
still get a working lead list, just without the media re-rank. Part 5 needs no credentials.

Verify the install — prints every path and whether it exists:

```bash
python paths.py
```

Most paths print `OK`. **These seven print `MISS` in a clean clone and are expected:**

| constant | why it is absent |
|---|---|
| `ENV_FILE` | you create `.env` yourself — see above |
| `FILINGS_CSV` | Stage 6 is disabled and nothing reads it |
| `ACCTYPE_CSV`, `SME_CSV` | legacy two-file layout, superseded by `companies.csv.gz` |
| `FLAT_CSV`, `PANEL_CSV` | retired designs; the constants remain only so older notebook copies still resolve |
| `LEADS_CSV` | an output — written when you run the pipeline |

Anything *else* printing `MISS` is a real problem.

### The data ships with the repo

**There is nothing to download.** Everything the pipeline needs to run is in the clone —
clone it, `pip install -r requirements.txt`, and the notebooks work.

The three tables are stored **gzipped** so they fit inside GitHub's 100 MB per-file limit.
`paths.py` points at the `.gz`, and pandas reads and writes that format transparently from
the extension, so no notebook code differs because of it.

| file | in repo | uncompressed | needed by |
|---|---|---|---|
| `companies.csv.gz` | 27 MB | 149 MB | every stage, and `5_score` |
| `charges_history.csv.gz` | 6 MB | 44 MB | the label and all charge features |
| `flat_pot.csv.gz` | 9 MB | 46 MB | `4_model` — the training table |

Also shipped: the GDELT Parquet caches, `Model/model.joblib` and its manifest, the client
input template, and the per-company **charge** JSON cache that `Stage 4b` rebuilds from.

Two things are deliberately **not** in the repo, and neither is needed:

- **`filings_history.csv` and the filing JSON cache.** Nothing reads them — `Stage 6` is
  disabled (see *The JSON cache* below). They were ~1.9 GB.
- **`.env` and real client lists.** API keys and customer data must never reach a public
  repository. Only the template ships.

You only need Companies House API keys if you intend to *pull fresh data*. Scoring a list
against the shipped tables needs no credentials at all.

### Paths

**`paths.py` is the single source of truth for every file location.** No notebook contains a
relative or absolute path. It derives the project root from its own location, so the notebooks
run from any working directory. Never hardcode a path; add a constant there instead.

### The JSON cache

`API/CompaniesHouse/company_info_json/` holds one raw JSON per company per endpoint
(`<com_num>_charges.json`, `<com_num>_filings.json`). It is a **cache, not a deliverable**:
everything in it can be re-fetched from the API. Only a **fraction** of the charge JSONs ship
(~11.7k of the ~105k firms that hold charges) — shipping them all would add ~350 MB to
regenerate a table the repo already contains. The filing JSONs do not ship at all, because
nothing reads them.

Nothing downstream reads the cache directly. Notebooks 2–5 work entirely from the two derived
tables, `companies.csv.gz` and `charges_history.csv.gz`, so **a list can be scored with the
cache absent.** Only `1_CompaniesHouse.ipynb` opens the JSONs, and only to rebuild those tables.

| | if the cache is deleted |
|---|---|
| **Stage 4** (charges) | **repairs itself** — it selects work by `Path.exists()` alone, so it re-fetches exactly the missing files (~105k companies, ~4.4 h on four keys) |
| **Stage 6** (filings) | **disabled by default** (`RUN_STAGE_6 = False`) and does not repair itself — its `todo` also requires `n_filings.isna()`, so a company whose count is already recorded can never be re-fetched. Blank `n_filings` in `companies.csv.gz` for those rows before re-enabling |

**Stage 4b merges, it does not rebuild.** It makes no API calls. A firm *with* a JSON on disk
is refreshed from it (the JSON is authoritative — Stage 3 has just written it); a firm *without*
one keeps whatever `charges_history.csv.gz` already says. That is what makes a fresh clone work
despite the partial cache, and it is also correct for a client bringing companies we do not
hold: Stage 3 writes a JSON for every company it pulls, so new firms are always refreshed.

Both Stage 4b and Stage 6 **refuse to write** if the result comes out more than 5% smaller than
the file already on disk. With the merge this should never happen in normal use — if it fires,
something is genuinely wrong. Investigate rather than deleting the guard.

**Stage 6 is switched off.** Filing history is not used anywhere: the shipped model's 13
features come from the Stage 1 profile call and `charges_history.csv` alone, and neither
notebook 3 nor notebook 5 opens `filings_history.csv`. The stage is kept, disabled, as the
raw material for the point-in-time `account_type` fix under *Known limitations*. Set
`RUN_STAGE_6 = True` in that cell to re-enable it.

---

## Client input

The client sends **one CSV** (or several — every `.csv` directly in `client/input/` is read;
subfolders are ignored). Template: `client/input/Template/client_companies_TEMPLATE.csv`.

| column | required | purpose |
|---|---|---|
| `company_number` | **yes** | the only reliable key |
| `company_name` | no | for their own checking; ignored |
| `relationship` | **yes** | `customer` → excluded from leads · `prospect` → scored |

Everything else — sector, region, age, charge history — comes from Companies House. The client
supplies **who to cross off**, not what the companies are like.

**Send company numbers, not names.** Name matching is unreliable and cannot safely resolve
"10 Castlebar Ltd" to one company. Rows without a valid number are reported back, never guessed.

**Leading zeros:** 28% of UK company numbers start with `0` and Excel strips them. The pipeline
repairs this automatically (`zfill(8)`), so send the file as-is rather than hand-fixing it.

---

## Outputs

`client/output/<date>/` — dated, never overwritten, because comparing this quarter's list
against next quarter's customer list is the only way to measure whether the leads converted.

| file | contents |
|---|---|
| `leads.csv` | top-K ranked, with a plain-language reason per lead |
| `not_found.csv` | every company **not** in the leads and **why** |
| `run_manifest.txt` | model version, ASOF, coverage, counts — traceability |

`not_found.csv` matters as much as `leads.csv`: "here are your leads, and here are the 43
numbers we could not match" is what makes the first file trustworthy.

---

## How it works

**Label.** A company is positive if a **Lloyds Banking Group entity** (Lloyds Bank, Bank of
Scotland, Halifax, Black Horse, Lex Autolease, …) appears as the lender on a registered charge.
Matched by a curated regex in Stage 3, which deliberately excludes "Lloyd's of London" and
"**Royal** Bank of Scotland".

**Forward-looking by construction.** Features are measured at `ASOF`; the label is the first
Lloyds charge in `(ASOF, LABEL_END]`. Getting this backwards answers "who *resembles* a
customer" rather than "who *will become* one".

**Existing customers are excluded** at `ASOF`. Every row is a genuine prospect, so the lead list
is simply the top of the ranking.

**Point-in-time discipline.** Charge features are cut at `ASOF`; non-Lloyds charges are allowed
as features (proven-borrower signal), Lloyds charges never are — they are the label.

**Evaluation** is Precision@K and lift at true prevalence, never accuracy. At 0.33% prevalence
accuracy is meaningless.

### The one rule to preserve

**The feature definitions in `5_score.ipynb` §3 must match `3_Flat_table.ipynb` §4 exactly.**
The definitions *are* the model. If they drift, the coefficients get applied to a different
quantity and scores become meaningless **without anything erroring**. Notebook 5 §4 validates
the built columns against the model manifest as a backstop.

---

## Scale (current build)

| | |
|---|---|
| companies pulled | 839,344 (**567,883** SMEs — the modelling population) |
| charge records | 399,706 (39,883 Lloyds-group) |
| training table | 360,600 rows, **1,203 positives** (0.33%) |
| features | 13 |
| media index | 21 sectors × 11 regions × 93 weeks |
| shipped model | LogReg, refit on all rows |

---

## Known limitations

Stated plainly because they matter for interpreting results.

1. **Population is a sample.** 567,883 SMEs, not the full UK register. Representativeness was
   checked across the twelve source slices: Lloyds prevalence 2.23–2.93% for all slices above
   45,000 companies. Two small early slices (1.16–1.47%, 1.6% of rows) reflect a known
   name-ordering artefact. Full deployment would use the Companies House **bulk data product**
   for company attributes, reserving API calls for charge histories.

2. **Positive-Unlabelled.** Lloyds lending that leaves no registered charge (unsecured,
   overdrafts, cards) is invisible. On the client side this disappears — their CRM knows.

3. **Three features are current snapshots**, not point-in-time: `account_type`,
   `accounts_overdue`, `sector`. Mild leakage. `filings_history.csv` (Stage 6) is the raw
   material to fix it; the features are not built, so Stage 6 ships disabled.

4. **Yorkshire has no media cells** — the region never matched GDELT's location spellings.
   Those leads get a neutral re-rank rather than a wrong one.

5. **The media re-rank is a tie-breaker, not a performance claim.** Sector-only `vol_z` showed
   **no signal** once seasonality was controlled (p=0.86). Sector × region did (z=+6.77 on real
   cells) but **not monotonically** — the hottest quintile sits at 0.97× chance. Hence
   `GATE_WEIGHT = 0.15`, real cells only, and nothing ever dropped.

6. **Scores are ranking values, not calibrated probabilities.** `class_weight="balanced"`
   inflates them deliberately. Report a rank, never "a 39% chance of borrowing".

7. **This is a prioritisation aid, not a credit decision.** It ranks who to call. It does not
   assess creditworthiness. PSC personal attributes (DOB, nationality) are deliberately excluded
   on fair-lending grounds.

---

## Repository layout

```
1_CompaniesHouse.ipynb    Stages 0–6: pull and assemble Companies House data
2_GDELT.ipynb             media index (Parts 3–4) + postcode → region (Part 5)
3_Flat_table.ipynb        training table  → API/flat_pot.csv
4_model.ipynb             train, evaluate, ship  → Model/model.joblib
5_score.ipynb             score a client list    → client/output/<date>/
paths.py                  every file location — import, never hardcode

genai/                    RM Copilot — see "RM Copilot — the agent layer"
    copilot/              the graph and its nodes (graph.py builds it)
    mcp_ch/               Companies House MCP server: 4 read-only tools
    policies/             the 23-clause lending policy (SEC / CON / EVD)
    policy_store.py       clause-level retriever (ChromaDB)
    production/           step-by-step build, one notebook per node
    tests/                pytest + Hypothesis
webapp/                   Streamlit front end: app.py (the page), pipeline.py (the work)

API/CompaniesHouse/company_data/
    companies.csv.gz      one row per company ever pulled (is_sme flags the population)
    charges_history.csv.gz  one row per charge — the label source
    filings_history.csv   Stage 6 output; unused — Stage 6 is disabled by default
    company_raw/          bulk company lists + ingested client lists
API/CompaniesHouse/company_info_json/    per-company charge/filing JSON (~600k files)
                          a rebuildable cache, safe to omit — see "The JSON cache"
API/GDELT/BigQuery Cache files/          Parquet caches — never re-query if present
API/flat_pot.csv.gz       the training table
Model/                    model.joblib + model_manifest.json
client/input/             what the client sends (gitignored except the template)
client/output/<date>/     what you hand back
```

**`.env` and real client lists are gitignored** — API keys and customer data must never
reach the repository or a code submission. Only the input template and the per-run
`run_manifest.txt` files ship.

**The BigQuery caches are the expensive artefact.** A fresh 104-week pull scans ~350 GB against
a 1 TB/month free tier. `USE_CACHE = True` / `USE_CACHE_REGION = True` load them for 0 GB —
leave them alone unless you deliberately want fresh data.
