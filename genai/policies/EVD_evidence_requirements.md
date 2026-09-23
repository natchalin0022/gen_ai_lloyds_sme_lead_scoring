# EVD — Evidence Requirements and Referral Triggers

**Lloyds Bank (Demo) · SME Lending Policy · v1.0 · effective 2026-01-01**

Scope: the minimum evidence required before a recommendation may be made, the rules
governing what a brief may assert, and the conditions under which an assessment must
be returned for further research rather than concluded.

---

### EVD-01 — Minimum evidence set
**Outcome: INSUFFICIENT EVIDENCE — return for research**

No recommendation may be made unless all of the following are held: the date of
incorporation, at least one accounts filing or confirmation that none exists, and a
complete charge position including the status and `lender_group` of every charge.

*Applies when:* any of the above is absent from the retrieved record.

---

### EVD-02 — Every assertion must be sourced
**Outcome: mandatory**

Every statement of fact in a brief must be traceable to a specific filing or charge
record. A statement that cannot be traced must be removed before the brief is issued.

---

### EVD-03 — No external knowledge
**Outcome: mandatory**

A brief must rely solely on the records retrieved for the subject company. General
knowledge about a lender, a sector or a market — including a lender's typical business,
specialism or reputation — must not appear in a brief, however accurate it may be.

*Rationale:* an assertion that cannot be traced to a record cannot be audited, and an
audit trail that contains untraceable claims is not a defence.

---

### EVD-04 — Charge particulars before collateral statements
**Outcome: INSUFFICIENT EVIDENCE — return for research**

Where the brief states or implies what a charge is secured against, the full charge
record must first be retrieved. The charge list alone does not carry collateral detail
and must not be used to infer it.

*Applies when:* a collateral statement is proposed and `particulars` has not been
retrieved for the charge concerned.

---

### EVD-05 — Limit on research cycles
**Outcome: PROCEED WITH QUALIFICATION**

An assessment may be returned for further research no more than twice. On the third
pass the brief must be issued with the heading "Thin evidence — RM verification
required" and the specific gaps listed.

*Applies when:* `research_attempts >= 2`.

---

### EVD-06 — Clause citation
**Outcome: mandatory**

Where a policy clause has determined an outcome, the brief must name that clause by its
identifier. A brief that states an outcome without naming the clause that produced it
is incomplete.

---

### EVD-07 — Conflicting outcomes
**Outcome: most restrictive applies**

Where two or more clauses produce different outcomes for the same company, the most
restrictive applies, in the order DECLINE > INSUFFICIENT EVIDENCE > REFER > PROCEED.
All triggered clauses must still be cited.
