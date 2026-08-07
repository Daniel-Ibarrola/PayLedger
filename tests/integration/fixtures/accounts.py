from typing import Any

import pytest
from mypy_boto3_dynamodb.service_resource import Table


def create_account_record(
    account_id: str, current_balance: int, available_balance: int
) -> dict[str, Any]:
    """Build a ledger-table item for an account's `META` record."""
    return {
        "PK": f"ACCT#{account_id}",
        "SK": "META",
        "account_id": account_id,
        "current_balance": current_balance,
        "available_balance": available_balance,
    }


@pytest.fixture
def test_account(ledger_table: Table) -> dict[str, Any]:
    """Seeds a "test-account" account with a $1,000.00 balance."""
    # $1,000.00 — comfortably above the $500.00 hold the happy-path test places.
    # Tests that need an insufficient balance re-seed a lower one of their own.
    account_record = create_account_record("test-account", 100000, 100000)
    ledger_table.put_item(Item=account_record)
    return account_record
