"""DynamoDB access.

The resource is cached across invocations: building it costs a session load and
credential resolution, and reusing it keeps the HTTPS connection warm between
warm-start invocations. The cache is keyed on endpoint and region rather than
held in a module global so tests can point at a container without the first test
to run pinning the endpoint for the whole session.
"""

import os
from functools import cache
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


@cache
def _resource(endpoint_url: str | None, region_name: str) -> "DynamoDBServiceResource":
    return boto3.resource(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region_name,
        # Retry the throttles DynamoDB signals with ProvisionedThroughputExceeded /
        # ThrottlingException. `standard` also retries on the transaction conflict
        # errors the ledger work in week 1 will start producing.
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def get_table(table_name: str) -> "Table":
    """A `Table` for `table_name`, honouring DYNAMODB_ENDPOINT_URL when set.

    That env var is only ever set by the test harness; in Lambda it is absent and
    boto3 resolves the real regional endpoint.
    """
    endpoint_url = os.environ.get("DYNAMODB_ENDPOINT_URL") or None
    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return _resource(endpoint_url, region_name).Table(table_name)


def get_dynamodb_resource() -> "DynamoDBServiceResource":
    endpoint_url = os.environ.get("DYNAMODB_ENDPOINT_URL") or None
    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return _resource(endpoint_url, region_name)


def required_env(name: str) -> str:
    """Fail loudly on a missing configuration variable rather than at first use."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable '{name}' is not set")
    return value
