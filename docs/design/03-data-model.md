# Data Models

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

**Accounts are created out-of-band, from a Cognito Post Confirmation trigger, not from a client-facing endpoint.**
There is no `POST /accounts` — accepting an account create from the API would let a caller choose their own
`account_id`, which is exactly what "`account_id` comes from the validated `sub`" exists to prevent. Instead,
Cognito invokes the `create_account` Lambda synchronously once a user confirms sign-up, and the handler writes the
account item keyed on the trigger's `sub` with the same `ConditionExpression: attribute_not_exists(PK)` pattern as
merchant creation, so a duplicate invocation is a no-op rather than a balance-resetting overwrite. Cognito does not
retry a failed trigger and the user is already confirmed by the time it runs, so the handler retries the DynamoDB
write itself on connectivity failures, bounded to stay well inside Cognito's fixed 5-second trigger timeout.

**Merchant**
- merchant_id (str)
- name (str)
- payable_balance (int) — the merchant's side of the ledger; credited on capture, debited on reversal
- created_at (str) — ISO-8601 UTC

Merchants are the counterparty on every transaction. They are a distinct entity rather than a kind of account: they
have no holds, no available balance, and no authorization lifecycle, so folding them into `Account` would mean an
entity where half the fields are permanently unused.

**Merchants are created through the API, with a client-supplied id.** `POST /merchants` takes the `merchant_id`
from the caller rather than generating one, and writes the item under `ConditionExpression:
attribute_not_exists(PK)`. That single choice settles three things at once, which is the reason for it:

- **It is the idempotency mechanism.** The natural key *is* the key, so a retried create is a no-op that returns
  `409 MerchantAlreadyExists` instead of quietly producing a second merchant. This is the one `POST` in the API
  that does not require an `Idempotency-Key` header, and it is exempt because it does not need one — see the
  error-response contract.
- **It is the safety mechanism, and this is the load-bearing part.** An unconditional `PutItem` against an existing
  `merchant_id` would overwrite the item and **reset a live `payable_balance` to `0` from a public endpoint** — a
  silent, unrecoverable corruption of a value the ledger considers authoritative. The condition is what makes
  creation non-destructive. Treat it as an invariant with a test behind it, not as an implementation detail of the
  handler.
- **It keeps merchant ids caller-chosen**, which matches how `POST /authorizations` already treats them: the client
  sends an id it knows, and the service validates it. The id is still an external identifier — the handler applies
  the `MERCHANT#` prefix, and a caller that sends one gets a 400 rather than a nested key.

Two further consequences worth stating rather than discovering:

- `POST /authorizations` carries a client-supplied `merchant_id`, so the Authorization Service must **`GetItem` the
  merchant and reject an unknown id with a 400** before placing the hold. Without that check a typo produces a
  perfectly balanced transaction against a merchant that does not exist, and the ledger invariant will not catch it
  — both sides sum to zero regardless. The set of valid ids is open and grows at runtime, so this check is the
  only thing standing between a typo and a transaction against a merchant nobody created.
- **Merchants can be created but never deleted.** Every role carries an explicit `Deny` on `dynamodb:DeleteItem`
  (see Security → IAM), and no exception is possible for `MERCHANT#*` — a merchant's identity item shares a
  partition with its ledger entries, and IAM cannot tell them apart. There is therefore no `DELETE /merchants/{id}`
  and there cannot be one. Retiring a merchant would be an `UpdateItem` on a status flag; that is not built.

`payable_balance` is created at `0` and thereafter only ever moved by capture and reversal. Nothing else writes it,
including the create endpoint, which cannot run twice against the same id.

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
- party_type (enum ACCOUNT, MERCHANT, EXTERNAL)
- source_authorization_id (str)
- amount (int)
- entry_type (enum DEBIT, CREDIT)
- created_at (str) — ISO-8601 UTC, matches the timestamp embedded in the sort key

An entry belongs to a *party*, not specifically an account, because the credit side of every transaction lands on a
merchant. `party_type` mirrors the key prefix (`ACCT#` / `MERCHANT#` / `EXTERNAL#`) so an entry can be resolved back
to its owner without a second lookup.

**Deposits** credit an account directly — no hold is involved, so `current_balance` and `available_balance` move
together in one `TransactWriteItems` call, unlike authorize, which only touches `available_balance`. The
counterparty is a third `party_type`, `EXTERNAL`, represented by a single system-owned party (`EXTERNAL#funding`)
rather than a caller-created entity: invariant 1 needs every deposit balanced by a debit somewhere, and at this
project's scope that somewhere is a fixed party standing in for an unmodeled funding source, not a real bank
integration. It has no `META` item — nothing ever reads an external balance, so there is no access pattern to
justify persisting one. Deposit-sourced ledger entries have no `source_authorization_id`, since there is no
authorization behind a deposit; the field is absent rather than populated with a placeholder.

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
