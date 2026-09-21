# Lender Group Membership — companion to `lender_groups.py`

Human-readable version of `LLOYDS_RE` in `lender_groups.py`; the two must be updated
together. Not part of the policy corpus: the lead pipeline excludes companies with a
group charge before they become leads, so the agent only ever sees third-party charges.
The `lender_group` tag on each charge exists to make that explicit in the brief.

## Lloyds Banking Group entities (`lender_group == "own"`)

A charge is own-group if **any** entitled person matches one of these. Joint facilities
with a group entity are group facilities for policy purposes.

| Entity / brand | Notes |
|---|---|
| Lloyds Bank plc, Lloyds TSB, Lloyds Bank Commercial Finance, Lloyds Development Capital (LDC) | any name containing the word "Lloyds" |
| Bank of Scotland plc | also the legal entity behind Halifax |
| HBOS plc | |
| Halifax | division of Bank of Scotland |
| Agricultural Mortgage Corporation (AMC) | agri lender |
| Birmingham Midshires | mortgages |
| Cheltenham & Gloucester | mortgages |
| Bank of Wales | historic BoS brand |
| Black Horse | motor / asset finance |
| Scottish Widows | pensions / insurance |
| MBNA | credit cards, acquired 2017 |

## Known false positives (`lender_group == "third_party"`)

| Name | Why it is NOT group |
|---|---|
| The Royal Bank of Scotland plc | NatWest Group. Contains "Bank of Scotland" as a substring; the resolver excludes it explicitly. Mislabelling this as Lloyds once removed 4,553 RBS borrowers from the prospect list. |
| Lloyd's of London | insurance market, not the bank. The apostrophe breaks the word match. |

*Last reviewed:* 2026-09-21. Membership changes with acquisitions and brand retirements;
review when the pipeline's label definition is reviewed.
