from __future__ import annotations

import datetime
import uuid

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2, event_source
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError
from schemas import AuthorizationRequest, AuthorizationResponse

logger = Logger()
app = APIGatewayHttpResolver()


@app.exception_handler(ValidationError)
def handle_validation_error(ex: ValidationError):
    metadata = {"errors": ex.errors()}
    return {"statusCode": 422, "body": metadata}


@app.post("/authorizations")
def create_authorization() -> dict:
    event: APIGatewayProxyEventV2 = app.current_event

    auth_request = AuthorizationRequest.model_validate(event.json_body)
    logger.info(f"Processing auth for {auth_request.merchant_id}")

    now = datetime.datetime.now()
    expires_at = datetime.date.today() + datetime.timedelta(days=7)
    response = AuthorizationResponse(
        authorization_id=f"authorization_{uuid.uuid4().hex}",
        status="PENDING",
        amount=auth_request.amount,
        merchant_id=auth_request.merchant_id,
        expires_at=expires_at.isoformat(),
        created_at=now.isoformat(),
        updated_at=now.isoformat()
    )
    return response.model_dump()


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@event_source(data_class=APIGatewayProxyEventV2)
def lambda_handler(event: APIGatewayProxyEventV2, context: LambdaContext) -> dict:
    return app.resolve(event, context)
