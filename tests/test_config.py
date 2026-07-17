"""
Tests for the config loader and validation rules.

These tests verify:
1. ``from_yaml`` correctly loads valid and invalid YAML files.
2. ``from_dict`` parses raw dicts into typed dataclasses.
3. ``validate`` catches all schema violations from SPEC §4.1.
4. Tier insertion order is preserved (determines escalation priority).
5. Invalid ``on_exceed`` values raise ValueError during parsing.

Fixtures are loaded from ``tests/fixtures/tiers.yaml`` (valid) and
``tests/fixtures/tiers-invalid.yaml`` (invalid).
"""

from pathlib import Path
import tempfile

import pytest

from ai_tierforge.config import TierForgeConfigLoader
from ai_tierforge.types import (
    TierForgeConfig,
)

# Path to the YAML fixture files
FIXTURES = Path(__file__).parent / "fixtures"


def test_from_yaml_valid():
    """Loading a valid YAML should return a TierForgeConfig with 3 tiers.

    The fixture tiers.yaml defines architect, workhorse, and utility
    tiers matching the SPEC §13.1 example.
    """
    config = TierForgeConfigLoader.from_yaml(
        FIXTURES / "tiers.yaml"
    )
    assert isinstance(config, TierForgeConfig)
    assert len(config.tiers) == 3
    assert "architect" in config.tiers
    assert "workhorse" in config.tiers
    assert "utility" in config.tiers


def test_from_yaml_unknown_file():
    """Loading a non-existent file should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        TierForgeConfigLoader.from_yaml("nonexistent.yaml")


def test_from_yaml_invalid_yaml():
    """Loading syntactically invalid YAML should raise an exception.

    We write a temp file with broken YAML syntax and verify that
    the loader raises (YAMLError or similar).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(": invalid yaml [")
        path = f.name
    with pytest.raises(Exception):
        TierForgeConfigLoader.from_yaml(path)


def test_validate_valid_config():
    """A valid config should produce no validation errors."""
    config = TierForgeConfigLoader.from_yaml(
        FIXTURES / "tiers.yaml"
    )
    errors = TierForgeConfigLoader.validate(config)
    assert errors == []


def test_validate_empty_tiers():
    """Config with no tiers should produce an error.

    This is a hard error — at least one tier is required.
    """
    config = TierForgeConfigLoader.from_dict({"tiers": {}})
    errors = TierForgeConfigLoader.validate(config)
    assert any("define at least one tier" in e for e in errors)


def test_validate_single_tier():
    """Config with only one tier should produce a warning.

    This is a warning (not an error) — the config is technically valid
    but escalation won't work with a single tier.
    """
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "only": {
                "model": "test", "max_tokens": 1000,
                "use_for": ["code"], "provider": "test",
            }
        }
    })
    errors = TierForgeConfigLoader.validate(config)
    assert any("at least 2 tiers" in e for e in errors)


def test_validate_empty_use_for():
    """A tier with empty use_for should produce an error.

    A tier with no task types is useless — the router can never
    route anything to it.
    """
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "a": {
                "model": "test", "max_tokens": 1000,
                "use_for": [], "provider": "test",
            },
            "b": {
                "model": "test2", "max_tokens": 1000,
                "use_for": ["code"], "provider": "test",
            },
        }
    })
    errors = TierForgeConfigLoader.validate(config)
    assert any("use_for must not be empty" in e for e in errors)


def test_validate_max_tokens_positive():
    """A tier with non-positive max_tokens should produce an error.

    Zero or negative max_tokens makes no sense — the model can't
    produce any output.
    """
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "a": {
                "model": "test", "max_tokens": 0,
                "use_for": ["code"], "provider": "test",
            },
            "b": {
                "model": "test2", "max_tokens": 1000,
                "use_for": ["code"], "provider": "test",
            },
        }
    })
    errors = TierForgeConfigLoader.validate(config)
    assert any("max_tokens must be positive" in e for e in errors)


def test_validate_duplicate_task_type():
    """Duplicate task_type across tiers should produce a warning.

    If two tiers claim the same task_type, the first one in YAML order
    wins.  The warning informs the user about the conflict.
    """
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "a": {
                "model": "test", "max_tokens": 1000,
                "use_for": ["code", "spec"], "provider": "test",
            },
            "b": {
                "model": "test2", "max_tokens": 1000,
                "use_for": ["code"], "provider": "test",
            },
        }
    })
    errors = TierForgeConfigLoader.validate(config)
    assert any("claimed by tiers" in e for e in errors)


def test_validate_threshold_range():
    """Escalation threshold must be between 0.0 and 1.0.

    A threshold of 1.5 (150%) is invalid — it's not a percentage.
    """
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "a": {
                "model": "test", "max_tokens": 1000,
                "use_for": ["code"], "provider": "test",
            },
            "b": {
                "model": "test2", "max_tokens": 1000,
                "use_for": ["spec"], "provider": "test",
            },
        },
        "escalation": {"default_threshold": 1.5},
    })
    errors = TierForgeConfigLoader.validate(config)
    assert any("between 0.0 and 1.0" in e for e in errors)


def test_from_dict_preserves_tier_order():
    """Tier order from dict should match insertion order.

    Python 3.7+ dicts preserve insertion order, and PyYAML preserves
    YAML mapping order.  This order determines escalation priority.
    """
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "alpha": {
                "model": "a", "max_tokens": 1000,
                "use_for": ["x"], "provider": "test",
            },
            "beta": {
                "model": "b", "max_tokens": 1000,
                "use_for": ["y"], "provider": "test",
            },
        }
    })
    tiers = list(config.tiers.keys())
    assert tiers == ["alpha", "beta"]


def test_from_dict_invalid_on_exceed():
    """Invalid on_exceed should raise ValueError during parsing.

    The OnExceedAction enum constructor raises ValueError for invalid
    strings.  This is caught at parse time (from_dict), not at
    validate time, so the error is immediate and clear.
    """
    with pytest.raises(ValueError, match="not a valid OnExceedAction"):
        TierForgeConfigLoader.from_dict({
            "tiers": {
                "a": {
                    "model": "test", "max_tokens": 1000,
                    "use_for": ["code"], "provider": "test",
                },
            },
            "budgets": {
                "per_task": {"limit": 0.10, "on_exceed": "invalid_option"},
            },
        })
