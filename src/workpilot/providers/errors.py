"""Stable Provider error taxonomy independent of vendor SDK classes."""

from enum import Enum
from typing import Any


class ProviderErrorType(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SERVER = "server"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


RETRYABLE_ERRORS = {
    ProviderErrorType.RATE_LIMIT,
    ProviderErrorType.TIMEOUT,
    ProviderErrorType.NETWORK,
    ProviderErrorType.SERVER,
}


class ProviderCallError(RuntimeError):
    """Sanitized Provider failure exposed to Runtime and Trace."""

    def __init__(self, provider: str, error_type: ProviderErrorType) -> None:
        self.provider = provider
        self.error_type = error_type
        self.retryable = error_type in RETRYABLE_ERRORS
        super().__init__(f"{provider} provider call failed: {error_type.value}")


def classify_provider_exception(exc: Exception) -> ProviderErrorType:
    """Classify common SDK/HTTP failures without importing vendor exceptions."""
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        response = getattr(exc, "response", None)
        candidate = getattr(response, "status_code", None)
        status_code = candidate if isinstance(candidate, int) else None

    if status_code in {401, 403}:
        return ProviderErrorType.AUTHENTICATION
    if status_code == 429:
        return ProviderErrorType.RATE_LIMIT
    if status_code in {408, 504}:
        return ProviderErrorType.TIMEOUT
    if status_code is not None and status_code >= 500:
        return ProviderErrorType.SERVER

    class_name = type(exc).__name__.lower()
    if "timeout" in class_name:
        return ProviderErrorType.TIMEOUT
    if any(signal in class_name for signal in ("connection", "network", "transport")):
        return ProviderErrorType.NETWORK
    return ProviderErrorType.UNKNOWN
