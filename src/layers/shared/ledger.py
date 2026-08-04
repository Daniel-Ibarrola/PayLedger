from shared import domain, dynamo

LEDGER_TABLE_NAME = "payledger-ledger-table"
LEDGER_PK_NAME = "PK"
LEDGER_SORT_KEY_NAME = "SK"


class Ledger:
    def __init__(self, table_name=LEDGER_TABLE_NAME):
        self._table = dynamo.get_table(table_name)

    def get_merchant(self, merchant_id: str) -> domain.Merchant | None:
        response = self._table.get_item(
            Key={LEDGER_PK_NAME: f"MERCHANT#{merchant_id}", LEDGER_SORT_KEY_NAME: "META"}
        )
        item = response.get("Item")
        if item is None:
            return None

        return domain.Merchant(
            merchant_id=item["merchant_id"],
            name=item["name"],
            payable_balance=item["payable_balance"],
        )

    def get_account(self, account_id: str) -> domain.Account | None:
        response = self._table.get_item(
            Key={LEDGER_PK_NAME: f"ACCT#{account_id}", LEDGER_SORT_KEY_NAME: "META"}
        )
        item = response.get("Item")
        if item is None:
            return None

        return domain.Account(
            account_id=item["account_id"],
            current_balance=item["current_balance"],
            available_balance=item["available_balance"],
        )
