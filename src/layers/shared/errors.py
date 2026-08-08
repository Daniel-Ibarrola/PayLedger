"""Errors that map onto an HTTP status.

Handlers raise these instead of assembling error responses inline, so the
mapping from failure to status code lives in one place.
"""


class ApiError(Exception):
    """Base for failures a client caused and should be told about."""

    status_code = 500
    code = "InternalServerError"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BadRequest(ApiError):
    status_code = 400
    code = "BadRequest"


class NotFound(ApiError):
    status_code = 404
    code = "NotFound"


class Conflict(ApiError):
    status_code = 409
    code = "Conflict"


class UnknownMerchant(BadRequest):
    code = "UnknownMerchant"


class MerchantAlreadyExists(BadRequest):
    code = "MerchantAlreadyExists"


class InsufficientFunds(Conflict):
    code = "InsufficientFunds"


class MissingIdempotencyKey(BadRequest):
    code = "MissingIdempotencyKey"


class IdempotencyKeyReuse(ApiError):
    """Same `Idempotency-Key`, different request body (design doc: 04-api.md,
    "Idempotency outcomes") — the one genuine 422 in this API.
    """

    status_code = 422
    code = "IdempotencyKeyReuse"
