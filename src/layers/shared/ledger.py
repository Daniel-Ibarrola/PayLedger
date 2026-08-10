import datetime
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

from aws_lambda_powertools.event_handler import Response
from pydantic import BaseModel

from shared import domain, dynamo, idempotency
from shared.table import (
    LEDGER_GSI1_PK_NAME,
    LEDGER_GSI1_SORT_KEY_NAME,
    LEDGER_PK_NAME,
    LEDGER_SORT_KEY_NAME,
    LEDGER_TABLE_NAME,
)


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
        self._table_name = table_name
        self._dynamodb = dynamo.get_dynamodb_resource()
        self._dynamodb_client = self._dynamodb.meta.client
        self._idempotency_repository = idempotency.IdempotencyRepository(table_name)

    def insert_authorization(
        self,
        account_id: str,
        merchant_id: str,
        amount: int,
        idempotency_key: str,
        request: BaseModel,
        build_response: Callable[[domain.Authorization], Response[dict[str, Any]]],
    ) -> Response[dict[str, Any]]:
        """Place a new PENDING authorization hold, expiring 7 days from today.

        `build_response` turns the not-yet-persisted authorization into the API
        response, so that response can be embedded as this key's idempotency
        snapshot in the same transaction that creates the hold.
        """
        now = datetime.datetime.now(datetime.UTC)
        expires_at = datetime.date.today() + datetime.timedelta(days=7)
        authorization_id = f"authorization_{uuid.uuid4().hex}"

        authorization = domain.Authorization(
            authorization_id=authorization_id,
            merchant_id=merchant_id,
            amount=Decimal(amount),
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            status=domain.AuthorizationStatus.PENDING,
        )
        response = build_response(authorization)

        try:
            self._dynamodb_client.transact_write_items(
                TransactItems=[
                    {
                        # Reserve funds on account. Fails the same way whether the
                        # hold exceeds available_balance or the account doesn't
                        # exist at all (available_balance is then undefined) -
                        # both are reported to the caller as insufficient funds.
                        "Update": {
                            "TableName": self._table_name,
                            "Key": {
                                LEDGER_PK_NAME: f"ACCT#{account_id}",
                                LEDGER_SORT_KEY_NAME: "META",
                            },
                            "UpdateExpression": "SET available_balance = available_balance - :amt",
                            "ConditionExpression": "available_balance >= :amt",
                            "ExpressionAttributeValues": {":amt": amount},
                        }
                    },
                    {
                        # Create the authorization record
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
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
                            },
                        }
                    },
                    idempotency.transact_item(self._table_name, idempotency_key, request, response),
                ]
            )
        except self._dynamodb_client.exceptions.TransactionCanceledException as ex:
            # CancellationReasons is positional, one entry per TransactItems entry
            # (design doc: Error-response contract, "Deriving the code from a
            # TransactionCanceledException"). Only the balance guard (item 0) is a
            # client-facing failure; item 2 (index) is the idempotency record, and
            # losing that race means replaying the winner's response, not failing.
            # Anything else (e.g. an authorization id collision) is a bug, so it
            # re-raises as-is for the 500 handler.
            reasons = ex.response.get("CancellationReasons", [])
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                raise domain.InsufficientFunds from None
            replay = idempotency.resolve_conflict(
                self._idempotency_repository, idempotency_key, reasons, item_index=2
            )
            if replay is not None:
                return replay
            raise

        return response
