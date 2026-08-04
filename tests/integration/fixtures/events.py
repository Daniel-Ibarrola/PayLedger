"""Sample API Gateway events.

These are HTTP API (payload format 2.0) events, trimmed to the fields the
handlers actually read.
"""

import json
from typing import Any

from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2


def http_event(
    method: str,
    path: str,
    *,
    path_parameters: dict[str, str] | None = None,
    body: Any = None,
    is_base64_encoded: bool = False,
) -> APIGatewayProxyEventV2:
    data = {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "headers": {"content-type": "application/json"},
        "requestContext": {
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
        },
        "pathParameters": path_parameters or {},
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": is_base64_encoded,
    }
    return APIGatewayProxyEventV2(data)


def create_new_authorization_event(amount: int, merchant_id: str) -> APIGatewayProxyEventV2:
    return http_event(
        "POST",
        "/authorizations",
        body={"amount": amount, "merchant_id": merchant_id},
    )
