"""Lambda handler for creating a new account.

This lambda is triggered by a post-confirmation event in Cognito and creates a new account in the
ledger database in DynamoDB.
"""
import functools
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes.cognito_user_pool_event import (
    PostConfirmationTriggerEvent,
)
from aws_lambda_powertools.utilities.typing import LambdaContext

from shared import domain
from shared.ledger import AccountRepository

logger = Logger()

@functools.cache
def _get_account_repository() -> AccountRepository:
    return AccountRepository()


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    trigger_event = PostConfirmationTriggerEvent(event)
    sub = trigger_event.request.user_attributes["sub"]

    account = domain.Account(account_id=sub)
    try:
        _get_account_repository().insert_account(account)
    except domain.AccountAlreadyExists:
        logger.info("Account already exists")

    return event
