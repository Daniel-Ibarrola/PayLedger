from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Powertools is not yet a runtime dependency of the shared layer (see
    # src/layers/shared/requirements.txt), so this import must stay
    # type-checking-only — importing the package for real pulls in Logger,
    # Metrics, and Tracer at module load time.
    from aws_lambda_powertools.utilities.typing import LambdaContext


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    raise NotImplementedError
