import pytest


@pytest.fixture
def insert_merchants(ledger_table, dynamodb) -> None:
    """Inserts test merchants to the ledger table."""
    merchants = [("MERCHANT#merchant_001", "Jose Cuervo"), ("MERCHANT#merchant_002", "Aldo Conti")]
    with ledger_table.batch_writer() as batch:
        for merchant in merchants:
            batch.put_item(Item={"account_id": merchant[0], "name": merchant[1]})
