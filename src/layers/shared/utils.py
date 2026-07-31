"""Helpers for speaking API Gateway's HTTP API (payload format 2.0) dialect."""

import base64
import json
import logging
import os
from decimal import Decimal
from typing import Any

from shared.errors import ApiError, BadRequest


def get_logger(name: str) -> logging.Logger:
    """A logger that respects LOG_LEVEL and does not fight Lambda's root handler."""
    logger = logging.getLogger(name)
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    return logger


def _json_default(value: Any) -> Any:
    # DynamoDB's resource API hands back every number as a Decimal. Money in this
    # system is integer minor units, so an integral Decimal must serialize as an
    # int — never as a float, which would reintroduce the rounding error the
    # integer-cents rule exists to avoid.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def json_response(status_code: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=_json_default),
        "isBase64Encoded": False,
    }


def error_response(error: ApiError) -> dict[str, Any]:
    return json_response(error.status_code, {"error": error.code, "message": error.message})


def route_key(event: dict[str, Any]) -> str:
    """`"POST /items"` — the same string API Gateway routes on.

    Derived from `requestContext` rather than the top-level `routeKey` so it is
    still correct for a `$default` route, where `routeKey` is literally
    `"$default"` and tells you nothing.
    """
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "")
    # The top-level routeKey carries the path *template* (`/items/{item_id}`),
    # which is what we want to dispatch on; rawPath carries the resolved value.
    declared = event.get("routeKey", "")
    if declared and declared != "$default":
        return declared
    return f"{method} {event.get('rawPath', '')}".strip()


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    """The request body as a dict, or `BadRequest` if it isn't one."""
    raw = event.get("body")
    if raw is None or raw == "":
        raise BadRequest("request body is required")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BadRequest("request body must be a JSON object")
    return parsed


def path_parameter(event: dict[str, Any], name: str) -> str:
    value = (event.get("pathParameters") or {}).get(name)
    if not value:
        raise BadRequest(f"path parameter '{name}' is required")
    return value


def required_str(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise BadRequest(f"'{name}' is required and must be a non-empty string")
    return value
