# Overview

## Objective

To build a card authorization and double-entry ledger system. This system should allow users to authorize
payments and manage their accounts by viewing their balance and their transaction history.

## Scope

In scope: placing an authorization hold, capturing a hold into a posted transaction, voiding a hold, depositing
funds into an account, querying an account's current and available balance, and paginated transaction history.

Explicitly out of scope: multi-currency/FX, interest accrual, statement generation, chargebacks/disputes, and
**partial capture** — a capture is all-or-nothing for the full authorized amount. These
are deliberate exclusions, not omissions — they keep the invariant surface (hold lifecycle + double-entry balance)
tractable within the project's timeline.


## Success Criteria

The system is considered correct if the following invariants hold under all conditions, including concurrent
requests and randomized/adversarial input:

1. For every transaction, the sum of all ledger entries equals zero.
2. `available_balance = current_balance - sum(active_holds)`, at all times.
3. An authorization can be captured at most once, for exactly its authorized amount.
4. Replaying a request with the same idempotency key returns the original response, never a second effect. The one
   exception is a capture the saga later reverses: compensation invalidates the idempotency record, so a replay
   re-executes and fails the `PENDING` guard rather than returning a stale success.
5. Expired holds (default 7 days) release automatically, without manual intervention.

## Functional Requirements

- Users should be able to view their account's balance
- Users should be able to view their transaction history
- Users should be able to authorize payments 

## Non-Functional Requirements

- Maintain financial integrity by preventing double charges (Idempotency)
- The transaction history can have eventual consistency. However, reading balances must always
return the latest value.

## User Experience

The interaction is done only through the exposed rest endpoints. There will be no GUI or other modes
of interaction.
