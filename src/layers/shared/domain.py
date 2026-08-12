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


class InsufficientFunds(Exception):
    """Raised when a hold would take an account's available balance negative."""

    pass


class AccountNotFound(Exception):
    """Raised when an operation targets an account that doesn't exist."""

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
    current_balance: decimal.Decimal = decimal.Decimal(0)
    available_balance: decimal.Decimal = decimal.Decimal(0)

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


class AuthorizationNotPending(Exception):
    """Raised when an operation needs a `PENDING` authorization and the write's
    guard found a terminal one.

    Carries the status the guard actually saw, because that is what decides
    which `409` the caller gets.
    """

    def __init__(self, status: AuthorizationStatus) -> None:
        super().__init__(f"authorization is {status.value}")
        self.status = status


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
