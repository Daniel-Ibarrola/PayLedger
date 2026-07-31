"""Shared pytest fixtures.

Integration tests run against DynamoDB Local in a throwaway container. The
container is session-scoped (starting a JVM per test would dominate the runtime);
isolation between tests comes from truncating the table, not from restarting it.
"""

import os
import time

# Ryuk, testcontainers' reaper sidecar, cannot start against the Docker Desktop
# socket on this setup — it fails before our container is ever created. Every
# container here is stopped explicitly in its fixture's `finally`, so the reaper
# buys us nothing. Must be set before `testcontainers.core.config` is imported.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from testcontainers.core.container import DockerContainer

DYNAMODB_LOCAL_IMAGE = "amazon/dynamodb-local:2.5.2"
DYNAMODB_LOCAL_PORT = 8000

TOY_TABLE_NAME = "payledger-toy-items"
TOY_TABLE_PK = "item_id"


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
def _fake_aws_credentials():
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
def dynamodb_endpoint(_fake_aws_credentials) -> str:
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
def dynamodb_client(dynamodb_endpoint: str):
    return boto3.client("dynamodb", endpoint_url=dynamodb_endpoint, region_name="us-east-1")


@pytest.fixture
def toy_table(dynamodb_client, dynamodb_endpoint: str, monkeypatch):
    """A freshly created toy table, plus the env vars the handler reads.

    Created and dropped per test: the table is tiny and DynamoDB Local creates it
    instantly, which is simpler than truncating and leaves no cross-test residue.
    """
    dynamodb_client.create_table(
        TableName=TOY_TABLE_NAME,
        KeySchema=[{"AttributeName": TOY_TABLE_PK, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": TOY_TABLE_PK, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    monkeypatch.setenv("TOY_TABLE_NAME", TOY_TABLE_NAME)
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", dynamodb_endpoint)
    try:
        yield TOY_TABLE_NAME
    finally:
        dynamodb_client.delete_table(TableName=TOY_TABLE_NAME)
