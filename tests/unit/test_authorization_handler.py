import pytest

from authorization_service import handler
from shared import domain, errors


class TestTerminalStatusError:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            pytest.param(
                domain.AuthorizationStatus.CAPTURED,
                errors.AuthorizationAlreadyCaptured,
                id="captured",
            ),
            pytest.param(
                domain.AuthorizationStatus.VOIDED, errors.AuthorizationAlreadyVoided, id="voided"
            ),
            pytest.param(
                domain.AuthorizationStatus.EXPIRED, errors.AuthorizationExpired, id="expired"
            ),
            pytest.param(
                domain.AuthorizationStatus.REVERSED, errors.AuthorizationReversed, id="reversed"
            ),
        ],
    )
    def test_maps_each_terminal_status_to_its_conflict(
        self, status: domain.AuthorizationStatus, expected: type[errors.ApiError]
    ) -> None:
        error = handler._terminal_status_error("authorization_1", status)

        assert isinstance(error, expected)
        assert error.status_code == 409

    def test_raises_rather_than_inventing_a_conflict_for_pending(self) -> None:
        # A guard failure reported for a PENDING authorization is a bug, not a 409
        # the caller can act on. This used to be a bare dict lookup, so it raised
        # `KeyError` and the log said nothing about what had gone wrong.
        with pytest.raises(RuntimeError, match="not a terminal state"):
            handler._terminal_status_error("authorization_1", domain.AuthorizationStatus.PENDING)
