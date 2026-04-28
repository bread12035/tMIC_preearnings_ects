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


def test_settings_defaults_for_new_fields(env_vars) -> None:
    """New web-search / prompt fields fall back to sensible defaults when the
    env doesn't define them."""
    s = get_settings()
    assert s.stocktitan_news_url == "https://www.stocktitan.net/news"
    assert s.motley_fool_url == "https://www.fool.com/earnings-call-transcripts"
    assert s.ects_web_search_flag is False
    assert s.prompt_pre_earnings_system_path == "prompts/pre_earnings_system.md.tmpl"
    assert (
        s.prompt_ects_web_search_template_path
        == "prompts/ects_web_search_template.md.tmpl"
    )


def test_settings_ects_web_search_flag_parses_true(env_vars, monkeypatch) -> None:
    monkeypatch.setenv("ECTS_WEB_SEARCH_FLAG", "true")
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    assert get_settings().ects_web_search_flag is True


def test_bool_helper_with_invalid_value(monkeypatch) -> None:
    from common.config import _bool

    monkeypatch.setenv("BAD_BOOL", "maybe")
    with pytest.raises(RuntimeError, match="BAD_BOOL"):
        _bool("BAD_BOOL", False)


def test_event_calendar_settings_loaded(env_vars) -> None:
    s = get_settings()
    assert s.event_calendar_watchlist_bucket == "test-watchlist-bucket"
    assert s.event_calendar_watchlist_blob == "configs/watchlist.json"
    assert s.event_calendar_registry_bucket == "test-registry-bucket"
    assert s.event_calendar_registry_prefix == "configs/event_calendar"
    assert s.event_calendar_lookahead_days == 14
    assert s.event_calendar_pre_earnings_offset_minutes == -30
    assert s.event_calendar_ects_offset_minutes == 30
    assert s.event_calendar_dispatch_window_minutes == 10


def test_calendar_sync_app_mode_does_not_require_subscription(
    env_vars, monkeypatch
) -> None:
    monkeypatch.setenv("APP_MODE", "calendar_sync")
    monkeypatch.delenv("GCP_PUBSUB_SUBSCRIPTION", raising=False)
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    s = get_settings()
    assert s.app_mode == "calendar_sync"
    assert s.gcp_pubsub_subscription is None


def test_task_dispatcher_app_mode_does_not_require_subscription(
    env_vars, monkeypatch
) -> None:
    monkeypatch.setenv("APP_MODE", "task_dispatcher")
    monkeypatch.delenv("GCP_PUBSUB_SUBSCRIPTION", raising=False)
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    s = get_settings()
    assert s.app_mode == "task_dispatcher"


def test_invalid_app_mode_lists_all_valid_modes(env_vars, monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "bogus")
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="task_dispatcher"):
        get_settings()
