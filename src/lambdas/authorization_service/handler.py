from __future__ import annotations

import decimal
import functools
from typing import Any

# Bare, not `authorization_service.authorization_schemas`: the deployed zip
# flattens this directory (infra/lambda.tf), so Lambda imports this file with
# `authorization_schemas.py` as a top-level sibling, not a package member. mypy
# can't resolve that without also reintroducing "Source file found twice under
# different module names" against the package identity `files` discovers it
# under, so the import is unverified.
from authorization_schemas import (  # type: ignore[import-not-found]
    AuthorizationRequest,
    AuthorizationResponse,
    MerchantRequest,
    MerchantResponse,
)
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.event_handler.content_types import APPLICATION_JSON
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError

from shared import domain, errors, idempotency
from shared.ledger import AuthorizationRepository, MerchantRepository
from shared.utils import error_body

logger = Logger()
# Instantiating Tracer patches boto3/botocore process-wide, so every DynamoDB
# call the repositories make shows up as a subsegment with no per-call
# decoration needed.
tracer = Tracer()
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
def _get_authorization_repository() -> AuthorizationRepository:
    return AuthorizationRepository()


@functools.cache
def _get_idempotency_repository() -> idempotency.IdempotencyRepository:
    return idempotency.IdempotencyRepository()


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


@app.exception_handler(errors.ApiError)  # type: ignore[untyped-decorator]
def handle_api_error(ex: errors.ApiError) -> Response[dict[str, Any]]:
    """Map any `ApiError` to the design doc's error-response envelope, so routes
    raise instead of hand-building a `Response` for every failure condition."""
    logger.warning(f"{ex.code}: {ex.message}")
    return Response(status_code=ex.status_code, content_type=APPLICATION_JSON, body=error_body(ex))


# Which 409 each terminal state earns (design doc: Error-response contract).
# PENDING is absent deliberately — it is the one status that isn't an error.
_TERMINAL_STATUS_ERRORS: dict[domain.AuthorizationStatus, type[errors.ApiError]] = {
    domain.AuthorizationStatus.CAPTURED: errors.AuthorizationAlreadyCaptured,
    domain.AuthorizationStatus.VOIDED: errors.AuthorizationAlreadyVoided,
    domain.AuthorizationStatus.EXPIRED: errors.AuthorizationExpired,
    domain.AuthorizationStatus.REVERSED: errors.AuthorizationReversed,
}


def _terminal_status_error(
    authorization_id: str, status: domain.AuthorizationStatus
) -> errors.ApiError:
    """The error a capture or void owes a caller whose authorization is `status`."""
    error_class = _TERMINAL_STATUS_ERRORS.get(status)
    if error_class is None:
        raise RuntimeError(
            f"authorization {authorization_id} is {status.value}, which is not a terminal state"
        )
    return error_class(f"authorization {authorization_id} is {status.value.lower()}")


def build_authorization_response(
    authorization: domain.Authorization, status_code: int = 201
) -> Response[dict[str, Any]]:
    auth_response = AuthorizationResponse(
        authorization_id=authorization.authorization_id,
        status=authorization.status.value,
        amount=int(authorization.amount),
        merchant_id=authorization.merchant_id,
        expires_at=authorization.expires_at.isoformat(),
        created_at=authorization.created_at.isoformat(),
        updated_at=authorization.updated_at.isoformat(),
    )
    return Response(
        status_code=status_code,
        content_type=APPLICATION_JSON,
        body=auth_response.model_dump(),
    )


@app.post("/authorizations")
def create_authorization() -> Response[dict[str, Any]]:
    """Place a PENDING hold for the caller's account against a known merchant.

    `account_id` comes from the validated Cognito `sub` claim, never the request
    body. Returns 400 for an unknown merchant, 409 if the account lacks
    sufficient available funds, and 201 with the new
    authorization otherwise.
    """
    event: APIGatewayProxyEventV2 = app.current_event

    auth_request = AuthorizationRequest.model_validate(event.json_body)
    account_id = event.request_context.authorizer.jwt_claim["sub"]

    idempotency_key = idempotency.require_key(event)
    replay = idempotency.check_replay(_get_idempotency_repository(), idempotency_key, auth_request)
    if replay is not None:
        logger.info("Replaying response for idempotency key %s", idempotency_key)
        return replay

    logger.info(
        "Processing auth for merchant %s, account %s",
        auth_request.merchant_id,
        account_id,
    )

    merchant = _get_merchant_repository().get_merchant(auth_request.merchant_id)
    if merchant is None:
        raise errors.UnknownMerchant(f"Merchant {auth_request.merchant_id} not found")

    try:
        response = _get_authorization_repository().insert_authorization(
            account_id,
            auth_request.merchant_id,
            auth_request.amount,
            idempotency_key,
            auth_request,
            build_authorization_response,
        )
    except domain.InsufficientFunds:
        raise errors.InsufficientFunds(f"Account {account_id} does not have enough funds") from None

    assert response.body is not None
    authorization_id = response.body["authorization_id"]
    logger.info("Created authorization %s (account_id=%s)", authorization_id, account_id)
    return response


@app.post("/authorizations/<authorization_id>/capture")
def capture_authorization(authorization_id: str) -> Response[dict[str, Any]]:
    event: APIGatewayProxyEventV2 = app.current_event

    account_id = event.request_context.authorizer.jwt_claim["sub"]
    idempotency_key = idempotency.require_key(event)
    replay = idempotency.check_replay(_get_idempotency_repository(), idempotency_key)
    if replay is not None:
        logger.info("Replaying response for idempotency key %s", idempotency_key)
        return replay

    logger.info("Capture initiated for authorization %s", authorization_id)
    authorization = _get_authorization_repository().get_authorization(authorization_id)

    if authorization is None or authorization.account_id != account_id:
        raise errors.AuthorizationNotFound(f"authorization {authorization_id} not found")

    try:
        response = _get_authorization_repository().capture_authorization(
            authorization, account_id, idempotency_key, build_authorization_response
        )
    except domain.AuthorizationNotPending as ex:
        # Not checked against the `authorization` read above: the capture's own
        # `ConditionExpression` is the only check that can't be raced, so the
        # status it rejected is the authoritative one.
        raise _terminal_status_error(authorization_id, ex.status) from None

    logger.info("Captured authorization %s", authorization_id)

    return response


@app.post("/authorizations/<authorization_id>/void")
def void_authorization(authorization_id: str) -> Response[dict[str, Any]]:
    event: APIGatewayProxyEventV2 = app.current_event

    account_id = event.request_context.authorizer.jwt_claim["sub"]
    idempotency_key = idempotency.require_key(event)
    replay = idempotency.check_replay(_get_idempotency_repository(), idempotency_key)
    if replay is not None:
        logger.info("Replaying response for idempotency key %s", idempotency_key)
        return replay

    logger.info("Void initiated for authorization %s", authorization_id)
    authorization = _get_authorization_repository().get_authorization(authorization_id)

    if authorization is None or authorization.account_id != account_id:
        raise errors.AuthorizationNotFound(f"authorization {authorization_id} not found")

    try:
        response = _get_authorization_repository().void_authorization(
            authorization, account_id, idempotency_key, build_authorization_response
        )
    except domain.AuthorizationNotPending as ex:
        raise _terminal_status_error(authorization_id, ex.status) from None

    logger.info("Voided authorization %s", authorization_id)

    return response


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
        raise errors.MerchantAlreadyExists("Merchant already exists") from None

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


@tracer.capture_lambda_handler
@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Lambda entry point: dispatch the API Gateway event to the matching route."""
    return app.resolve(event, context)
