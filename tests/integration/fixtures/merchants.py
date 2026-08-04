import datetime

import pytest
from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table


def create_merchant_record(merchant_id: str, merchant_name: str) -> dict:
    return {
        "PK": f"MERCHANT#{merchant_id}",
        "SK": "META",
        "merchant_id": merchant_id,
        "name": merchant_name,
        "payable_balance": 0,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


@pytest.fixture
def insert_merchants(ledger_table: Table, dynamodb: DynamoDBServiceResource) -> None:
    """Inserts test merchants to the ledger table."""
    merchants = [("merchant_001", "Jose Cuervo"), ("merchant_002", "Aldo Conti")]
    with ledger_table.batch_writer() as batch:
        for merchant in merchants:
            batch.put_item(Item=create_merchant_record(merchant[0], merchant[1]))
