# PayLedger Design Doc

This is the source of truth for the data model, API shape, invariants, ADRs, cost model, and security posture.
Where any other document disagrees with this one, this one wins. It is split into the files below so that each
concern can be read (and linked to) on its own; read them in order for the full review-board narrative, or jump
straight to the section you need.

1. [Overview](01-overview.md) — objective, scope, success criteria, functional/non-functional requirements, UX.
2. [Cloud Architecture](02-architecture.md) — the system diagram and the authorize → capture/void → reversal data
   flow.
3. [Data Model](03-data-model.md) — Account, Merchant, Authorization, Ledger Entry, Idempotency.
4. [API](04-api.md) — the endpoint list and the full error-response contract.
5. [Design Decisions](05-design-decisions.md) — DynamoDB (single-table design, access patterns, transactions),
   Aurora Serverless (the read path, CQRS projection), Step Functions (the capture saga).
6. [Architecture Decision Records](06-adrs.md) — ADR-1 through ADR-8, each with decision, rejected alternative,
   rationale, and revisit condition.
7. [Cost Model](07-cost-model.md) — steady-state production cost at 100 TPS, and the separate 3-week development
   budget.
8. [Security](08-security.md) — authentication, authorization, IAM, network boundary, encryption at rest/in
   transit, secrets management, abuse controls, audit logging, PII and data classification.
9. [Appendix](09-appendix.md) — known gaps still to write, and the implementation plan (see also
   `docs/roadmap.json`).
