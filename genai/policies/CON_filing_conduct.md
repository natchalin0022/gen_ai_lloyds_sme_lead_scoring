# CON — Filing Conduct and Information Quality

**Lloyds Bank (Demo) · SME Lending Policy · v1.0 · effective 2026-01-01**

Scope: what a company's Companies House filing record indicates about its
administrative conduct, and whether enough current financial information exists
to support a lending decision.

Statutory reference: annual accounts are due **9 months** after the accounting
reference date. A company's **first** accounts are due **21 months** after the date
of incorporation.

---

### CON-01 — Late filing of annual accounts
**Outcome: REFER**

Accounts filed more than 30 days after the statutory deadline indicate weak
administrative control. Refer with the calculated delay stated in the brief.

*Applies when:* `days_late > 30` for any accounts filing in the last 3 years.

---

### CON-02 — Materially late filing
**Outcome: DECLINE**

Accounts filed more than 180 days after the statutory deadline are treated as a
material conduct failure. Decline unless a documented explanation is held.

*Applies when:* `days_late > 180` for the most recent accounts filing.

---

### CON-03 — Pattern of late filing
**Outcome: REFER**

Two or more late accounts filings within the last 3 years constitute a pattern rather
than an isolated lapse, irrespective of the size of each delay.

*Applies when:* count of accounts filings with `days_late > 0` in the last 3 years >= 2.

---

### CON-04 — Micro-entity accounts
**Outcome: REFER**

Micro-entity accounts do not disclose turnover or profit. They are insufficient on their
own to assess serviceability. Management accounts covering the most recent 12 months
must be obtained before a decision.

*Applies when:* most recent accounts filing `description` contains `micro-entity` or
`total-exemption`.

---

### CON-05 — No accounts on record
**Outcome: DECLINE**

A company incorporated more than 21 months ago with no accounts filing on record is
outside appetite. The statutory first-accounts deadline has passed without filing.

*Applies when:* no filing with `category == "accounts"` and incorporation date more
than 21 months before assessment date.

---

### CON-06 — Stale financial information
**Outcome: REFER**

Where the most recent accounts are made up to a date more than 18 months before
assessment, the financial position on record is no longer current.

*Applies when:* most recent accounts `made_up_date` more than 18 months before
assessment date.

---

### CON-07 — Insolvency proceedings
**Outcome: DECLINE**

Any filing in the `liquidation` category, or any record of receivership, administration
or a winding-up petition, places the company outside appetite. No further assessment
is required.

*Applies when:* any filing with `category == "liquidation"`.

---

### CON-08 — Paper filing
**Outcome: PROCEED**

Paper filing is recorded for information only. It is not of itself an adverse indicator
and must not be presented as a compliance concern.

*Applies when:* `paper_filed == true`.
