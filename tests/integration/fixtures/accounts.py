from typing import Any

import pytest


def create_account_record(
    account_id: str, current_balance: int, available_balance: int
) -> dict[str, Any]:
    return {
        "PK": f"ACCT#{account_id}",
        "SK": "META",
        "account_id": account_id,
        "current_balance": current_balance,
        "available_balance": available_balance,
    }


@pytest.fixture
def insert_test_account(ledger_table):
    # $1,000.00 — comfortably above the $500.00 hold the happy-path test places.
    # Tests that need an insufficient balance re-seed a lower one of their own.
    ledger_table.put_item(Item=create_account_record("test-account", 100000, 100000))
