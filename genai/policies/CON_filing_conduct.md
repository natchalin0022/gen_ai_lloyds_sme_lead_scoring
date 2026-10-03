# CON — Filing Conduct and Information Quality

**Lloyds Bank (Demo) · SME Lending Policy · v1.2 · effective 2026-10-02**

*Changes in v1.2:* CON-06 now applies when the next accounts are overdue, instead of when the
latest accounts are more than 18 months old. A company filing on time can hold accounts up to
21 months old (a 12-month period plus the 9-month filing window), so the 18-month test referred
every on-time company with a 31 March year end from 1 October each year. On the 2026-10-02 lead
list it was cited in 18 of 27 REFERs, and 5 of the 6 companies it referred on its own were filing
on time.

*Changes in v1.1:* CON-04 now attaches a condition instead of referring. Screening the
2026-09-14 lead list showed it applied to 77 of 80 SME prospects, so as a REFER it no
longer separated one lead from another. Missing earnings figures are a document to
collect, not an adverse indicator.

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

### CON-04 — Earnings not disclosed
**Outcome: PROCEED WITH CONDITION**

Micro-entity and total-exemption accounts do not disclose turnover or profit, so the
public record cannot show whether the company can service new borrowing. This is
missing information, not an adverse indicator, and it does not by itself change the
outcome: it ranks as PROCEED under EVD-07, and its condition stands whatever the overall
outcome. A PROCEED under this clause means the public record shows no adverse indicator;
it is not an assessment of affordability.

*Condition:* management accounts covering the most recent 12 months must be obtained
before a facility is offered.

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

Where the deadline for the next accounts has passed and they have not been filed, the
financial position on record is no longer current: newer accounts should exist and do not.
The age of the latest accounts does not show this on its own. A company filing on time can
hold accounts up to 21 months old (a 12-month period plus the 9-month filing window), and
older after an extended accounting period.

*Applies when:* at least one accounts filing is on record, and the next accounts' due date
(`next_accounts_due` on the company profile) is before the assessment date. A company with
no accounts on record is assessed under CON-05.

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
