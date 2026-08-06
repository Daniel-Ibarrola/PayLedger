# Architecture Decision Records

Each ADR states the decision, the alternative rejected, the reasoning, and the condition under which it would be
revisited.

## ADR-1: DynamoDB over Aurora for the write path

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

## ADR-2: Step Functions orchestration over pure event choreography

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

## ADR-3: Provisioned concurrency / SnapStart over accepting cold starts

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

## ADR-4: Single-table over multi-table DynamoDB design

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

## ADR-5: Reversal entries over mutable ledger records

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

## ADR-6: EventBridge over direct SQS fan-out

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

## ADR-7: Cardholder as the sole API caller

**Decision:** The **cardholder** is the only authenticated caller. Every endpoint that touches money acts on the
caller's own account, derived from the Cognito `sub`. Merchants remain a first-class entity in the data model and
the ledger, but have no authentication path and cannot act — `POST /merchants` is called by a cardholder, not by a
merchant.

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

**`POST /merchants` is the one route the single-axis model does not reach, and it is priced deliberately.**
Merchants are shared reference data: the route is callable by any authenticated cardholder, the resource it creates
belongs to nobody, and its protection is a conditional write rather than an ownership rule. The alternative is an
administrator caller class, which is the second caller class this ADR exists to avoid — invented to guard an entity
that grants no access and reveals nothing about other users.

That is what makes the trade acceptable at this scope. A merchant is worth nothing to an attacker who already holds
a valid token: creating one confers no privilege, and the only destructive act against an existing one —
overwriting its `payable_balance` — is exactly what `attribute_not_exists` blocks. Meanwhile the property worth
demonstrating survives intact, because the exception is confined to an entity that is never addressed relative to a
caller: one caller class, one ownership rule, and that rule still covers every route where money moves.

On a real deployment merchant creation is the first route that would get an administrator caller class, and the
`GET`/`PATCH`/deactivate surface that comes with it.

**Revisit when:** merchant-initiated authorization becomes a goal. That means a second caller class (Cognito app
clients using `client_credentials` with a `merchant_id` claim and scopes such as `authorizations:capture`) and a
second ownership rule (the merchant on the authorization must match the caller's `merchant_id`), which in turn
means the `/accounts/me/...` shape no longer carries the whole enforcement burden on its own.

## ADR-8: EventBridge Pipes over a forwarder Lambda

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
