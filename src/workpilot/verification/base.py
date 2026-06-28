"""Verifier abstract base — Phase 3 will add concrete implementations."""

from abc import ABC, abstractmethod
from typing import Any


class VerifyResult:
    """Single verification check result."""

    def __init__(
        self,
        check_id: str,
        status: str,
        severity: str = "error",
        artifact: str = "",
        location: str = "",
        message: str = "",
    ) -> None:
        self.check_id = check_id
        self.status = status
        self.severity = severity
        self.artifact = artifact
        self.location = location
        self.message = message

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "severity": self.severity,
            "artifact": self.artifact,
            "location": self.location,
            "message": self.message,
        }


class Verifier(ABC):
    """Abstract base for all verifiers."""

    @abstractmethod
    def verify(self, **kwargs: Any) -> list[VerifyResult]:
        """Run verification checks. Returns list of results."""
        ...
