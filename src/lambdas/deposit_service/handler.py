import functools
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.event_handler.content_types import APPLICATION_JSON
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
from aws_lambda_powertools.utilities.typing import LambdaContext

# Bare, not `deposit_service.deposit_schemas`: the deployed zip flattens this
# directory (infra/lambda.tf), so Lambda imports this file with
# `deposit_schemas.py` as a top-level sibling, not a package member. mypy can't
# resolve that without also reintroducing "Source file found twice under
# different module names" against the package identity `files` discovers it
# under, so the import is unverified.
from deposit_schemas import DepositRequest, DepositResponse  # type: ignore[import-not-found]
from pydantic import ValidationError

from shared import domain, errors, idempotency
from shared.ledger import AccountRepository
from shared.utils import error_body

logger = Logger()
tracer = Tracer()

app = APIGatewayHttpResolver()


@functools.cache
def _get_idempotency_repository() -> idempotency.IdempotencyRepository:
    return idempotency.IdempotencyRepository()


@functools.cache
def _get_account_repository() -> AccountRepository:
    return AccountRepository()


@app.exception_handler(ValidationError)  # type: ignore[untyped-decorator]
def handle_validation_error(ex: ValidationError) -> Response[dict[str, Any]]:
    """Map a pydantic validation failure to the design doc's error-response envelope."""
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


@app.post("/deposits")
def create_deposit() -> Response[dict[str, Any]]:
    """Create a new deposit."""
    event: APIGatewayProxyEventV2 = app.current_event

    deposit_request = DepositRequest.model_validate(event.json_body)
    account_id = event.request_context.authorizer.jwt_claim["sub"]

    idempotency_key = idempotency.require_key(event)
    replay = idempotency.check_replay(
        _get_idempotency_repository(), idempotency_key, deposit_request
    )
    if replay is not None:
        logger.info(f"Replaying response for idempotency key {idempotency_key}")
        return replay

    logger.info(
        "Processing deposit for account %s",
        account_id,
    )

    def build_response(account: domain.Account) -> Response[dict[str, Any]]:
        deposit_response = DepositResponse(
            current_balance=int(account.current_balance),
            available_balance=int(account.available_balance),
        )
        return Response(
            status_code=201, content_type=APPLICATION_JSON, body=deposit_response.model_dump()
        )

    try:
        response = _get_account_repository().deposit(
            account_id, deposit_request.amount, idempotency_key, deposit_request, build_response
        )
    except domain.AccountNotFound:
        raise errors.NotFound(f"Account {account_id} not found") from None

    logger.info("Deposit processed for account %s", account_id)
    return response


@tracer.capture_lambda_handler
@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Lambda entry point: dispatch the API Gateway event to the matching route."""
    return app.resolve(event, context)
