"""Which lenders belong to Lloyds Banking Group — the ONE place this is decided.

The pattern list is copied verbatim from 1_CompaniesHouse.ipynb (Stage 3), where it
labels the training data. The agent must classify lenders the same way the pipeline
does, or a charge the model calls "third party" could be one the label calls Lloyds.

Entity resolution is a fact, not a judgement: resolve it here, tag each charge with
`lender_group` before the model sees it, and write policy clauses against the tag.
The human-readable membership list lives in lender_groups.md (next to this file) and must
be updated together with this file.
"""
import re

LLOYDS_PATTERNS = [
    r"\blloyds\b",                       # Lloyds Bank, Lloyds TSB, Lloyds Commercial Finance, Lloyds Dev. Capital
    # NOTE the lookbehind: a bare "bank of scotland" also matches "The ROYAL Bank of
    # Scotland" (NatWest Group), which mislabelled 14,305 competitor charges as Lloyds
    # -- 26% of all matches -- and wrongly excluded 4,553 RBS borrowers from the
    # prospect population. Those are exactly the poaching targets the lead list wants.
    r"(?<!royal )bank of scotland",      # Bank of Scotland plc (Halifax's legal entity too)
    r"\bhbos\b",                         # HBOS plc
    r"agricultural mortgage corp",       # AMC — Lloyds' farm/agri lender
    r"\bhalifax\b",                      # Halifax (division of Bank of Scotland)
    r"birmingham midshires",             # BM — mortgages
    r"cheltenham (?:& |and )gloucester", # C&G — mortgages
    r"bank of wales",                    # historic BoS brand
    r"black horse",                      # Lloyds motor/asset finance
    r"scottish widows",                  # pensions/insurance
    r"\bmbna\b",                         # credit cards (Lloyds acquired 2017)
]
LLOYDS_RE = re.compile("|".join(LLOYDS_PATTERNS), re.I)

OWN, THIRD_PARTY = "own", "third_party"


def is_lloyds_group(name: str | None) -> bool:
    """True if a lender name (as it appears in `persons_entitled`) is a Lloyds entity."""
    return bool(name) and LLOYDS_RE.search(name) is not None


def lender_group(names: list[str] | str | None) -> str:
    """Classify a charge by its lender(s): "own" if ANY name is Lloyds-group, else "third_party".

    A charge is own-group if any entitled person is — joint facilities with a Lloyds
    entity are Lloyds facilities for policy purposes.
    """
    if isinstance(names, str):
        names = [names]
    return OWN if any(is_lloyds_group(n) for n in names or []) else THIRD_PARTY


if __name__ == "__main__":
    cases = {
        "Lloyds Bank plc":                    OWN,
        "LLOYDS TSB BANK PLC":                OWN,
        "Bank of Scotland plc":               OWN,
        "The Royal Bank of Scotland plc":     THIRD_PARTY,   # the lookbehind trap
        "Royal Bank of Scotland Plc":         THIRD_PARTY,
        "Halifax plc":                        OWN,
        "Black Horse Limited":                OWN,
        "MBNA Limited":                       OWN,
        "Interbay Funding Limited":           THIRD_PARTY,
        "Barclays Bank UK plc":               THIRD_PARTY,
        "Lloyd's of London":                  THIRD_PARTY,   # insurer, not the bank
        "":                                   THIRD_PARTY,
    }
    bad = [(n, lender_group(n), want) for n, want in cases.items() if lender_group(n) != want]
    for n, got, want in bad:
        print(f"FAIL  {n!r}: got {got}, want {want}")
    print(f"{len(cases) - len(bad)}/{len(cases)} cases pass")
