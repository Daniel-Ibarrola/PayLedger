import json
from decimal import Decimal
from typing import Any

import pytest

from shared import utils
from shared.errors import BadRequest, NotFound
from tests.integration.fixtures.events import http_event


def body_of(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])  # type: ignore[no-any-return]


class TestRouteKey:
    def test_falls_back_to_method_and_raw_path_for_the_default_route(self) -> None:
        event = http_event("GET", "/items/abc")
        assert utils.route_key(event) == "GET /items/abc"

    def test_returns_empty_string_for_an_event_with_no_routing_information(self) -> None:
        assert utils.route_key({}) == ""


class TestParseJsonBody:
    def test_decodes_a_base64_encoded_body(self) -> None:
        import base64

        event = http_event(
            "POST",
            "/items",
            body=base64.b64encode(b'{"item_id": "x"}').decode(),
            is_base64_encoded=True,
        )
        assert utils.parse_json_body(event) == {"item_id": "x"}

    @pytest.mark.parametrize("raw", [None, "", "{not json", "[1, 2]", '"a string"', "null"])
    def test_rejects_anything_that_is_not_a_json_object(self, raw: str | None) -> None:
        event = http_event("POST", "/items", body=raw)
        with pytest.raises(BadRequest):
            utils.parse_json_body(event)


class TestJsonResponse:
    def test_sets_the_json_content_type(self) -> None:
        response = utils.json_response(200, {"a": 1})
        assert response["headers"]["content-type"] == "application/json"
        assert response["isBase64Encoded"] is False

    def test_serializes_an_integral_decimal_as_an_int(self) -> None:
        # DynamoDB returns every number as a Decimal; money is integer cents, so
        # an integral value must never round-trip through a float.
        response = utils.json_response(200, {"amount": Decimal("40000")})
        assert response["body"] == '{"amount": 40000}'

    def test_serializes_a_fractional_decimal_as_a_float(self) -> None:
        assert body_of(utils.json_response(200, {"rate": Decimal("1.5")})) == {"rate": 1.5}

    def test_raises_on_a_type_it_cannot_serialize(self) -> None:
        with pytest.raises(TypeError):
            utils.json_response(200, {"when": object()})


class TestErrorResponse:
    def test_carries_the_status_and_code_of_the_error(self) -> None:
        response = utils.error_response(NotFound("nope"))
        assert response["statusCode"] == 404
        assert body_of(response) == {"error": "NotFound", "message": "nope"}


class TestPathParameter:
    @pytest.mark.parametrize("event", [{}, {"pathParameters": None}, {"pathParameters": {}}])
    def test_rejects_a_missing_parameter(self, event: dict[str, Any]) -> None:
        with pytest.raises(BadRequest):
            utils.path_parameter(event, "item_id")


class TestRequiredStr:
    def test_returns_the_value(self) -> None:
        assert utils.required_str({"item_id": "abc"}, "item_id") == "abc"

    @pytest.mark.parametrize("payload", [{}, {"item_id": ""}, {"item_id": 7}, {"item_id": None}])
    def test_rejects_a_missing_or_non_string_value(self, payload: dict[str, Any]) -> None:
        with pytest.raises(BadRequest):
            utils.required_str(payload, "item_id")
