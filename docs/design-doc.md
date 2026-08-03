# Payledger System

## Overview

### Objective

To build a card authorization and double-entry ledger system. This system should allow users to authorize
payments and manage their accounts by viewing their balance and their transaction history.

### Scope

In scope: placing an authorization hold, capturing a hold into a posted transaction, voiding a hold, querying an
account's current and available balance, and paginated transaction history.

Explicitly out of scope: multi-currency/FX, interest accrual, statement generation, chargebacks/disputes, and
**partial capture** — a capture is all-or-nothing for the full authorized amount. These
are deliberate exclusions, not omissions — they keep the invariant surface (hold lifecycle + double-entry balance)
tractable within the project's timeline.


### Success Criteria

The system is considered correct if the following invariants hold under all conditions, including concurrent
requests and randomized/adversarial input:

1. For every transaction, the sum of all ledger entries equals zero.
2. `available_balance = current_balance - sum(active_holds)`, at all times.
3. An authorization can be captured at most once, for exactly its authorized amount.
4. Replaying a request with the same idempotency key returns the original response, never a second effect. The one
   exception is a capture the saga later reverses: compensation invalidates the idempotency record, so a replay
   re-executes and fails the `PENDING` guard rather than returning a stale success.
5. Expired holds (default 7 days) release automatically, without manual intervention.

### Functional Requirements

- Users should be able to view their account's balance
- Users should be able to view their transaction history
- Users should be able to authorize payments 

### Non-Functional Requirements

- Maintain financial integrity by preventing double charges (Idempotency)
- The transaction history can have eventual consistency. However, reading balances must always
return the latest value.

### User Experience

The interaction is done only through the exposed rest endpoints. There will be no GUI or other modes
of interaction.

### Cloud Architecture

```mermaid
flowchart TB
    Cardholder(["Cardholder (only API caller)"])

    subgraph Edge["AWS Cloud"]
        APIGW["API Gateway"]
        Cognito["Cognito"]
        
        AuthServ["Authorization Service (lambda)"]
        DynamoDB[("Dynamo DB")]
        DDBS["DynamoDB Streams"]
        Pipe["EventBridge Pipe (filter + transform)"]
        
        EB["EventBridge (event bus)"]
        Scheduler["EventBridge Scheduler"]
        EHS["Expired Hold Sweeper (lambda)"]

        subgraph SF["Step Functions"]
            FraudLambda["Fraud Lambda"]
            SettlementLambda["Submit Settlement Lambda"]
            NotifyLambda["Notification Lambda"]
            CompensateLambda["Compensate Ledger Lambda"]
        end

        Secrets["Secrets Manager (acquirer credentials)"]

        subgraph VPC["VPC (private subnets, single AZ, no NAT)"]
            ProjectorLambda["Aurora Projector Lambda"]
            RDSP["RDS Proxy"]
            Aurora[("Aurora (serverless)")]
            TxnServ["Transaction History Service (lambda)"]
            SQSEP{{"SQS interface endpoint"}}
            XRayEP{{"X-Ray interface endpoint"}}
        end

        SQS["SQS (projector queue)"]
        ProjDLQ["Projector DLQ"]
        EBDLQ["EventBridge target DLQ"]
        FwdDLQ["Pipe source DLQ"]

        SNS["SNS notifications"]

        BalanceServ["Balance Service (lambda)"]
    end

    Cardholder --> |"sign in"| Cognito
    Cardholder --> |"JWT"| APIGW
    APIGW <--> |"validate JWT"| Cognito
    APIGW --> AuthServ
    AuthServ --> DynamoDB
    DynamoDB --> DDBS --> Pipe --> |"PutEvents"| EB
    Pipe --> |"source DLQ"| FwdDLQ
    
    EB --> SF
    EB --> SQS --> SQSEP --> ProjectorLambda
    EB --> |"undeliverable target"| EBDLQ
    SQS --> |"redrive after maxReceiveCount"| ProjDLQ
    
    FraudLambda --> |"Approved"| SettlementLambda
    FraudLambda --> |"Rejected"| CompensateLambda
    SettlementLambda --> |"Approved"| NotifyLambda
    SettlementLambda --> |"Rejected"| CompensateLambda
    SettlementLambda --> Secrets
    CompensateLambda --> DynamoDB
    CompensateLambda --> NotifyLambda
    NotifyLambda --> SNS
    
    ProjectorLambda --> RDSP --> Aurora
    ProjectorLambda -.-> |"traces"| XRayEP
    TxnServ -.-> |"traces"| XRayEP
    APIGW --> BalanceServ --> DynamoDB
    APIGW --> TxnServ --> Aurora

    Scheduler --> |"schedule"| EHS --> DynamoDB
```

Balance reads go to DynamoDB with a strongly-consistent `GetItem`, not to Aurora — the non-functional requirement
says balance must always return the latest value, and Aurora is by construction an eventually-consistent derived
model. Transaction history goes to Aurora, where eventual consistency is acceptable.

#### Data flow

**Authorizing payments**
1. A customer has $500 in their account.
2. Customer books a hotel room at $400
```
POST /authorizations
Authorization: Bearer <jwt>          // sub → account_id
Idempotency-Key: 7c3a1e2f-...
{
  "amount": 40000,        // cents
  "merchant_id": "mrch_456",
  "expires_in_days": 7
}
```
3. Write `Authorization` record for $400 with pending status. `current_balance` stays $500 (no money has moved).
`available_balance` becomes $100 ($500 - $400 hold).
The customer's other card swipes will now only succeed if they're ≤ $100.
4. Capture the payment. The capture takes no body — it is always for the full authorized amount.
```
POST /authorizations/{authId}/capture
Idempotency-Key: 9f2b4d1c-...
```
Update the authorization record status to captured. Write two new ledger entries (debit the account, credit the
merchant). `current_balance` becomes $100. `available_balance` stays $100 — the hold is released at the same moment
the money leaves, so the two changes cancel out.
5. Alternative, the payment gets canceled.
```
POST /authorizations/{authId}/void
Idempotency-Key: 3e8a7b5d-...
```
Update authorization record to voided. `current_balance` stays $500, `available_balance` goes back to $500.
6. Alternative, the capture is reversed by the saga (fraud flagged, or settlement permanently failed). Write a
balanced REVERSAL transaction, restore `current_balance` to $500 and `available_balance` to $500, and move the
authorization to `REVERSED`. The original ledger entries are never touched.

### Data Models

All monetary amounts are integer **minor units (cents)**. No floats anywhere; `Decimal` is used only at the
presentation boundary if a decimal representation is ever needed.

**Account**
- account_id (str)
- current_balance (int)
- available_balance (int)

`available_balance` is materialized rather than derived so that a balance read is a single `GetItem` instead of a
query summing every active hold. The cost of that choice is that it must be maintained transactionally by every
operation that touches a hold — authorize, capture, void, expiry, reversal — and the sufficient-funds
`ConditionExpression` guards it at **authorize** time, which is the only moment the check is meaningful. Invariant 2
therefore becomes an assertion to test against (the property-based test recomputes it from the holds), not the
mechanism by which the value is produced.

**Merchant**
- merchant_id (str)
- name (str)
- payable_balance (int) — the merchant's side of the ledger; credited on capture, debited on reversal
- created_at (str) — ISO-8601 UTC

Merchants are the counterparty on every transaction. They are a distinct entity rather than a kind of account: they
have no holds, no available balance, and no authorization lifecycle, so folding them into `Account` would mean an
entity where half the fields are permanently unused.

**Merchants are seeded, not created.** Because the cardholder is the only API caller (ADR-7), nothing in the API
creates a merchant. A **fixed set is seeded in Terraform** as `aws_dynamodb_table_item` resources — the merchant
catalogue is configuration, not runtime state. Two consequences worth stating rather than discovering:

- `POST /authorizations` carries a client-supplied `merchant_id`, so the Authorization Service must **`GetItem` the
  merchant and reject an unknown id with a 400** before placing the hold. Without that check a typo produces a
  perfectly balanced transaction against a merchant that does not exist, and the ledger invariant will not catch it
  — both sides sum to zero regardless.
- `payable_balance` is seeded at `0` and thereafter only ever moved by capture and reversal. Terraform must not
  manage that attribute on subsequent applies, or an apply will silently reset a balance the ledger considers
  authoritative. Seed the item once with `lifecycle { ignore_changes = [item] }`, or seed only the identity
  attributes and let the first capture create the balance.

**Authorization**
- authorization_id (str)
- account_id (str)
- merchant_id (str)
- status (enum PENDING, CAPTURED, VOIDED, EXPIRED, REVERSED)
- amount (int) — the authorized amount; a capture is always for exactly this amount
- expires_at (str) — ISO-8601 UTC; the attribute the sparse expiry GSI is keyed on
- created_at (str) — ISO-8601 UTC
- updated_at (str) — ISO-8601 UTC

`expires_in_days` is a *request* field only. It is resolved to an absolute `expires_at` at write time, because the
expiry sweep needs an absolute value to range-query against.

Because partial capture is out of scope, there is no `captured_amount` and no `PARTIALLY_CAPTURED` status: the
`PENDING → CAPTURED` transition guarded by a `ConditionExpression` is the whole of invariant 3's enforcement.

**Ledger Entry**
- transaction_id (str)
- party_id (str) — the account or merchant this entry belongs to
- party_type (enum ACCOUNT, MERCHANT)
- source_authorization_id (str)
- amount (int)
- entry_type (enum DEBIT, CREDIT)
- created_at (str) — ISO-8601 UTC, matches the timestamp embedded in the sort key

An entry belongs to a *party*, not specifically an account, because the credit side of every transaction lands on a
merchant. `party_type` mirrors the key prefix (`ACCT#` / `MERCHANT#`) so an entry can be resolved back to its owner
without a second lookup.

**Idempotency**
- idempotency_key (str)
- request_hash (str) — hash of the request body; a replay with the same key but a different payload is a 422, not a
  silent replay of the original response
- status (enum IN_PROGRESS, COMPLETED) — lets a retry distinguish "still running" from "done", which is what makes
  the crash-after-write-before-response case recoverable
- response_snapshot (json) — the original response, returned verbatim on replay
- ttl (int) — epoch seconds, 24–48h

Saga compensation **deletes** the idempotency record for a capture it reverses. Returning the stored success
snapshot after the money has been reversed would be a lie about current state; deleting it means a replay
re-executes the capture, hits the `PENDING` guard against a now-`REVERSED` authorization, and returns a terminal
error instead.

### API

The API will consist of the following endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/authorizations` | Place a hold. Idempotent via `Idempotency-Key` header. |
| `POST` | `/authorizations/{id}/capture` | Convert hold to posted transaction, for the full authorized amount. No body. Idempotent via `Idempotency-Key`. |
| `POST` | `/authorizations/{id}/void` | Release the hold. Idempotent via `Idempotency-Key`. |
| `GET` | `/accounts/me/balance` | Returns both current and **available** balance. Served from DynamoDB, strongly consistent. |
| `GET` | `/accounts/me/transactions` | Paginated history, cursor-based. Served from Aurora, eventually consistent. |

Account, merchant, and authorization identifiers are opaque strings in the public API. The `ACCT#` / `MERCHANT#` /
`AUTH#` prefixes in the table design below are an internal key encoding and are never exposed to or accepted from
clients.

Account-scoped routes are addressed as `me`, not by account id, and `POST /authorizations` carries no `account_id`
field: the account is always derived from the caller's validated `sub` claim. See Security → Authorization for why
this is a shape rather than a validation rule.

**There is no merchant endpoint, by design.** The cardholder is the only caller (ADR-7) and merchants are seeded as
configuration (see Merchant, above), so there is nothing to create and nobody to create it. `POST /authorizations`
takes a `merchant_id` from the client, which the handler validates against the seeded set and rejects with a 400 if
unknown — the one place the API accepts an identifier it did not derive from the token.


## Design Decisions

### DynamoDB

We use DynamoDB as the database for the write path using a single table design. 

**Access patterns to satisfy:**

1. Get account by ID
2. Get merchant by ID — both to resolve the counterparty on capture and to validate the `merchant_id` on a new
   authorization. There is deliberately no "create merchant" pattern; the set is seeded (see Merchant).
3. Get authorization by ID
4. List authorizations for an account, newest first
5. List ledger entries for a party, by date range — for integrity checks and projection rebuild, **not** for the
   transaction-history endpoint, which is served from Aurora
6. Get every entry of one transaction, to verify it sums to zero
7. Look up an idempotency record by key
8. Find expired holds for release

**Table design:**

| Entity | PK | SK | GSI1PK | GSI1SK |
|---|---|---|---|---|
| Account | `ACCT#<id>` | `META` | — | — |
| Merchant | `MERCHANT#<id>` | `META` | — | — |
| Authorization | `ACCT#<id>` | `AUTH#<ts>#<authId>` | `AUTH#<authId>` | `META` |
| Ledger entry | `<party>` | `TXN#<ts>#<txnId>#<seq>` | `TXN#<txnId>` | `ENTRY#<seq>` |
| Idempotency | `IDEM#<key>` | `META` | — | — |

`<party>` is either `ACCT#<id>` or `MERCHANT#<id>` — both sides of a transaction use the same sort-key shape, so the
zero-sum check (access pattern 6) is one GSI1 query regardless of who the counterparty is.

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

### Aurora Serverless

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

### Step Functions

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

## Architecture Decision Records

Each ADR states the decision, the alternative rejected, the reasoning, and the condition under which it would be
revisited.

### ADR-1: DynamoDB over Aurora for the write path

**Decision:** The write path (accounts, authorizations, ledger entries, idempotency records) is DynamoDB, not
Aurora/Postgres.

**Rejected:** Aurora as the single database for both writes and reads.

**Rationale:** The write path's access patterns are fixed and known in advance (get by ID, list by account, look up
idempotency key) — exactly what a single-table DynamoDB design is built for. `TransactWriteItems` gives atomic,
conditional multi-item writes, which is what's needed to move a hold and write ledger entries together. DynamoDB is
also serverless with true scale-to-zero economics on-demand, whereas an always-on Aurora writer has a floor cost
even when idle.

**Revisit when:** the write path needs ad hoc queries, multi-row joins, or strong relational constraints that
outgrow what a handful of fixed access patterns can express — at that point the operational cost of running
Aurora as the primary store may be worth it.

### ADR-2: Step Functions orchestration over pure event choreography

**Decision:** The capture saga (fraud screen → settlement submission → notification) is orchestrated with Step
Functions **Express** workflows with execution logging enabled, with explicit compensating transactions on failure.

**Rejected:** Pure choreography — each service reacting to the previous service's event with no central
coordinator. Also rejected: Standard workflows for the saga.

**Rationale:** A multi-step financial saga needs a single place that knows the full sequence, can time out, and can
run reversal logic when a downstream step fails. Choreography spreads that knowledge across every consumer, making
"what state is this capture actually in?" hard to answer and hard to debug during an incident.

Express over Standard is the second half of this decision, and it is a cost decision made with open eyes. Standard
retains per-execution history natively for 90 days with no configuration, which is genuinely the better debugging
experience. But Standard bills **per state transition** where Express bills **per execution plus duration**: on
this cost model's own numbers, the same saga is roughly $3,900/month on Standard against roughly $60/month on
Express — a ~60× difference for a debugging convenience, not a capability. Express buys back most of that
capability by logging execution data to CloudWatch Logs, at a cost of tens of dollars rather than thousands.

The trade is explicit: Express is chosen, and the CloudWatch Logs configuration that makes it debuggable is treated
as mandatory rather than optional. An Express workflow without execution logging would be the worst of both worlds
— cheap and undiagnosable.

**Revisit when:** *(a)* the number of independently-scaling, loosely-coupled consumers grows large enough that a
central orchestrator becomes a bottleneck or a single point of coordination failure — at that point event
choreography (or a mix, with orchestration only for the compensating-transaction-critical steps) is worth
reconsidering; or *(b)* the saga needs to exceed Express's **5-minute** ceiling or needs the stronger
exactly-once-style execution semantics Standard provides, at which point Standard's state-transition bill becomes
the price of correctness rather than of convenience. A hybrid — Express on the happy path, Standard reserved for
the compensation branch — is the middle option if only the reversal path needs that guarantee.

### ADR-3: Provisioned concurrency / SnapStart over accepting cold starts

**Decision:** Latency-sensitive Lambdas (the synchronous write path: authorize/capture/void, balance reads) use
either provisioned concurrency or SnapStart rather than accepting on-demand cold starts.

**Rejected:** Accepting cold starts as-is, relying only on Lambda's default warm-pool behavior.

**Rationale:** Card authorization is a synchronous, user-facing call — a multi-second cold start on `POST
/authorizations` is a bad customer experience and, at the margin, a lost sale (the same pressure card networks
apply with their own timeout budgets). SnapStart (Python support as of late 2024) and provisioned concurrency both
remove that tail at different cost/complexity trade-offs, worth measuring against each other under load rather than
assumed.

**Revisit when:** traffic is high and steady enough that the Lambda stays warm on its own, or cost pressure makes the
provisioned-concurrency/SnapStart overhead not worth a tail-latency improvement nobody is measuring.

### ADR-4: Single-table over multi-table DynamoDB design

**Decision:** Accounts, authorizations, ledger entries, and idempotency records all live in one DynamoDB table,
distinguished by key prefix, per the table design above.

**Rejected:** A separate table per entity type.

**Rationale:** The core write operation — capture — must atomically touch the account balance, ledger entries, the
authorization status, and the idempotency record in one `TransactWriteItems` call. DynamoDB transactions are
constrained more easily (and cheaply) within a single table, and a single table also means one set of
capacity/throughput knobs to reason about instead of several. The access-pattern list was enumerated up front
specifically to make this single-table design tractable.

**Revisit when:** an entity's access patterns diverge so far from the others (radically different read/write
volume, different scaling or backup requirements) that sharing a table creates more operational coupling than the
transactional convenience is worth.

### ADR-5: Reversal entries over mutable ledger records

**Decision:** Correcting a posted transaction (e.g. a failed settlement after capture) is done by writing new,
opposite-sign ledger entries — never by updating or deleting the original entries.

**Rejected:** Mutating or deleting the original ledger entry to "fix" it.

**Rationale:** The core invariant is that every transaction's entries sum to zero and the ledger is an append-only,
auditable record — a requirement that comes directly from the domain, not a technical preference. A mutated ledger
is no longer a trustworthy audit trail: it can't answer "what did the balance look like at 3pm yesterday" or survive
a dispute investigation. A reversal is itself a balanced transaction, so the zero-sum invariant holds through
corrections, not just through the happy path.

**Revisit when:** never, for posted entries — this is a hard invariant of the domain, not a scoping choice. (Ledger
entries for an authorization that is still `PENDING`, i.e. a hold with no posted movement, are not in scope of this
rule.)

### ADR-6: EventBridge over direct SQS fan-out

**Decision:** DynamoDB Streams feed EventBridge, which fans out to Step Functions and to the Aurora projector,
rather than the stream feeding SQS queues directly.

**Rejected:** Direct SQS fan-out from the stream consumer to each downstream consumer.

**Rationale:** EventBridge decouples "an event happened" from "who currently cares about it" — new consumers (a
second projector, a fraud-analytics stream) can subscribe via a rule without touching the producer or existing
consumers, and schema/versioning is centralized at the bus rather than duplicated per queue. SQS still sits between
EventBridge and each individual consumer for buffering, retry, and DLQ semantics — EventBridge and SQS are
complementary here, not a replacement for one another.

**Revisit when:** the event volume is high enough, or the fan-out pattern static enough, that EventBridge's
per-event cost and added hop stop being worth the routing flexibility — a fixed, small number of consumers may be
simpler and cheaper wired directly to SQS.

### ADR-7: Cardholder as the sole API caller

**Decision:** The **cardholder** is the only authenticated caller. Every endpoint acts on the caller's own account,
derived from the Cognito `sub`. Merchants remain a first-class entity in the data model and the ledger, but have no
authentication path and cannot call the API.

**Rejected:** Merchant-initiated flows, in which the merchant's system places the hold and captures it and the
cardholder never touches the API — which is how real card authorization actually works.

**Rationale:** This is a deliberate divergence from the real domain, taken to keep the authorization model
single-axis. With one caller class there is exactly one ownership rule — `account_id == sub` — and it can be
enforced by the *shape* of the API (`/accounts/me/...`, no `account_id` in any request body) rather than by a check
each handler has to remember. That property is the thing worth building and demonstrating here; a second caller
class would dilute it into a pair of conditional rules before the first one has been proven.

The cost of the divergence is that the merchant side of every transaction is written by the system on the
cardholder's behalf rather than by the merchant, so `payable_balance` is maintained without any merchant ever
authenticating. The ledger is still correct and still balances — merchants are counterparties in the data model,
not actors in the API.

**Revisit when:** merchant-initiated authorization becomes a goal. That means a second caller class (Cognito app
clients using `client_credentials` with a `merchant_id` claim and scopes such as `authorizations:capture`) and a
second ownership rule (the merchant on the authorization must match the caller's `merchant_id`), which in turn
means the `/accounts/me/...` shape no longer carries the whole enforcement burden on its own.

### ADR-8: EventBridge Pipes over a forwarder Lambda

**Decision:** The DynamoDB Streams → EventBridge link is an **EventBridge Pipe**, using the pipe's filter and input
transformer. There is no forwarder function.

**Rejected:** A Lambda with a DynamoDB Streams event source mapping that calls `events:PutEvents`.

**Rationale:** The job is "poll a stream, drop what nobody wants, reshape the rest, put it on a bus" — which is
precisely the managed integration Pipes exists to be, and a Lambda doing it is a function whose entire body is
plumbing. Removing it removes a deployment package, a runtime version to keep current, a cold-start path on the
write-to-projection latency, a concurrency setting to tune, and a log group to pay for.

Two things follow that are worth more than the code deletion:

- **Filtering happens before the bus.** Records that no consumer wants are dropped at the source, so they are never
  published and never billed at EventBridge's $1/M. A forwarder Lambda would have to publish first and let rules
  discard, or reimplement filtering in code.
- **It gives the compensation loop a natural home.** `CompensateLedger` writes to DynamoDB, and those writes flow
  back through the stream toward the saga that produced them (see In progress). A pipe filter can exclude them at
  the source, which is a better place to break the cycle than a negative condition in every downstream rule.

**The cost of this choice is that there is no arbitrary code in the transform,** and that has a concrete casualty:
the week-2 plan of "a versioned event schema, Pydantic models doubling as the schema definition" assumed a
producer that constructs events in Python. Pipes' input transformer is a JSON template, not a function, so the
event shape is now declared in Terraform and Pydantic becomes a **consumer-side** contract — the projector and the
saga validate what arrives rather than the producer guaranteeing what leaves. Worse, stream records arrive as
DynamoDB JSON (`{"S": "..."}`), so the template carries the unmarshalling, and templates are an unpleasant place to
do that for anything deeply nested.

**Revisit when:** the transform stops being expressible as a template — genuine enrichment, conditional shaping, or
a schema version negotiation. Pipes supports an enrichment step for exactly this, but an enrichment Lambda is the
function this ADR just removed, so reaching for it means the decision has inverted and should be re-argued rather
than quietly patched.

## Cost Model

This estimates steady-state production cost at **100 TPS sustained, 24/7** — a capacity-planning exercise, distinct
from the cost of *building* the project (see Development cost below).

### Assumptions

- 100 TPS blended average → 100 × 2,592,000s ≈ **259.2M requests/month**.
- Traffic split: ~30 TPS write path (`authorize`/`capture`/`void`), ~70 TPS read path (`balance`/`transactions`).
- Of the write traffic, only `capture` runs the full saga and writes ledger entries; `authorize`/`void` are lighter
  DynamoDB operations. Captures are taken as ~10 TPS of the 30 — roughly **25.9M captures/month**. Figures below are
  order-of-magnitude, not a quote — the point is to reason about the shape of the bill, not to be precise to the
  dollar.
- The saga is ~6 states per execution (three task states plus the choice/terminal states around them), running
  ~1s end to end. On Express this bills as one execution plus duration; state count affects the bill only through
  the duration and the log volume it produces.

### Cost by component (monthly, us-east-1 list pricing)

| Component | Est. monthly cost | Driver |
|---|---|---|
| API Gateway (REST) | ~$910 | $3.50 / million requests × 259.2M |
| Lambda (sync handlers) | ~$490 | $0.20/M invocations + GB-s compute at ~512MB / ~200ms |
| DynamoDB (on-demand) | ~$1,150 | `TransactWriteItems` on every capture bills each of 6 items at **2× normal WCU** |
| DynamoDB Streams | ~$0 | Read requests are included in the Pipe's per-request cost, not billed separately |
| EventBridge Pipes | ~$31 | $0.40/M requests × ~78M write-path stream records (64KB chunks) |
| EventBridge | ~$80 | $1/M events published, all write-path events |
| Step Functions (Express) | ~$60 | $1/M executions × 25.9M captures, plus duration (GB-s) |
| SQS | ~$20 | $0.40/M requests, DLQ included |
| Aurora Serverless v2 + RDS Proxy | ~$300 | ~2 ACU sustained average — at this volume it does **not** scale to zero |
| CloudWatch Logs + X-Ray | ~$200 | 7-day retention, 5–10% X-Ray sampling, **plus Express execution-data logging** |
| VPC interface endpoints (SQS, X-Ray) | ~$45 | 2 endpoints × 2 AZs × ~$7.30/month, plus ~$0.01/GB processed at this volume |
| NAT Gateway | $0 | Avoided by design — interface endpoints instead (see Network path out of the VPC) |
| **Total** | **~$3,280/month** | |

The Express saga's real cost is split across two lines: ~$60 for the workflow itself and ~$50 of the CloudWatch
line for the execution-data logging that makes it debuggable (ADR-2). Counted together that is ~$110/month against
~$3,900 for the same saga on Standard.

### Where the cliff is

The bill is roughly linear in traffic *except* in two places:

- **Aurora ACU scaling.** Aurora Serverless v2 scales smoothly up to its configured max, but if sustained load
  pushes past that ceiling, query latency degrades before cost visibly jumps — the "cliff" is a latency cliff before
  it's a cost cliff, and it's easy to miss until the projector falls behind or reads start timing out.
- **Provisioned concurrency / SnapStart** (ADR-3). If cold-start mitigation is added on the write path, provisioned
  concurrency is billed **per hour regardless of traffic**, not per invocation — at low-to-moderate sustained
  traffic this can flip Lambda from a variable cost to a cost floor that doesn't shrink even if TPS drops.
- **Switching Step Functions to Standard.** Standard bills per state transition rather than per execution, so the
  same saga jumps from ~$60 to ~$3,900/month at this volume — a ~60× step change that more than doubles the total
  bill. It is the largest single cost decision in the design (ADR-2), and the 5-minute Express ceiling is the thing
  most likely to force it.

### Dominant line item and the lever

**DynamoDB and API Gateway dominate**, at roughly $1,150 and $910/month respectively, with Lambda close behind.
Orchestration is deliberately *not* on this list: choosing Express over Standard (ADR-2) is what keeps it off, and
that single choice is worth more than every other lever on this page combined.

- **DynamoDB's** cost is driven almost entirely by `TransactWriteItems` on capture — 6 items per transaction, each
  billed at double the standalone WCU rate. The lever: reduce items-per-transaction where possible, or — if traffic
  is steady and predictable rather than bursty — move to **provisioned capacity with auto-scaling**, which is
  roughly 5–7× cheaper per request unit than on-demand at steady volume. On-demand is the right choice for this
  project's spiky, low-volume dev/test usage; it stops being the right choice once traffic is this steady.
- **API Gateway's** cost is a near-fixed $3.50/million regardless of payload or logic. The lever: migrating from
  REST API to **HTTP API** (API Gateway v2) cuts this to $1.00/million — roughly a 70% reduction — at the cost of
  losing REST-only features (request validators, usage plans) that would need to move into the Lambda layer.

### Development cost (this project, 3 weeks)

Building and testing this system is a separate, much smaller budget line — per the project plan's cost guardrails:
target **$10–30 total**, enforced via a day-one AWS Budget alarm, DynamoDB on-demand, no NAT Gateway, Aurora min
capacity set to 0 ACUs (and verified to actually pause), 7-day CloudWatch log retention, and `terraform destroy` at
the end of each day in week 3 when not actively testing.

**The VPC interface endpoints are the one component that fights this budget.** They bill per hour per AZ whether or
not a single message is processed — the same shape of cost as provisioned concurrency in the cliff section above,
and the only line in this project that does not scale to zero with idleness. Two endpoints across two AZs would be
roughly $0.04/hour, about **$20 over three weeks**, consuming most of the target on its own.

Two decisions bring that down:

- **Single AZ** (see AZ span, above): two endpoints in one AZ is ~$0.02/hour, about **$10 over three weeks** if left
  standing the whole time.
- **The endpoints live inside the daily `terraform destroy`**, not in a long-lived "just the networking" stack.
  Endpoints are the strongest argument against splitting networking into a persistent root — a VPC with no
  endpoints costs nothing to leave up, which is exactly why it is tempting to leave the endpoints up with it. If
  they are only up during active testing in week 3, the real figure is a few dollars.

Worth checking on the Budget alarm rather than assuming: this is the one line that accrues while nothing is
happening, so it is also the one that will show up on a day nothing was deployed.


## Security

### Authentication

Authentication is done via Cognito Authorizer. Cognito stable user id (`sub`) will map directly to the 
account_id as we won't support multiple accounts per user. The Cognito user pool will enforce a strict password
policy consisting of minimum 12 characters, at least one uppercase letter, one lowercase letter, one number, 
and one special character, and use of MFA

### Authorization (access control)

Terminology note: this subsection is about *access control*. Elsewhere in this document "authorization" means a
card hold, and the "Authorization Service" is the Lambda that places one. They are unrelated.

The Cognito authorizer establishes **who** the caller is. It says nothing about **what** they may act on, and that
gap is the whole of this subsection: without an ownership rule, any authenticated user could read another user's
balance or place a hold against their account.

**The rule: `account_id` is derived from the token, never from the request.**

It is not read from the request body, and not read from the path. It is the validated `sub` claim, and nothing
else. The API shape enforces this rather than relying on a check:

- `POST /authorizations` takes no `account_id` field. The hold is always placed against the caller's own account.
  A request that includes an `account_id` is rejected with 400 rather than ignored, so a client built against the
  wrong assumption fails loudly instead of silently operating on the caller's account.
- Balance and history are addressed as `/accounts/me/...`. There is no path variable to tamper with.

This matters more than the equivalent check would. A validation rule is something every new endpoint has to
remember; a shape with nowhere to put the wrong account is one where the mistake cannot be expressed. The
alternative — keeping `/accounts/{id}/...` and asserting `id == sub` in each handler — is functionally equivalent
and structurally worse, because it is one forgotten line away from an IDOR on any endpoint added later.

**Ownership on authorization-scoped routes.** `POST /authorizations/{id}/capture` and `/void` are addressed by
authorization id, which is not derivable from the token. These handlers load the authorization and reject it unless
its `account_id` equals `sub`. The rejection is a **404, not a 403** — a 403 confirms that the id exists, which
turns the endpoint into an oracle for enumerating other users' authorization ids.

**Caller model: cardholder only (ADR-7).** There is exactly one authenticated caller class, and it is the
cardholder. This is what makes everything above work as a *shape* rather than as a rule: with a single caller
class, `account_id == sub` is the entire authorization model, and there is nowhere in the API to express a
different account. Merchants appear throughout the data model and the ledger as counterparties, but they have no
Cognito identity, no scopes, and no path into the API — the merchant side of a capture is written by the system on
the cardholder's behalf.

This diverges from real card flows, which are merchant-initiated, and the divergence is deliberate rather than an
oversight — see ADR-7 for the reasoning and for what adding a merchant caller class would cost. The practical
consequence for this section: any endpoint added later that cannot be expressed as "the caller acting on their own
account" is a signal that the single-axis model is being outgrown, and it should go through ADR-7 rather than
acquiring a bespoke ownership check.

### IAM

Least privilege is applied **per function**, not per service: every Lambda gets its own role, and no role is shared.
The default posture is that a role can perform exactly the operations its handler makes, on exactly the resources it
names.

Two conventions used throughout:

- **Resources are ARNs, never `*`.** DynamoDB policies name the table ARN, and separately name
  `…:table/payledger/index/GSI1` where the handler queries the GSI — a table-only policy silently fails every index
  query, which is the failure mode to design out rather than debug.
- **Every role that touches DynamoDB also needs KMS.** The table uses a customer-managed key, so encryption is
  transparent to the code but not to IAM. Those roles carry `kms:Decrypt` and `kms:GenerateDataKey` on the key,
  conditioned with `kms:ViaService: dynamodb.<region>.amazonaws.com` so the key cannot be used for anything else.

| Role | Actions | Resource |
|---|---|---|
| Authorization Service | `dynamodb:PutItem`, `UpdateItem`, `GetItem`, `Query` | Table + GSI1 |
| Balance Service | `dynamodb:GetItem` | Table only — no `Query`, no index |
| Transaction History Service | `rds-db:connect` | `dbuser:<proxy-id>/<read-only-user>` |
| EventBridge Pipe (Streams → bus) | `dynamodb:GetRecords`, `GetShardIterator`, `DescribeStream`, `ListStreams`; `events:PutEvents`; `sqs:SendMessage` for the source DLQ | Stream ARN; bus ARN; DLQ ARN. Trusts `pipes.amazonaws.com`, not `lambda.amazonaws.com` |
| Aurora projector | `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes`, `ChangeMessageVisibility`; `rds-db:connect` | Queue ARN; `dbuser:<proxy-id>/<writer-user>` |
| Expired-hold sweeper | `dynamodb:Query`, `UpdateItem` | GSI (expiry index) for read; table for write |
| FraudScreen | `dynamodb:Query` | Table + GSI1 — **read only** |
| SubmitSettlement | `secretsmanager:GetSecretValue` | The acquirer secret's ARN — **no DynamoDB access at all** |
| NotifyCustomer | `sns:Publish` | Topic ARN |
| CompensateLedger | `dynamodb:PutItem`, `UpdateItem`, `DeleteItem` (scoped, below) | Table |
| Step Functions execution role | `lambda:InvokeFunction`; log delivery (below) | The four task Lambda ARNs, listed individually |
| EventBridge rule target role | `states:StartExecution`, `sqs:SendMessage` | State machine ARN; queue ARN |
| DLQ replay tool (operator) | `sqs:ReceiveMessage`, `DeleteMessage`, `SendMessage`, `GetQueueAttributes`, `StartMessageMoveTask`, `ListMessageMoveTasks` | DLQ + source queue ARNs |
| Terraform CI role | Deploy-time only; `iam:PassRole` scoped to the execution role ARNs above; `dynamodb:PutItem`, `GetItem` for merchant seeding — **no `DeleteItem`** (below) | Table, `LeadingKeys` conditioned to `MERCHANT#*` |

Baseline on every Lambda: `logs:CreateLogGroup`, `CreateLogStream`, `PutLogEvents`, plus `xray:PutTraceSegments`
and `PutTelemetryRecords`. Anything reaching Aurora through RDS Proxy additionally needs the VPC ENI permissions
(`ec2:CreateNetworkInterface`, `DescribeNetworkInterfaces`, `DeleteNetworkInterface`). Powertools' EMF metrics need
**no** IAM permission — they are emitted as structured log lines, so `cloudwatch:PutMetricData` is a reflex to
resist. The Pipe is the one row that baseline does *not* apply to: it is not a Lambda, so it gets no X-Ray
permissions, and its logging is a property of the pipe (log destination plus log level) rather than something the
runtime does on its own — it needs `logs:CreateLogStream` and `PutLogEvents` on its own explicitly-created log
group.

**The append-only ledger is enforced in IAM, not just in code.** ADR-5 says posted entries are never mutated or
deleted. Every role above except `CompensateLedger` carries an explicit `Deny` on `dynamodb:DeleteItem`, which
means a bug or a hotfix cannot delete a ledger entry even if someone writes the call. `CompensateLedger` is the
sole exception because reversal must delete the capture's idempotency record (OQ-9), and its permission is scoped
by key prefix so it can delete *only* that:

```
Allow   dynamodb:DeleteItem
        Condition: StringLike { "dynamodb:LeadingKeys": ["IDEM#*"] }
Deny    dynamodb:DeleteItem  on everything else
```

Ledger entries live under `ACCT#` and `MERCHANT#` partition keys, so the condition makes deleting one impossible
for every principal in the system. This is the strongest single control in the design: the core domain invariant is
enforced by the platform rather than trusted to application code.

**Merchant seeding does not get an exception to this.** The obvious way to let Terraform manage seeded merchants is
`DeleteItem` conditioned on `LeadingKeys: ["MERCHANT#*"]` — and that would blow a hole straight through the control
above. `dynamodb:LeadingKeys` constrains the **partition** key only; there is no equivalent condition key for the
sort key. A merchant's identity item (`MERCHANT#<id>` / `META`) and its ledger entries (`MERCHANT#<id>` /
`TXN#...`) share a partition, so any policy that can delete the first can delete the second, and IAM cannot tell
them apart.

So the Terraform role gets `PutItem` and `GetItem` and carries the same `DeleteItem` deny as everything else.
Consequences: seeding is **additive** — a merchant can be added or corrected by an apply, but removing one is a
table-level operation (destroy and re-seed), not an item-level one. That is an acceptable trade for a fixed
catalogue that changes on the order of never, and the daily `terraform destroy` in week 3 takes the table with it
anyway. `PutItem` is granted without a sort-key constraint for the same reason it is granted to the Authorization
Service — the enforcement story here is specifically about deletion, and an overwrite hazard that already exists
system-wide is not made worse by the seeder.

**Note the two places transactions and IAM interact.** There is no `dynamodb:TransactWriteItems` IAM action —
transactional writes are authorized through the underlying `PutItem` / `UpdateItem` / `DeleteItem` permissions, so
the Authorization Service's policy grants those rather than naming the API it calls. And because the deny above
applies inside transactions too, a transaction containing a `Delete` on a ledger entry fails authorization as a
whole rather than partially applying.

**The one unavoidable wildcard.** ADR-2 makes Express execution logging mandatory, and the log-delivery permissions
that requires — `logs:CreateLogDelivery`, `GetLogDelivery`, `UpdateLogDelivery`, `DeleteLogDelivery`,
`ListLogDeliveries`, `PutResourcePolicy`, `DescribeResourcePolicies`, `DescribeLogGroups` — only function with
`Resource: "*"`. This is an AWS constraint, not an oversight. It is confined to the Step Functions execution role,
which holds no data-plane permissions, so the blast radius is log delivery configuration and nothing else.

**Deriving the real list.** These are the permissions the design implies. The permissions the system actually uses
should be generated from CloudTrail with IAM Access Analyzer policy generation, after week 3's chaos testing has
exercised the rarely-taken paths — compensation and DLQ redrive — since anything never invoked will be absent from
a generated policy.

### PII and data classification

Cognito holds the smallest possible authentication footprint (email/phone + password hash), the data stores hold
account and financial data under an owned KMS key, and the two are joined only by an opaque `sub`. Everything below
follows from keeping that split intact.

**No card data ever enters this system.** There is no PAN, no CVV, no expiry date, no cardholder name — not in the
data models, not in a request body, not in transit. An "authorization" here is a hold against an internal account
balance, not a message to a card network. This is the most important sentence in the section: it places the system
entirely **outside PCI DSS scope**, and it is a property to defend deliberately rather than one to rediscover later.
If a real card reference is ever needed, it arrives as a network token from a vault the system does not own, and the
token — never a PAN — is what gets stored.

**What is held, and where**

| Data | Classification | Store | Protection |
|---|---|---|---|
| Email, phone, password hash | Direct identifiers | Cognito **only** | Managed by Cognito; never copied into DynamoDB or Aurora |
| `account_id` (= `sub`) | Pseudonymous identifier | DynamoDB, Aurora, logs | Opaque; resolves to a person only via Cognito |
| Amounts, timestamps, `merchant_id` | Sensitive financial data | DynamoDB, Aurora | CMK at rest, TLS in transit |
| `merchant.name` | Business data | DynamoDB, Aurora | Not personal data |
| `response_snapshot` | Copy of a response body | DynamoDB (idempotency records) | CMK at rest; TTL-bounded to 24–48h |

The row that gets underrated is the third. A transaction set carries no name and no email, and is still sensitive:
merchant plus amount plus timestamp is a spending profile, and a spending profile is disclosive on its own. That is
what justifies encryption and retention limits on the financial data, not just on the credentials — and it is why
pseudonymity is a mitigation here rather than an exemption.

**Aurora holds a second copy of the financial data.** The projector replicates ledger entries out of DynamoDB, so
the protections above have to hold in two places, not one:

- Encrypted at rest with a customer-managed KMS key, set at cluster creation (it cannot be changed afterward).
- Reached only through RDS Proxy with TLS enforced, in private subnets, with no public accessibility and a security
  group that admits the proxy and nothing else.
- Queried with **parameterized statements**, so account ids and amounts never appear as literals in query text.
  This matters more than it looks: the runbook's Aurora procedure sends an operator to Performance Insights to read
  top SQL by load, and inlined literals would put customer data on that screen.
- Automated backups and snapshots inherit the cluster's encryption.

Being a derived store cuts both ways. It is a second copy to protect — but because it is disposable and rebuildable
from DynamoDB, it is also the copy that can simply be dropped and reprojected, which is what makes the erasure story
below tractable.

**Logs are the leak path, and this design has three of them.** Structured logging and tracing will capture whatever
they are handed, so the rule is that request and response bodies are never logged whole:

- **Application logs.** Powertools' `Logger` logs explicit fields only — never the raw event, never the response.
- **X-Ray.** No identifiers in annotations. Annotations are indexed and searchable, which makes them the worst
  place to put an `account_id`; correlation happens on the request id instead.
- **Step Functions execution logging.** This is the design-specific one. ADR-2 makes Express execution logging
  mandatory and sets log level `ALL` *including execution data* — which means the payload passed between saga
  states lands in CloudWatch Logs by design. That payload carries `account_id`, amount, and `merchant_id`. The
  mitigation is to keep the saga payload minimal (ids and a decision, not a copy of the record) and to accept that
  this log group holds sensitive financial data and must be scoped, retained, and access-controlled accordingly.

As a backstop rather than a primary control, a **CloudWatch Logs data protection policy** with managed data
identifiers masks email addresses and phone numbers if one ever reaches a log group. It is a safety net for a
mistake, not a substitute for not making it.

**Erasure versus an append-only ledger.** These are in genuine conflict and the conflict has to be resolved
explicitly rather than hand-waved. ADR-5 makes posted ledger entries immutable and IAM enforces it, so a deletion
request *cannot* be satisfied by deleting financial records — and should not be, since retaining them is a legal
obligation in its own right.

The resolution is to delete the identity and keep the pseudonymous record:

1. Delete the Cognito user — email, phone, and password hash go, and with them the only mapping from `sub` to a
   person.
2. Retain ledger entries, authorizations, and balances keyed by `sub`, under financial record-keeping retention.
3. Drop and reproject the affected rows in Aurora, since it is derived and holds no authority.

What remains afterward is a set of amounts and timestamps attached to an identifier that no longer resolves to
anyone. The ledger stays balanced and auditable, and the personal data is genuinely gone. The retention periods
themselves belong in the data-retention section, which is still to be written.

## Known gaps — sections still to write

The project plan specifies this doc should read as a review-board document. Not yet present:

- **SLOs.** ADR-3 and the cost model both reason about p99 latency and cold-start tails against an unstated target.
- **Data retention.** Ledger entries, Aurora rows, and log groups all need a stated policy.
- **Regional failure behavior.** "Aurora could be rebuilt from DynamoDB" is asserted but no RTO/RPO is given.
- **Error-response contract.** No status codes for insufficient funds, already-captured, expired hold, or
  idempotency-key conflict.
- **Cost model — missing line items.** KMS is absent entirely and, with a customer-managed key on DynamoDB at this
  request volume, is potentially large enough to change the ordering below Step Functions. Cognito has no line
  either. (The EventBridge driver label has been corrected to match its figure.)
- - **Security — remaining subsections.** Authentication, authorization, per-function IAM, and PII/data
  classification are now written. Still missing: Secrets Manager rotation (the project plan calls for it and
  `SubmitSettlement` depends on it), encryption in transit as its own subsection (TLS minimums on API Gateway; the
  Aurora leg is covered under PII), abuse controls (throttling, WAF), and audit logging
  (CloudTrail, with DynamoDB Streams as a natural audit trail). The VPC/network boundary is now covered under
  Network path out of the VPC, though security groups and subnet CIDRs are still unstated.


## Implementation plan

### Week 1 — Core correctness

- Domain model with **Pydantic**: `Account`, `Authorization`, `LedgerEntry`, `Money` (Decimal-backed, never float).
- Finalize the single-table design against the access-pattern list above.
- Seed the fixed merchant catalogue in Terraform, and validate `merchant_id` against it in the authorize handler —
  both halves, together, or the ledger will happily balance against merchants that do not exist.
- Idempotency layer — start with Powertools' decorator, then read its source to understand the conditional-write mechanics.
- Terraform modules + CI pipeline running on day 2, not day 10.
- Integration tests against **DynamoDB Local via Testcontainers** (the `testcontainers-python` package).
- **A property-based test with Hypothesis asserting the ledger always balances** after any random sequence of valid operations. This one test is worth more than fifty unit tests and it's a great thing to point at in conversation.

*Exit criteria: authorize / capture / void work end to end, idempotently, with the balance invariant verified under randomized input.*

### Week 2 — Distributed systems

- DynamoDB Streams → EventBridge via an **EventBridge Pipe** (ADR-8): filter, input transformer, source DLQ. The
  versioned event schema is declared in the transformer template; the Pydantic models become the consumer-side
  contract that the projector and saga validate against, not the producer that builds the event.
- Step Functions capture saga with real compensating transactions.
- Aurora Serverless v2 + the stream projector, connecting through RDS Proxy. **Create Aurora now, not in week 1.**
- Observability: CloudWatch dashboard, alarms on DLQ depth and p99 latency, X-Ray traces that show a full request path across async boundaries.

*Exit criteria: a capture flows through the saga, lands in Aurora, and you can trace one request end to end in X-Ray.*

### Week 3

- **Break it on purpose.** Kill a Lambda mid-saga. Throttle DynamoDB. Poison the queue. Force a `TransactionCanceledException`. Fix what breaks.
- Build a **DLQ replay tool** — a small CLI (Python + boto3) that inspects, edits, and re-drives failed messages.
- Load test with **k6**: watch cold starts (with and without provisioned concurrency — note SnapStart now supports Python as of late 2024, worth trying), throttling behavior, projection lag under sustained write pressure.
- Write everything up: finalize this design doc, the ADRs, the cost model, and the runbook.

*Exit criteria: you can describe three failure modes you induced, what the system did, and what you changed.*
