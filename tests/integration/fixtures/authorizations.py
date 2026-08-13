import datetime
from typing import Any

from boto3.dynamodb.conditions import Key
from mypy_boto3_dynamodb.service_resource import Table

from shared.ledger import authorization_sort_key


def create_authorization_record(
    authorization_id: str,
    *,
    account_id: str = "test-account",
    merchant_id: str = "merchant_001",
    amount: int = 50000,
    status: str = "PENDING",
    expires_at: datetime.date | None = None,
) -> dict[str, Any]:
    """Build a ledger-table item for an authorization.

    Mirrors what `AuthorizationRepository.insert_authorization` writes: the item
    lives in the owning account's partition and is reachable by id only through
    GSI1, so seeding it any other way would make it invisible to the handler.
    """
    now = datetime.datetime.now(datetime.UTC)
    if expires_at is None:
        expires_at = datetime.date.today() + datetime.timedelta(days=7)
    return {
        "PK": f"ACCT#{account_id}",
        "SK": authorization_sort_key(now, authorization_id),
        "GSI1-PK": f"AUTH#{authorization_id}",
        "GSI1-SK": "META",
        "authorization_id": authorization_id,
        "merchant_id": merchant_id,
        "amount": amount,
        "status": status,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "account_id": account_id,
    }


def query_ledger(
    ledger_table: Table, pk_value: str, pk_prefix: str, sk_value: str = "META"
) -> dict[str, Any] | None:
    result = ledger_table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1-PK").eq(f"{pk_prefix}#{pk_value}")
        & Key("GSI1-SK").eq(sk_value),
        Limit=1,
    )
    items = result.get("Items")
    if items is None:
        return None
    return items[0]


def get_authorization(authorization_id: str, ledger_table: Table) -> dict[str, Any] | None:
    return query_ledger(ledger_table, authorization_id, "AUTH")


def list_authorizations_for_account(ledger_table: Table, account_id: str) -> list[dict[str, Any]]:
    """All `AUTH#` items under an account's partition, for asserting a replay didn't
    double-write."""
    result = ledger_table.query(
        KeyConditionExpression=Key("PK").eq(f"ACCT#{account_id}") & Key("SK").begins_with("AUTH#")
    )
    return result.get("Items", [])
