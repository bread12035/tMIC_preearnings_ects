"""Tests for common.config."""

from __future__ import annotations

import pytest

from common.config import _int, get_settings


def test_settings_loads_from_env(env_vars) -> None:
    s = get_settings()
    assert s.app_mode == "pre_earnings"
    assert s.gcp_pubsub_max_inflight == 5
    assert s.anthropic_max_retries == 3


def test_settings_safe_dict_redacts_api_key(env_vars) -> None:
    s = get_settings()
    safe = s.safe_dict()
    assert safe["anthropic_api_key"] == "sk-ant-***REDACTED***"
    assert safe["app_mode"] == "pre_earnings"


def test_missing_required_var_raises(env_vars, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_settings()


def test_invalid_app_mode_raises(env_vars, monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "bogus")
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="APP_MODE"):
        get_settings()


def test_int_helper_with_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("BAD_INT", "not-a-number")
    with pytest.raises(RuntimeError, match="BAD_INT"):
        _int("BAD_INT", 0)
