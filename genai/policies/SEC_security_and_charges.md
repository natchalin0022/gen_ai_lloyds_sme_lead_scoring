# SEC — Security and Existing Charges

**Lloyds Bank (Demo) · SME Lending Policy · v1.0 · effective 2026-01-01**

Scope: how existing registered charges affect a company's suitability for a new
secured facility. Applies to all UK limited companies assessed for first-charge
commercial lending.

---

### SEC-01 — Outstanding third-party charge
**Outcome: REFER**

A company with one or more charges recorded as `outstanding` in favour of a lender
outside the group (`lender_group == "third_party"`) may not be offered a
first-charge facility. Any new facility ranks as a second charge unless the existing
charge is redeemed at completion.

*Applies when:* any `charges[].status == "outstanding" and charges[].lender_group == "third_party"`.

---

### SEC-02 — Clean security position
**Outcome: PROCEED**

A company with no charges registered at any time, or whose charges are all recorded as
`fully-satisfied`, holds a clean security position regardless of who the lenders were.
A first-charge facility may be offered subject to the remaining policies.

*Applies when:* `charges` is empty, or every `charges[].status == "fully-satisfied"`.

---

### SEC-03 — Satisfied charges disregarded
**Outcome: PROCEED**

Charges recorded as `fully-satisfied` are disregarded for ranking purposes and are not
themselves grounds for referral. The date of satisfaction may be noted as evidence of
prior borrowing capacity.

*Applies when:* `charges[].status == "fully-satisfied"`.

---

### SEC-04 — Part-satisfied charge treated as outstanding
**Outcome: REFER**

A charge recorded as `part-satisfied` indicates partial redemption only. It is treated
as outstanding for all ranking purposes until full redemption is evidenced.

*Applies when:* any `charges[].status == "part-satisfied"`.

---

### SEC-05 — Recently banked elsewhere
**Outcome: DECLINE**

Where a company has registered a charge in favour of a lender outside the group
(`lender_group == "third_party"`) within the last 6 months, it is presumed to have
satisfied its current funding requirement. Deprioritise for outbound contact.

*Applies when:* any `charges[].lender_group == "third_party"` with `created_on` within
180 days of assessment date.

---

### SEC-06 — Multiple simultaneous charges to one lender
**Outcome: REFER**

Two or more charges created within 30 days of each other in favour of the same lender
indicate a structured or tranched facility. Full redemption figures for all charges are
required before any refinance is quoted.

*Applies when:* two or more `charges[]` share a `persons_entitled` entry and their
`created_on` dates fall within 30 days.

---

### SEC-07 — Negative pledge
**Outcome: REFER**

Where an existing charge contains a negative pledge, no further charge may be created
over the same security without the existing lender's written consent. Consent must be
evidenced before a facility is offered.

*Applies when:* `charges[].particulars.contains_negative_pledge == true`.
*Note:* requires `particulars` to be retained by the charge summariser.

---

### SEC-08 — Floating charge over the whole undertaking
**Outcome: REFER**

A subsisting floating charge over the company's whole undertaking materially restricts
available security. Refer for assessment of what unencumbered assets remain.

*Applies when:* `charges[].particulars.contains_floating_charge == true` and status is
not `fully-satisfied`.
