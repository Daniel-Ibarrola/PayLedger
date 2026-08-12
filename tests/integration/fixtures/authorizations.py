import datetime
from typing import Any

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
