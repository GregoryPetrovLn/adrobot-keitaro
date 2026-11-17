"""Application-level exceptions and their HTTP mapping."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


# A rejection Keitaro attributes to the request is the caller's to fix, so it is passed
# through as a client error. Anything else (5xx, timeouts, unreachable) is a gateway
# problem and stays a 502.
_PASS_THROUGH_STATUSES = {400: 422, 404: 404, 409: 409, 422: 422}


class KeitaroError(AppError):
    """Keitaro rejected the call or could not be reached."""

    status_code = 502
    code = "keitaro_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: Any = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.keitaro_status = status_code
        self.path = path
        if status_code in _PASS_THROUGH_STATUSES:
            self.status_code = _PASS_THROUGH_STATUSES[status_code]
            self.code = "keitaro_rejected"


class KeitaroNotConfiguredError(AppError):
    status_code = 503
    code = "keitaro_not_configured"
