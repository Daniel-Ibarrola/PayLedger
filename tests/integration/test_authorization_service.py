import datetime
import json
from typing import Any

import pytest
from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table

from authorization_service import handler
from tests.integration.fixtures import events

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
        ledger_table: Table,
        dynamodb: DynamoDBServiceResource,
        lambda_context,
    ) -> None:
        event = events.create_new_authorization_event(50000, "merchant_001")

        before = datetime.datetime.now()
        response = handler.lambda_handler(event, lambda_context)
        after = datetime.datetime.now()

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


@pytest.mark.usefixtures("insert_test_account")
@pytest.mark.usefixtures("insert_merchants")
class TestNewAuthorizationErrors:
    """Unhappy paths for POST /authorizations, per the error-response contract in
    docs/design-doc.md (§ Error-response contract).
    """

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
        ledger_table: Table,
        dynamodb: DynamoDBServiceResource,
        lambda_context,
    ) -> None:
        event = events.http_event("POST", "/authorizations", body=body)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "InvalidRequest"

    def test_returns_400_for_unknown_merchant(
        self, ledger_table: Table, dynamodb: DynamoDBServiceResource, lambda_context
    ) -> None:
        # The table is empty, so "merchant_999" names no merchant that exists.
        event = events.create_new_authorization_event(50000, "merchant_999")

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "UnknownMerchant"

    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_409_for_insufficient_funds(
        self,
        ledger_table: Table,
        dynamodb: DynamoDBServiceResource,
        lambda_context,
    ) -> None:
        # Available balance is $10.00; the hold requested is $500.00.
        event = events.create_new_authorization_event(50000, "merchant_001", sub="test-account")

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 409
        assert _body(response)["error"] == "InsufficientFunds"
