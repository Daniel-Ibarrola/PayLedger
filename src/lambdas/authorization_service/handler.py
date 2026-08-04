from __future__ import annotations

import datetime
import uuid

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.event_handler.content_types import APPLICATION_JSON
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError
from schemas import AuthorizationRequest, AuthorizationResponse

from shared import dynamo, ledger

logger = Logger()
app = APIGatewayHttpResolver()

ledger_table = dynamo.get_table(ledger.LEDGER_TABLE_NAME)

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
def create_authorization() -> dict:
    event: APIGatewayProxyEventV2 = app.current_event

    auth_request = AuthorizationRequest.model_validate(event.json_body)
    account_id = event.request_context.authorizer.jwt_claim["sub"]

    logger.info(f"Processing auth for merchant {auth_request.merchant_id}, account {account_id}")

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
    return response.model_dump()


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
