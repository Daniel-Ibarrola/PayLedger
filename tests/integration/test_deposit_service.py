import json
from typing import Any

import pytest
from aws_lambda_powertools.utilities.typing import LambdaContext
from mypy_boto3_dynamodb.service_resource import Table

from deposit_service import handler
from tests.integration.fixtures import events
from tests.integration.fixtures.accounts import get_account
from tests.integration.fixtures.ledger_entries import get_ledger_entries

pytestmark = pytest.mark.integration


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])  # type: ignore[no-any-return]


class TestNewDeposit:
    """Tests for the POST /deposits endpoint"""

    def test_returns_201_on_success(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        deposit_amount = 50000
        event = events.new_deposit_event(deposit_amount)
        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 201
        body = json.loads(response["body"])

        assert body["current_balance"] == test_account["current_balance"] + deposit_amount
        assert body["available_balance"] == test_account["available_balance"] + deposit_amount

        # Check that two ledger entries were written, one for the account and one
        # for the external party
        account_pk = f"ACCT#{test_account['account_id']}"
        account_ledger_entries = get_ledger_entries(account_pk, ledger_table)
        assert len(account_ledger_entries) == 1
        ledger_entry = account_ledger_entries[0]
        assert ledger_entry["amount"] == deposit_amount
        assert ledger_entry["entry_type"] == "DEBIT"
        assert ledger_entry["party_type"] == "ACCOUNT"
        assert ledger_entry["transaction_id"].startswith("transaction_")

        external_party_ledger_entries = get_ledger_entries("EXTERNAL#funding", ledger_table)
        assert len(external_party_ledger_entries) == 1
        ledger_entry = external_party_ledger_entries[0]
        assert ledger_entry["amount"] == deposit_amount
        assert ledger_entry["entry_type"] == "CREDIT"
        assert ledger_entry["party_type"] == "EXTERNAL"
        assert ledger_entry["transaction_id"].startswith("transaction_")

    def test_is_idempotent(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        event = events.new_deposit_event(50000, idempotency_key="replay-key")

        first_response = handler.lambda_handler(event, lambda_context)
        second_response = handler.lambda_handler(event, lambda_context)

        assert first_response["statusCode"] == 201
        # The replay must be the *original* response verbatim, not a fresh 201 —
        # same balances, byte-for-byte body.
        assert second_response["statusCode"] == first_response["statusCode"]
        assert _body(second_response) == _body(first_response)

        account_pk = f"ACCT#{test_account['account_id']}"
        account_ledger_entries = get_ledger_entries(account_pk, ledger_table)
        assert len(account_ledger_entries) == 1

        account = get_account(test_account["account_id"], ledger_table)
        assert account is not None
        # Funds credited once, not twice.
        assert account["current_balance"] == test_account["current_balance"] + 50000
        assert account["available_balance"] == test_account["available_balance"] + 50000

    @pytest.mark.usefixtures("test_account")
    def test_returns_400_on_invalid_amount(self, lambda_context: LambdaContext) -> None:
        event = events.new_deposit_event(0)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "InvalidRequest"

    @pytest.mark.usefixtures("test_account")
    def test_returns_400_for_missing_idempotency_key(self, lambda_context: LambdaContext) -> None:
        event = events.new_deposit_event(50000, idempotency_key=None)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MissingIdempotencyKey"

    @pytest.mark.usefixtures("test_account")
    def test_returns_422_for_same_idempotency_key_with_different_body(
        self, lambda_context: LambdaContext
    ) -> None:
        first_event = events.new_deposit_event(50000, idempotency_key="reused-key")
        second_event = events.new_deposit_event(75000, idempotency_key="reused-key")

        first_response = handler.lambda_handler(first_event, lambda_context)
        second_response = handler.lambda_handler(second_event, lambda_context)

        assert first_response["statusCode"] == 201
        assert second_response["statusCode"] == 422
        assert _body(second_response)["error"] == "IdempotencyKeyReuse"
