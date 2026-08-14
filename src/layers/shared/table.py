"""Key-schema constants for the single-table ledger design (design doc:
03-data-model.md). Every repository that reads or writes the table imports
these rather than restating them, so the partition/sort-key naming lives in
exactly one place — including `shared/idempotency.py`, which can't import them
from `shared/ledger.py` without a circular import (`ledger.py` imports
`idempotency.py` for the reusable idempotency-record plumbing).
"""

# TODO: get layer table name from env variables
LEDGER_TABLE_NAME = "payledger-ledger-table"

LEDGER_PK_NAME = "PK"
LEDGER_SORT_KEY_NAME = "SK"

LEDGER_GSI1_NAME = "GSI1"
LEDGER_GSI1_PK_NAME = "GSI1-PK"
LEDGER_GSI1_SORT_KEY_NAME = "GSI1-SK"

LEDGER_EXPIRED_HOLD_GS1_NAME = "EXPIRED_GSI"
LEDGER_EXPIRED_HOLD_PK_NAME = "expires_at"
