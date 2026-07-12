"""Tests for file-backed model routing configuration."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from workpilot.providers.routing import ModelTier, TaskType
from workpilot.providers.routing_config import load_model_routing_config


def test_example_routing_config_is_valid_and_secret_free() -> None:
    path = Path("examples/model_routes.json")
    config = load_model_routing_config(path)

    assert config.schema_version == "1.0"
    assert config.retry_policy.max_attempts == 3
    routes = {route.task_type: route for route in config.routes}
    assert routes[TaskType.EVIDENCE_EXTRACTION].minimum_tier == ModelTier.ECONOMY
    assert routes[TaskType.SEMANTIC_VERIFICATION].minimum_tier == ModelTier.PREMIUM
    serialized = path.read_text(encoding="utf-8").lower()
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_routing_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps({
            "schema_version": "1.0",
            "routes": [{
                "task_type": "analysis",
                "targets": [{
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "tier": "balanced"
                }]
            }],
            "api_key": "must-not-be-accepted"
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="Extra inputs"):
        load_model_routing_config(path)
