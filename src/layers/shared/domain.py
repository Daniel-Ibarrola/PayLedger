import dataclasses
import datetime
import decimal
import enum


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


class AuthorizationStatus(enum.Enum):
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    VOIDED = "VOIDED"
    EXPIRED = "EXPIRED"
    REVERSED = "REVERSED"


@dataclasses.dataclass
class Authorization:
    authorization_id: str
    merchant_id: str
    amount: decimal.Decimal
    created_at: datetime.datetime
    updated_at: datetime.datetime
    status: AuthorizationStatus
    expires_at: datetime.date
