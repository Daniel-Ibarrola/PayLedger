import datetime
import json
from typing import Any

import pytest
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from mypy_boto3_dynamodb.service_resource import Table

from authorization_service import handler
from tests.integration.fixtures import accounts, events

pytestmark = pytest.mark.integration


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])  # type: ignore[no-any-return]


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


def get_account(account_id: str, ledger_table: Table) -> dict[str, Any] | None:
    """Account META items aren't projected into GSI1, so fetch by primary key."""
    response = ledger_table.get_item(Key={"PK": f"ACCT#{account_id}", "SK": "META"})
    return response.get("Item")


@pytest.mark.usefixtures("insert_merchants")
class TestNewAuthorization:
    """Tests for creating a new authorization with a pending hold. These are tests
    for the POST /authorizations endpoint

    """

    def test_returns_201_for_successful_authorization(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        authorization_amount = 50000
        event = events.create_new_authorization_event(authorization_amount, "merchant_001")

        before = datetime.datetime.now(datetime.UTC)
        response = handler.lambda_handler(event, lambda_context)
        after = datetime.datetime.now(datetime.UTC)

        assert response["statusCode"] == 201
        body = _body(response)
        created_at = datetime.datetime.fromisoformat(body.pop("created_at"))
        updated_at = datetime.datetime.fromisoformat(body.pop("updated_at"))
        authorization_id = body.pop("authorization_id")

        # `now()` isn't reproducible, so bracket it instead of asserting an exact value.
        assert before <= created_at <= after
        assert before <= updated_at <= after
        # Same story for the generated id: assert its shape, not a literal value.
        assert authorization_id.startswith("authorization_")

        today = datetime.date.today()
        a_week_from_today = today + datetime.timedelta(days=7)

        assert body == {
            "amount": 50000,
            "merchant_id": "merchant_001",
            "status": "PENDING",
            "expires_at": a_week_from_today.isoformat(),
        }

        authorization = get_authorization(authorization_id, ledger_table)
        assert authorization is not None
        assert authorization["amount"] == 50000
        assert authorization["merchant_id"] == "merchant_001"
        assert authorization["status"] == "PENDING"
        assert authorization["expires_at"] == a_week_from_today.isoformat()

        account = get_account(test_account["account_id"], ledger_table)
        assert account is not None
        assert account["account_id"] == test_account["account_id"]
        assert account["current_balance"] == test_account["current_balance"]
        assert (
            account["available_balance"] == test_account["available_balance"] - authorization_amount
        )

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"amount": 0, "merchant_id": "merchant_001"}, id="non_positive_amount"),
            pytest.param({"amount": 50000}, id="missing_merchant_id"),
            pytest.param(
                {"amount": 50000, "merchant_id": "merchant_001", "account_id": "someone-else"},
                id="account_id_in_body",
            ),
        ],
    )
    @pytest.mark.usefixtures("test_account")
    def test_returns_400_for_invalid_request(
        self,
        body: dict[str, Any],
        lambda_context: LambdaContext,
    ) -> None:
        event = events.http_event("POST", "/authorizations", body=body)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "InvalidRequest"

    def test_returns_400_for_unknown_merchant(self, lambda_context: LambdaContext) -> None:
        # The table is empty, so "merchant_999" names no merchant that exists.
        event = events.create_new_authorization_event(50000, "merchant_999")

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "UnknownMerchant"

    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_409_for_insufficient_funds(
        self,
        ledger_table: Table,
        lambda_context: LambdaContext,
    ) -> None:
        # Overrides the class-level insert_test_account balance ($1,000.00, sufficient
        # for the happy path) with one too low for the $500.00 hold this test places.
        ledger_table.put_item(Item=accounts.create_account_record("test-account", 1000, 1000))
        event = events.create_new_authorization_event(50000, "merchant_001", sub="test-account")

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 409
        assert _body(response)["error"] == "InsufficientFunds"


def list_authorizations_for_account(ledger_table: Table, account_id: str) -> list[dict[str, Any]]:
    """All `AUTH#` items under an account's partition, for asserting a replay didn't
    double-write."""
    result = ledger_table.query(
        KeyConditionExpression=Key("PK").eq(f"ACCT#{account_id}") & Key("SK").begins_with("AUTH#")
    )
    return result.get("Items", [])


@pytest.mark.usefixtures("insert_merchants")
class TestAuthorizationIdempotency:
    """Tests for the `Idempotency-Key` contract on `POST /authorizations`
    (design doc: `04-api.md` "Idempotency outcomes"). Written ahead of the
    handler-side implementation (TDD) — expected to fail red until idempotency
    is wired up.
    """

    def test_replays_original_response_for_same_key_and_body(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        event = events.create_new_authorization_event(
            50000, "merchant_001", idempotency_key="replay-key"
        )

        first_response = handler.lambda_handler(event, lambda_context)
        second_response = handler.lambda_handler(event, lambda_context)

        assert first_response["statusCode"] == 201
        # The replay must be the *original* response verbatim, not a fresh 201 —
        # same authorization_id, same timestamps, byte-for-byte body.
        assert second_response["statusCode"] == first_response["statusCode"]
        assert _body(second_response) == _body(first_response)

        authorizations = list_authorizations_for_account(ledger_table, test_account["account_id"])
        assert len(authorizations) == 1

        account = get_account(test_account["account_id"], ledger_table)
        assert account is not None
        # Funds reserved once, not twice.
        assert account["available_balance"] == test_account["available_balance"] - 50000

    @pytest.mark.usefixtures("test_account")
    def test_returns_422_for_same_key_different_body(
        self,
        lambda_context: LambdaContext,
    ) -> None:
        first_event = events.create_new_authorization_event(
            50000, "merchant_001", idempotency_key="reused-key"
        )
        second_event = events.create_new_authorization_event(
            75000, "merchant_001", idempotency_key="reused-key"
        )

        first_response = handler.lambda_handler(first_event, lambda_context)
        second_response = handler.lambda_handler(second_event, lambda_context)

        assert first_response["statusCode"] == 201
        assert second_response["statusCode"] == 422
        assert _body(second_response)["error"] == "IdempotencyKeyReuse"

    @pytest.mark.usefixtures("test_account")
    def test_returns_400_for_missing_idempotency_key(
        self,
        lambda_context: LambdaContext,
    ) -> None:
        event = events.create_new_authorization_event(50000, "merchant_001", idempotency_key=None)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MissingIdempotencyKey"


@pytest.mark.usefixtures("test_account")
class TestCreateMerchant:
    """Tests for POST /merchants"""

    def test_returns_201_for_valid_merchant(
        self, lambda_context: LambdaContext, ledger_table: Table
    ) -> None:
        merchant_id = "new_merchant"
        event = events.create_new_merchant_event(merchant_id, "bacardi")
        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 201

        body = _body(response)
        assert body == {
            "merchant_name": "bacardi",
            "payable_balance": 0,
            "merchant_id": merchant_id,
        }

        merchant_entry = ledger_table.get_item(Key={"PK": f"MERCHANT#{merchant_id}", "SK": "META"})
        item = merchant_entry.get("Item")

        assert item is not None

        merchant = item
        assert merchant["name"] == "bacardi"
        assert merchant["payable_balance"] == 0
        assert merchant["merchant_id"] == merchant_id

    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_400_if_merchant_already_exists(self, lambda_context: LambdaContext) -> None:
        event = events.create_new_merchant_event("merchant_001", "new_merchant")
        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MerchantAlreadyExists"
