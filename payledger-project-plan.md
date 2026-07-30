# PayLedger — 3-Week Backend & AWS Ramp-Up Project

A card authorization and double-entry ledger service, built to rehearse the patterns a Lead Software Engineer is expected to have opinions about: idempotency, transactional integrity without a transaction manager, event-driven choreography vs. orchestration, CQRS, and cloud cost discipline.

**Target stack:** Python 3.12, AWS Lambda Powertools for Python, Terraform, DynamoDB, Lambda, Step Functions, Aurora Serverless v2.

**Why Python here:** the goal of this project is AWS and distributed-systems judgment, not language acquisition. Capital One is polyglot (Java, Go, Python all in production), so the transferable asset is architecture, not syntax — staying in a language you're fluent in means every hour goes toward the parts that are actually language-agnostic: transaction design, IAM, observability, failure handling.

**Why this domain:** holds-vs-posted balances are genuinely subtle; double-entry gives a hard invariant to test against (entries always sum to zero); payment networks retry, so idempotency is non-negotiable rather than decorative.

---

## 1. Scope

### Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/authorizations` | Place a hold. Idempotent via `Idempotency-Key` header. |
| `POST` | `/authorizations/{id}/capture` | Convert hold to posted transaction. Supports partial capture. |
| `POST` | `/authorizations/{id}/void` | Release the hold. |
| `GET` | `/accounts/{id}/balance` | Returns both current and **available** balance. |
| `GET` | `/accounts/{id}/transactions` | Paginated history, cursor-based. |

### Invariants to enforce and test

1. For every transaction, the sum of all ledger entries equals zero.
2. `available_balance = current_balance - sum(active_holds)`.
3. An authorization can be captured at most once, for at most its authorized amount.
4. Replaying a request with the same idempotency key returns the original response, never a second effect.
5. Expired holds (default 7 days) release automatically without manual intervention.

### Explicitly out of scope

Multi-currency, FX, interest accrual, statement generation, chargebacks/disputes. Note them in the design doc as deliberate exclusions — that's the signal, not the omission.

---

## 2. Architecture

### Write path (serverless)

```
Client → API Gateway (REST, request validators)
       → Lambda authorizer (JWT)
       → Lambda: Python 3.12, AWS Lambda Powertools
       → DynamoDB (single table, on-demand)
```

Use **Lambda Powertools for Python** for the event handler / router (its `APIGatewayRestResolver`), input validation (Pydantic models — use these for every request/response, not just a nice-to-have), structured logging, tracing, and idempotency utilities. Powertools actually ships a built-in **idempotency decorator** backed by DynamoDB — worth using out of the box in week 1, then understanding well enough to explain what it's doing under the hood (conditional writes + TTL, same mechanics you'd hand-roll).

The core technique is a single `transact_write_items` call (via boto3) that atomically:

- **Update** the account balance with a `ConditionExpression` asserting sufficient available funds
- **Put** the ledger entries (debit + credit)
- **Put** the idempotency record with `attribute_not_exists(pk)`

If any condition fails, the whole thing rolls back. This is the serverless answer to "how do you get ACID without a transaction manager," and it's worth being able to explain cold — including its limits: 100 items max per call, no cross-region, and a `TransactionCanceledException` requires reading `CancellationReasons` in order to know *which* condition failed.

**Money handling:** use Python's `Decimal`, never `float`, or store everything as integer minor units (cents). This is a real interview topic in fintech — have an opinion and be able to defend it.

### Async path

```
DynamoDB Streams → EventBridge → Step Functions (capture saga)
                                   ├─ fraud screen
                                   ├─ settlement submission
                                   └─ notification
```

Use Step Functions **Express** workflows for the saga (cheap, high volume, 5-min ceiling) with compensating transactions on failure — a failed settlement must reverse the ledger posting, not delete it. Reversal entries, never mutation. SQS between stages, DLQ on every consumer, and partial batch failure reporting (`ReportBatchItemFailures`, supported natively by Powertools' batch processing utility) so one poison message doesn't re-drive nine healthy ones.

### Read path (CQRS)

```
DynamoDB Streams → Lambda projector (Python) → Aurora Serverless v2 (Postgres)
```

The read model exists to answer questions DynamoDB structurally can't: aggregations, joins, ad hoc date ranges, "top 10 merchants by spend last quarter." This is where RDS earns its place in the design and gives you a real answer to *"why two databases?"* — different access patterns, different consistency requirements, one source of truth. Use `psycopg` (v3) and connect through **RDS Proxy** so you don't exhaust Postgres connections from bursty concurrent Lambda invocations — this is a very real, very common failure mode worth designing around deliberately rather than discovering.

Expect to handle the hard parts: projection lag, at-least-once delivery (make projections idempotent via sequence numbers), and rebuild-from-scratch capability.

### Cross-cutting

- **AWS Lambda Powertools for Python** — structured JSON logging, X-Ray tracing (`@tracer.capture_lambda_handler`), EMF custom metrics (`@metrics.log_metrics`). Don't hand-roll these.
- Correlation ID generated at the edge, propagated through every hop including async, using Powertools' correlation-ID support.
- **Least-privilege IAM per function.** Not one shared role. This is the single most common review finding at a bank.
- Encryption at rest with a customer-managed KMS key; secrets in Secrets Manager with rotation configured.
- **Terraform** for IaC, modularized (`modules/dynamodb`, `modules/lambda`, `modules/step-functions`, `modules/aurora`); remote state in S3 with a DynamoDB lock table (nicely recursive for this project); GitHub Actions running `terraform plan` on PR and `terraform apply` on merge.
- Package Lambdas with **Lambda layers** for shared dependencies (boto3, Powertools, pydantic) so deploys stay fast and small.

---

## 3. DynamoDB single-table design

Spend real time here before writing code. This is where most people flail.

**Access patterns to satisfy:**

1. Get account by ID
2. Get authorization by ID
3. List authorizations for an account, newest first
4. List transactions for an account, by date range, paginated
5. Look up an idempotency record by key
6. Find expired holds for release

**Proposed keys:**

| Entity | PK | SK | GSI1PK | GSI1SK |
|---|---|---|---|---|
| Account | `ACCT#<id>` | `META` | — | — |
| Authorization | `ACCT#<id>` | `AUTH#<ts>#<authId>` | `AUTH#<authId>` | `META` |
| Ledger entry | `ACCT#<id>` | `TXN#<ts>#<txnId>#<seq>` | `TXN#<txnId>` | `ENTRY#<seq>` |
| Idempotency | `IDEM#<key>` | `META` | — | — |

Notes:

- Sort keys are time-prefixed so range queries and reverse scans come free.
- GSI1 handles lookup-by-id when you don't know the account.
- Idempotency records get a **TTL** attribute (24–48h). Free cleanup. (Or let Powertools' idempotency utility manage this for you.)
- Expired-hold sweep: either a TTL-driven stream event, or a sparse GSI keyed on `expiresAt` for holds only. The sparse GSI is the better answer — TTL deletion timing isn't guaranteed and can lag hours.
- Watch for hot partitions on high-volume accounts. Know what write sharding would look like even if you don't implement it.

---

## 4. Three-week plan

### Week 1 — Core correctness

- Domain model with **Pydantic**: `Account`, `Authorization`, `LedgerEntry`, `Money` (Decimal-backed, never float).
- Finalize the single-table design against the access-pattern list above.
- Idempotency layer — start with Powertools' decorator, then read its source to understand the conditional-write mechanics.
- Terraform modules + CI pipeline running on day 2, not day 10.
- Integration tests against **DynamoDB Local via Testcontainers** (the `testcontainers-python` package).
- **A property-based test with Hypothesis asserting the ledger always balances** after any random sequence of valid operations. This one test is worth more than fifty unit tests and it's a great thing to point at in conversation.

*Exit criteria: authorize / capture / void work end to end, idempotently, with the balance invariant verified under randomized input.*

### Week 2 — Distributed systems

- DynamoDB Streams → EventBridge, with a versioned event schema (Pydantic models doubling as the schema definition).
- Step Functions capture saga with real compensating transactions.
- Aurora Serverless v2 + the stream projector, connecting through RDS Proxy. **Create Aurora now, not in week 1.**
- Observability: CloudWatch dashboard, alarms on DLQ depth and p99 latency, X-Ray traces that show a full request path across async boundaries.

*Exit criteria: a capture flows through the saga, lands in Aurora, and you can trace one request end to end in X-Ray.*

### Week 3 — The part most people skip

- **Break it on purpose.** Kill a Lambda mid-saga. Throttle DynamoDB. Poison the queue. Force a `TransactionCanceledException`. Fix what breaks.
- Build a **DLQ replay tool** — a small CLI (Python + boto3) that inspects, edits, and re-drives failed messages.
- Load test with **k6**: watch cold starts (with and without provisioned concurrency — note SnapStart now supports Python as of late 2024, worth trying), throttling behavior, projection lag under sustained write pressure.
- Write everything up (section 5).

*Exit criteria: you can describe three failure modes you induced, what the system did, and what you changed.*

---

## 5. Deliverables that make this a *Lead* project

You already have the job. The goal isn't to prove you can code — it's to be effective in your first 90 days when you're reviewing other people's designs, in whatever language they're written in.

**Architecture Decision Records (5–6).** Each states the decision, the option rejected, and the conditions under which you'd revisit:

1. DynamoDB over Aurora for the write path
2. Step Functions orchestration over pure event choreography
3. Provisioned concurrency / SnapStart over accepting cold starts
4. Single-table over multi-table DynamoDB design
5. Reversal entries over mutable ledger records
6. EventBridge over direct SQS fan-out

**Design doc**, written as if for a review board: SLOs, failure modes, data retention, regional failure behavior, PII handling and what's tokenized.

**Cost model.** At 100 TPS sustained, what does this cost per month? Where is the cliff? Which line item dominates, and what's the lever? Leads get asked this constantly at Capital One and a vague answer lands badly.

**Runbook.** *"The ledger is out of balance"* — what do you do at 3am? Also: DLQ filling, projection lag alarm, Aurora at max ACU, RDS Proxy connection exhaustion.

**README** with a real architecture diagram and a one-command deploy (`terraform apply`).

---

## 6. Cost guardrails

Target: **$10–30 total** for the three weeks.

- **Set an AWS Budget alarm at $20 on day one.** Before writing any code.
- **Avoid NAT Gateway.** ~$32/month plus data processing, and it's the single most common way people get surprised. Use a *gateway* VPC endpoint for DynamoDB (free) and keep Lambdas off any internet route. If you need an interface endpoint, note that those do cost ~$7/month each.
- **Aurora Serverless v2 scales to zero ACUs** — set min capacity to 0 so it pauses when idle. Create it in week 2, and `terraform destroy` the moment week 3 testing is done. Verify the pause actually happens; a stray open connection (from RDS Proxy or a debugging session) keeps it warm and billing.
- DynamoDB **on-demand**, never provisioned, at this scale.
- X-Ray sampling at 5–10% once past initial debugging.
- CloudWatch Logs retention set to 7 days on every log group via Terraform. The default is *never expire* and it silently accumulates.
- `terraform destroy` at the end of each day in week 3 if you're not actively testing. Keep DynamoDB table definitions and Lambda code in git so re-`apply` is a five-minute operation, not a rebuild.

---

## 7. Optional stretch

- **MSK Serverless** replacing EventBridge for one flow. Capital One runs Kafka heavily, and partitioning/ordering/consumer-group semantics are worth having hands on (Python via `confluent-kafka` or `aiokafka`). Note that MSK Serverless has a real hourly floor — do this in a tight, deliberate window.
- **A second consumer** on the event stream, to prove the schema actually supports fan-out rather than just claiming it does.
- **Schema registry** with an explicit compatibility policy.
- **Multi-region read replica** design — as a written design, not an implementation. The write-up is the valuable artifact.
- If you want a taste of Go or Java without committing the whole project to it, port just the DLQ-replay CLI or one Lambda into whichever language you're curious about, in week 3, once the core is solid. Low-risk way to sample the ecosystem you might land in.

---

## 8. Interview-grade questions to be able to answer cold

- How does `transact_write_items` differ from a database transaction, and where does it fall short?
- Your idempotency record was written but the Lambda died before responding. What happens on retry?
- Why reversal entries instead of updating the original record?
- Streams give at-least-once delivery. How does your projector stay correct?
- Provisioned concurrency vs. SnapStart vs. accepting cold starts — when does each win, and what does it cost?
- Your available balance is wrong in production. Walk through the diagnosis.
- Why not just put everything in Aurora and skip DynamoDB?
- Why RDS Proxy instead of connecting Lambda straight to Postgres?

---

## Notes on language choice

This plan is written for Python because that's where you're fastest, and the point of this project is AWS depth, not syntax practice. Everything here — transaction design, IAM boundaries, observability, saga compensation — reads directly onto Java or Go if that's what you end up using day to day. If anything, doing the design work in a language you don't have to think about frees up the three weeks for the parts that actually transfer.
