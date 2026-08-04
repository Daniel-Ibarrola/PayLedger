import decimal

from shared import domain


class TestAccount:
    def test_has_sufficient_funds(self):
        account = domain.Account(
            account_id="test-account",
            current_balance=decimal.Decimal(100),
            available_balance=decimal.Decimal(100),
        )
        assert account.has_sufficient_funds(decimal.Decimal(50))

    def test_has_insufficient_funds(self):
        account = domain.Account(
            account_id="test-account",
            current_balance=decimal.Decimal(100),
            available_balance=decimal.Decimal(100),
        )
        assert not account.has_sufficient_funds(decimal.Decimal(150))
