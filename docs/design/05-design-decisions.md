# Design Decisions

## DynamoDB

We use DynamoDB as the database for the write path using a single table design. 

**Access patterns to satisfy:**

1. Get account by ID
2. Get merchant by ID — both to resolve the counterparty on capture and to validate the `merchant_id` on a new
   authorization
3. Create a merchant by ID — a conditional `PutItem` guarded on `attribute_not_exists(PK)` (see Merchant). Note
   what is *not* on this list: there is no "list all merchants" pattern. Merchant items share no partition, so
   enumeration would be a scan or a GSI on a constant partition key, and the API deliberately offers no route that
   needs it
4. Get authorization by ID
5. List authorizations for an account, newest first
6. List ledger entries for a party, by date range — for integrity checks and projection rebuild, **not** for the
   transaction-history endpoint, which is served from Aurora
7. Get every entry of one transaction, to verify it sums to zero
8. Look up an idempotency record by key
9. Find expired holds for release

**Table design:**

| Entity | PK | SK | GSI1PK | GSI1SK |
|---|---|---|---|---|
| Account | `ACCT#<id>` | `META` | — | — |
| Merchant | `MERCHANT#<id>` | `META` | — | — |
| Authorization | `ACCT#<id>` | `AUTH#<ts>#<authId>` | `AUTH#<authId>` | `META` |
| Ledger entry | `<party>` | `TXN#<ts>#<txnId>#<seq>` | `TXN#<txnId>` | `ENTRY#<seq>` |
| Idempotency | `IDEM#<key>` | `META` | — | — |

`<party>` is either `ACCT#<id>` or `MERCHANT#<id>` — both sides of a transaction use the same sort-key shape, so the
zero-sum check (access pattern 7) is one GSI1 query regardless of who the counterparty is.

Notes:

- Sort keys are time-prefixed so range queries and reverse scans come free.
- GSI1 handles lookup-by-id when you don't know the account.
- Idempotency records get a **TTL** attribute (24–48h). Free cleanup.
- Expired-hold sweep: a sparse GSI keyed on `expires_at` for holds only. Preferred over a TTL-driven stream event
  because TTL deletion timing is not guaranteed and can lag by hours — unacceptable for an invariant that says holds
  release automatically.
- Watch for hot partitions on high-volume accounts. Know what write sharding would look like even if you don't implement it.

**Placing a hold**

Authorize is where the sufficient-funds decision is made, so it is where the guard belongs. Nothing moves in
`current_balance` — only the reservation is taken.

```
TransactWriteItems([

  // 1. Reserve the funds. This ConditionExpression IS the overdraft
  //    protection; nothing downstream re-checks it.
  Update {
    PK: "ACCT#alice", SK: "META",
    UpdateExpression: "SET available_balance = available_balance - :amt",
    ConditionExpression: "available_balance >= :amt",
    Values: { ":amt": 7500 }
  },

  // 2. The hold itself, with an absolute expiry for the sweeper's GSI
  Put {
    PK: "ACCT#alice",
    SK: "AUTH#2026-07-30T10:00:00Z#auth-001",
    ConditionExpression: "attribute_not_exists(PK)",
    status: "PENDING",
    amount: 7500,
    merchantId: "mrch_bobs-store",
    expires_at: "2026-08-06T10:00:00Z"
  },

  // 3. Idempotency record
  Put {
    PK: "IDEM#<authorize-idempotency-key>", SK: "META",
    ConditionExpression: "attribute_not_exists(PK)",
    requestHash: "...", status: "COMPLETED",
    responseSnapshot: {...}, ttl: ...
  }
])
```

A `TransactionCanceledException` here needs `CancellationReasons` read positionally to tell the two failure modes
apart: item 1 failing is *insufficient funds*, item 3 failing is *a replayed idempotency key*. They are different
HTTP responses and conflating them is the easy bug.

**Updating the ledger**

When we capture an authorization we'll use DynamoDB `TransactWriteItems` to ensure that the ledger entries are created
and the account balance is properly updated

Note what capture does **not** do: it does not re-check sufficient funds. The money was already reserved at
authorize time, and re-testing `current_balance` here would spuriously fail a legitimate capture whenever other
holds have since been placed against the same account. Capture's only guard is the authorization's own state.

For example:

```
TransactWriteItems([

  // 1. Debit Alice's account balance. available_balance is untouched:
  //    the hold is released and the money leaves in the same instant,
  //    so the two changes cancel out exactly.
  Update {
    PK: "ACCT#alice", SK: "META",
    UpdateExpression: "SET current_balance = current_balance - :amt",
    Values: { ":amt": 7500 }
  },

  // 2. Credit the merchant's payable balance
  Update {
    PK: "MERCHANT#bobs-store", SK: "META",
    UpdateExpression: "SET payable_balance = payable_balance + :amt",
    Values: { ":amt": 7500 }
  },

  // 3. Ledger entry: debit (money leaving Alice's account)
  Put {
    PK: "ACCT#alice",
    SK: "TXN#2026-07-30T10:05:00Z#txn-500#0",
    txnId: "txn-500",
    partyType: "ACCOUNT",
    entryType: "DEBIT",
    amount: 7500,
    sourceAuthId: "auth-001"
  },

  // 4. Ledger entry: credit (money arriving in merchant payable)
  Put {
    PK: "MERCHANT#bobs-store",
    SK: "TXN#2026-07-30T10:05:00Z#txn-500#1",
    txnId: "txn-500",
    partyType: "MERCHANT",
    entryType: "CREDIT",
    amount: 7500,
    sourceAuthId: "auth-001"
  },

  // 5. Close the authorization. This ConditionExpression is the whole
  //    of invariant 3 — one capture, full amount, no second effect.
  //    `status` is a DynamoDB reserved word, so it must go through
  //    ExpressionAttributeNames as #status.
  Update {
    PK: "ACCT#alice", SK: "AUTH#...#auth-001",
    UpdateExpression: "SET #status = :captured",
    ConditionExpression: "#status = :pending",
    Names:  { "#status": "status" },
    Values: { ":captured": "CAPTURED", ":pending": "PENDING" }
  },

  // 6. Idempotency record
  Put {
    PK: "IDEM#<capture-idempotency-key>",
    SK: "META",
    ConditionExpression: "attribute_not_exists(PK)",
    requestHash: "...", status: "COMPLETED",
    responseSnapshot: {...},
    ttl: ...
  }
])
```

**Reversing a capture**

When the saga compensates (see Step Functions below), the reversal is itself a balanced transaction. It restores
both balances, moves the authorization to a terminal `REVERSED`, and deletes the capture's idempotency record so a
replay cannot return a success snapshot that no longer reflects reality.

```
TransactWriteItems([

  // 1-2. Restore both balances. available_balance moves with
  //      current_balance because the hold is already gone.
  Update {
    PK: "ACCT#alice", SK: "META",
    UpdateExpression: "SET current_balance   = current_balance   + :amt,
                           available_balance = available_balance + :amt",
    Values: { ":amt": 7500 }
  },
  Update {
    PK: "MERCHANT#bobs-store", SK: "META",
    UpdateExpression: "SET payable_balance = payable_balance - :amt",
    Values: { ":amt": 7500 }
  },

  // 3-4. Two NEW opposite-sign entries under a new txnId. The original
  //      entries are never touched — see ADR-5.
  Put { PK: "ACCT#alice",             SK: "TXN#...#txn-501#0",
        entryType: "CREDIT", amount: 7500, reversalOf: "txn-500" },
  Put { PK: "MERCHANT#bobs-store",    SK: "TXN#...#txn-501#1",
        entryType: "DEBIT",  amount: 7500, reversalOf: "txn-500" },

  // 5. Terminal state, guarded so a duplicate compensation is a no-op
  Update {
    PK: "ACCT#alice", SK: "AUTH#...#auth-001",
    UpdateExpression: "SET #status = :reversed",
    ConditionExpression: "#status = :captured",
    Names:  { "#status": "status" },
    Values: { ":reversed": "REVERSED", ":captured": "CAPTURED" }
  },

  // 6. Invalidate the capture's idempotency record
  Delete { PK: "IDEM#<capture-idempotency-key>", SK: "META" }
])
```

**Handling expired authorizations**

A lambda will be run every 15 minutes to remove expired authorizations. It will scan the sparse index and remove holds that
have `expires_at` >= 7 days. The lambda will be triggered using EventBridge Scheduler. The reason this is preferred over 
DynamoDB TTL is that it deletes itmes typically within a few days after their expiration.

**Why not use a relational database?**

DynamoDB has several advantages over a relational database. It is serverless, can scale automatically and is highly 
available by default. DynamoDB also supports transactions which will help us ensure atomicity when dealing with 
financial transactions, ensuring data integrity. As we'll run on demand the initial costs will be much less that 
having an Aurora Cluster or an RDS cluster.

## Aurora Serverless

Aurora is used in serverless mode for the read path. We use a separate database as the access patterns are different
for the read path. As we'll be using lambda we'll use RDS proxy to pool and share database connections.

**Why a second database at all?**

DynamoDB is built to satisfy the fixed, known-in-advance access patterns above — it is structurally unable to answer
ad hoc questions: aggregations, joins, arbitrary date-range scans, "top merchants by spend last quarter." Aurora
Postgres exists purely to serve that class of query. This gives the system two databases with two different jobs
rather than one database stretched past its access-pattern fit: DynamoDB is the source of truth for the write path,
Aurora is a derived, disposable read model. If Aurora were lost entirely, it could be rebuilt from DynamoDB.

Aurora serves `GET /accounts/me/transactions`. Balance is deliberately *not* served from here — it must be
current, and Aurora is eventually consistent by construction.

It is worth being honest about the strength of this argument at the current scope. Transaction history alone could
be served from DynamoDB: the ledger-entry sort key is already time-prefixed and paginating it is a `Query` with a
`LastEvaluatedKey` cursor. The justification for the second database is the *analytics* class of query, which is not
yet in scope. Aurora is therefore carried here on the strength of what it enables next, and because standing up a
CQRS read path — projector, at-least-once handling, projection lag, rebuild-from-scratch — is a deliberate goal of
this project rather than an incidental cost of it. A production system at this scope with no analytics roadmap
should use one database.

**How data gets there**

```
DynamoDB Streams → EventBridge Pipe → EventBridge → SQS → Lambda projector → RDS Proxy → Aurora Serverless v2
```

The projector reads batches from SQS and writes into Aurora using `psycopg` (v3). This is the CQRS pattern:
DynamoDB owns writes, Aurora is eventually consistent and exists only to serve reads that need SQL. Every hop in
that chain delivers **at-least-once**, so the projector must be idempotent — each stream record carries a monotonic
sequence number, and the projector upserts using that sequence to discard replays rather than double-apply them.

That sequence number is the load-bearing part, and it is worth noticing that it now has to survive a transform it
did not used to: the Pipe's input transformer (ADR-8) must carry the stream's `SequenceNumber` through into the
published event, or the projector loses the only thing that lets it tell a replay from a new record. Dropping it
would not fail loudly — the projection would simply start double-applying under retry.

Because the projection is derived, it must also support a full **rebuild from scratch** (replaying the table from a
DynamoDB export) if the projector logic changes or the read model needs to be repaired.

**Why RDS Proxy**

Lambda invocations scale out independently and can burst to hundreds of concurrent executions; each one opening its
own Postgres connection will exhaust Aurora's connection limit long before it exhausts CPU or ACU capacity. RDS
Proxy sits in front of Aurora and pools/multiplexes connections so bursty, concurrent Lambda invocations don't take
the database down. Connecting Lambda straight to Postgres is the failure mode to design around deliberately, not
discover in production.

**Network path out of the VPC**

The projector runs in a private subnet with **no NAT Gateway** — a deliberate cost guardrail, stated in the cost
model and again under Development cost. A private subnet with no NAT has no route to the public internet, so every
AWS API the function calls must be reachable through a VPC endpoint or the call simply times out at the end of the
socket timeout — the failure mode is a hang, not an error, which makes it worth designing rather than discovering.

Two **interface endpoints** (PrivateLink ENIs in the private subnets) are required:

| Endpoint | Needed by | Why |
|---|---|---|
| `com.amazonaws.<region>.sqs` | Aurora projector | The projector's event source is `sqs:ReceiveMessage`/`DeleteMessage`. This is the endpoint the whole read path depends on. |
| `com.amazonaws.<region>.xray` | Aurora projector, Transaction History Service | The X-Ray daemon in a VPC-configured function sends segments over the VPC network path. Without it, tracing silently stops at the VPC boundary while the function keeps working. |

Three things that look like they need an endpoint but do not:

- **KMS.** The customer-managed key is used by SQS and DynamoDB *server-side* — those services call KMS on the
  caller's behalf, so the role needs `kms:Decrypt` (see IAM) but the function never opens a socket to KMS.
- **CloudWatch Logs.** The Lambda service delivers logs outside the function's VPC network path.
- **Secrets Manager.** Only `SubmitSettlement` reads a secret, and it is not VPC-attached.

**No DynamoDB gateway endpoint.** Nothing inside the VPC talks to DynamoDB — the projector's input is the queue,
not the table, and the Transaction History Service reads only Aurora. Gateway endpoints are free, so one could be
added defensively, but an endpoint with no traffic is a claim in the diagram that the architecture does not
actually make. It gets added when something in the VPC first needs the table.

Interface endpoints bill hourly per AZ (~$7.30/month each per AZ) plus ~$0.01/GB processed, so this is not free the
way a gateway endpoint would have been — but it still lands under a NAT Gateway's hourly charge, and it keeps the
subnets genuinely without an internet route rather than merely unrouted by convention.

**AZ span: one, deliberately.** Both interface endpoints and both VPC-attached Lambdas are placed in a **single
AZ** for this build. Endpoints bill per AZ, so this halves the only line item in the project that does not scale to
zero when idle (see Development cost), and multi-AZ endpoints buy tolerance to an AZ failure — an availability
property this project is not exercising or testing. A production deployment of this design would span two AZs;
that is what the cost model above prices.

The one place this cannot be taken literally: **Aurora's DB subnet group and RDS Proxy both require subnets in at
least two AZs.** That is an AWS constraint, not a choice, and `terraform apply` rejects the single-subnet version
rather than degrading gracefully. So the VPC still *defines* subnets in two AZs — subnets themselves are free — and
the single-AZ decision applies to where the billable things actually run: the endpoint ENIs, the Lambda ENIs, and
the Aurora instance. Keep the projector's subnet and the Aurora writer's AZ the same, or every row the projector
writes crosses an AZ boundary at ~$0.01/GB in each direction.

**Cost posture**

Aurora Serverless v2 is configured with **minimum capacity of 0 ACUs**, so it scales to zero and stops billing
compute when idle. This only works if nothing keeps a connection open — a stray RDS Proxy connection or a forgotten
debugging session will keep it warm indefinitely. Aurora is provisioned only once the write path and projector are
in place, not from day one.

## Step Functions

An **Express** workflow kicks in after the ledger write succeeds, triggered via DynamoDB Streams → EventBridge, to
orchestrate what happens next: fraud screening, settlement submission, and notification — three separate calls that
can each fail independently.

**Express, with execution logging explicitly enabled.** This is the one piece of configuration that must not be
skipped. Express workflows do **not** retain queryable execution history the way Standard does — the console shows
little, and "which state did this `authId` fail in?" is unanswerable unless the state machine is configured to log
execution data to CloudWatch Logs (log level `ALL`, including execution data). That logging is opt-in, it costs
money, and turning it off to save a few dollars silently removes the debugging surface the runbook depends on
during an incident. Treat it as part of the workflow definition, not an observability nice-to-have.

Two Express constraints the saga has to live within:

- **5-minute execution ceiling.** `SubmitSettlement` retries with backoff, so the retry policy has to fit inside
  that budget — an unbounded backoff would have the workflow time out rather than reach `CompensateLedger`, which
  would leave a capture posted with no compensation. The retry count and max interval are a correctness concern
  here, not a tuning knob.
- **At-least-once execution.** An Express workflow triggered asynchronously can run more than once for the same
  event, so every step must be safe to repeat. Compensation already is: its `ConditionExpression` requires
  `CAPTURED`, so a second run is a no-op rather than a double reversal.

See ADR-2 for why Express over Standard.

**Fraud screening runs after the money moves.** This is deliberate and worth stating plainly, because the
conventional design gates the *authorization* on fraud. Here the ledger write is authoritative and fraud screening
is a downstream reviewer, so a `FLAGGED` result is handled by compensation rather than by refusal. The reason is
that exercising a real compensating transaction — with the reversal semantics of ADR-5 — is a primary goal of this
project, and a pre-authorization fraud check never produces one. A production system would screen at authorization
and keep compensation for the settlement failures that genuinely cannot be known in advance.

**The saga shape**

```
1. FraudScreen
   → APPROVED or FLAGGED
   → if FLAGGED: go to CompensateLedger, end.

2. SubmitSettlement
   → the step most likely to fail (simulated network/acquirer)
   → retries with backoff; if still failing: go to CompensateLedger, end.

3. NotifyCustomer
   → if this fails, just retry/log — a failed notification is never
     a reason to reverse a real payment.

CompensateLedger (only reached from steps 1 or 2 failing):
   → writes a balanced REVERSAL transaction — new opposite-sign entries
     under a new txnId, never deletes/edits the originals, per ADR-5.
   → restores current_balance and available_balance on the account,
     and payable_balance on the merchant.
   → moves the authorization PENDING→...→REVERSED (terminal).
   → deletes the capture's idempotency record, so a client replay
     re-executes and fails the PENDING guard instead of replaying a
     success snapshot that is no longer true.

   All five of these happen in one TransactWriteItems — see
   "Reversing a capture" above. A compensation that partially applied
   would itself break the invariant it exists to protect.
```

**Why it needs an orchestrator, not just chained Lambdas**

- Compensation logic (undoing a capture if a later step fails) is explicit and visible in the state machine, not buried in `try/except` blocks.
- Retries/backoff per step are declarative, not hand-rolled.
- Long-running steps don't tie up a billed Lambda invocation.
- Execution history is available per execution — for any `authId`, exactly which state ran, failed, or succeeded is
  answerable. This is what the runbook leans on during an incident, and on Express it exists only because execution
  logging is switched on deliberately.

**Since there's no real fraud model or card network, both steps are simulated**

**Fraud screen** — deterministic rules, not ML, plus a random-injection knob so you can reliably force the `FLAGGED` path in tests:

```python
def fraud_screen(event, context):
    amount = event["amount"]
    account_id = event["accountId"]

    if amount > 100_000:
        return {"decision": "FLAGGED", "reason": "amount_threshold"}
    if is_velocity_exceeded(account_id):
        return {"decision": "FLAGGED", "reason": "velocity"}
    if random.random() < 0.05:
        return {"decision": "FLAGGED", "reason": "random_test_injection"}
    return {"decision": "APPROVED"}
```

**Settlement submission** — a stand-in for "the outside world" that misbehaves on command via an env var, so you can trigger compensation deliberately during chaos testing rather than waiting for a real edge case:

```python
def submit_settlement(event, context):
    outcome = os.environ.get("SETTLEMENT_TEST_MODE", "normal")

    if outcome == "always_fail":
        raise SettlementTimeoutError("simulated acquirer timeout")
    if outcome == "flaky":
        if random.random() < 0.3:
            raise SettlementTimeoutError("simulated transient failure")
        time.sleep(random.uniform(0.5, 2))
    if outcome == "always_reject":
        return {"status": "REJECTED", "reason": "simulated_decline"}

    return {"status": "SETTLED", "settlementId": str(uuid.uuid4())}
```
