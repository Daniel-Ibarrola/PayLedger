import datetime
import json
from typing import Any

import pytest
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from mypy_boto3_dynamodb.service_resource import Table

from authorization_service import handler
from tests.integration.fixtures import accounts, events
from tests.integration.fixtures.accounts import get_account
from tests.integration.fixtures.authorizations import create_authorization_record
from tests.integration.fixtures.ledger_entries import get_ledger_entries

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


def list_authorizations_for_account(ledger_table: Table, account_id: str) -> list[dict[str, Any]]:
    """All `AUTH#` items under an account's partition, for asserting a replay didn't
    double-write."""
    result = ledger_table.query(
        KeyConditionExpression=Key("PK").eq(f"ACCT#{account_id}") & Key("SK").begins_with("AUTH#")
    )
    return result.get("Items", [])


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


class TestCaptureAuthorization:
    """Tests for capturing an authorization through the
    POST /authorizations/{id}/capture endpoint
    """

    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_201_on_successful_capture(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        authorization_amount = 500
        merchant_id = "merchant_001"
        new_authorization_event = events.create_new_authorization_event(
            authorization_amount, merchant_id, idempotency_key="new_authorization_ik"
        )
        response = handler.lambda_handler(new_authorization_event, lambda_context)
        authorization = _body(response)

        event = events.capture_authorization_event(
            authorization["authorization_id"], idempotency_key="capture_authorization_ik"
        )
        response = handler.lambda_handler(event, lambda_context)

        # Check the authorization is now captured and the `updated_at` field was updated
        assert response["statusCode"] == 200
        captured_authorization = _body(response)
        updated_at = captured_authorization.pop("updated_at")
        assert captured_authorization == {
            "authorization_id": authorization["authorization_id"],
            "merchant_id": merchant_id,
            "amount": authorization_amount,
            "status": "CAPTURED",
            "created_at": authorization["created_at"],
            "expires_at": authorization["expires_at"],
        }
        assert updated_at > authorization["updated_at"]

        stored = list_authorizations_for_account(ledger_table, test_account["account_id"])
        assert len(stored) == 1
        assert stored[0]["status"] == "CAPTURED"

        # The account available and current balance are updated
        account_id = test_account["account_id"]
        account = get_account(test_account["account_id"], ledger_table)
        final_balance = test_account["current_balance"] - authorization_amount
        assert account is not None
        assert account["current_balance"] == final_balance
        assert account["available_balance"] == final_balance

        # Two ledger entries are created
        account_ledger_entries = get_ledger_entries(f"ACCT#{account_id}", ledger_table)
        assert len(account_ledger_entries) == 1
        ledger_entry = account_ledger_entries[0]
        assert ledger_entry["amount"] == authorization_amount
        assert ledger_entry["entry_type"] == "DEBIT"
        assert ledger_entry["party_type"] == "ACCOUNT"
        assert ledger_entry["transaction_id"].startswith("transaction_")

        merchant_ledger_entries = get_ledger_entries(f"MERCHANT#{merchant_id}", ledger_table)
        assert len(merchant_ledger_entries) == 1
        ledger_entry = merchant_ledger_entries[0]
        assert ledger_entry["amount"] == authorization_amount
        assert ledger_entry["entry_type"] == "CREDIT"
        assert ledger_entry["party_type"] == "MERCHANT"
        assert ledger_entry["transaction_id"].startswith("transaction_")

    @pytest.mark.usefixtures("insert_merchants", "test_account")
    def test_returns_400_for_missing_idempotency_key(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
    ) -> None:
        # A capturable authorization, so the only thing wrong with the request is
        # the missing header.
        authorization_id = "authorization_pending"
        ledger_table.put_item(Item=create_authorization_record(authorization_id, amount=50000))
        event = events.capture_authorization_event(authorization_id, idempotency_key=None)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MissingIdempotencyKey"

    @pytest.mark.usefixtures("insert_merchants", "test_account")
    def test_returns_404_for_unknown_authorization(self, lambda_context: LambdaContext) -> None:
        # No authorization was seeded, so this id names nothing that exists. The
        # same 404 is owed to an id owned by another account (design doc:
        # Error-response contract) — a 403 there would confirm the id exists.
        event = events.capture_authorization_event("authorization_does_not_exist")

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 404
        assert _body(response)["error"] == "AuthorizationNotFound"

    @pytest.mark.usefixtures("insert_merchants", "test_account")
    def test_returns_404_when_trying_to_capture_an_authorization_from_another_account(
        self, lambda_context: LambdaContext, ledger_table: Table
    ) -> None:
        new_account_id = "new-account"
        ledger_table.put_item(Item=accounts.create_account_record(new_account_id, 100000, 100000))

        authorization_id = "authorization_test_account"
        ledger_table.put_item(
            Item=create_authorization_record(
                authorization_id, account_id="test-account", amount=50000
            )
        )

        event = events.capture_authorization_event(authorization_id, sub=new_account_id)
        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 404
        assert _body(response)["error"] == "AuthorizationNotFound"

    @pytest.mark.parametrize(
        ("status", "error"),
        [
            pytest.param("CAPTURED", "AlreadyCaptured", id="already_captured"),
            pytest.param("VOIDED", "AlreadyVoided", id="already_voided"),
            pytest.param("EXPIRED", "AuthorizationExpired", id="expired"),
            pytest.param("REVERSED", "AuthorizationReversed", id="reversed"),
        ],
    )
    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_409_for_terminal_authorization(
        self,
        status: str,
        error: str,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        # The idempotency key is fresh, so this is a real request that has to fail
        # the PENDING guard rather than replay a stored response (design doc:
        # Idempotency outcomes).
        authorization_id = f"authorization_{status.lower()}"
        # The sweeper only marks an authorization EXPIRED once `expires_at` has
        # passed; every other terminal state is reached while the hold is in date.
        expires_at = (
            datetime.date.today() - datetime.timedelta(days=1) if status == "EXPIRED" else None
        )
        ledger_table.put_item(
            Item=create_authorization_record(authorization_id, status=status, expires_at=expires_at)
        )
        event = events.capture_authorization_event(
            authorization_id, idempotency_key=f"capture-{status.lower()}"
        )

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 409
        assert _body(response)["error"] == error

        # A rejected capture moves no money and posts no entries.
        account = get_account(test_account["account_id"], ledger_table)
        assert account is not None
        assert account["current_balance"] == test_account["current_balance"]
        assert account["available_balance"] == test_account["available_balance"]
        assert get_ledger_entries(f"ACCT#{test_account['account_id']}", ledger_table) == []


class TestVoidAuthorization:
    """Tests for releasing a hold through the POST /authorizations/{id}/void endpoint"""

    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_200_on_successful_void(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        authorization_amount = 500
        merchant_id = "merchant_001"
        new_authorization_event = events.create_new_authorization_event(
            authorization_amount, merchant_id, idempotency_key="new_authorization_ik"
        )
        response = handler.lambda_handler(new_authorization_event, lambda_context)
        authorization = _body(response)

        event = events.void_authorization_event(
            authorization["authorization_id"], idempotency_key="void_authorization_ik"
        )
        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 200
        voided_authorization = _body(response)
        updated_at = voided_authorization.pop("updated_at")
        assert voided_authorization == {
            "authorization_id": authorization["authorization_id"],
            "merchant_id": merchant_id,
            "amount": authorization_amount,
            "status": "VOIDED",
            "created_at": authorization["created_at"],
            "expires_at": authorization["expires_at"],
        }
        assert updated_at > authorization["updated_at"]

        stored = list_authorizations_for_account(ledger_table, test_account["account_id"])
        assert len(stored) == 1
        assert stored[0]["status"] == "VOIDED"

        # The hold is released: available_balance goes back to what it was before
        # the authorization, and current_balance never moved (design doc:
        # 02-architecture.md, the void branch of the walkthrough).
        account_id = test_account["account_id"]
        account = get_account(account_id, ledger_table)
        assert account is not None
        assert account["current_balance"] == test_account["current_balance"]
        assert account["available_balance"] == test_account["available_balance"]

        # A void moves no money, so unlike a capture it posts no ledger entries.
        assert get_ledger_entries(f"ACCT#{account_id}", ledger_table) == []
        assert get_ledger_entries(f"MERCHANT#{merchant_id}", ledger_table) == []

    @pytest.mark.usefixtures("insert_merchants")
    def test_replays_original_response_for_same_key(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        authorization_amount = 500
        new_authorization_event = events.create_new_authorization_event(
            authorization_amount, "merchant_001", idempotency_key="new_authorization_ik"
        )
        authorization = _body(handler.lambda_handler(new_authorization_event, lambda_context))

        event = events.void_authorization_event(
            authorization["authorization_id"], idempotency_key="void_authorization_ik"
        )
        first_response = handler.lambda_handler(event, lambda_context)
        second_response = handler.lambda_handler(event, lambda_context)

        assert first_response["statusCode"] == 200
        # The replay is the *original* response verbatim — same `updated_at`, not a
        # fresh 200 and not the 409 AlreadyVoided a new key would have earned.
        assert second_response["statusCode"] == first_response["statusCode"]
        assert _body(second_response) == _body(first_response)

        # The hold is released once, not twice: a second release would push
        # available_balance above current_balance.
        account = get_account(test_account["account_id"], ledger_table)
        assert account is not None
        assert account["current_balance"] == test_account["current_balance"]
        assert account["available_balance"] == test_account["available_balance"]

    @pytest.mark.usefixtures("insert_merchants", "test_account")
    def test_returns_400_for_missing_idempotency_key(
        self,
        lambda_context: LambdaContext,
        ledger_table: Table,
    ) -> None:
        # A voidable authorization, so the only thing wrong with the request is
        # the missing header.
        authorization_id = "authorization_pending"
        ledger_table.put_item(Item=create_authorization_record(authorization_id, amount=50000))
        event = events.void_authorization_event(authorization_id, idempotency_key=None)

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MissingIdempotencyKey"

    @pytest.mark.usefixtures("insert_merchants", "test_account")
    def test_returns_404_for_unknown_authorization(self, lambda_context: LambdaContext) -> None:
        # No authorization was seeded, so this id names nothing that exists.
        event = events.void_authorization_event("authorization_does_not_exist")

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 404
        assert _body(response)["error"] == "AuthorizationNotFound"

    @pytest.mark.usefixtures("insert_merchants", "test_account")
    def test_returns_404_when_trying_to_void_an_authorization_from_another_account(
        self, lambda_context: LambdaContext, ledger_table: Table
    ) -> None:
        # An id owned by another account is owed the same 404 as an id that does
        # not exist (design doc: Error-response contract) — a 403 here would
        # confirm the id exists.
        new_account_id = "new-account"
        ledger_table.put_item(Item=accounts.create_account_record(new_account_id, 100000, 100000))

        authorization_id = "authorization_test_account"
        ledger_table.put_item(
            Item=create_authorization_record(
                authorization_id, account_id="test-account", amount=50000
            )
        )

        event = events.void_authorization_event(authorization_id, sub=new_account_id)
        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 404
        assert _body(response)["error"] == "AuthorizationNotFound"

    @pytest.mark.parametrize(
        ("status", "error"),
        [
            pytest.param("CAPTURED", "AlreadyCaptured", id="already_captured"),
            pytest.param("VOIDED", "AlreadyVoided", id="already_voided"),
            pytest.param("EXPIRED", "AuthorizationExpired", id="expired"),
            pytest.param("REVERSED", "AuthorizationReversed", id="reversed"),
        ],
    )
    @pytest.mark.usefixtures("insert_merchants")
    def test_returns_409_for_terminal_authorization(
        self,
        status: str,
        error: str,
        lambda_context: LambdaContext,
        ledger_table: Table,
        test_account: dict[str, Any],
    ) -> None:
        # The idempotency key is fresh, so this is a real request that has to fail
        # the PENDING guard rather than replay a stored response — voiding a VOIDED
        # authorization under a new key is 409, not a courtesy 200 (design doc:
        # Idempotency outcomes).
        authorization_id = f"authorization_{status.lower()}"
        # The sweeper only marks an authorization EXPIRED once `expires_at` has
        # passed; every other terminal state is reached while the hold is in date.
        expires_at = (
            datetime.date.today() - datetime.timedelta(days=1) if status == "EXPIRED" else None
        )
        ledger_table.put_item(
            Item=create_authorization_record(authorization_id, status=status, expires_at=expires_at)
        )
        event = events.void_authorization_event(
            authorization_id, idempotency_key=f"void-{status.lower()}"
        )

        response = handler.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 409
        assert _body(response)["error"] == error

        # A rejected void releases nothing and posts no entries.
        account = get_account(test_account["account_id"], ledger_table)
        assert account is not None
        assert account["current_balance"] == test_account["current_balance"]
        assert account["available_balance"] == test_account["available_balance"]
        assert get_ledger_entries(f"ACCT#{test_account['account_id']}", ledger_table) == []
