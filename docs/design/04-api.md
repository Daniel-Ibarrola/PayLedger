# API

The API will consist of the following endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/merchants` | Create a merchant with a client-supplied `merchant_id`. Idempotent via a conditional write, not via a header. |
| `POST` | `/authorizations` | Place a hold. Idempotent via `Idempotency-Key` header. |
| `POST` | `/authorizations/{id}/capture` | Convert hold to posted transaction, for the full authorized amount. No body. Idempotent via `Idempotency-Key`. |
| `POST` | `/authorizations/{id}/void` | Release the hold. Idempotent via `Idempotency-Key`. |
| `POST` | `/deposits` | Credit the caller's account directly. No hold involved — `current_balance` and `available_balance` move together. Idempotent via `Idempotency-Key`. |
| `GET` | `/accounts/me/balance` | Returns both current and **available** balance. Served from DynamoDB, strongly consistent. |
| `GET` | `/accounts/me/transactions` | Paginated history, cursor-based. Served from Aurora, eventually consistent. |

Account, merchant, and authorization identifiers are opaque strings in the public API. The `ACCT#` / `MERCHANT#` /
`AUTH#` prefixes in the table design below are an internal key encoding and are never exposed to or accepted from
clients.

Account-scoped routes are addressed as `me`, not by account id, and `POST /authorizations` carries no `account_id`
field: the account is always derived from the caller's validated `sub` claim. See Security → Authorization for why
this is a shape rather than a validation rule.

**`POST /merchants` is the one route that is not account-scoped, and that is worth being precise about.** Any
authenticated cardholder can call it, and the created merchant belongs to nobody — there is no owner field, no
`account_id` on the item, and no ownership check on any subsequent read of it. This does not weaken the ownership
rule for everything else: `account_id == sub` still holds wherever an account is involved, because a merchant is
not an account and is never addressed relative to the caller.

What it does mean is that "the shape of the API is the enforcement mechanism" is a statement about the
*account-scoped* routes, not about every route. Merchants sit outside that model deliberately — they are shared
reference data in a system whose only caller class is the cardholder (ADR-7), and giving them an owner would
require inventing a merchant-administrator role that has nobody to be. Stated plainly: authentication is the only
gate on merchant creation, there is no authorization step beyond it, and on a real deployment this is the first
route that would need a caller class of its own.

`POST /merchants` and `POST /authorizations` are the only two places the API accepts an identifier it did not
derive from the token. In both cases the identifier names a merchant, and in both cases the handler is responsible
for the check — `attribute_not_exists` on create, `GetItem` on use.

**There is no `GET /merchants`.** A caller knows the id it chose, so listing is not needed to use the API, and
adding it would cost either a table scan or a GSI on a constant partition key — merchant items share no partition,
so there is no cheap way to enumerate them (see DynamoDB, access patterns). Discovery across callers is therefore
out of band, which is a real limitation and an accepted one at this scope.

## Error-response contract

Success is uninteresting and short: `201` for `POST /authorizations` and `POST /deposits`, `200` for capture, void,
balance, and transactions. Everything below is about the failures, because in a payments API the failure codes are
the part clients actually build logic against — "was this rejected forever, or should I retry?" has to be answerable
from the response alone.

**Envelope.** Every error the service itself generates has one shape, the one `shared/utils.error_response` already
emits:

```json
{ "error": "InsufficientFunds", "message": "available balance is lower than the requested amount" }
```

`error` is a stable machine-readable code and is part of the contract; `message` is human-readable, may change at
any time, and must never be parsed. Clients branch on `error`, not on the status code alone — `409` covers six
distinct conditions with six different remedies. Correlation to logs is the `x-amzn-RequestId` response header
rather than a body field, which keeps request-scoped internals out of a body the caller keeps.

Two failures cannot use this envelope because they are produced before the handler runs: the Cognito authorizer's
`401` (`{"message":"Unauthorized"}`) and API Gateway's throttling `429`. API Gateway *Gateway Responses* can be
remapped in Terraform to restore uniformity; that is deliberately not done, since the cost is a body template per
response type and the benefit is cosmetic for the one caller class this API has.

**The catalogue.**

| Status | `error` | Condition | Retry? |
|---|---|---|---|
| 400 | `InvalidRequest` | Body is not JSON, a required field is missing or ill-typed, `amount` ≤ 0, `expires_in_days` out of range, or the body carries an `account_id` (see Security → Authorization) | Not as sent |
| 400 | `UnknownMerchant` | `merchant_id` on an authorization names a merchant that does not exist | Not as sent |
| 400 | `MissingIdempotencyKey` | No `Idempotency-Key` header on `POST /authorizations`, capture, void, or `POST /deposits` | Not as sent |
| 400 | `InvalidCursor` | `GET /accounts/me/transactions` cursor is unreadable or expired | Not as sent |
| 404 | `AuthorizationNotFound` | Unknown authorization id, **or** one owned by another account | No |
| 409 | `MerchantAlreadyExists` | `POST /merchants` with a `merchant_id` that is already taken | No |
| 409 | `InsufficientFunds` | `available_balance < amount` at authorize time | After a deposit |
| 409 | `AlreadyCaptured` | Capture or void against a `CAPTURED` authorization | No |
| 409 | `AlreadyVoided` | Capture or void against a `VOIDED` authorization | No |
| 409 | `AuthorizationExpired` | Capture or void against an `EXPIRED` authorization | No |
| 409 | `AuthorizationReversed` | Capture or void against a `REVERSED` authorization (the saga compensated) | No |
| 409 | `RequestInFlight` | The idempotency record exists with status `IN_PROGRESS` — the original request is still running | Yes, with `Retry-After: 1` |
| 422 | `IdempotencyKeyReuse` | Same `Idempotency-Key`, different request body | No |
| 429 | *(gateway shape)* | API Gateway throttling | Yes, honour `Retry-After` |
| 500 | `InternalServerError` | Anything unhandled. Body is the bare code and a fixed message; exception text never reaches the caller | Unsafe without a new key |
| 503 | `ServiceUnavailable` | DynamoDB transaction contention or a dependency failure that survived the SDK's retries | Yes, with `Retry-After` |

`404` for an authorization owned by someone else is not a slip: a `403` there confirms the id exists and turns the
endpoint into an oracle for enumerating other users' authorization ids. The reasoning is in Security →
Authorization; it is restated here because this table is where someone will look for it.

**Why insufficient funds is a `409` and not a `402`.** `402 Payment Required` reads as the obvious choice and is
wrong for this API: RFC 9110 still marks it reserved, and the proxies and API clients that do assign it meaning read
it as *payment required to use the API*, not *this payment failed*. `409` says what is actually true — the request
conflicts with the current state of the account, and the same bytes sent after a deposit succeed. That is precisely
the `409` / `422` split used throughout the table: **`409` means the request is well-formed and would succeed
against a different resource state; `422` means the request is understood and will never succeed as written.**
Idempotency-key reuse is the only genuine `422` here, because no change in account or authorization state makes a
key that is already bound to a different payload usable again.

`MerchantAlreadyExists` sits on the correct side of that split: the same bytes sent while the id was still free
would have succeeded, so it is a state conflict rather than a permanently invalid request.

**Idempotency outcomes.** The `Idempotency-Key` header is required on the four `POST` routes that move money —
authorize, capture, void, deposit. `POST /merchants` is deliberately outside this machinery: its client-supplied id
is already a natural idempotency key, and a conditional write on it gives the same guarantee without an idempotency
record, a request hash, or a stored snapshot to expire. A retry gets `409 MerchantAlreadyExists` rather than a
replayed `201`, which is a weaker contract than the money routes get — the caller cannot distinguish "you created
this a moment ago" from "someone else took this id" — and that is acceptable only because merchant creation is not
a financial operation. Given a key, a request hash, and the stored record, there are exactly four outcomes:

| Stored record | Hash | Response |
|---|---|---|
| Absent | — | Execute; store `COMPLETED` with the response snapshot |
| `COMPLETED` | Matches | Replay the snapshot verbatim, original status code included |
| `COMPLETED` | Differs | `422 IdempotencyKeyReuse` |
| `IN_PROGRESS` | Either | `409 RequestInFlight`, `Retry-After: 1` |

Idempotency is keyed on the header, not on the operation, so a *new* key against an already-terminal authorization
is a real request that fails the state guard: voiding a `VOIDED` authorization under a fresh key is `409
AlreadyVoided`, not a courtesy `200`. Only a replay of the original key replays the original response. And per
invariant 4, a capture the saga later reversed has had its idempotency record deleted — replaying that key
re-executes the capture, which then fails the `PENDING` guard and returns `409 AuthorizationReversed`. A client that
treated its stored `200` as final learns the truth on retry rather than never.

**Deriving the code from a `TransactionCanceledException`.** The mapping above is only implementable if
`CancellationReasons` is read **positionally** — the array has one entry per transaction item, in the order the
items were submitted, and only the entries with `ConditionalCheckFailed` matter. For the authorize transaction:

| Failed item | Meaning | Response |
|---|---|---|
| 1 (`ACCT#…` balance) | `available_balance >= :amt` failed | `409 InsufficientFunds` |
| 2 (`AUTH#…` put) | Authorization id collision — a generated-id bug, not a client error | `500` |
| 3 (`IDEM#…` put) | Key already exists | Re-read the record and apply the idempotency table above |

Conflating items 1 and 3 is the easy bug and produces the worst possible failure mode: a client told "insufficient
funds" for what was actually a successful retry. For the capture transaction, item 5 is the `PENDING` guard, and a
failure there means the authorization is in *some* terminal state — the handler then does a single `GetItem` to
read which one and picks between `AlreadyCaptured`, `AlreadyVoided`, `AuthorizationExpired`, and
`AuthorizationReversed`. That extra read happens only on the error path, where an extra RCU is free compared to
returning a code that does not say what went wrong.

**The expiry window is real and is accepted.** Expiry is enforced by the 15-minute sweeper, so an authorization past
its `expires_at` that the sweeper has not reached yet is still `PENDING` and a capture against it succeeds with
`200`. If that window ever needs to close, the fix is to add `expires_at > :now` to the capture's
`ConditionExpression` rather than a read-then-write check in the handler — the same positional discrimination then
distinguishes it, via the `GetItem` that already runs on that path.

**What a `2xx` does not promise.** Capture returns `200` once the ledger write commits, which is before the saga
runs. A later `FLAGGED` fraud screen or an exhausted settlement retry produces a compensating reversal, and there is
no HTTP response left to carry that news — the client learns it from `GET /accounts/me/transactions`, from the
notification step, or from a replay returning `409 AuthorizationReversed`. This is inherent to posting the ledger
before screening (see Step Functions), not an omission in this contract.

**Handler-side shape.** `shared/errors.py` already carries `BadRequest` (400), `NotFound` (404), and `Conflict`
(409); this contract adds `UnprocessableEntity` (422) and `ServiceUnavailable` (503), and each subclass needs a
per-condition `code` rather than reusing the class-level default, since the table's granularity lives in `error`,
not in the status.
