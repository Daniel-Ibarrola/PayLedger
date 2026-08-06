from __future__ import annotations

import decimal
import functools
from typing import Any

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
from schemas import (  # type: ignore[import-not-found]
    AuthorizationRequest,
    AuthorizationResponse,
    MerchantRequest,
    MerchantResponse,
)

from shared import domain
from shared.ledger import AccountRepository, AuthorizationRepository, MerchantRepository

logger = Logger()
app = APIGatewayHttpResolver()


# Built lazily rather than at import time: a repository's `__init__` binds a
# DynamoDB `Table` to whatever DYNAMODB_ENDPOINT_URL is set *at construction*, and
# the test harness only sets that env var once a test's fixtures have started —
# after this module is imported. `cache` still gives warm invocations a single
# reused instance, just deferred to first call instead of import time.
@functools.cache
def _get_merchant_repository() -> MerchantRepository:
    return MerchantRepository()


@functools.cache
def _get_account_repository() -> AccountRepository:
    return AccountRepository()


@functools.cache
def _get_authorization_repository() -> AuthorizationRepository:
    return AuthorizationRepository()


@app.exception_handler(ValidationError)  # type: ignore[untyped-decorator]
def handle_validation_error(ex: ValidationError) -> Response[dict[str, Any]]:
    """Map a pydantic validation failure to the design doc's error-response envelope."""
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
def create_authorization() -> Response[dict[str, Any]]:
    """Place a PENDING hold for the caller's account against a known merchant.

    `account_id` comes from the validated Cognito `sub` claim, never the request
    body. Returns 400 for an unknown merchant/account, 409 if the account lacks
    sufficient available funds, and 201 with the new authorization otherwise.
    """
    event: APIGatewayProxyEventV2 = app.current_event

    auth_request = AuthorizationRequest.model_validate(event.json_body)
    account_id = event.request_context.authorizer.jwt_claim["sub"]

    logger.info(f"Processing auth for merchant {auth_request.merchant_id}, account {account_id}")

    merchant = _get_merchant_repository().get_merchant(auth_request.merchant_id)
    if merchant is None:
        return Response(
            status_code=400,
            content_type=APPLICATION_JSON,
            body={
                "error": "UnknownMerchant",
                "message": f"Merchant {auth_request.merchant_id} not found",
            },
        )
    account = _get_account_repository().get_account(account_id)
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

    authorization = _get_authorization_repository().insert_authorization(
        account_id, auth_request.merchant_id, auth_request.amount
    )

    response = AuthorizationResponse(
        authorization_id=authorization.authorization_id,
        status=authorization.status.value,
        amount=auth_request.amount,
        merchant_id=auth_request.merchant_id,
        expires_at=authorization.expires_at.isoformat(),
        created_at=authorization.created_at.isoformat(),
        updated_at=authorization.updated_at.isoformat(),
    )
    logger.info(f"Created authorization {authorization.authorization_id} (account_id={account_id})")
    return Response(
        status_code=201,
        content_type=APPLICATION_JSON,
        body=response.model_dump(),
    )


@app.post("/merchants")
def create_merchant() -> Response[dict[str, Any]]:
    """Create a merchant with a zero payable balance; 400 if the id is already taken."""
    event: APIGatewayProxyEventV2 = app.current_event
    merchant_request = MerchantRequest.model_validate(event.json_body)

    merchant = domain.Merchant(
        merchant_id=merchant_request.merchant_id,
        name=merchant_request.merchant_name,
        payable_balance=decimal.Decimal(0),
    )

    try:
        _get_merchant_repository().insert_merchant(merchant)
    except domain.MerchantAlreadyExists:
        return Response(
            status_code=400,
            content_type=APPLICATION_JSON,
            body={"error": "MerchantAlreadyExists", "message": "Merchant already exists"},
        )

    merchant_response = MerchantResponse(
        merchant_id=merchant.merchant_id,
        merchant_name=merchant.name,
        payable_balance=int(merchant.payable_balance),
    )
    logger.info(f"Created merchant {merchant.name} (merchant_id={merchant.merchant_id})")
    return Response(
        status_code=201,
        content_type=APPLICATION_JSON,
        body=merchant_response.model_dump(),
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Lambda entry point: dispatch the API Gateway event to the matching route."""
    return app.resolve(event, context)
