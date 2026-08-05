import decimal

import pytest
from aws_lambda_powertools.utilities.typing import LambdaContext
from mypy_boto3_dynamodb.service_resource import Table

from create_account import handler
from tests.integration.fixtures import events

pytestmark = pytest.mark.integration


class TestCreateAccount:
    def test_inserts_account_record_to_dynamodb(
        self, lambda_context: LambdaContext, ledger_table: Table
    ) -> None:
        account_id = "test-account"
        event = events.post_confirmation_event({"sub": account_id, "email": "test@example.com"})
        handler.lambda_handler(event, lambda_context)

        account_entry = ledger_table.get_item(Key={"PK": f"ACCT#{account_id}", "SK": "META"})
        item = account_entry.get("Item")

        assert item is not None
        assert item["current_balance"] == decimal.Decimal(0)
        assert item["available_balance"] == decimal.Decimal(0)
        assert item["account_id"] == account_id
