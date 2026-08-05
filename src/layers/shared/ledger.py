import datetime
import uuid
from decimal import Decimal
from typing import cast

from shared import domain, dynamo

# TODO: get layer table name from env variables
LEDGER_TABLE_NAME = "payledger-ledger-table"

LEDGER_PK_NAME = "PK"
LEDGER_SORT_KEY_NAME = "SK"

LEDGER_GSI1_NAME = "GSI1"
LEDGER_GSI1_PK_NAME = "GSI1-PK"
LEDGER_GSI1_SORT_KEY_NAME = "GSI1-SK"


class MerchantRepository:
    """Read/write access to merchant items in the single-table ledger."""

    def __init__(self, table_name: str = LEDGER_TABLE_NAME) -> None:
        self._table = dynamo.get_table(table_name)

    def get_merchant(self, merchant_id: str) -> domain.Merchant | None:
        """Fetch a merchant's metadata item, or `None` if it doesn't exist."""
        response = self._table.get_item(
            Key={LEDGER_PK_NAME: f"MERCHANT#{merchant_id}", LEDGER_SORT_KEY_NAME: "META"}
        )
        item = response.get("Item")
        if item is None:
            return None

        # The resource API's attribute-value stubs cover every DynamoDB type; these
        # attributes are always written as string/number, so the runtime shape is
        # narrower than what the stubs report.
        return domain.Merchant(
            merchant_id=cast(str, item["merchant_id"]),
            name=cast(str, item["name"]),
            payable_balance=cast(Decimal, item["payable_balance"]),
        )

    def insert_merchant(self, merchant: domain.Merchant) -> None:
        """Create a merchant with a zero payable balance, or `None` if it already exists."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        pk = f"MERCHANT#{merchant.merchant_id}"

        try:
            self._table.put_item(
                Item={
                    LEDGER_PK_NAME: pk,
                    LEDGER_SORT_KEY_NAME: "META",
                    "merchant_id": merchant.merchant_id,
                    "name": merchant.name,
                    "payable_balance": Decimal(0),
                    "created_at": now,
                },
                ConditionExpression=f"attribute_not_exists({LEDGER_PK_NAME})",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            raise domain.MerchantAlreadyExists from None


class AccountRepository:
    """Read/write access to account items in the single-table ledger."""

    def __init__(self, table_name: str = LEDGER_TABLE_NAME) -> None:
        self._table = dynamo.get_table(table_name)

    def get_account(self, account_id: str) -> domain.Account | None:
        """Fetch an account's metadata item, or `None` if it doesn't exist."""
        response = self._table.get_item(
            Key={LEDGER_PK_NAME: f"ACCT#{account_id}", LEDGER_SORT_KEY_NAME: "META"}
        )
        item = response.get("Item")
        if item is None:
            return None

        return domain.Account(
            account_id=cast(str, item["account_id"]),
            current_balance=cast(Decimal, item["current_balance"]),
            available_balance=cast(Decimal, item["available_balance"]),
        )

    def insert_account(self, account: domain.Account) -> None:
        try:
            self._table.put_item(
                Item={
                    LEDGER_PK_NAME: f"ACCT#{account.account_id}",
                    LEDGER_SORT_KEY_NAME: "META",
                    "account_id": account.account_id,
                    "current_balance": Decimal(0),
                    "available_balance": Decimal(0),
                },
                ConditionExpression=f"attribute_not_exists({LEDGER_PK_NAME})",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            raise domain.AccountAlreadyExists from None


class AuthorizationRepository:
    """Read/write access to authorization items in the single-table ledger."""

    def __init__(self, table_name: str = LEDGER_TABLE_NAME) -> None:
        self._table = dynamo.get_table(table_name)

    def insert_authorization(
        self, account_id: str, merchant_id: str, amount: int
    ) -> domain.Authorization:
        """Place a new PENDING authorization hold, expiring 7 days from today."""
        now = datetime.datetime.now(datetime.UTC)
        expires_at = datetime.date.today() + datetime.timedelta(days=7)
        authorization_id = f"authorization_{uuid.uuid4().hex}"
        self._table.put_item(
            Item={
                LEDGER_PK_NAME: f"ACCT#{account_id}",
                LEDGER_SORT_KEY_NAME: f"AUTH#{now}#{authorization_id}",
                LEDGER_GSI1_PK_NAME: f"AUTH#{authorization_id}",
                LEDGER_GSI1_SORT_KEY_NAME: "META",
                "authorization_id": authorization_id,
                "merchant_id": merchant_id,
                "amount": amount,
                "status": domain.AuthorizationStatus.PENDING.value,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        )
        return domain.Authorization(
            authorization_id=authorization_id,
            merchant_id=merchant_id,
            amount=Decimal(amount),
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            status=domain.AuthorizationStatus.PENDING,
        )
