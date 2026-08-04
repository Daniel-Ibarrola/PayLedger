import datetime
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table

from authorization_service import handler
from tests.integration.fixtures import events

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

pytestmark = pytest.mark.integration


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])  # type: ignore[no-any-return]


@pytest.mark.skip
class TestNewAuthorization:
    """Tests for creating a new authorization with a pending hold. These are tests
    for the POST /authorizations endpoint

    """

    def test_returns_201_for_successful_authorization(
        self, ledger_table: Table, dynamodb: DynamoDBServiceResource
    ) -> None:
        event = events.create_new_authorization_event(50000, "merchant_001")

        response = handler.lambda_handler(event, cast("LambdaContext", None))

        today = datetime.date.today()
        a_week_from_today = today + datetime.timedelta(days=7)

        assert _body(response) == {
            "authorization_id": "authorization_001",
            "amount": 50000,
            "merchant_id": "merchant_001",
            "status": "PENDING",
            "expires_at": a_week_from_today.isoformat(),
            # TODO: created_at and updated_at should contain hours and minutes
            "created_at": today.isoformat(),
            "updated_at": today.isoformat(),
        }
