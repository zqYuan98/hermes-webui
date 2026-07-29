import json
from types import SimpleNamespace
from urllib.parse import urlencode

import api.profiles
import api.routes as routes


def _memory_payload(tmp_path, monkeypatch, *, config_text, env_text="", healthy=True):
    home = tmp_path / "hermes"
    (home / "memories").mkdir(parents=True)
    (home / "config.yaml").write_text(config_text, encoding="utf-8")
    (home / ".env").write_text(env_text, encoding="utf-8")

    monkeypatch.setattr(api.profiles, "get_active_hermes_home", lambda: home)
    monkeypatch.setattr(routes, "_memory_project_context_workspace", lambda _parsed: tmp_path)
    monkeypatch.setattr(routes, "_external_notes_sources_enabled", lambda: False)
    monkeypatch.setattr(routes, "_probe_hy_memory_health", lambda _url: healthy)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)
    return routes._handle_memory_read(object(), SimpleNamespace(query=""))


def test_memory_response_exposes_active_hy_memory_without_secrets(tmp_path, monkeypatch):
    secret = "sk-test-secret-must-never-leak"
    payload = _memory_payload(
        tmp_path,
        monkeypatch,
        config_text="memory:\n  provider: hy-memory\n",
        env_text=(
            "HY_MEMORY_MODE=pro\n"
            "HY_MEMORY_SERVER_URL=http://127.0.0.1:19527\n"
            "MEMORY_LLM_MODEL=deepseek-chat\n"
            "MEMORY_LLM_API_KEY=" + secret + "\n"
            "MEMORY_EMBEDDER_MODEL=BAAI/bge-m3\n"
            "MEMORY_EMBEDDER_API_KEY=" + secret + "\n"
            "MEMORY_EMBEDDING_DIMS=1024\n"
            "MEMORY_VECTOR_STORE=chroma\n"
        ),
    )

    status = payload["external_memory"]
    assert status == {
        "provider": "hy-memory",
        "display_name": "Hy-Memory",
        "enabled": True,
        "health": "online",
        "mode": "pro",
        "llm_model": "deepseek-chat",
        "embedder_model": "BAAI/bge-m3",
        "embedding_dims": 1024,
        "vector_store": "chroma",
    }
    assert secret not in json.dumps(payload)


def test_memory_response_marks_hy_memory_unavailable_when_sidecar_is_down(tmp_path, monkeypatch):
    payload = _memory_payload(
        tmp_path,
        monkeypatch,
        config_text="memory:\n  provider: hy-memory\n",
        env_text="HY_MEMORY_MODE=pro\nHY_MEMORY_SERVER_URL=http://127.0.0.1:19527\n",
        healthy=False,
    )

    assert payload["external_memory"]["enabled"] is True
    assert payload["external_memory"]["health"] == "offline"


def test_memory_response_reports_builtin_only_when_no_external_provider(tmp_path, monkeypatch):
    payload = _memory_payload(
        tmp_path,
        monkeypatch,
        config_text="memory:\n  nudge_interval: 10\n",
    )

    assert payload["external_memory"] == {
        "provider": "",
        "display_name": "Built-in only",
        "enabled": False,
        "health": "disabled",
        "mode": "",
        "llm_model": "",
        "embedder_model": "",
        "embedding_dims": None,
        "vector_store": "",
    }


def test_memory_panel_declares_external_provider_as_read_only_visual_section():
    panels = (routes.Path(__file__).parent.parent / "static" / "panels.js").read_text(encoding="utf-8")

    assert "key: 'external_memory'" in panels
    assert "externalMemoryBadge" in panels
    assert "function _renderExternalMemoryStatus()" in panels
    assert "external_memory" in panels


def _external_memory_payload(tmp_path, monkeypatch, *, query=None, provider="hy-memory", env_text="", response=None, error=None):
    home = tmp_path / "hermes"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(f"memory:\n  provider: {provider}\n", encoding="utf-8")
    (home / ".env").write_text(
        "HY_MEMORY_SERVER_URL=http://127.0.0.1:19527\n"
        "HY_MEMORY_USER_ID=test-user\n"
        "HY_MEMORY_AGENT_ID=test-agent\n"
        + env_text,
        encoding="utf-8",
    )
    calls = []

    def fake_post(server_url, path, payload):
        calls.append((server_url, path, payload))
        if error:
            raise error
        return response or {}

    monkeypatch.setattr(api.profiles, "get_active_hermes_home", lambda: home)
    monkeypatch.setattr(routes, "_hy_memory_post", fake_post, raising=False)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {"_status": status, **payload},
    )
    parsed = SimpleNamespace(query=urlencode(query or {}, doseq=True))
    return routes._handle_external_memory_read(object(), parsed), calls


def test_external_memory_list_normalizes_safe_fields_and_drops_internal_data(tmp_path, monkeypatch):
    secret = "sk-super-secret"
    response = {
        "vdb": {
            "memories": [{
                "memory_id": "mem-1",
                "content": "A durable fact",
                "layer": "l2_fact",
                "status": "active",
                "memory_at": 123,
                "gmt_created": 124,
                "user_id": "private-user",
                "session_id": "private-session",
                "custom": {"token": secret},
                "tags": ["one", "two"],
            }],
            "total": 1,
        },
        "request_id": secret,
        "elapsed_ms": 2.5,
    }

    payload, calls = _external_memory_payload(tmp_path, monkeypatch, response=response)

    assert payload == {
        "_status": 200,
        "provider": "hy-memory",
        "query": "",
        "memories": [{
            "memory_id": "mem-1",
            "content": "A durable fact",
            "layer": "l2_fact",
            "status": "active",
            "tags": ["one", "two"],
            "memory_at": 123,
            "gmt_created": 124,
        }],
        "total": 1,
        "elapsed_ms": 2.5,
    }
    assert calls == [("http://127.0.0.1:19527", "/api/v1/list", {
        "user_id": "test-user", "agent_id": "test-agent", "limit": 20,
    })]
    assert secret not in json.dumps(payload)
    assert "user_id" not in json.dumps(payload)
    assert "session_id" not in json.dumps(payload)


def test_external_memory_search_merges_layers_deduplicates_and_keeps_score(tmp_path, monkeypatch):
    response = {
        "memories": {
            "profile": [{"memory_id": "same", "content": "profile", "score": 0.91, "layer": "profile"}],
            "proactive": [{"memory_id": "pro", "content": "proactive", "score": 0.8, "layer": "l2_fact"}],
            "normal": [
                {"memory_id": "same", "content": "duplicate", "score": 0.7, "layer": "l2_fact"},
                {"memory_id": "normal", "content": "normal", "score": 0.6, "layer": "l1"},
            ],
        },
        "elapsed_ms": 12.25,
    }

    payload, calls = _external_memory_payload(
        tmp_path, monkeypatch, query={"q": "what matters", "limit": "5"}, response=response,
    )

    assert [item["memory_id"] for item in payload["memories"]] == ["same", "pro", "normal"]
    assert payload["memories"][0]["score"] == 0.91
    assert payload["query"] == "what matters"
    assert calls[0][1:] == ("/api/v1/search", {
        "query": "what matters",
        "user_ids": ["test-user"],
        "agent_ids": ["test-agent"],
        "limit": 5,
    })


def test_external_memory_search_includes_active_builtin_agent(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    pointer = home / "hy-memory-builtin-active.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('{"agent_id":"test-agent-builtin-012345abcdef"}', encoding="utf-8")
    payload, calls = _external_memory_payload(
        tmp_path,
        monkeypatch,
        query={"q": "what matters", "limit": "5"},
        env_text=f"HY_MEMORY_BUILTIN_POINTER={pointer}\n",
        response={"memories": {"normal": []}},
    )
    assert payload["_status"] == 200
    assert calls[0][2]["agent_ids"] == ["test-agent", "test-agent-builtin-012345abcdef"]


def test_external_memory_list_merges_primary_and_builtin_namespaces(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    pointer = home / "hy-memory-builtin-active.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('{"agent_id":"test-agent-builtin-012345abcdef"}', encoding="utf-8")
    responses = [
        {"vdb": {"memories": [{"memory_id": "dialog", "content": "dialog fact"}], "total": 1}, "elapsed_ms": 2},
        {"vdb": {"memories": [{"memory_id": "builtin", "content": "builtin fact"}], "total": 1}, "elapsed_ms": 3},
    ]
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("memory:\n  provider: hy-memory\n", encoding="utf-8")
    (home / ".env").write_text(
        "HY_MEMORY_SERVER_URL=http://127.0.0.1:19527\n"
        "HY_MEMORY_USER_ID=test-user\n"
        "HY_MEMORY_AGENT_ID=test-agent\n"
        f"HY_MEMORY_BUILTIN_POINTER={pointer}\n",
        encoding="utf-8",
    )
    calls = []

    def fake_post(server_url, path, request_payload):
        calls.append((server_url, path, request_payload))
        return responses[len(calls) - 1]

    monkeypatch.setattr(api.profiles, "get_active_hermes_home", lambda: home)
    monkeypatch.setattr(routes, "_hy_memory_post", fake_post)
    monkeypatch.setattr(routes, "j", lambda _h, data, status=200, **_k: {"_status": status, **data})
    payload = routes._handle_external_memory_read(object(), SimpleNamespace(query=""))

    assert [m["memory_id"] for m in payload["memories"]] == ["dialog", "builtin"]
    assert payload["total"] == 2
    assert [call[2]["agent_id"] for call in calls] == ["test-agent", "test-agent-builtin-012345abcdef"]


def test_external_memory_rejects_inactive_provider_without_sidecar_call(tmp_path, monkeypatch):
    payload, calls = _external_memory_payload(tmp_path, monkeypatch, provider="other-memory")
    assert payload["_status"] == 409
    assert payload["error"] == "External memory is not available."
    assert calls == []


def test_external_memory_rejects_non_loopback_server_without_request(tmp_path, monkeypatch):
    payload, calls = _external_memory_payload(
        tmp_path,
        monkeypatch,
        env_text="HY_MEMORY_SERVER_URL=http://example.com:19527\n",
    )
    assert payload["_status"] == 503
    assert payload["error"] == "External memory is temporarily unavailable."
    assert calls == []


def test_external_memory_clamps_limit_and_rejects_overlong_query(tmp_path, monkeypatch):
    payload, calls = _external_memory_payload(
        tmp_path, monkeypatch, query={"limit": "999"}, response={"vdb": {"memories": [], "total": 0}},
    )
    assert payload["_status"] == 200
    assert calls[0][2]["limit"] == 100

    payload, calls = _external_memory_payload(
        tmp_path, monkeypatch, query={"q": "x" * 501}, response={},
    )
    assert payload["_status"] == 400
    assert payload["error"] == "Search query is too long."
    assert calls == []


def test_external_memory_sidecar_errors_are_redacted(tmp_path, monkeypatch):
    payload, _calls = _external_memory_payload(
        tmp_path, monkeypatch, error=OSError("connection failed with sk-super-secret"),
    )
    assert payload == {
        "_status": 503,
        "error": "External memory is temporarily unavailable.",
    }
    assert "secret" not in json.dumps(payload)


def test_external_memory_drops_non_finite_numeric_fields():
    item = routes._safe_external_memory_item({
        "memory_id": "mem-1",
        "content": "safe",
        "score": float("nan"),
        "memory_at": float("inf"),
        "gmt_created": float("-inf"),
    })

    assert item == {"memory_id": "mem-1", "content": "safe"}


def test_memory_panel_declares_external_memory_list_and_semantic_search_ui():
    panels = (routes.Path(__file__).parent.parent / "static" / "panels.js").read_text(encoding="utf-8")

    assert "function loadExternalMemories(" in panels
    assert "function searchExternalMemories(" in panels
    assert "/api/memory/external" in panels
    assert "externalMemoryQuery" in panels
    assert "external_memory_recall_trace_hint" in panels
    assert "_externalMemoryRequestSeq" in panels
    assert "if (force)" in panels
