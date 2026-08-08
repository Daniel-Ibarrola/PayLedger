"""Re-exports fixtures split by domain under fixtures/ so pytest can find them.

Pytest only auto-collects fixtures from conftest.py (or the test module itself),
not from arbitrary modules — importing them here is what makes `insert_merchants`
and `insert_test_account` usable via `@pytest.mark.usefixtures(...)`.
"""

from tests.integration.fixtures.accounts import test_account
from tests.integration.fixtures.merchants import insert_merchants

__all__ = ["insert_merchants", "test_account"]
