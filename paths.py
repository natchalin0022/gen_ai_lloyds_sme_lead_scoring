"""Central path configuration for the Lloyds SME lead-scoring project.

Import this instead of hardcoding absolute paths, so the project runs on ANY
machine (yours, a teammate's, the client's) regardless of where it's checked out
or which directory the notebook happens to be running from.

The four numbered pipeline notebooks live in the project root next to this file,
so they can simply:

    from paths import COMPANIES_CSV, CHARGES_CSV          # etc.

From a notebook in a SUBfolder (API/, Model/, ...), bootstrap sys.path first:

    import sys
    from pathlib import Path
    ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "paths.py").exists())
    sys.path.insert(0, str(ROOT))
    from paths import COMPANIES_CSV, CHARGES_CSV          # etc.

Usage in a plain script:

    from paths import COMPANIES_CSV

All names below are pathlib.Path objects. Build sub-paths with `/`, e.g.
`CH_DATA / "charges_history.csv"` — never with string `+`.
"""

from pathlib import Path


def find_root(start=None):
    """Walk up from `start` (default: cwd) to the project root.

    Only needed by callers that cannot import this module yet (they must find the
    root to put it on sys.path). Once `paths` IS imported, use `ROOT` below, which
    is derived from this file's own location and therefore cannot be wrong.

    The root is the folder holding this file — equivalently the one holding API/
    and Model/, and normally the git root.
    """
    start = Path(start).resolve() if start else Path.cwd().resolve()
    for cand in [start, *start.parents]:
        if (cand / "paths.py").is_file():
            return cand
        if (cand / "API").is_dir() and (cand / "Model").is_dir():
            return cand
        if (cand / ".git").exists() and (cand / "API").is_dir():
            return cand
    raise RuntimeError(
        f"Could not locate the project root from {start}. Expected an ancestor "
        "directory containing 'paths.py' (or both 'API/' and 'Model/')."
    )


# The root is THIS FILE's folder. Deriving it from __file__ rather than from the
# working directory is what lets every notebook run from anywhere — Jupyter, a
# terminal, or an IDE with a different cwd — without a single relative path.
ROOT = Path(__file__).resolve().parent

# --- top-level folders -----------------------------------------------------
API_DIR   = ROOT / "API"
MODEL_DIR = ROOT / "Model"
ENV_FILE  = ROOT / ".env"                      # Companies House API keys

# --- Companies House -------------------------------------------------------
CH_DIR          = API_DIR / "CompaniesHouse"
CH_DATA         = CH_DIR / "company_data"
CHARGE_JSON_DIR = CH_DIR / "company_info_json"          # per-company charge/filing JSON

# The single company table: one row per company ever pulled, `is_sme` flags the
# modelling population. Non-SME rows are kept deliberately — they are the
# "already checked, do not re-pull" cache that keeps Stage 1 resumable.
# Stored gzipped so it fits inside GitHub's 100 MB per-file limit (149 MB -> 27 MB).
# pandas reads and writes .csv.gz transparently from the extension, so no notebook
# code changes: read_csv, to_csv, usecols and dtype all behave identically.
COMPANIES_CSV = CH_DATA / "companies.csv.gz"

# Legacy two-file layout, archived 2026-08-14 — superseded by COMPANIES_CSV.
# Nothing active reads these; the constants remain only so any old//notebook copy
# still resolves instead of throwing. Both are reproducible from COMPANIES_CSV.
ACCTYPE_CSV = CH_DATA / "Archive" / "company_with_acctype" / "com_names_with_acctype_test.csv"
SME_CSV     = CH_DATA / "Archive" / "company_sme_with_acctype" / "com_names_sme_test.csv"
CHARGES_CSV = CH_DATA / "charges_history.csv.gz"        # label source (is_lloyds); gzipped, see above
FILINGS_CSV = CH_DATA / "filings_history.csv"           # Stage 6 output (may not exist yet)

# Archived 2026-08-14: the original 13.5k-company snapshot. `11_7_26.csv` was the
# input to two superseded model notebooks; the context-media copy is still read by
# GDELT.ipynb as a postcode backfill source (down to ~24 usable rows, so it will
# retire naturally once every company has a post_code).
ARCHIVE               = CH_DIR / "Archive"
FINAL_DATA            = ARCHIVE / "Final data"
LEGACY_CSV            = FINAL_DATA / "11_7_26.csv"
POSTCODE_BACKFILL_SRC = FINAL_DATA / "11_7_26_with_context_media.csv"

# --- GDELT -----------------------------------------------------------------
GDELT_DIR   = API_DIR / "GDELT"
GDELT_CACHE = GDELT_DIR / "BigQuery Cache files"
MEDIA_INDEX = GDELT_CACHE / "context_media_index_104w.parquet"

# --- modelling tables / outputs --------------------------------------------
PANEL_CSV = API_DIR / "panel.csv"          # per (company, cutoff) panel (retired design)
FLAT_CSV  = API_DIR / "flat.csv"           # one row per company (look-alike, retired)

# THE live training table: one row per company, FORWARD-looking label.
# Written by 3_Flat_table.ipynb, read by 4_model.ipynb. This path is the one
# whitelisted in .gitignore, so keep the file here (gzipped, as above).
FLAT_POT_CSV = API_DIR / "flat_pot.csv.gz"
LEADS_CSV = API_DIR / "leads_flat.csv"     # ranked RM lead list

# --- client I/O (5_score.ipynb) --------------------------------------------
# NOT under API/ — this is what the client sends in and what we hand back, not
# data pulled from an external API. Everything here is gitignored: a customer
# list is confidential commercial data and must never reach the repo or a
# code submission. Ship the TEMPLATE, never a real list.
CLIENT_DIR      = ROOT / "client"
CLIENT_INPUT    = CLIENT_DIR / "input"
CLIENT_OUTPUT   = CLIENT_DIR / "output"        # one dated subfolder per run
# In Template/, NOT directly in client/input/: Stage 0 globs client/input/*.csv
# non-recursively, so a template sitting there would be ingested as a real client list.
CLIENT_TEMPLATE = CLIENT_INPUT / "Template" / "client_companies_TEMPLATE.csv"

# --- the shipped model artifact (written by 4_model.ipynb) -----------------
MODEL_FILE     = MODEL_DIR / "model.joblib"
MODEL_MANIFEST = MODEL_DIR / "model_manifest.json"


if __name__ == "__main__":
    # `python paths.py` prints every path and whether it exists — a quick check
    # that the project is wired up correctly on this machine.
    print(f"ROOT = {ROOT}\n")
    for name, val in sorted(globals().items()):
        if name.isupper() and isinstance(val, Path):
            print(f"  {'OK  ' if val.exists() else 'MISS'}  {name:<22} {val}")
