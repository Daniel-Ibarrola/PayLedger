## Runbook

Operational procedures for the failure modes most likely to page someone. Each entry: symptom, likely causes,
diagnosis, mitigation, and the follow-up that prevents a recurrence.

### The ledger is out of balance

**Symptom:** the balance-invariant check (sum of ledger entries for a transaction ≠ 0) fails — caught either by a
scheduled integrity-check Lambda or a customer-reported balance discrepancy.

**Likely causes:** a `TransactWriteItems` call that partially applied outside of DynamoDB's atomicity guarantee is
not possible in isolation, but a bug that writes ledger entries *outside* the transactional path (a hotfix, a manual
console edit, a saga compensation that wrote a non-balanced reversal) is.

**Diagnosis:**
1. Do **not** touch the account's live data yet. Pull every ledger entry for the affected `transaction_id`
   (`GSI1PK = TXN#<txnId>`) and sum `amount` by `entry_type`. Confirm which transaction(s) are actually unbalanced —
   don't assume it's the one that was reported.
2. Check CloudWatch/X-Ray for that transaction's trace: was it written via the normal capture path, a saga
   compensation, or an out-of-band write (console, one-off script)? Out-of-band writes are the top suspect.
3. Check whether the write happened inside a single `TransactWriteItems` call or as separate `PutItem`s — a
   non-atomic multi-step write is the other top suspect.

**Mitigation:** never edit or delete the broken entries. Write a **reversal transaction** that zeroes out the
incorrect entries, followed by a corrected transaction if the underlying intent is known. This preserves ADR-5
(reversal entries over mutation) even when fixing a bug in the system that's supposed to enforce it.

**Follow-up:** if the cause was an out-of-band write, remove whatever access path allowed it (console edit
permissions, a script bypassing the transactional write path). Add a regression case to the Hypothesis
property-based test if the bug was in application logic.

### DLQ filling up

**Symptom:** CloudWatch alarm on DLQ depth fires.

**Likely causes:** a poison message (malformed event, a downstream dependency — fraud screen, settlement — down or
throttling), or a bug in a consumer causing every message of a certain shape to fail.

**Diagnosis:**
1. Inspect a sample of messages with the DLQ replay tool (week 3 deliverable) without removing them from the queue.
2. Check whether failures are one poison message repeatedly retried (`ReportBatchItemFailures` should already
   isolate this from healthy messages — if it isn't, that's the bug) or a systemic failure affecting all messages.
3. Check the downstream dependency's health (fraud screen, settlement submission) and recent deploys to the
   consumer Lambda.

**Mitigation:** if systemic (downstream outage), pause redrive until the dependency recovers, then redrive the
whole DLQ. If a poison message (bad payload), fix or discard that specific message via the replay tool and redrive
the rest.

**Follow-up:** if a bug shipped, the batch-failure reporting should have contained the blast radius to that message
shape — if it didn't, that's the thing to fix, not just the immediate bug.

### Projection lag alarm (Aurora read model falling behind)

**Symptom:** alarm on projector lag (time between a DynamoDB write and its corresponding row landing in Aurora)
exceeds threshold.

**Likely causes:** projector Lambda throttling or erroring, Aurora at capacity (see below) rejecting writes, or a
genuine burst of write volume the projector can't keep up with.

**Diagnosis:**
1. Check the projector Lambda's error rate and concurrency/throttle metrics first — a failing projector is the most
   common cause and the cheapest to confirm.
2. Check Aurora ACU utilization and RDS Proxy connection metrics — if Aurora is saturated, writes from the
   projector will queue or fail, not just slow down.
3. Compare DynamoDB stream `IteratorAge` against the lag alarm — confirm the lag is in the projector/Aurora leg and
   not upstream.

**Mitigation:** reads of `balance` are served from DynamoDB directly (per the non-functional requirement that
balance reads are always current), so lag does **not** put the balance-correctness invariant at risk — it only
affects the Aurora-backed transaction-history/analytics endpoints. Communicate that distinction before treating this
as a P1. Scale the projector's concurrency or Aurora's max ACU if the cause is genuine volume; fix and redeploy if
it's a projector bug.

**Follow-up:** if the projector fell far enough behind that data quality is in doubt, use the rebuild-from-scratch
capability (replay from DynamoDB) rather than trying to reconcile incrementally.

### Aurora at max ACU

**Symptom:** Aurora Serverless v2 alarm on ACU utilization near its configured maximum; query latency rising.

**Likely causes:** genuine sustained load past the configured ceiling, an unoptimized query (missing index, full
scan) consuming disproportionate capacity, or a connection leak preventing Aurora from freeing resources.

**Diagnosis:**
1. Check Aurora Performance Insights for the top SQL by load — a single bad query is the most common and cheapest
   fix.
2. Check RDS Proxy's active connection count against expected Lambda concurrency — a leak here (Lambdas not
   releasing connections cleanly) looks like "Aurora is overloaded" but is really "Aurora is starved of headroom by
   idle-but-open connections."
3. Check whether this correlates with a genuine traffic increase (see Cost Model — this is the latency cliff
   described there).

**Mitigation:** raise the configured max ACU as an immediate relief valve if genuine load is the cause. If it's a
bad query, fix or add an index rather than scaling around it. If it's a connection leak, that's a code fix in
whatever Lambda is holding connections open, not a capacity problem.

**Follow-up:** verify the projector and any query-serving Lambda close connections properly — this is also what
would silently prevent Aurora from scaling to zero during idle dev/test periods (see cost guardrails).

### RDS Proxy connection exhaustion

**Symptom:** Lambdas serving Aurora-backed reads start erroring with connection-pool-exhausted or timeout errors
from RDS Proxy.

**Likely causes:** a burst of concurrent Lambda invocations exceeding the proxy's configured max connections, or
connections not being released (leak) by a consumer.

**Diagnosis:**
1. Check RDS Proxy's `DatabaseConnectionsCurrentlyInUse` against `MaxConnectionsPercent` — confirm it's actually
   exhaustion and not an unrelated Aurora-side error being misreported.
2. Check Lambda concurrency for the affected function(s) — a spike here that outpaces the proxy's configured
   connection borrow limit is the expected, designed-for failure mode under a big enough burst (this is exactly
   what RDS Proxy exists to absorb, but it has a ceiling).
3. Check for connections held open across invocations (e.g. a connection opened outside the Lambda handler's
   per-invocation lifecycle, or an error path that skips cleanup).

**Mitigation:** if it's a leak, that's a code fix. If it's a genuine burst past the proxy's capacity, add
back-pressure (API Gateway throttling, SQS buffering for non-synchronous reads) rather than uncapping the proxy —
uncapping just moves the exhaustion point to Aurora itself.

**Follow-up:** this is the exact failure mode RDS Proxy was introduced to prevent (see Aurora Serverless design
decision) — recurrence means either the proxy is undersized for real traffic or a regression reintroduced
direct/leaky connection handling.