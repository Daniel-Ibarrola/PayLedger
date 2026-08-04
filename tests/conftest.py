"""Shared pytest fixtures.

Integration tests run against DynamoDB Local in a throwaway container. The
container is session-scoped (starting a JVM per test would dominate the runtime);
isolation between tests comes from truncating the table, not from restarting it.
"""

import os
import time
from collections.abc import Generator
from typing import Any

# Ryuk, testcontainers' reaper sidecar, cannot start against the Docker Desktop
# socket on this setup — it fails before our container is ever created. Every
# container here is stopped explicitly in its fixture's `finally`, so the reaper
# buys us nothing. Must be set before `testcontainers.core.config` is imported.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table
from testcontainers.core.container import DockerContainer

from shared import ledger

DYNAMODB_LOCAL_IMAGE = "amazon/dynamodb-local:2.5.2"
DYNAMODB_LOCAL_PORT = 8000


def _wait_until_ready(endpoint_url: str, timeout: float = 60.0) -> None:
    """Poll the DynamoDB API itself — the container's log line appears before it serves."""
    client = boto3.client("dynamodb", endpoint_url=endpoint_url, region_name="us-east-1")
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client.list_tables()
            return
        except (EndpointConnectionError, OSError) as exc:  # container not listening yet
            last_error = exc
            time.sleep(0.25)
    raise TimeoutError(f"DynamoDB Local not ready after {timeout}s") from last_error


@pytest.fixture(scope="session", autouse=True)
def _fake_aws_credentials() -> Generator[None, Any]:
    """Stop boto3 from picking up the developer's real credentials or region."""
    overrides = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="session")
def dynamodb_endpoint(_fake_aws_credentials: None) -> Generator[str, Any]:
    container = DockerContainer(DYNAMODB_LOCAL_IMAGE).with_exposed_ports(DYNAMODB_LOCAL_PORT)
    container.start()
    try:
        endpoint = (
            f"http://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(DYNAMODB_LOCAL_PORT)}"
        )
        _wait_until_ready(endpoint)
        yield endpoint
    finally:
        container.stop()


@pytest.fixture(scope="session")
def dynamodb(dynamodb_endpoint: str) -> DynamoDBServiceResource:
    return boto3.resource("dynamodb", endpoint_url=dynamodb_endpoint, region_name="us-east-1")


@pytest.fixture
def ledger_table(
    dynamodb: DynamoDBServiceResource, dynamodb_endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> Generator[Table, Any]:
    """The main ledger table"""
    table = dynamodb.create_table(
        TableName=ledger.LEDGER_TABLE_NAME,
        KeySchema=[{"AttributeName": ledger.ACCOUNT_PK, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": ledger.ACCOUNT_PK, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    monkeypatch.setenv("LEDGER_TABLE_NAME", ledger.LEDGER_TABLE_NAME)
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", dynamodb_endpoint)

    try:
        yield table
    finally:
        table.delete()
