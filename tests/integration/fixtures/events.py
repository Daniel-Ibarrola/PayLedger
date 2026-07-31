"""Sample API Gateway events.

These are HTTP API (payload format 2.0) events, trimmed to the fields the
handlers actually read.
"""

import json
from typing import Any


def http_event(
    method: str,
    path: str,
    *,
    path_parameters: dict[str, str] | None = None,
    body: Any = None,
) -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "headers": {"content-type": "application/json"},
        "requestContext": {
            "http": {"method": method, "path": path},
            "requestId": "test-request-id",
        },
        "pathParameters": path_parameters or {},
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": False,
    }


def put_item_event(item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return http_event("POST", "/items", body={"item_id": item_id, **payload})


def get_item_event(item_id: str) -> dict[str, Any]:
    return http_event(
        "GET",
        "/items/{item_id}",
        path_parameters={"item_id": item_id},
    )
