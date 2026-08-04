import dataclasses
import decimal


@dataclasses.dataclass
class Merchant:
    merchant_id: str
    name: str
    payable_balance: decimal.Decimal


@dataclasses.dataclass
class Account:
    account_id: str
    current_balance: decimal.Decimal
    available_balance: decimal.Decimal

    def has_sufficient_funds(self, amount: decimal.Decimal) -> bool:
        return self.available_balance >= amount
