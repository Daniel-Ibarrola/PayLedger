"""Integration tests for the toy items handler against DynamoDB Local.

The point of this suite is the wiring — handler → shared layer → boto3 → a real
DynamoDB API — not the domain logic, which is still a placeholder table.
"""

import json

import pytest

from items_service import handler
from tests.integration.fixtures.events import get_item_event, http_event, put_item_event

pytestmark = pytest.mark.integration


def _body(response: dict) -> dict:
    return json.loads(response["body"])


def test_put_item_returns_201_and_persists_the_item(toy_table, dynamodb_client):
    event = put_item_event("item-1", {"note": "hello", "count": 3})

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 201
    assert _body(response) == {"item_id": "item-1", "note": "hello", "count": 3}

    stored = dynamodb_client.get_item(TableName=toy_table, Key={"item_id": {"S": "item-1"}})
    assert stored["Item"]["note"] == {"S": "hello"}
    assert stored["Item"]["count"] == {"N": "3"}


def test_get_item_returns_the_item_that_was_put(toy_table):
    handler.lambda_handler(put_item_event("item-2", {"note": "round trip"}), None)

    response = handler.lambda_handler(get_item_event("item-2"), None)

    assert response["statusCode"] == 200
    assert _body(response) == {"item_id": "item-2", "note": "round trip"}


def test_get_missing_item_returns_404(toy_table):
    response = handler.lambda_handler(get_item_event("does-not-exist"), None)

    assert response["statusCode"] == 404
    assert _body(response)["error"] == "NotFound"


def test_put_item_without_item_id_returns_400(toy_table):
    event = http_event("POST", "/items", body={"note": "no id"})

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert _body(response)["error"] == "BadRequest"


def test_put_item_with_invalid_json_returns_400(toy_table):
    event = http_event("POST", "/items")
    event["body"] = "{not json"

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 400


def test_unknown_route_returns_404(toy_table):
    response = handler.lambda_handler(http_event("DELETE", "/items"), None)

    assert response["statusCode"] == 404


def test_put_item_is_a_full_overwrite(toy_table):
    handler.lambda_handler(put_item_event("item-3", {"note": "first", "extra": "gone"}), None)
    handler.lambda_handler(put_item_event("item-3", {"note": "second"}), None)

    response = handler.lambda_handler(get_item_event("item-3"), None)

    assert _body(response) == {"item_id": "item-3", "note": "second"}
