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


@pytest.mark.usefixtures("insert_test_account")
@pytest.mark.usefixtures("insert_merchants")
class TestNewAuthorization:
    """Tests for creating a new authorization with a pending hold. These are tests
    for the POST /authorizations endpoint

    """

    def test_returns_201_for_successful_authorization(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
    ) -> None:
        event = events.create_new_authorization_event(50000, "merchant_001")

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

        authorization_entry = ledger_table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1-PK").eq(f"AUTH#{authorization_id}")
            & Key("GSI1-SK").eq("META"),
            Limit=1,
        )
        item = authorization_entry.get("Items")

        assert item is not None
        assert len(item) == 1
        assert item[0]["amount"] == 50000
        assert item[0]["merchant_id"] == "merchant_001"
        assert item[0]["status"] == "PENDING"
        assert item[0]["expires_at"] == a_week_from_today.isoformat()

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


@pytest.mark.usefixtures("insert_test_account")
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
