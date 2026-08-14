"""Regression coverage for atomic WebUI run admission draining."""

from __future__ import annotations

import json
import queue
import threading
import time
import types

import pytest


def _reset_runtime(config) -> None:
    with config.STREAMS_LOCK:
        config.STREAMS.clear()
    with config.ACTIVE_RUNS_LOCK:
        config.ACTIVE_RUNS.clear()
        config.LAST_RUN_FINISHED_AT = None


@pytest.fixture
def isolated_admission(tmp_path, monkeypatch):
    from api import config
    from api import run_admission

    _reset_runtime(config)
    monkeypatch.setattr(run_admission, "STATE_DIR", tmp_path / "state")
    run_admission.reset_in_process_shutdown_for_tests()
    yield run_admission
    run_admission.reset_in_process_shutdown_for_tests()
    try:
        run_admission.disable_run_drain("test-attempt", allow_missing=True)
    except Exception:
        pass
    _reset_runtime(config)


def test_drain_waits_for_inflight_admission_and_returns_after_start_is_visible(
    isolated_admission,
):
    """A drain cannot commit between admission check and run/stream publication."""
    from api import config

    run_admission = isolated_admission
    admission_entered = threading.Event()
    allow_publication = threading.Event()
    drain_finished = threading.Event()
    errors: list[BaseException] = []

    def admit() -> None:
        try:
            with run_admission.run_admission_transaction():
                admission_entered.set()
                assert allow_publication.wait(5)
                with config.STREAMS_LOCK:
                    config.STREAMS["stream-race"] = queue.Queue()
                config.register_active_run(
                    "stream-race",
                    session_id="session-race",
                    phase="admitted",
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def drain() -> None:
        try:
            run_admission.enable_run_drain(
                "test-attempt",
                reason="release",
                candidate_id="candidate-v1",
            )
            drain_finished.set()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    admission_thread = threading.Thread(target=admit)
    drain_thread = threading.Thread(target=drain)
    admission_thread.start()
    assert admission_entered.wait(5)
    drain_thread.start()

    time.sleep(0.15)
    assert not drain_finished.is_set(), "drain committed while admission was unpublished"

    allow_publication.set()
    admission_thread.join(5)
    drain_thread.join(5)

    assert not admission_thread.is_alive()
    assert not drain_thread.is_alive()
    assert errors == []
    snapshot = run_admission.runtime_admission_snapshot()
    assert snapshot["draining"] is True
    assert snapshot["active_streams"] == 1
    assert snapshot["active_runs"] == 1


def test_drain_marker_is_strict_and_fails_closed(isolated_admission):
    run_admission = isolated_admission
    marker = run_admission.run_drain_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("not-json", encoding="utf-8")

    snapshot = run_admission.runtime_admission_snapshot()
    assert snapshot["draining"] is True
    assert snapshot["drain_state_valid"] is False
    with pytest.raises(run_admission.RunAdmissionRejected):
        with run_admission.run_admission_transaction():
            raise AssertionError("malformed drain marker must reject admission")


def test_main_stream_rejects_drain_before_session_mutation(
    isolated_admission, monkeypatch
):
    from api import routes

    run_admission = isolated_admission
    run_admission.enable_run_drain(
        "test-attempt", reason="release", candidate_id="candidate-v1"
    )
    session = types.SimpleNamespace(
        session_id="drained-main",
        profile=None,
        title="Untitled",
        active_stream_id=None,
        pending_user_message=None,
        pending_started_at=None,
    )
    monkeypatch.setattr(
        routes,
        "_prepare_chat_start_session_for_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("session mutated after drain")
        ),
    )

    response = routes._start_chat_stream_for_session(
        session,
        msg="hello",
        attachments=[],
        workspace="/tmp",
        model="test-model",
        model_provider="test-provider",
        external_runtime_owned=False,
    )

    assert response["_status"] == 503
    assert response["type"] == "service_draining"
    assert session.active_stream_id is None


def test_process_wakeup_rejects_drain_before_loading_session(
    isolated_admission, monkeypatch
):
    from api import routes

    run_admission = isolated_admission
    run_admission.enable_run_drain(
        "test-attempt", reason="release", candidate_id="candidate-v1"
    )
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("session loaded after drain")
        ),
    )

    response = routes.start_session_turn("session-1", "wake up")

    assert response["_status"] == 503
    assert response["type"] == "service_draining"


@pytest.mark.parametrize(
    ("route_name", "body"),
    [
        ("_handle_btw", {"session_id": "session-1", "question": "question"}),
        ("_handle_background", {"session_id": "session-1", "prompt": "prompt"}),
        ("_handle_chat_sync", {"session_id": "session-1", "message": "message"}),
    ],
)
def test_other_agent_entrypoints_reject_drain_before_loading_session(
    isolated_admission, monkeypatch, route_name, body
):
    from api import routes

    run_admission = isolated_admission
    run_admission.enable_run_drain(
        "test-attempt", reason="release", candidate_id="candidate-v1"
    )
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("session loaded after drain")
        ),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {
            "status": status,
            "payload": payload,
        },
    )

    response = getattr(routes, route_name)(object(), body)

    assert response["status"] == 503
    assert response["payload"]["type"] == "service_draining"


def test_real_stream_publication_blocks_drain_until_stream_and_run_are_registered(
    isolated_admission, monkeypatch
):
    """Exercise the production chokepoint, not only the low-level lock helper."""
    from api import config, routes, turn_journal

    run_admission = isolated_admission
    real_thread = threading.Thread
    prepare_entered = threading.Event()
    release_prepare = threading.Event()
    route_result: dict = {}
    drain_result: dict = {}

    session = types.SimpleNamespace(
        session_id="route-race",
        profile=None,
        title="Race",
        active_stream_id=None,
        pending_user_message=None,
        pending_started_at=None,
    )

    def prepare(current, **kwargs):
        prepare_entered.set()
        assert release_prepare.wait(5)
        current.active_stream_id = kwargs["stream_id"]
        current.pending_started_at = 123.0

    class FakeThread:
        def __init__(self, *, target, args, kwargs, daemon):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(routes, "_active_run_stream_for_session", lambda _sid: None)
    monkeypatch.setattr(routes, "_is_hidden_empty_session", lambda _s: False)
    monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", prepare)
    monkeypatch.setattr(routes, "set_last_workspace", lambda _workspace: None)
    monkeypatch.setattr(routes.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        turn_journal, "append_turn_journal_event", lambda *_args, **_kwargs: {}
    )

    def start_route():
        route_result.update(
            routes._start_chat_stream_for_session(
                session,
                msg="hello",
                attachments=[],
                workspace="/tmp",
                model="test-model",
                model_provider="test-provider",
                external_runtime_owned=False,
            )
        )

    def start_drain():
        drain_result.update(
            run_admission.enable_run_drain(
                "test-attempt", reason="release", candidate_id="candidate-v1"
            )
        )

    route_thread = real_thread(target=start_route)
    drain_thread = real_thread(target=start_drain)
    route_thread.start()
    assert prepare_entered.wait(5)
    drain_thread.start()
    time.sleep(0.15)
    assert drain_result == {}, "drain bypassed the route publication critical section"

    release_prepare.set()
    route_thread.join(5)
    drain_thread.join(5)

    assert not route_thread.is_alive()
    assert not drain_thread.is_alive()
    stream_id = route_result["stream_id"]
    with config.STREAMS_LOCK:
        assert stream_id in config.STREAMS
    with config.ACTIVE_RUNS_LOCK:
        assert config.ACTIVE_RUNS[stream_id]["phase"] == "admitted"
    assert drain_result["attempt_id"] == "test-attempt"


def test_in_process_shutdown_rejects_without_a_marker(isolated_admission):
    run_admission = isolated_admission
    run_admission.begin_in_process_shutdown()

    with pytest.raises(run_admission.RunAdmissionRejected) as exc_info:
        with run_admission.run_admission_transaction():
            pass

    assert exc_info.value.payload["type"] == "service_draining"
    assert not run_admission.run_drain_marker_path().exists()


def test_request_http_shutdown_closes_admission_before_idempotent_server_stop(
    isolated_admission, monkeypatch
):
    run_admission = isolated_admission
    shutdown_requested = run_admission.threading.Event()
    events = []

    class FakeServer:
        def shutdown(self):
            events.append("server-shutdown")

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            assert name == "webui-sigterm-shutdown"
            assert daemon is True
            self._target = target

        def start(self):
            events.append("thread-start")
            self._target()

    monkeypatch.setattr(run_admission.threading, "Thread", ImmediateThread)

    assert run_admission.request_http_shutdown(
        FakeServer(), shutdown_requested
    ) is True
    assert events == ["thread-start", "server-shutdown"]
    assert shutdown_requested.is_set()
    with pytest.raises(run_admission.RunAdmissionRejected):
        with run_admission.run_admission_transaction():
            pass

    assert run_admission.request_http_shutdown(
        FakeServer(), shutdown_requested
    ) is False
    assert events == ["thread-start", "server-shutdown"]


def test_request_http_shutdown_does_not_wait_for_admission_gate(isolated_admission):
    run_admission = isolated_admission
    shutdown_requested = run_admission.threading.Event()
    gate_held = run_admission.threading.Event()
    release_gate = run_admission.threading.Event()
    request_done = run_admission.threading.Event()
    server_shutdown = run_admission.threading.Event()

    class FakeServer:
        def shutdown(self):
            server_shutdown.set()

    def hold_gate():
        with run_admission._exclusive_gate():
            gate_held.set()
            assert release_gate.wait(5)

    def request_shutdown():
        run_admission.request_http_shutdown(FakeServer(), shutdown_requested)
        request_done.set()

    holder = run_admission.threading.Thread(target=hold_gate)
    requester = run_admission.threading.Thread(target=request_shutdown)
    holder.start()
    assert gate_held.wait(5)
    requester.start()
    try:
        assert request_done.wait(0.25), "signal shutdown waited on the admission gate"
        assert shutdown_requested.is_set()
        assert not server_shutdown.is_set(), "HTTP shutdown ran before admission closed"
    finally:
        release_gate.set()
        holder.join(5)
        requester.join(5)

    assert not holder.is_alive()
    assert not requester.is_alive()
    assert server_shutdown.wait(5)
    assert run_admission._IN_PROCESS_SHUTDOWN.is_set()


def test_concurrent_shutdown_requests_start_only_one_worker(isolated_admission):
    run_admission = isolated_admission
    start = run_admission.threading.Barrier(3)
    results = []
    shutdown_calls = []
    shutdown_calls_lock = run_admission.threading.Lock()

    class SlowEvent:
        def __init__(self):
            self._flag = False
            self._lock = run_admission.threading.Lock()

        def is_set(self):
            with self._lock:
                observed = self._flag
            time.sleep(0.05)
            return observed

        def set(self):
            with self._lock:
                self._flag = True

    class FakeServer:
        def shutdown(self):
            with shutdown_calls_lock:
                shutdown_calls.append("shutdown")

    shutdown_requested = SlowEvent()

    def request_shutdown():
        start.wait()
        results.append(
            run_admission.request_http_shutdown(FakeServer(), shutdown_requested)
        )

    callers = [run_admission.threading.Thread(target=request_shutdown) for _ in range(2)]
    for caller in callers:
        caller.start()
    start.wait()
    for caller in callers:
        caller.join(5)

    assert all(not caller.is_alive() for caller in callers)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(shutdown_calls) < 1:
        time.sleep(0.01)
    assert sorted(results) == [False, True]
    assert shutdown_calls == ["shutdown"]


def test_marker_payload_has_private_bounded_shape(isolated_admission):
    run_admission = isolated_admission
    state = run_admission.enable_run_drain(
        "test-attempt", reason="release", candidate_id="candidate-v1"
    )
    marker = run_admission.run_drain_marker_path()
    raw = marker.read_bytes()
    payload = json.loads(raw)

    assert state["attempt_id"] == "test-attempt"
    assert payload["version"] == 1
    assert payload["draining"] is True
    assert len(raw) < 4096
    assert marker.stat().st_mode & 0o777 == 0o600


def test_only_owner_attempt_can_clear_drain(isolated_admission):
    run_admission = isolated_admission
    run_admission.enable_run_drain(
        "test-attempt", reason="release", candidate_id="candidate-v1"
    )

    with pytest.raises(run_admission.RunAdmissionConflict):
        run_admission.disable_run_drain("other-attempt")

    assert run_admission.runtime_admission_snapshot()["draining"] is True
    assert run_admission.disable_run_drain("test-attempt") is True
    assert run_admission.runtime_admission_snapshot()["draining"] is False


def test_health_snapshot_reports_drain_and_runtime_counts(isolated_admission):
    from api import config

    run_admission = isolated_admission
    with config.STREAMS_LOCK:
        config.STREAMS["health-stream"] = queue.Queue()
    config.register_active_run(
        "health-stream", session_id="health-session", phase="running"
    )
    run_admission.enable_run_drain(
        "test-attempt", reason="release", candidate_id="candidate-v1"
    )

    snapshot = run_admission.runtime_admission_snapshot()

    assert snapshot == {
        "active_runs": 1,
        "active_streams": 1,
        "drain_attempt_id": "test-attempt",
        "drain_candidate_id": "candidate-v1",
        "drain_state_valid": True,
        "draining": True,
        "in_process_shutdown": False,
    }
