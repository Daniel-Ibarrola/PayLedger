from __future__ import annotations

import datetime
import decimal
import functools
import uuid

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.event_handler.content_types import APPLICATION_JSON
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError

# Bare, not `authorization_service.schemas`: the deployed zip flattens this
# directory (infra/lambda.tf), so Lambda imports this file with `schemas.py` as a
# top-level sibling, not a package member. mypy can't resolve that without also
# reintroducing "Source file found twice under different module names" against
# the package identity `files` discovers it under, so the import is unverified.
from schemas import AuthorizationRequest, AuthorizationResponse  # type: ignore[import-not-found]

from shared import ledger

logger = Logger()
app = APIGatewayHttpResolver()


@functools.cache
def _get_ledger() -> ledger.Ledger:
    # Built lazily rather than at import time: `Ledger.__init__` binds a DynamoDB
    # `Table` to whatever DYNAMODB_ENDPOINT_URL is set *at construction*, and the
    # test harness only sets that env var once a test's fixtures have started —
    # after this module is imported. `cache` still gives warm invocations a single
    # reused instance, just deferred to first call instead of import time.
    return ledger.Ledger()


@app.exception_handler(ValidationError)
def handle_validation_error(ex: ValidationError) -> Response:
    # Full field-level detail is for the logs, not the client — the response
    # envelope (design doc: Error-response contract) carries just `error`/`message`.
    logger.warning(f"Request failed validation: {ex.errors()}")
    message = "; ".join(
        f"{'.'.join(str(loc) for loc in err['loc']) or 'body'}: {err['msg']}" for err in ex.errors()
    )
    return Response(
        status_code=400,
        content_type=APPLICATION_JSON,
        body={"error": "InvalidRequest", "message": message},
    )


@app.post("/authorizations")
def create_authorization() -> Response:
    event: APIGatewayProxyEventV2 = app.current_event

    auth_request = AuthorizationRequest.model_validate(event.json_body)
    account_id = event.request_context.authorizer.jwt_claim["sub"]

    logger.info(f"Processing auth for merchant {auth_request.merchant_id}, account {account_id}")

    merchant = _get_ledger().get_merchant(auth_request.merchant_id)
    if merchant is None:
        return Response(
            status_code=400,
            content_type=APPLICATION_JSON,
            body={
                "error": "UnknownMerchant",
                "message": f"Merchant {auth_request.merchant_id} not found",
            },
        )
    account = _get_ledger().get_account(account_id)
    if account is None:
        return Response(
            status_code=400,
            content_type=APPLICATION_JSON,
            body={
                "error": "UnknownAccount",
                "message": f"Account {account_id} not found",
            },
        )

    if not account.has_sufficient_funds(decimal.Decimal(auth_request.amount)):
        return Response(
            status_code=409,
            content_type=APPLICATION_JSON,
            body={
                "error": "InsufficientFunds",
                "message": f"Account {account_id} does not have enough funds",
            },
        )

    now = datetime.datetime.now()
    expires_at = datetime.date.today() + datetime.timedelta(days=7)
    response = AuthorizationResponse(
        authorization_id=f"authorization_{uuid.uuid4().hex}",
        status="PENDING",
        amount=auth_request.amount,
        merchant_id=auth_request.merchant_id,
        expires_at=expires_at.isoformat(),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )
    return Response(
        status_code=201,
        content_type=APPLICATION_JSON,
        body=response.model_dump(),
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
