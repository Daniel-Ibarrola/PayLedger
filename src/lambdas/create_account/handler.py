"""Lambda handler for creating a new account.

This lambda is triggered by a post-confirmation event in Cognito and creates a new account in the
ledger database in DynamoDB.
"""

import functools
import time
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes.cognito_user_pool_event import (
    PostConfirmationTriggerEvent,
)
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import ConnectionError as BotoConnectionError

from shared import domain
from shared.ledger import AccountRepository

logger = Logger()

# Cognito enforces a hard 5-second timeout on this trigger regardless of the
# function's own configured timeout, so attempts/backoff have to stay well
# inside that budget even stacked on top of the boto3 client's own retries.
INSERT_ACCOUNT_MAX_ATTEMPTS = 3
INSERT_ACCOUNT_RETRY_BACKOFF_SECONDS = 0.2


@functools.cache
def _get_account_repository() -> AccountRepository:
    return AccountRepository()


def _insert_account_with_retry(account: domain.Account) -> None:
    """Insert the account, retrying if DynamoDB can't be reached at all.

    boto3's own retry config (`shared.dynamo`) already retries throttling and
    connection errors before raising, but that's bounded to a single client
    call. This adds a second, coarser layer on top so a connectivity blip that
    outlasts the SDK's own retries doesn't fail the whole post-confirmation flow.
    """
    for attempt in range(1, INSERT_ACCOUNT_MAX_ATTEMPTS + 1):
        try:
            _get_account_repository().insert_account(account)
            return
        except BotoConnectionError:
            if attempt == INSERT_ACCOUNT_MAX_ATTEMPTS:
                raise
            logger.warning(
                f"Could not reach DynamoDB (attempt {attempt}/{INSERT_ACCOUNT_MAX_ATTEMPTS}), "
                "retrying"
            )
            time.sleep(INSERT_ACCOUNT_RETRY_BACKOFF_SECONDS * attempt)


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    trigger_event = PostConfirmationTriggerEvent(event)
    sub = trigger_event.request.user_attributes["sub"]

    account = domain.Account(account_id=sub)
    try:
        _insert_account_with_retry(account)
    except domain.AccountAlreadyExists:
        logger.info("Account already exists")

    return event
