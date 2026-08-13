import datetime

import pytest
from aws_lambda_powertools.utilities.typing import LambdaContext
from mypy_boto3_dynamodb.service_resource import Table

from expired_hold_sweeper import handler
from tests.integration.fixtures.accounts import create_account_record, get_account
from tests.integration.fixtures.authorizations import (
    create_authorization_record,
    list_authorizations_for_account,
)

pytestmark = pytest.mark.integration


def seed_authorizations(ledger_table: Table, account_id: str) -> None:
    now = datetime.date.today()
    authorizations = [
        create_authorization_record(
            "auth-1",
            account_id=account_id,
            amount=50000,
            status="PENDING",
            expires_at=now - datetime.timedelta(days=8),
        ),
        create_authorization_record(
            "auth-2",
            account_id=account_id,
            status="CAPTURED",
            expires_at=now - datetime.timedelta(days=7),
        ),
        create_authorization_record(
            "auth-3",
            account_id=account_id,
            status="PENDING",
            expires_at=now + datetime.timedelta(days=7),
        ),
    ]
    with ledger_table.batch_writer() as batch:
        for auth in authorizations:
            batch.put_item(auth)


class TestExpiredHoldSweeper:
    def test_updates_expired_holds_status_and_releases_funds(
        self, ledger_table: Table, lambda_context: LambdaContext
    ) -> None:
        account_id = "test-account"
        # $1,000.00 current, $500.00 available: a $500.00 hold (auth-1) already reserved.
        ledger_table.put_item(Item=create_account_record(account_id, 100000, 50000))
        seed_authorizations(ledger_table, account_id)
        response = handler.lambda_handler(
            {"action": "expired_hold_cleanup", "source": "eventbridge_scheduler"}, lambda_context
        )

        assert response["status"] == "SUCCESS"
        assert response["authorizations_expired"] == 1

        authorizations = list_authorizations_for_account(ledger_table, account_id)
        assert len(authorizations) == 3

        assert (
            next(a for a in authorizations if a["authorization_id"] == "auth-1")["status"]
            == "EXPIRED"
        )
        assert (
            next(a for a in authorizations if a["authorization_id"] == "auth-2")["status"]
            == "CAPTURED"
        )
        assert (
            next(a for a in authorizations if a["authorization_id"] == "auth-3")["status"]
            == "PENDING"
        )

        account = get_account(account_id, ledger_table)
        assert account is not None
        assert account["available_balance"] == 100000
        assert account["current_balance"] == 100000

    def test_when_there_are_no_expired_holds(
        self, ledger_table: Table, lambda_context: LambdaContext
    ) -> None:
        response = handler.lambda_handler(
            {"action": "expired_hold_cleanup", "source": "eventbridge_scheduler"}, lambda_context
        )

        assert response["status"] == "SUCCESS"
        assert response["authorizations_expired"] == 0
