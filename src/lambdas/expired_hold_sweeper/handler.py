import functools
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

from shared.ledger import AuthorizationRepository

logger = Logger()
tracer = Tracer()


@functools.cache
def _get_authorization_repository() -> AuthorizationRepository:
    return AuthorizationRepository()


@tracer.capture_lambda_handler
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    logger.info("Expired Hold Sweeper initiating")
    logger.info(event)

    authorization_repo = _get_authorization_repository()
    expired_holds = authorization_repo.get_expired_holds()
    logger.info("Found %s expired hold(s)", len(expired_holds))

    expired_count = authorization_repo.update_expired_holds(expired_holds)

    logger.info("Expired hold sweeper cleaned %s record(s)", expired_count)

    return {"status": "SUCCESS", "authorizations_expired": expired_count}
