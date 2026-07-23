"""Configuration validation for high-availability runtime controls."""

import pytest
from pydantic import ValidationError

from workpilot.config import Settings


def test_lease_timing_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.workpilot_lease_ttl_seconds == 30
    assert settings.workpilot_heartbeat_seconds == 10


def test_lease_timing_loads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("WORKPILOT_LEASE_TTL_SECONDS", "60")
    monkeypatch.setenv("WORKPILOT_HEARTBEAT_SECONDS", "20")

    settings = Settings(_env_file=None)

    assert settings.workpilot_lease_ttl_seconds == 60
    assert settings.workpilot_heartbeat_seconds == 20


def test_heartbeat_cannot_exceed_one_third_of_ttl(monkeypatch) -> None:
    monkeypatch.setenv("WORKPILOT_LEASE_TTL_SECONDS", "30")
    monkeypatch.setenv("WORKPILOT_HEARTBEAT_SECONDS", "11")

    with pytest.raises(ValidationError, match="one third"):
        Settings(_env_file=None)
