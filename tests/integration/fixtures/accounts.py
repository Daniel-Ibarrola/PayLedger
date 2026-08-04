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
    ledger_table.put_item(Item=create_account_record("test-account", 1000, 1000))