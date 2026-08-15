"""Admission/drain regression coverage for non-chat auxiliary model work."""

import contextlib
from collections import OrderedDict
import sys
import threading
import time
import types

import pytest


def _reset_active_runs():
    from api import config

    with config.ACTIVE_RUNS_LOCK:
        config.ACTIVE_RUNS.clear()
        config.LAST_RUN_FINISHED_AT = None


def test_exclusive_gate_uses_windows_byte_lock(monkeypatch, tmp_path):
    from api import run_admission

    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd, mode, nbytes):
            calls.append((fd, mode, nbytes))

    monkeypatch.setattr(run_admission, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run_admission, "fcntl", None)
    monkeypatch.setattr(run_admission, "msvcrt", FakeMsvcrt, raising=False)
    monkeypatch.delattr(run_admission.os, "fchmod", raising=False)

    with pytest.raises(RuntimeError, match="probe"):
        with run_admission._exclusive_gate():
            assert [call[1:] for call in calls] == [(FakeMsvcrt.LK_LOCK, 1)]
            raise RuntimeError("probe")

    assert [call[1:] for call in calls] == [
        (FakeMsvcrt.LK_LOCK, 1),
        (FakeMsvcrt.LK_UNLCK, 1),
    ]
    assert run_admission.run_admission_lock_path().read_bytes() == b"\0"


def test_marker_write_works_without_posix_chmod_or_directory_fsync(
    monkeypatch, tmp_path
):
    from api import run_admission

    real_os = run_admission.os

    class WindowsOsProxy:
        name = "nt"

        def __getattr__(self, name):
            if name == "fchmod":
                raise AttributeError(name)
            return getattr(real_os, name)

    monkeypatch.setattr(run_admission, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run_admission, "os", WindowsOsProxy())

    payload = {
        "version": 1,
        "draining": True,
        "attempt_id": "windows-marker",
        "candidate_id": "candidate",
        "reason": "test",
        "enabled_at": 1.0,
    }
    run_admission._write_marker_locked(payload)

    assert run_admission._read_marker_locked()["attempt_id"] == "windows-marker"


def test_exclusive_gate_fails_closed_without_cross_process_lock(monkeypatch, tmp_path):
    from api import run_admission

    monkeypatch.setattr(run_admission, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run_admission, "fcntl", None)
    monkeypatch.setattr(run_admission, "msvcrt", None, raising=False)

    with pytest.raises(RuntimeError, match="cross-process run admission locking"):
        with run_admission._exclusive_gate():
            raise AssertionError("unlocked admission gate opened")


def test_admitted_auxiliary_run_is_count_visible_and_always_cleans_up(monkeypatch):
    from api import config
    from api import run_admission

    _reset_active_runs()
    monkeypatch.setattr(run_admission, "run_admission_transaction", lambda: _NullContext())

    with run_admission.admitted_auxiliary_run(
        "aux-test-1",
        session_id="session-1",
        phase="aux-running",
        backend="test",
    ):
        with config.ACTIVE_RUNS_LOCK:
            entry = dict(config.ACTIVE_RUNS["aux-test-1"])
        assert {
            key: entry[key]
            for key in ("stream_id", "session_id", "phase", "backend", "started_at")
        } == pytest.approx(
            {
                "stream_id": "aux-test-1",
                "session_id": "session-1",
                "phase": "aux-running",
                "backend": "test",
                "started_at": entry["started_at"],
            }
        )
        assert entry["profile"] == "default"
        assert entry["profile_generation"] == "default-profile"

    with config.ACTIVE_RUNS_LOCK:
        assert "aux-test-1" not in config.ACTIVE_RUNS
        assert isinstance(config.LAST_RUN_FINISHED_AT, float)

    with pytest.raises(RuntimeError, match="boom"):
        with run_admission.admitted_auxiliary_run(
            "aux-test-2",
            session_id="session-2",
        ):
            raise RuntimeError("boom")

    with config.ACTIVE_RUNS_LOCK:
        assert "aux-test-2" not in config.ACTIVE_RUNS


def test_admitted_auxiliary_run_lease_rejects_recreated_profile_before_commit(
    monkeypatch, tmp_path
):
    from api import config
    from api import profiles
    from api import run_admission
    from api import skill_ui_descriptions
    from api.profile_generation import (
        ProfileGenerationMismatch,
        ensure_profile_generation,
    )

    _reset_active_runs()
    profile_home = tmp_path / ".hermes" / "profiles" / "work"
    profile_home.mkdir(parents=True)
    original_generation = ensure_profile_generation(profile_home)

    monkeypatch.setattr(run_admission, "run_admission_transaction", lambda: _NullContext())
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda name: profile_home if name == "work" else tmp_path / ".hermes",
    )
    monkeypatch.setattr(
        skill_ui_descriptions,
        "skill_transaction",
        lambda _key: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "api.agent_runtime.ensure_agent_runtime_current",
        lambda: None,
    )

    with run_admission.admitted_auxiliary_run(
        "aux-profile-lease",
        session_id="session-profile",
        profile="work",
        backend="test",
    ) as lease:
        assert lease.profile_generation == original_generation
        with config.ACTIVE_RUNS_LOCK:
            entry = dict(config.ACTIVE_RUNS["aux-profile-lease"])
        assert entry["profile"] == "work"
        assert entry["profile_generation"] == original_generation

        generation_path = profile_home / ".webui-profile-generation"
        generation_path.unlink()
        replacement_generation = ensure_profile_generation(profile_home)
        assert replacement_generation != original_generation

        with pytest.raises(ProfileGenerationMismatch):
            lease.assert_current()

    with config.ACTIVE_RUNS_LOCK:
        assert "aux-profile-lease" not in config.ACTIVE_RUNS


def test_admitted_auxiliary_run_rejects_drain_before_publication(monkeypatch):
    from api import config
    from api import run_admission

    _reset_active_runs()
    payload = {
        "error": "draining",
        "type": "service_draining",
        "retryable": True,
    }

    class RejectingContext:
        def __enter__(self):
            raise run_admission.RunAdmissionRejected(payload)

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(run_admission, "run_admission_transaction", lambda: RejectingContext())

    with pytest.raises(run_admission.RunAdmissionRejected) as exc_info:
        with run_admission.admitted_auxiliary_run(
            "aux-rejected",
            session_id="session-rejected",
        ):
            raise AssertionError("drained auxiliary work started")

    assert exc_info.value.payload == payload
    with config.ACTIVE_RUNS_LOCK:
        assert "aux-rejected" not in config.ACTIVE_RUNS


def test_git_commit_message_drain_rejects_before_profile_or_model_work(monkeypatch):
    from api import profiles
    from api import routes
    from api import run_admission

    payload = {
        "error": "draining",
        "type": "service_draining",
        "retryable": True,
    }

    @contextlib.contextmanager
    def reject(*_args, **_kwargs):
        raise run_admission.RunAdmissionRejected(payload)
        yield

    monkeypatch.setattr(routes, "admitted_auxiliary_run", reject, raising=False)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        profiles,
        "profile_env_for_background_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("profile/model work started while draining")
        ),
    )

    with pytest.raises(run_admission.RunAdmissionRejected) as exc_info:
        routes._llm_git_commit_message("system", "user")

    assert exc_info.value.payload == payload


def test_git_commit_message_route_preserves_typed_drain_503(monkeypatch, tmp_path):
    from api import routes
    from api import run_admission
    from api import workspace_git

    payload = {
        "error": "draining",
        "type": "service_draining",
        "retryable": True,
    }
    session = types.SimpleNamespace(workspace=str(tmp_path), model="", model_provider=None)
    monkeypatch.setattr(routes, "require", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(
        workspace_git,
        "staged_commit_message_prompt",
        lambda _workspace: {"system_prompt": "system", "user_prompt": "user"},
    )
    monkeypatch.setattr(
        routes,
        "_llm_git_commit_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            run_admission.RunAdmissionRejected(payload)
        ),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, body, status=200, **_kwargs: {"status": status, "payload": body},
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: {
            "status": status,
            "payload": {"error": message},
        },
    )

    response = routes._handle_git_commit_message(object(), {"session_id": "git-session"})

    assert response == {"status": 503, "payload": payload}


def test_git_commit_message_rechecks_lease_before_return(monkeypatch):
    from api import routes
    from api.profile_generation import ProfileGenerationMismatch

    class StaleLease:
        profile = "default"

        @contextlib.contextmanager
        def commit_guard(self):
            raise ProfileGenerationMismatch("profile changed")
            yield

    @contextlib.contextmanager
    def admitted(*_args, **_kwargs):
        yield StaleLease()

    monkeypatch.setattr(routes, "admitted_auxiliary_run", admitted)
    monkeypatch.setattr(
        routes,
        "_llm_git_commit_message_admitted",
        lambda *_args, **_kwargs: "feat: stale result",
    )

    with pytest.raises(ProfileGenerationMismatch, match="profile changed"):
        routes._llm_git_commit_message("system", "user")


@pytest.mark.parametrize(
    "error_kind",
    ["runtime", "profile", "drain"],
)
def test_git_commit_message_auxiliary_control_flow_does_not_fallback(
    monkeypatch,
    error_kind,
):
    from api import profiles
    from api import routes
    from api.profile_generation import ProfileGenerationMismatch
    from api.run_admission import RunAdmissionRejected

    errors = {
        "runtime": routes.AgentRuntimeChangedError("runtime changed"),
        "profile": ProfileGenerationMismatch("profile changed"),
        "drain": RunAdmissionRejected(
            {
                "error": "draining",
                "type": "service_draining",
                "retryable": True,
            }
        ),
    }
    control_error = errors[error_kind]

    monkeypatch.setattr(
        profiles,
        "profile_env_for_background_worker",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(routes, "ensure_agent_runtime_current", lambda: None)
    monkeypatch.setattr(
        routes,
        "require_ai_agent_class",
        lambda: (_ for _ in ()).throw(
            AssertionError("main-model fallback started after control-flow error")
        ),
    )
    fake_auxiliary_client = types.ModuleType("agent.auxiliary_client")
    fake_auxiliary_client.get_text_auxiliary_client = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(control_error)
    )
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", fake_auxiliary_client)

    session = types.SimpleNamespace(
        session_id="git-control-flow",
        profile="default",
        model="test-model",
        model_provider="test",
    )
    with pytest.raises(type(control_error)) as exc_info:
        routes._llm_git_commit_message_admitted(
            "system",
            "user",
            session=session,
            profile="default",
        )

    assert exc_info.value is control_error


def test_git_commit_message_is_count_visible_and_cleans_up(monkeypatch):
    from api import agent_runtime
    from api import config
    from api import routes

    _reset_active_runs()
    observed = []
    monkeypatch.setattr(agent_runtime, "ensure_agent_runtime_current", lambda: None)

    def admitted_model(*_args, **_kwargs):
        with config.ACTIVE_RUNS_LOCK:
            observed.extend(
                dict(entry)
                for entry in config.ACTIVE_RUNS.values()
                if entry.get("backend") == "git-commit-message"
            )
        return "feat: count visible"

    monkeypatch.setattr(routes, "_llm_git_commit_message_admitted", admitted_model)
    session = types.SimpleNamespace(session_id="git-count", profile="default")

    assert routes._llm_git_commit_message("system", "user", session=session) == (
        "feat: count visible"
    )
    assert len(observed) == 1
    assert observed[0]["session_id"] == "git-count"
    assert observed[0]["profile"] == "default"
    with config.ACTIVE_RUNS_LOCK:
        assert config.ACTIVE_RUNS == {}


def test_git_commit_message_handler_holds_guard_through_response(
    monkeypatch, tmp_path
):
    from api import routes
    from api import workspace_git

    guard_active = []

    class Lease:
        profile = "default"

        @contextlib.contextmanager
        def commit_guard(self):
            guard_active.append(True)
            try:
                yield
            finally:
                guard_active.pop()

    @contextlib.contextmanager
    def admitted(*_args, **_kwargs):
        yield Lease()

    session = types.SimpleNamespace(
        session_id="git-guard",
        profile="default",
        workspace=str(tmp_path),
        model="",
        model_provider=None,
    )
    monkeypatch.setattr(routes, "require", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "admitted_auxiliary_run", admitted)
    monkeypatch.setattr(
        routes,
        "_llm_git_commit_message_admitted",
        lambda *_args, **_kwargs: "feat: guarded response",
    )
    monkeypatch.setattr(
        workspace_git,
        "staged_commit_message_prompt",
        lambda _workspace: {
            "system_prompt": "system",
            "user_prompt": "user",
            "truncated": False,
        },
    )

    def guarded_json(_handler, body, status=200, **_kwargs):
        assert guard_active == [True]
        return {"status": status, "payload": body}

    monkeypatch.setattr(routes, "j", guarded_json)

    response = routes._handle_git_commit_message(
        object(), {"session_id": "git-guard"}
    )

    assert response["status"] == 200
    assert response["payload"]["message"] == "feat: guarded response"
    assert guard_active == []


def test_git_commit_message_handler_preserves_profile_stale_409(
    monkeypatch, tmp_path
):
    from api import routes
    from api import workspace_git
    from api.profile_generation import ProfileGenerationMismatch

    session = types.SimpleNamespace(
        session_id="git-stale",
        profile="default",
        workspace=str(tmp_path),
        model="",
        model_provider=None,
    )
    monkeypatch.setattr(routes, "require", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(
        workspace_git,
        "staged_commit_message_prompt",
        lambda _workspace: {"system_prompt": "system", "user_prompt": "user"},
    )
    monkeypatch.setattr(
        routes,
        "_llm_git_commit_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProfileGenerationMismatch("profile changed")
        ),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, body, status=200, **_kwargs: {
            "status": status,
            "payload": body,
        },
    )

    response = routes._handle_git_commit_message(
        object(), {"session_id": "git-stale"}
    )

    assert response == {
        "status": 409,
        "payload": {
            "error": "profile changed",
            "type": "profile_generation_stale",
            "retryable": True,
        },
    }


def test_update_summary_drain_is_not_swallowed_as_fallback():
    from api import run_admission
    from api.updates import summarize_update_payload

    payload = {
        "error": "draining",
        "type": "service_draining",
        "retryable": True,
    }

    def reject(_system, _prompt):
        raise run_admission.RunAdmissionRejected(payload)

    with pytest.raises(run_admission.RunAdmissionRejected) as exc_info:
        summarize_update_payload(
            {
                "webui": {
                    "behind": 1,
                    "current_sha": "old",
                    "latest_sha": "new",
                    "compare_url": "https://example.test/compare",
                }
            },
            llm_callback=reject,
            use_cache=False,
        )

    assert exc_info.value.payload == payload


def test_update_summary_drain_rejects_before_profile_or_model_work(monkeypatch):
    from api import profiles
    from api import routes
    from api import run_admission

    payload = {
        "error": "draining",
        "type": "service_draining",
        "retryable": True,
    }

    def reject(*_args, **_kwargs):
        raise run_admission.RunAdmissionRejected(payload)

    monkeypatch.setattr(
        routes,
        "capture_auxiliary_profile_snapshot",
        reject,
        raising=False,
    )
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        profiles,
        "profile_env_for_background_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("profile/model work started while draining")
        ),
    )

    with pytest.raises(run_admission.RunAdmissionRejected) as exc_info:
        routes._llm_update_summary("system", "user")

    assert exc_info.value.payload == payload


def test_update_summary_handler_preserves_typed_drain_503(monkeypatch):
    from api import routes
    from api import run_admission

    payload = {
        "error": "draining",
        "type": "service_draining",
        "retryable": True,
    }
    monkeypatch.setattr(
        routes,
        "_llm_update_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            run_admission.RunAdmissionRejected(payload)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, body, status=200, **_kwargs: {"status": status, "payload": body},
    )

    response = routes._handle_update_summary(
        object(),
        {
            "target": "webui",
            "updates": {
                "webui": {
                    "behind": 1,
                    "current_sha": "old-handler",
                    "latest_sha": "new-handler",
                    "compare_url": "https://example.test/compare",
                }
            },
        },
    )

    assert response == {"status": 503, "payload": payload}


def test_update_summary_without_model_backend_does_not_register_active_run(monkeypatch):
    from api import config
    from api import routes

    _reset_active_runs()
    monkeypatch.setattr(
        routes,
        "_prepare_update_summary_model",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    @contextlib.contextmanager
    def forbidden_admission(*_args, **_kwargs):
        raise AssertionError("local update fallback registered an active model run")
        yield

    monkeypatch.setattr(routes, "admitted_auxiliary_run", forbidden_admission)

    assert routes._llm_update_summary("system", "user") == ""
    with config.ACTIVE_RUNS_LOCK:
        assert config.ACTIVE_RUNS == {}


def test_update_summary_local_fallback_holds_noncounting_commit_guard(monkeypatch):
    from api import config
    from api import routes
    from api import updates

    snapshot = object()
    guard_active = []

    _reset_active_runs()
    updates._summary_cache.clear()
    monkeypatch.setattr(routes, "capture_auxiliary_profile_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        routes,
        "_prepare_update_summary_model",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    @contextlib.contextmanager
    def local_commit_guard(actual_snapshot):
        assert actual_snapshot is snapshot
        with config.ACTIVE_RUNS_LOCK:
            assert config.ACTIVE_RUNS == {}
        guard_active.append(True)
        try:
            yield
        finally:
            guard_active.pop()

    @contextlib.contextmanager
    def forbidden_admission(*_args, **_kwargs):
        raise AssertionError("local update fallback registered an active model run")
        yield

    class GuardedCache(OrderedDict):
        def __setitem__(self, key, value):
            assert guard_active == [True]
            return super().__setitem__(key, value)

    def guarded_json(_handler, body, status=200, **_kwargs):
        assert guard_active == [True]
        return {"status": status, "payload": body}

    monkeypatch.setattr(routes, "auxiliary_profile_commit_guard", local_commit_guard, raising=False)
    monkeypatch.setattr(routes, "admitted_auxiliary_run", forbidden_admission)
    monkeypatch.setattr(updates, "_summary_cache", GuardedCache())
    monkeypatch.setattr(routes, "j", guarded_json)

    response = routes._handle_update_summary(
        object(),
        {
            "target": "webui",
            "updates": {
                "webui": {
                    "behind": 1,
                    "current_sha": "old-local",
                    "latest_sha": "new-local",
                    "compare_url": "https://example.test/compare",
                }
            },
        },
    )

    assert response["status"] == 200
    assert response["payload"]["generated_by"] == "fallback"
    assert len(updates._summary_cache) == 1
    assert guard_active == []
    with config.ACTIVE_RUNS_LOCK:
        assert config.ACTIVE_RUNS == {}


def test_auxiliary_profile_commit_guard_is_reentrant_for_named_profile(
    monkeypatch,
    tmp_path,
):
    from api import profiles
    from api import run_admission
    from api.profile_generation import ensure_profile_generation

    profile_home = tmp_path / ".hermes" / "profiles" / "work"
    profile_home.mkdir(parents=True)
    ensure_profile_generation(profile_home)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "work")
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda name: profile_home if name == "work" else tmp_path / ".hermes",
    )
    monkeypatch.setattr(
        "api.agent_runtime.ensure_agent_runtime_current",
        lambda: None,
    )

    snapshot = run_admission.capture_auxiliary_profile_snapshot("work")
    with run_admission.auxiliary_profile_commit_guard(snapshot):
        pass


def test_update_summary_rechecks_lease_before_cache_commit(monkeypatch):
    from api import routes
    from api import updates
    from api.profile_generation import ProfileGenerationMismatch

    class StaleLease:
        profile = "default"

        @contextlib.contextmanager
        def commit_guard(self):
            raise ProfileGenerationMismatch("profile changed")
            yield

    @contextlib.contextmanager
    def admitted(*_args, **_kwargs):
        yield StaleLease()

    updates._summary_cache.clear()
    monkeypatch.setattr(routes, "admitted_auxiliary_run", admitted)
    monkeypatch.setattr(routes, "_prepare_update_summary_model", lambda *_args: True)
    monkeypatch.setattr(
        routes,
        "_llm_update_summary_admitted",
        lambda *_args, **_kwargs: "Notice: stale result",
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, body, status=200, **_kwargs: {
            "status": status,
            "payload": body,
        },
    )

    response = routes._handle_update_summary(
        object(),
        {
            "target": "webui",
            "updates": {
                "webui": {
                    "behind": 1,
                    "current_sha": "old-lease",
                    "latest_sha": "new-lease",
                    "compare_url": "https://example.test/compare",
                }
            },
        },
    )

    assert response == {
        "status": 409,
        "payload": {
            "error": "profile changed",
            "type": "profile_generation_stale",
            "retryable": True,
        },
    }
    assert updates._summary_cache == {}


def test_update_summary_probe_profile_drift_rejects_before_model(
    monkeypatch,
    tmp_path,
):
    from api import config
    from api import profiles
    from api import routes
    from api import skill_ui_descriptions
    from api.profile_generation import (
        ProfileGenerationMismatch,
        ensure_profile_generation,
    )

    _reset_active_runs()
    profile_home = tmp_path / ".hermes" / "profiles" / "work"
    profile_home.mkdir(parents=True)
    original_generation = ensure_profile_generation(profile_home)

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "work")
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda name: profile_home if name == "work" else tmp_path / ".hermes",
    )
    monkeypatch.setattr(
        skill_ui_descriptions,
        "skill_transaction",
        lambda _key: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "api.agent_runtime.ensure_agent_runtime_current",
        lambda: None,
    )

    def replace_profile_during_probe(profile):
        assert profile == "work"
        generation_path = profile_home / ".webui-profile-generation"
        generation_path.unlink()
        replacement_generation = ensure_profile_generation(profile_home)
        assert replacement_generation != original_generation
        return True

    monkeypatch.setattr(
        routes,
        "_prepare_update_summary_model",
        replace_profile_during_probe,
    )
    monkeypatch.setattr(
        routes,
        "_llm_update_summary_admitted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model started after Profile drift")
        ),
    )

    with pytest.raises(ProfileGenerationMismatch):
        routes._llm_update_summary("system", "user")

    with config.ACTIVE_RUNS_LOCK:
        assert config.ACTIVE_RUNS == {}


@pytest.mark.parametrize(
    "error_kind",
    ["runtime", "profile", "drain"],
)
def test_update_summary_auxiliary_control_flow_does_not_fallback(
    monkeypatch,
    error_kind,
):
    from api import profiles
    from api import routes
    from api.profile_generation import ProfileGenerationMismatch
    from api.run_admission import RunAdmissionRejected

    errors = {
        "runtime": routes.AgentRuntimeChangedError("runtime changed"),
        "profile": ProfileGenerationMismatch("profile changed"),
        "drain": RunAdmissionRejected(
            {
                "error": "draining",
                "type": "service_draining",
                "retryable": True,
            }
        ),
    }
    control_error = errors[error_kind]

    monkeypatch.setattr(
        profiles,
        "profile_env_for_background_worker",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(routes, "ensure_agent_runtime_current", lambda: None)
    monkeypatch.setattr(
        routes,
        "_update_summary_main_runtime",
        lambda: {
            "provider": "test",
            "model": "test-model",
            "base_url": "https://example.test/v1",
            "api_key": "test-key",
            "api_mode": None,
            "command": None,
        },
    )
    fake_auxiliary_client = types.ModuleType("agent.auxiliary_client")
    fake_auxiliary_client.get_text_auxiliary_client = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(control_error)
    )
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", fake_auxiliary_client)
    monkeypatch.setattr(
        routes,
        "require_ai_agent_class",
        lambda: (_ for _ in ()).throw(
            AssertionError("main-model fallback started after control-flow error")
        ),
    )

    with pytest.raises(type(control_error)) as exc_info:
        routes._llm_update_summary_admitted("system", "user", profile="default")

    assert exc_info.value is control_error


def test_update_summary_is_count_visible_and_cleans_up(monkeypatch):
    from api import agent_runtime
    from api import config
    from api import routes

    _reset_active_runs()
    observed = []
    monkeypatch.setattr(agent_runtime, "ensure_agent_runtime_current", lambda: None)

    def admitted_model(*_args, **_kwargs):
        with config.ACTIVE_RUNS_LOCK:
            observed.extend(
                dict(entry)
                for entry in config.ACTIVE_RUNS.values()
                if entry.get("backend") == "update-summary"
            )
        return "Notice: count visible"

    monkeypatch.setattr(routes, "_prepare_update_summary_model", lambda *_args: True)
    monkeypatch.setattr(routes, "_llm_update_summary_admitted", admitted_model)

    assert routes._llm_update_summary("system", "user") == "Notice: count visible"
    assert len(observed) == 1
    assert observed[0]["session_id"] == "updates-summary"
    assert observed[0]["profile"] == "default"
    with config.ACTIVE_RUNS_LOCK:
        assert config.ACTIVE_RUNS == {}


def test_update_summary_holds_guard_through_format_cache_and_response(monkeypatch):
    from api import routes
    from api import updates

    guard_active = []

    class Lease:
        profile = "default"

        @contextlib.contextmanager
        def commit_guard(self):
            guard_active.append(True)
            try:
                yield
            finally:
                guard_active.pop()

    @contextlib.contextmanager
    def admitted(*_args, **_kwargs):
        yield Lease()

    class GuardedCache(OrderedDict):
        def __setitem__(self, key, value):
            assert guard_active == [True]
            return super().__setitem__(key, value)

    guarded_cache = GuardedCache()
    monkeypatch.setattr(routes, "admitted_auxiliary_run", admitted)
    monkeypatch.setattr(routes, "_prepare_update_summary_model", lambda *_args: True)
    monkeypatch.setattr(
        routes,
        "_llm_update_summary_admitted",
        lambda *_args, **_kwargs: "Notice: guarded cache",
    )
    monkeypatch.setattr(updates, "_summary_cache", guarded_cache)
    real_format = updates._format_update_summary_sections

    def guarded_format(*args, **kwargs):
        assert guard_active == [True]
        return real_format(*args, **kwargs)

    def guarded_json(_handler, body, status=200, **_kwargs):
        assert guard_active == [True]
        return {"status": status, "payload": body}

    monkeypatch.setattr(updates, "_format_update_summary_sections", guarded_format)
    monkeypatch.setattr(routes, "j", guarded_json)

    response = routes._handle_update_summary(
        object(),
        {
            "target": "webui",
            "updates": {
                "webui": {
                    "behind": 1,
                    "current_sha": "old-guard",
                    "latest_sha": "new-guard",
                    "compare_url": "https://example.test/compare",
                }
            },
        },
    )

    assert response["status"] == 200
    assert response["payload"]["generated_by"] == "llm"
    assert len(guarded_cache) == 1
    assert guard_active == []


def test_update_summary_handler_preserves_runtime_stale_409(monkeypatch):
    from api import routes

    monkeypatch.setattr(
        routes,
        "_llm_update_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            routes.AgentRuntimeChangedError("runtime changed")
        ),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, body, status=200, **_kwargs: {
            "status": status,
            "payload": body,
        },
    )

    response = routes._handle_update_summary(
        object(),
        {
            "target": "webui",
            "updates": {
                "webui": {
                    "behind": 1,
                    "current_sha": "old-runtime",
                    "latest_sha": "new-runtime",
                    "compare_url": "https://example.test/compare",
                }
            },
        },
    )

    assert response == {
        "status": 409,
        "payload": {
            "error": "runtime changed",
            "type": "agent_runtime_stale",
            "retryable": True,
        },
    }


def _install_handoff_fallback_route(monkeypatch, *, api_key=""):
    from api import config as cfg
    from api import models
    from api import routes

    monkeypatch.setattr(routes, "require", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "ensure_agent_runtime_current", lambda: None)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, body, status=200, **_kwargs: {
            "status": status,
            "payload": body,
        },
    )
    monkeypatch.setattr(
        models,
        "count_conversation_rounds",
        lambda _sid, since=None: models.CONVERSATION_ROUND_THRESHOLD,
    )
    monkeypatch.setattr(
        models,
        "get_cli_session_messages",
        lambda _sid: [
            {"role": "user", "content": "Need a handoff", "timestamp": 1.0},
            {"role": "assistant", "content": "Context is ready", "timestamp": 2.0},
        ],
    )
    monkeypatch.setattr(
        cfg,
        "resolve_model_provider",
        lambda _model=None: ("gpt-test", "openrouter", None),
    )

    runtime_module = types.ModuleType("hermes_cli.runtime_provider")
    runtime_module.resolve_runtime_provider = lambda requested=None: {
        "api_key": api_key,
        "provider": "openrouter",
        "base_url": None,
    }
    hermes_cli_module = types.ModuleType("hermes_cli")
    hermes_cli_module.__path__ = []
    hermes_cli_module.runtime_provider = runtime_module
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli_module)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", runtime_module)


def test_handoff_no_key_fallback_rejects_real_drain_before_persist(
    monkeypatch,
    tmp_path,
):
    from api import routes
    from api import run_admission

    _install_handoff_fallback_route(monkeypatch, api_key="")
    persisted = []
    monkeypatch.setattr(
        routes,
        "_persist_handoff_summary",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(run_admission, "STATE_DIR", state_dir)
    run_admission.reset_in_process_shutdown_for_tests()
    run_admission.enable_run_drain(
        "handoff-drain-test",
        reason="test",
        candidate_id="candidate",
    )
    try:
        response = routes._handle_handoff_summary(
            object(),
            {"session_id": "no-key-drain"},
        )
    finally:
        run_admission.disable_run_drain("handoff-drain-test")

    assert response["status"] == 503
    assert response["payload"]["type"] == "service_draining"
    assert persisted == []


def test_handoff_no_key_fallback_rechecks_profile_before_persist(monkeypatch):
    from api import routes
    from api.profile_generation import ProfileGenerationMismatch

    _install_handoff_fallback_route(monkeypatch, api_key="")
    snapshot = object()
    persisted = []
    monkeypatch.setattr(
        routes,
        "capture_auxiliary_profile_snapshot",
        lambda _profile=None: snapshot,
    )

    @contextlib.contextmanager
    def stale_guard(actual_snapshot):
        assert actual_snapshot is snapshot
        raise ProfileGenerationMismatch("profile changed")
        yield

    monkeypatch.setattr(routes, "auxiliary_profile_commit_guard", stale_guard)
    monkeypatch.setattr(
        routes,
        "_persist_handoff_summary",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    response = routes._handle_handoff_summary(
        object(),
        {"session_id": "no-key-stale"},
    )

    assert response == {
        "status": 409,
        "payload": {
            "error": "profile changed",
            "type": "profile_generation_stale",
            "retryable": True,
        },
    }
    assert persisted == []


def test_handoff_no_key_fallback_persist_failure_is_truthful(monkeypatch):
    from api import routes

    _install_handoff_fallback_route(monkeypatch, api_key="")

    def fail_persist(*_args, **_kwargs):
        raise OSError("state store unavailable")

    monkeypatch.setattr(routes, "_persist_handoff_summary", fail_persist)

    response = routes._handle_handoff_summary(
        object(),
        {"session_id": "no-key-persist-failure"},
    )

    assert response["status"] == 500
    assert response["payload"]["error"].startswith("Handoff summary failed")
    assert "state store unavailable" in response["payload"]["error"]
    assert response["payload"].get("ok") is not True


def test_handoff_preparation_fallback_persist_failure_is_truthful(monkeypatch):
    from api import config as cfg
    from api import routes

    _install_handoff_fallback_route(monkeypatch, api_key="unused")
    monkeypatch.setattr(
        cfg,
        "resolve_model_provider",
        lambda _model=None: (_ for _ in ()).throw(RuntimeError("prepare failed")),
    )

    def fail_persist(*_args, **_kwargs):
        raise OSError("state store unavailable")

    monkeypatch.setattr(routes, "_persist_handoff_summary", fail_persist)

    response = routes._handle_handoff_summary(
        object(),
        {"session_id": "prepare-persist-failure"},
    )

    assert response["status"] == 500
    assert response["payload"]["error"].startswith("Handoff summary failed")
    assert "state store unavailable" in response["payload"]["error"]
    assert response["payload"].get("ok") is not True


@pytest.mark.parametrize("messaging", [False, True])
def test_handoff_all_persistence_backends_fail_truthfully(
    monkeypatch, messaging
):
    from api import routes

    attempts = []
    monkeypatch.setattr(
        routes, "_is_messaging_session_id", lambda _sid: messaging
    )
    monkeypatch.setattr(
        routes,
        "_persist_handoff_summary_locally",
        lambda *_args, **_kwargs: attempts.append("local") or False,
    )
    monkeypatch.setattr(
        routes,
        "_persist_handoff_summary_to_state_db",
        lambda *_args, **_kwargs: attempts.append("state-db") or False,
    )

    with pytest.raises(RuntimeError, match="persist handoff summary"):
        routes._persist_handoff_summary(
            "persist-failure",
            "summary",
            "telegram" if messaging else None,
            1,
        )

    assert sorted(attempts) == ["local", "state-db"]


def test_handoff_provider_preparation_uses_captured_session_profile(monkeypatch, tmp_path):
    from api import config as cfg
    from api import models
    from api import profiles
    from api import routes
    from api.run_admission import AuxiliaryProfileSnapshot

    _install_handoff_fallback_route(monkeypatch, api_key="unused")
    profile_home = tmp_path / "profiles" / "work"
    profile_home.mkdir(parents=True)
    snapshot = AuxiliaryProfileSnapshot(
        profile="work",
        profile_home=profile_home,
        profile_generation="generation-work",
        profile_identity=None,
        named_profile=True,
    )
    state = {"profile": "ambient"}
    observed = []

    monkeypatch.setattr(models, "get_session", lambda _sid: types.SimpleNamespace(
        model="session-model",
        profile="work",
        source_label=None,
        raw_source=None,
        source_tag=None,
        session_source=None,
    ))
    monkeypatch.setattr(
        routes,
        "capture_auxiliary_profile_snapshot",
        lambda actual_profile=None: snapshot if actual_profile == "work" else None,
    )

    @contextlib.contextmanager
    def commit_guard(actual_snapshot):
        assert actual_snapshot is snapshot
        yield

    monkeypatch.setattr(routes, "auxiliary_profile_commit_guard", commit_guard)

    @contextlib.contextmanager
    def profile_env(actual_profile, *_args, **_kwargs):
        assert actual_profile == "work"
        previous = state["profile"]
        state["profile"] = actual_profile
        try:
            yield
        finally:
            state["profile"] = previous

    monkeypatch.setattr(profiles, "profile_env_for_background_worker", profile_env)

    def resolve_model(model=None):
        observed.append(("model", state["profile"], model))
        return ("profile-model", "custom:work-provider", None)

    monkeypatch.setattr(cfg, "resolve_model_provider", resolve_model)

    runtime_module = sys.modules["hermes_cli.runtime_provider"]

    def resolve_runtime_provider(requested=None):
        observed.append(("runtime", state["profile"], requested))
        return {
            "api_key": "",
            "provider": "custom:work-provider",
            "base_url": None,
        }

    runtime_module.resolve_runtime_provider = resolve_runtime_provider

    def resolve_custom_provider_connection(provider):
        observed.append(("custom", state["profile"], provider))
        return (None, None)

    monkeypatch.setattr(
        cfg,
        "resolve_custom_provider_connection",
        resolve_custom_provider_connection,
    )
    monkeypatch.setattr(routes, "_persist_handoff_summary", lambda *_args, **_kwargs: None)

    response = routes._handle_handoff_summary(
        object(),
        {"session_id": "profile-scoped-handoff"},
    )

    assert response["status"] == 200
    assert response["payload"]["fallback"] is True
    assert [entry[:2] for entry in observed] == [
        ("model", "work"),
        ("runtime", "work"),
        ("custom", "work"),
    ]
    assert state["profile"] == "ambient"


def test_update_summary_cache_hit_rejects_real_drain(monkeypatch, tmp_path):
    from api import routes
    from api import run_admission
    from api import updates

    body = {
        "target": "webui",
        "updates": {
            "webui": {
                "behind": 1,
                "current_sha": "old-cache",
                "latest_sha": "new-cache",
                "compare_url": "https://example.test/compare",
            }
        },
    }
    updates._summary_cache.clear()
    first = updates.summarize_update_payload(
        body["updates"],
        llm_callback=lambda *_args: "Notice: cached result",
        target="webui",
    )
    assert first["cached"] is False

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(run_admission, "STATE_DIR", state_dir)
    run_admission.reset_in_process_shutdown_for_tests()
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {
            "status": status,
            "payload": payload,
        },
    )
    run_admission.enable_run_drain(
        "update-cache-drain-test",
        reason="test",
        candidate_id="candidate",
    )
    try:
        response = routes._handle_update_summary(object(), body)
    finally:
        run_admission.disable_run_drain("update-cache-drain-test")
        updates._summary_cache.clear()

    assert response["status"] == 503
    assert response["payload"]["type"] == "service_draining"


def test_update_summary_cache_hit_holds_guard_through_lru_and_response(monkeypatch):
    from api import routes
    from api import updates

    body = {
        "target": "webui",
        "updates": {
            "webui": {
                "behind": 1,
                "current_sha": "old-guarded-cache",
                "latest_sha": "new-guarded-cache",
                "compare_url": "https://example.test/compare",
            }
        },
    }
    guard_active = []

    class GuardedCache(OrderedDict):
        def move_to_end(self, key, last=True):
            assert guard_active == [True]
            return super().move_to_end(key, last=last)

    guarded_cache = GuardedCache()
    monkeypatch.setattr(updates, "_summary_cache", guarded_cache)
    first = updates.summarize_update_payload(
        body["updates"],
        llm_callback=lambda *_args: "Notice: guarded cache hit",
        target="webui",
    )
    assert first["cached"] is False

    snapshot = object()
    monkeypatch.setattr(
        routes,
        "capture_auxiliary_profile_snapshot",
        lambda _profile=None: snapshot,
    )

    @contextlib.contextmanager
    def guarded_commit(actual_snapshot):
        assert actual_snapshot is snapshot
        guard_active.append(True)
        try:
            yield
        finally:
            guard_active.pop()

    monkeypatch.setattr(routes, "auxiliary_profile_commit_guard", guarded_commit)

    def guarded_json(_handler, payload, status=200, **_kwargs):
        assert guard_active == [True]
        return {"status": status, "payload": payload}

    monkeypatch.setattr(routes, "j", guarded_json)

    response = routes._handle_update_summary(object(), body)

    assert response["status"] == 200
    assert response["payload"]["cached"] is True
    assert guard_active == []


def test_manual_compression_terminal_publication_rechecks_lease(monkeypatch):
    from api import routes
    from api.profile_generation import ProfileGenerationMismatch

    sid = "compression-terminal-stale"
    persisted = threading.Event()
    allow_return = threading.Event()
    guard_calls = []
    releases = []

    class Lease:
        profile = "default"

        def release(self):
            releases.append(True)

        @contextlib.contextmanager
        def commit_guard(self):
            guard_calls.append(len(guard_calls) + 1)
            if len(guard_calls) > 1:
                raise ProfileGenerationMismatch(
                    "profile changed before terminal publication"
                )
            yield

    class Handler:
        status = 200

        def payload(self):
            return {"ok": True, "session": {}, "summary": {}}

    def fake_compress(_handler, _body, *, lease=None):
        with lease.commit_guard():
            persisted.set()
        assert allow_return.wait(timeout=5)

    monkeypatch.setattr(routes, "_ManualCompressionMemoryHandler", Handler)
    monkeypatch.setattr(routes, "_handle_session_compress", fake_compress)
    monkeypatch.setattr(routes, "get_session", lambda _sid: None)

    with routes._MANUAL_COMPRESSION_JOBS_LOCK:
        routes._MANUAL_COMPRESSION_JOBS[sid] = {
            "session_id": sid,
            "status": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
        }

    worker = threading.Thread(
        target=routes._run_manual_compression_job,
        args=(sid, {"session_id": sid}, None, Lease()),
    )
    worker.start()
    assert persisted.wait(timeout=5)
    allow_return.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    with routes._MANUAL_COMPRESSION_JOBS_LOCK:
        job = dict(routes._MANUAL_COMPRESSION_JOBS.pop(sid))

    assert guard_calls == [1, 2]
    assert job["status"] == "error"
    assert job["error_status"] == 409
    assert job["error_type"] == "profile_generation_stale"
    assert job["retryable"] is True
    assert releases == [True]


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False
