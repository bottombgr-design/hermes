"""Tests for attach_provider_enabled_flags (Provider Manager activation).

The desktop Provider Manager needs every provider row annotated with an
`enabled` flag so it can show + toggle activation. Built-in providers are
disabled via `model.disabled_providers`; custom providers via an
`enabled: false` flag on the matching `custom_providers` entry. Disabled
rows must NOT be dropped (the manager re-enables them).
"""

import pytest

from hermes_cli.inventory import attach_provider_enabled_flags


def _payload(slugs):
    return {"providers": [{"slug": s, "name": s} for s in slugs]}


def test_builtin_enabled_by_default():
    payload = _payload(["openai", "anthropic"])
    out = attach_provider_enabled_flags(payload, {})
    assert [p["enabled"] for p in out["providers"]] == [True, True]


def test_builtin_disabled_via_model_disabled_providers():
    cfg = {"model": {"disabled_providers": ["openai"]}}
    payload = _payload(["openai", "anthropic"])
    out = attach_provider_enabled_flags(payload, cfg)
    by_slug = {p["slug"]: p["enabled"] for p in out["providers"]}
    assert by_slug["openai"] is False
    assert by_slug["anthropic"] is True


def test_custom_provider_enabled_flag_false():
    cfg = {
        "custom_providers": [
            {"name": "My Lab", "base_url": "https://lab/v1", "enabled": False}
        ]
    }
    payload = _payload(["custom:my-lab", "openai"])
    out = attach_provider_enabled_flags(payload, cfg)
    by_slug = {p["slug"]: p["enabled"] for p in out["providers"]}
    assert by_slug["custom:my-lab"] is False
    assert by_slug["openai"] is True


def test_custom_provider_enabled_flag_absent_defaults_true():
    cfg = {
        "custom_providers": [
            {"name": "My Lab", "base_url": "https://lab/v1"}
        ]
    }
    payload = _payload(["custom:my-lab"])
    out = attach_provider_enabled_flags(payload, cfg)
    assert out["providers"][0]["enabled"] is True


def test_custom_name_normalization_spaces_and_case():
    cfg = {
        "custom_providers": [
            {"name": "My Cool Provider", "base_url": "x", "enabled": False}
        ]
    }
    payload = _payload(["custom:my-cool-provider"])
    out = attach_provider_enabled_flags(payload, cfg)
    assert out["providers"][0]["enabled"] is False


def test_disabled_rows_are_not_dropped():
    cfg = {"model": {"disabled_providers": ["openai"]}}
    payload = _payload(["openai", "anthropic"])
    out = attach_provider_enabled_flags(payload, cfg)
    # Both rows remain; only the flag changed.
    assert [p["slug"] for p in out["providers"]] == ["openai", "anthropic"]
    assert out["providers"][0]["enabled"] is False


def test_missing_providers_key_is_safe():
    out = attach_provider_enabled_flags({}, {"model": {"disabled_providers": ["openai"]}})
    assert out == {}
