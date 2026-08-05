import dataclasses
import datetime
import decimal
import enum


class MerchantAlreadyExists(Exception):
    """Raised when a merchant with the same ID already exists."""

    pass


class AccountAlreadyExists(Exception):
    """Raised when an account with the same ID already exists."""

    pass


@dataclasses.dataclass
class Merchant:
    """A merchant that can receive authorizations, with its accrued payable balance."""

    merchant_id: str
    name: str
    payable_balance: decimal.Decimal


@dataclasses.dataclass
class Account:
    """A cardholder account, tracking both its posted and hold-adjusted balances."""

    account_id: str
    current_balance: decimal.Decimal
    available_balance: decimal.Decimal

    def has_sufficient_funds(self, amount: decimal.Decimal) -> bool:
        """Whether the available balance covers `amount`."""
        return self.available_balance >= amount


class AuthorizationStatus(enum.Enum):
    """Lifecycle states of an `Authorization`."""

    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    VOIDED = "VOIDED"
    EXPIRED = "EXPIRED"
    REVERSED = "REVERSED"


@dataclasses.dataclass
class Authorization:
    """A pending or resolved hold placed against an account for a merchant."""

    authorization_id: str
    merchant_id: str
    amount: decimal.Decimal
    created_at: datetime.datetime
    updated_at: datetime.datetime
    status: AuthorizationStatus
    expires_at: datetime.date
