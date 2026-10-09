"""Domain exceptions mapped to the ErrorBody shape by the API layer."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class: every non-2xx body is {"error": {"code", "message", "details"}}."""

    default_status = 400

    def __init__(self, code: str, message: str, status: int | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else self.default_status
        self.details: dict[str, Any] = details or {}


class NotFound(DomainError):
    default_status = 404


class ValidationFailed(DomainError):
    default_status = 422


class Conflict(DomainError):
    default_status = 409


class DependencyUnavailable(DomainError):
    default_status = 503


class CorruptTemplate(DomainError):
    """A stored template row no longer parses; indicates data corruption, not a client error."""

    default_status = 500


class CorruptIdentity(DomainError):
    """A stored identity row no longer matches its resolved_hash; indicates data corruption."""

    default_status = 500
