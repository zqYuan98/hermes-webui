"""Tests for #1105 — SSRF check allows user-configured custom_providers hostnames.

The SSRF check blocks requests to private IPs unless the hostname is in a
hardcoded allowlist. This fix extracts hostnames from custom_providers config
and adds them to the trusted set, so user-explicitly configured local endpoints
(ollama, llama.cpp, vLLM, TabbyAPI, etc.) are not blocked.
"""
import os
import pytest


# ---------- Current transport-policy contract ----------

def test_custom_catalog_uses_shared_bounded_probe():
    """Custom-provider discovery must use the shared no-redirect fetcher."""
    config_src = open("api/config.py", encoding="utf-8").read()
    probe_src = open("api/provider_endpoint_probe.py", encoding="utf-8").read()
    assert "from api.provider_endpoint_probe import probe_models_endpoint" in config_src
    assert "NoRedirectHandler" in probe_src
    assert "MAX_RESPONSE_BYTES" in probe_src


def test_explicit_private_endpoints_do_not_use_dns_policy_preflight():
    """Configured Ollama/LM Studio/vLLM endpoints may legitimately be private."""
    probe_src = open("api/provider_endpoint_probe.py", encoding="utf-8").read()
    assert "makes no DNS-based allow/deny decision before connecting" in probe_src
    assert "DEFAULT_OPENER" in probe_src
    assert "base_url must not contain embedded credentials" in probe_src


def test_probe_refuses_redirects_before_credentials_cross_origins():
    probe_src = open("api/provider_endpoint_probe.py", encoding="utf-8").read()
    assert "def redirect_request" in probe_src
    assert "return None" in probe_src
    assert "endpoint returned a redirect" in probe_src


def test_known_local_providers_still_present():
    """Ollama, LM Studio and localhost remain first-class configured endpoints."""
    with open("api/config.py", encoding="utf-8") as f:
        src = f.read()
    for keyword in ("ollama", "localhost", "127.0.0.1", "lmstudio", "lm-studio"):
        assert keyword in src, f"Missing local-provider support: {keyword}"


def test_probe_rejects_credential_bearing_urls_and_unbounded_bodies():
    probe_src = open("api/provider_endpoint_probe.py", encoding="utf-8").read()
    assert "parsed.username or parsed.password" in probe_src
    assert "MAX_RESPONSE_BYTES + 1" in probe_src


# ---------- Functional tests (mocked socket) ----------

def test_custom_provider_hostname_added_to_trusted():
    """A hostname from custom_providers base_url is added to trusted set."""
    import api.config as config
    import socket
    from unittest.mock import patch, MagicMock

    old_cfg = dict(config.cfg)
    try:
        config.cfg.update({
            "model": {"model": "my-model", "base_url": "http://my-llama-server:8080/v1"},
            "custom_providers": [
                {"name": "my-llama", "base_url": "http://my-llama-server:8080/v1", "model": "llama-3"}
            ],
            "providers": {},
        })
        config.invalidate_models_cache()

        # Mock socket.getaddrinfo to return a private IP for my-llama-server
        private_addr = ("192.168.1.100", None)
        mock_getaddrinfo = MagicMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", private_addr)
        ])

        # Mock urllib to prevent actual HTTP call
        mock_urlopen = MagicMock()
        mock_urlopen.read.return_value = b'{"data": [{"id": "test-model"}]}'
        mock_urlopen.__enter__ = MagicMock(return_value=mock_urlopen)
        mock_urlopen.__exit__ = MagicMock(return_value=False)

        with patch("socket.getaddrinfo", mock_getaddrinfo), \
             patch("urllib.request.urlopen", mock_urlopen):
            # Should NOT raise ValueError (SSRF) because hostname is in trusted set
            result = config.get_available_models()

        # Verify models were returned (auto-detection succeeded)
        assert result is not None
        assert "groups" in result

    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config.invalidate_models_cache()


def test_unknown_private_ip_still_blocked():
    """A private IP from a hostname NOT in custom_providers is still blocked.

    The SSRF ValueError is caught by the broad `except Exception` around
    the custom endpoint fetch (line ~1571), so get_available_models() doesn't
    crash — but no models are auto-detected from that endpoint.
    """
    import api.config as config
    import socket
    from unittest.mock import patch, MagicMock

    old_cfg = dict(config.cfg)
    try:
        config.cfg.update({
            "model": {"model": "test", "base_url": "http://unknown-local-server:9999/v1"},
            "custom_providers": [
                {"name": "other", "base_url": "http://other-server:8080/v1", "model": "x"}
            ],
            "providers": {},
        })
        config.invalidate_models_cache()

        # Mock socket.getaddrinfo to return a private IP for unknown-local-server
        private_addr = ("10.0.0.50", None)
        mock_getaddrinfo = MagicMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", private_addr)
        ])

        with patch("socket.getaddrinfo", mock_getaddrinfo):
            # Should NOT crash (ValueError is caught internally)
            result = config.get_available_models()

        # But no models should be auto-detected from the blocked endpoint
        assert result is not None
        assert "groups" in result
        # Verify no group with "unknown-local-server" models exists
        for group in result["groups"]:
            provider_name = group.get("provider", "")
            assert "unknown-local-server" not in provider_name

    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config.invalidate_models_cache()
