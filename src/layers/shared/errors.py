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
