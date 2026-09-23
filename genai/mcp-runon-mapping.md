## MCP integration layer → Run·On mapping

Run·On's own description: *"MCP adapters and read-only APIs connecting to Bloomberg,
SimCorp, Aladdin, MSCI, data warehouses."* The Companies House MCP server
(`mcp_ch/`) is the same pattern applied to a different source, and every claim below
traces to a specific line of code I can point to if asked.

**1. Read-only is a deliberate boundary, not a limitation.**
Every function in `mcp_ch/tools.py` and `ch_client.py` is a GET — nothing writes back
to Companies House. That mirrors the boundary Run·On draws around Bloomberg and
SimCorp: an integration layer that can only read can't corrupt the system of record,
which is exactly the property you want when the thing on the other end is a bank's
book of record.

**2. It's a real API, not a mock, and I hit real production friction:**
- Rate limit — 600 requests / 5 min. `ch_client.py` paces calls to one every 0.55s
  and disk-caches every response (including 404s) so a second run costs zero API
  calls.
- Auth — HTTP basic auth with the API key as the username and a blank password
  (`_AUTH = (_KEY, "")`).
- Pagination — `get_paged()` follows `start_index`/`items_per_page` until
  `total_count` is reached.
- Templated fields — CH returns `description` as a code like
  `accounts-with-accounts-type-total-exemption-full`; `_describe()` decodes it before
  the model ever sees it. Same idea for `lender_group` and `days_late` — resolved in
  code, not left for the model to infer.
- Response shrinking — the raw CH payloads are 5-10x bigger than what `tools.py`
  returns; only the fields a credit analyst actually uses go into the model's
  context. That's the same context-discipline problem an adapter to Bloomberg or
  SimCorp would face, just with different field names.

None of that exists in a toy/mock tool — it's the kind of mess you only hit wiring up
a real external system, which is what makes it comparable to what Run·On's adapters
actually do.

**3. MCP is the adapter pattern itself.**
Bloomberg's API doesn't look like SimCorp's, which doesn't look like Companies
House's. MCP is what lets an agent (or Run·On's agents) call all of them through one
uniform tool-calling interface regardless of what's underneath. `mcp_ch/server.py` is
one instance of that pattern: four tools with a consistent shape (`get_company_profile`,
`get_filing_history`, `get_charges`, `get_officers`), each wrapping an API with its
own separate quirks, and `tools.py` kept deliberately separate from `server.py` so the
adapter logic is testable without a protocol round-trip — the same "protocol layer
stays thin" design an integration layer serving many firms would need.

**4. Spec detail worth having ready if pushed further.**
The 2026-07-28 MCP spec update moved to stateless request/response and cacheable tool
catalogs — exactly what an integration layer serving many firms at once needs, since
there's no shared session state to manage across many concurrent agent runs.

---
*Source: written after the interview-prep conversation on the MCP/connectors mapping
(prep plan Day 5, referenced again around Day 9). Meant to fold into the architecture
README section (Day 12) and the final write-up (Day 14) — keep this file as the raw
material rather than re-deriving it then.*
