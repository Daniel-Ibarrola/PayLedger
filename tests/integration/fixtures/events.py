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
    is_base64_encoded: bool = False,
    sub: str | None = "test-account",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a payload-format-2.0 API Gateway event for `method path`.

    `sub` fills in the Cognito JWT authorizer claim; pass `None` to build an
    unauthenticated event with no `authorizer` context. `headers` is merged over
    the default `content-type`, lowercased like API Gateway does before the
    Lambda ever sees it.
    """
    request_context: dict[str, Any] = {
        "http": {
            "method": method,
            "path": path,
            "protocol": "HTTP/1.1",
            "sourceIp": "127.0.0.1",
            "userAgent": "pytest",
        },
        "requestId": "test-request-id",
        "accountId": "123456789012",
        "apiId": "api-id",
        "domainName": "localhost",
        "domainPrefix": "localhost",
        "stage": "$default",
        "time": "12/Mar/2020:19:03:58 +0000",
        "timeEpoch": 1583348638390,
    }
    # Shape of a Cognito JWT authorizer context on an HTTP API (payload format 2.0);
    # not read by the handler yet, but future account_id-from-sub derivation needs it.
    if sub is not None:
        request_context["authorizer"] = {"jwt": {"claims": {"sub": sub}}}
    data = {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "headers": {"content-type": "application/json", **(headers or {})},
        "requestContext": request_context,
        "pathParameters": path_parameters or {},
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": is_base64_encoded,
    }
    return data


def create_new_authorization_event(
    amount: int,
    merchant_id: str,
    *,
    sub: str = "test-account",
    idempotency_key: str | None = "test-idempotency-key",
) -> dict[str, Any]:
    """Build a `POST /authorizations` event.

    `idempotency_key` fills in the `Idempotency-Key` header; pass `None` to build
    a request with no key at all (for the `MissingIdempotencyKey` case).
    """
    headers = {} if idempotency_key is None else {"idempotency-key": idempotency_key}
    return http_event(
        "POST",
        "/authorizations",
        body={"amount": amount, "merchant_id": merchant_id},
        sub=sub,
        headers=headers,
    )


def create_new_merchant_event(
    merchant_id: str, merchant_name: str, *, sub: str = "test-account"
) -> dict[str, Any]:
    """Build a `POST /merchants` event."""
    return http_event(
        "POST",
        "/merchants",
        body={"merchant_name": merchant_name, "merchant_id": merchant_id},
        sub=sub,
    )


def post_confirmation_event(
    user_attributes: dict[str, str],
    *,
    client_metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a Cognito Post Confirmation trigger event."""
    return {
        "request": {
            "userAttributes": user_attributes,
            "clientMetadata": client_metadata or {},
        },
        "response": {},
    }
