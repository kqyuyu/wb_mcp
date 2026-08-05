"""Typed exceptions for Wildberries API interactions."""

from __future__ import annotations

from typing import Any


class WBError(Exception):
    """Base class for all Wildberries-related errors raised by wb-mcp."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        operation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.operation_id = operation_id
        self.payload = payload or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "status_code": self.status_code,
            "operation_id": self.operation_id,
            "payload": self.payload,
        }


class WBAuthError(WBError):
    """Missing or invalid credentials (HTTP 401)."""


class WBForbiddenError(WBError):
    """Insufficient permissions or token category mismatch (HTTP 403)."""


class WBValidationError(WBError):
    """Request payload failed Wildberries-side validation (HTTP 400)."""


class WBNotFoundError(WBError):
    """Requested resource does not exist (HTTP 404)."""


class WBConflictError(WBError):
    """Request conflicts with current state (HTTP 409)."""


class WBRateLimitError(WBError):
    """Rate limit exceeded (HTTP 429)."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class WBServerError(WBError):
    """Wildberries backend failure (HTTP 5xx)."""


class WBClientValidationError(WBError):
    """Request failed local jsonschema validation before being sent."""