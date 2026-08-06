# Cost Model

This estimates steady-state production cost at **100 TPS sustained, 24/7** — a capacity-planning exercise, distinct
from the cost of *building* the project (see Development cost below).

## Assumptions

- 100 TPS blended average → 100 × 2,592,000s ≈ **259.2M requests/month**.
- Traffic split: ~30 TPS write path (`authorize`/`capture`/`void`), ~70 TPS read path (`balance`/`transactions`).
- Of the write traffic, only `capture` runs the full saga and writes ledger entries; `authorize`/`void` are lighter
  DynamoDB operations. Captures are taken as ~10 TPS of the 30 — roughly **25.9M captures/month**. Figures below are
  order-of-magnitude, not a quote — the point is to reason about the shape of the bill, not to be precise to the
  dollar.
- The saga is ~6 states per execution (three task states plus the choice/terminal states around them), running
  ~1s end to end. On Express this bills as one execution plus duration; state count affects the bill only through
  the duration and the log volume it produces.

## Cost by component (monthly, us-east-1 list pricing)

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

## Where the cliff is

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

## Dominant line item and the lever

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

## Development cost (this project, 3 weeks)

Building and testing this system is a separate, much smaller budget line, with its own cost guardrails:
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
