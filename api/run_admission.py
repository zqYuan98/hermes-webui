"""Atomic, persistent admission barrier for WebUI agent turns.

The release supervisor writes a persistent drain marker through this module.
Every real turn admission holds the same thread/process lock from its drain
check through publication in ``STREAMS`` and ``ACTIVE_RUNS``.  Therefore, once
``enable_run_drain()`` returns, every pre-existing admission is count-visible
and every later admission is rejected.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

try:  # Unix production path (Linux/macOS/WSL).
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is thread-local only.
    fcntl = None

from api.config import STATE_DIR

_MARKER_NAME = "run-drain.json"
_LOCK_NAME = "run-admission.lock"
_MARKER_VERSION = 1
_MAX_MARKER_BYTES = 16 * 1024
_GATE = threading.RLock()
_GATE_LOCAL = threading.local()
_IN_PROCESS_SHUTDOWN = threading.Event()
_SHUTDOWN_REQUEST_GUARD = threading.Lock()


class RunAdmissionRejected(RuntimeError):
    """Raised when a turn is attempted while the process is draining."""

    def __init__(self, payload: dict):
        self.payload = dict(payload)
        super().__init__(str(self.payload.get("error") or "service is draining"))


class RunAdmissionConflict(RuntimeError):
    """Raised when a different release attempt owns the persistent drain."""


def run_drain_marker_path() -> Path:
    return Path(STATE_DIR) / _MARKER_NAME


def run_admission_lock_path() -> Path:
    return Path(STATE_DIR) / _LOCK_NAME


def _bounded_text(value, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum or any(ord(ch) < 32 for ch in text):
        raise ValueError(f"{field} is invalid")
    return text


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_state() -> dict:
    return {
        "draining": True,
        "valid": False,
        "attempt_id": None,
        "candidate_id": None,
        "reason": None,
        "enabled_at": None,
    }


def _not_draining_state() -> dict:
    return {
        "draining": False,
        "valid": True,
        "attempt_id": None,
        "candidate_id": None,
        "reason": None,
        "enabled_at": None,
    }


def _read_marker_locked() -> dict:
    path = run_drain_marker_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _not_draining_state()
    except OSError:
        return _invalid_state()

    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_MARKER_BYTES:
        return _invalid_state()

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return _invalid_state()
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_MARKER_BYTES:
            return _invalid_state()
        chunks = []
        remaining = _MAX_MARKER_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError:
        return _invalid_state()
    finally:
        os.close(fd)

    if not raw or len(raw) > _MAX_MARKER_BYTES:
        return _invalid_state()
    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(payload, dict):
            raise ValueError("marker must be an object")
        if set(payload) != {
            "attempt_id",
            "candidate_id",
            "draining",
            "enabled_at",
            "reason",
            "version",
        }:
            raise ValueError("unexpected marker fields")
        if payload.get("version") != _MARKER_VERSION or payload.get("draining") is not True:
            raise ValueError("unsupported marker state")
        attempt_id = _bounded_text(payload.get("attempt_id"), field="attempt_id", maximum=128)
        candidate_id = _bounded_text(payload.get("candidate_id"), field="candidate_id", maximum=256)
        reason = _bounded_text(payload.get("reason"), field="reason", maximum=256)
        enabled_at = float(payload.get("enabled_at"))
        if not enabled_at or enabled_at < 0:
            raise ValueError("enabled_at is invalid")
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return _invalid_state()

    return {
        "draining": True,
        "valid": True,
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "reason": reason,
        "enabled_at": enabled_at,
    }


def _fsync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path.parent, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_marker_locked(payload: dict) -> None:
    path = run_drain_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(raw) > _MAX_MARKER_BYTES:
        raise ValueError("run drain marker exceeds size limit")

    temp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(temp, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write for run drain marker")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        finally:
            os.close(fd)
        raise
    else:
        os.close(fd)

    try:
        os.replace(temp, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        _fsync_parent(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _exclusive_gate() -> Iterator[None]:
    """Acquire the process-wide gate, re-entrantly within one request thread."""
    with _GATE:
        depth = int(getattr(_GATE_LOCAL, "depth", 0) or 0)
        if depth:
            _GATE_LOCAL.depth = depth + 1
            try:
                yield
            finally:
                _GATE_LOCAL.depth = depth
            return

        lock_path = run_admission_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            _GATE_LOCAL.depth = 1
            try:
                yield
            finally:
                _GATE_LOCAL.depth = 0
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _rejection_payload(state: dict | None = None) -> dict:
    state = state or {}
    payload = {
        "error": "WebUI is draining for a managed restart; retry after service recovery",
        "type": "service_draining",
        "retryable": True,
    }
    if state.get("valid") is False:
        payload["drain_state_valid"] = False
    return payload


@contextlib.contextmanager
def run_admission_transaction() -> Iterator[None]:
    """Reject drains, then hold the gate through count-visible admission."""
    with _exclusive_gate():
        state = _read_marker_locked()
        if _IN_PROCESS_SHUTDOWN.is_set() or state.get("draining"):
            raise RunAdmissionRejected(_rejection_payload(state))
        yield


def enable_run_drain(attempt_id: str, *, reason: str, candidate_id: str) -> dict:
    """Persist a drain marker after every in-flight admission is published."""
    attempt_id = _bounded_text(attempt_id, field="attempt_id", maximum=128)
    candidate_id = _bounded_text(candidate_id, field="candidate_id", maximum=256)
    reason = _bounded_text(reason, field="reason", maximum=256)
    with _exclusive_gate():
        current = _read_marker_locked()
        if current.get("draining"):
            if (
                current.get("valid")
                and current.get("attempt_id") == attempt_id
                and current.get("candidate_id") == candidate_id
            ):
                return dict(current)
            owner = current.get("attempt_id") or "invalid-marker"
            raise RunAdmissionConflict(f"run drain is already owned by {owner}")
        payload = {
            "version": _MARKER_VERSION,
            "draining": True,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "reason": reason,
            "enabled_at": time.time(),
        }
        _write_marker_locked(payload)
        return _read_marker_locked()


def disable_run_drain(attempt_id: str, *, allow_missing: bool = False) -> bool:
    """Clear the marker only when the caller still owns the drain attempt."""
    attempt_id = _bounded_text(attempt_id, field="attempt_id", maximum=128)
    with _exclusive_gate():
        current = _read_marker_locked()
        if not current.get("draining"):
            if allow_missing:
                return False
            raise RunAdmissionConflict("run drain marker is not present")
        if not current.get("valid"):
            raise RunAdmissionConflict("run drain marker is invalid; refusing unsafe removal")
        if current.get("attempt_id") != attempt_id:
            raise RunAdmissionConflict(
                f"run drain is owned by {current.get('attempt_id') or 'unknown'}"
            )
        path = run_drain_marker_path()
        path.unlink()
        _fsync_parent(path)
        return True


def begin_in_process_shutdown() -> None:
    """Atomically close admission before the HTTP accept loop is stopped."""
    with _exclusive_gate():
        _IN_PROCESS_SHUTDOWN.set()


def request_http_shutdown(httpd, shutdown_requested: threading.Event) -> bool:
    """Request signal-safe shutdown without waiting on the admission gate.

    The signal-handler path only claims the idempotence event and starts a
    worker.  The worker then closes admission under the shared gate before
    stopping the HTTP accept loop, so no new turn can be admitted after the
    server shutdown begins.
    """
    if not _SHUTDOWN_REQUEST_GUARD.acquire(blocking=False):
        return False
    try:
        if shutdown_requested.is_set():
            return False
        shutdown_requested.set()

        def _close_admission_then_shutdown() -> None:
            begin_in_process_shutdown()
            httpd.shutdown()

        threading.Thread(
            target=_close_admission_then_shutdown,
            name="webui-sigterm-shutdown",
            daemon=True,
        ).start()
        return True
    except BaseException:
        shutdown_requested.clear()
        raise
    finally:
        _SHUTDOWN_REQUEST_GUARD.release()


def reset_in_process_shutdown_for_tests() -> None:
    with _exclusive_gate():
        _IN_PROCESS_SHUTDOWN.clear()


def publish_admitted_stream(
    stream_id: str,
    stream,
    *,
    session_id: str,
    **metadata,
) -> None:
    """Publish SSE and starting-run state in the admission lock domain."""
    from api import config

    with run_admission_transaction():
        config.register_stream_owner(stream_id, session_id)
        with config.STREAMS_LOCK:
            if stream_id in config.STREAMS:
                config.unregister_stream_owner(stream_id)
                raise RuntimeError(f"stream already exists: {stream_id}")
            config.STREAMS[stream_id] = stream
        try:
            run_metadata = dict(metadata)
            run_metadata.setdefault("phase", "admitted")
            run_metadata.setdefault("started_at", time.time())
            config.register_active_run(
                stream_id,
                session_id=session_id,
                **run_metadata,
            )
        except BaseException:
            with config.STREAMS_LOCK:
                config.STREAMS.pop(stream_id, None)
            config.unregister_stream_owner(stream_id)
            raise


def rollback_admitted_stream(stream_id: str) -> None:
    """Remove publication if the worker thread could not be started."""
    from api import config

    with config.STREAMS_LOCK:
        config.STREAMS.pop(stream_id, None)
    config.unregister_active_run(stream_id)
    config.unregister_stream_owner(stream_id)


def register_admitted_run(stream_id: str, *, session_id: str, **metadata) -> None:
    """Register a non-SSE run while holding the same admission barrier."""
    from api import config

    with run_admission_transaction():
        run_metadata = dict(metadata)
        run_metadata.setdefault("phase", "admitted")
        run_metadata.setdefault("started_at", time.time())
        config.register_active_run(stream_id, session_id=session_id, **run_metadata)


def runtime_admission_snapshot() -> dict:
    """Return drain and 0/0 counters from the admission synchronization domain."""
    from api import config

    with _exclusive_gate():
        state = _read_marker_locked()
        with config.STREAMS_LOCK:
            active_streams = len(config.STREAMS)
        with config.ACTIVE_RUNS_LOCK:
            active_runs = len(config.ACTIVE_RUNS)
        return {
            "active_runs": active_runs,
            "active_streams": active_streams,
            "drain_attempt_id": state.get("attempt_id"),
            "drain_candidate_id": state.get("candidate_id"),
            "drain_state_valid": bool(state.get("valid")),
            "draining": bool(state.get("draining") or _IN_PROCESS_SHUTDOWN.is_set()),
            "in_process_shutdown": _IN_PROCESS_SHUTDOWN.is_set(),
        }
