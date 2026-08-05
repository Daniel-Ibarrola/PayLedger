"""Lambda handler for creating a new account.

This lambda is trigered by a sign in event in Cognito and creates a new account in the
ledger database in DynamoDB.
"""

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    raise NotImplementedError
