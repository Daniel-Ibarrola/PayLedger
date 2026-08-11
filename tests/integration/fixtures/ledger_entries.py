from typing import Any

from boto3.dynamodb.conditions import Key
from mypy_boto3_dynamodb.service_resource import Table


def get_ledger_entries(party_id: str, ledger_table: Table) -> list[dict[str, Any]]:
    result = ledger_table.query(
        KeyConditionExpression=Key("PK").eq(party_id) & Key("SK").begins_with("TXN#")
    )
    return result["Items"]
