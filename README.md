# Lloyds SME Lead-Scoring

Final group project, University of Bristol MSc Data Science.

A **lead-scoring model** that ranks UK SME companies by how likely they are to need new
lending, so Lloyds relationship managers (RMs) know who to approach first. Scope is
**lending only**. The output is a ranked list of **non-customers** who look like customers.

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
