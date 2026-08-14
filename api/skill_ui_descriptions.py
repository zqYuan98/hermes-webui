"""WebUI-only human descriptions for Hermes skills.

The sidecar lives in the WebUI state directory, never under a Hermes ``skills/``
root.  Agent skill discovery and ``skill_view`` therefore cannot load these
strings.  Routes must also opt in before returning them so ordinary skill API
consumers keep the runtime-only payload.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

from api.config import STATE_DIR
from api.paths import _fsync_directory

logger = logging.getLogger(__name__)

SIDECAR_FILENAME = "skill-ui-descriptions.json"
LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
MAX_UI_DESCRIPTION_CHARS = 2000
_MAX_SIDECAR_BYTES = 2 * 1024 * 1024
_LOCK = threading.RLock()
_BINDING_CONTEXT: ContextVar[tuple[str, Path] | None] = ContextVar(
    "skill_ui_binding_context", default=None
)


class SkillUIDescriptionStale(RuntimeError):
    """Stored UI metadata is not bound to the currently visible Skill bytes."""


class _SkillUIDescriptionSnapshot(str):
    """String-compatible rollback value carrying one entry's v2 binding state."""

    def __new__(
        cls,
        value: str,
        *,
        profile_generation: str | None = None,
        runtime_digest: str | None = None,
        legacy_unbound: bool = False,
    ):
        instance = super().__new__(cls, value)
        instance.profile_generation = profile_generation
        instance.runtime_digest = runtime_digest
        instance.legacy_unbound = bool(legacy_unbound)
        return instance


class _ProfileDescriptionsSnapshot(dict):
    """Dict-compatible rollback value carrying one Profile's v2 sidecar state."""

    def __init__(self, descriptions: dict[str, str], *, binding, legacy_unbound):
        super().__init__(descriptions)
        self.binding = binding
        self.legacy_unbound = list(legacy_unbound or [])


@contextmanager
def _shared_profile_mutation_locks(profile_keys):
    """Load the Agent's authoritative sorted Profile locks only when needed.

    WebUI can still start when the Agent checkout is absent, but a Profile
    transaction must never silently fall back to a second lock domain.
    """
    try:
        from hermes_constants import profile_mutation_locks
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_constants":
            raise
        raise RuntimeError(
            "Hermes Agent shared Profile mutation lock is unavailable"
        ) from exc

    with profile_mutation_locks(profile_keys):
        yield


@contextmanager
def _shared_profile_mutation_lock(profile_key: str):
    """Backward-compatible single-Profile bridge to the shared lock domain."""
    with _shared_profile_mutation_locks((profile_key,)):
        yield


def sidecar_path() -> Path:
    return Path(STATE_DIR) / SIDECAR_FILENAME


@contextmanager
def _portable_file_lock(lock_path: Path):
    """Take one exclusive advisory lock on POSIX or Windows."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b", buffering=0) as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return
        if msvcrt is not None:
            if os.fstat(lock_file.fileno()).st_size == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return
        yield


@contextmanager
def _sidecar_file_lock():
    """Serialize sidecar read-modify-write cycles across worker processes."""
    path = sidecar_path()
    with _portable_file_lock(path.with_name(f".{path.name}.lock")):
        yield


@contextmanager
def bind_ui_description_to_runtime(profile_generation: str, runtime_path: Path):
    """Bind nested sidecar writes to one immutable runtime revision."""
    generation = str(profile_generation or "").strip()
    if not generation:
        raise ValueError("profile generation is required")
    token = _BINDING_CONTEXT.set((generation, Path(runtime_path)))
    try:
        yield
    finally:
        _BINDING_CONTEXT.reset(token)


@contextmanager
def skill_transaction(profile_key: str):
    """Serialize all Profile mutations with Agent, CLI, Hub, sync, and curator.

    The authoritative lock lives in ``hermes_constants`` outside the deletable
    Profile tree.  The sidecar's narrower global RMW lock is always acquired
    underneath this shared Profile lock.
    """
    key = str(profile_key or "").strip()
    if not key:
        raise ValueError("profile key is required")
    with _shared_profile_mutation_lock(key):
        yield


@contextmanager
def profile_transaction(profile_keys):
    """Serialize one lifecycle operation across every Profile it can mutate.

    Canonicalization, deduplication, deterministic ordering, and the shared
    timeout budget remain owned by the Agent's authoritative helper.
    """
    if isinstance(profile_keys, (str, os.PathLike)):
        raw_keys = (profile_keys,)
    else:
        raw_keys = tuple(profile_keys)
    keys = tuple(str(key or "").strip() for key in raw_keys)
    if not keys or any(not key for key in keys):
        raise ValueError("at least one profile key is required")
    with _shared_profile_mutation_locks(keys):
        yield


def _profile_sidecar_mutation(func):
    """Prevent direct sidecar writers from bypassing the Profile lock."""
    @wraps(func)
    def _locked(profile_key, *args, **kwargs):
        key = str(profile_key or "").strip()
        if not key:
            raise ValueError("profile key is required")
        with _shared_profile_mutation_lock(key):
            return func(key, *args, **kwargs)

    return _locked


def _empty_payload() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "profiles": {},
        "bindings": {},
        "legacy_unbound": {},
    }


def _validated_payload(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("skill UI descriptions sidecar must contain a JSON object")
    source_version = value.get("version")
    if source_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        raise ValueError("unsupported skill UI descriptions sidecar version")
    raw_profiles = value.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise ValueError("skill UI descriptions sidecar profiles must be an object")

    profiles: dict[str, dict[str, str]] = {}
    for profile_key, raw_descriptions in raw_profiles.items():
        if not isinstance(profile_key, str) or not profile_key:
            raise ValueError("skill UI descriptions sidecar has an invalid profile key")
        if not isinstance(raw_descriptions, dict):
            raise ValueError("skill UI descriptions profile entries must be objects")
        descriptions: dict[str, str] = {}
        for skill_name, description in raw_descriptions.items():
            if not isinstance(skill_name, str) or not skill_name:
                raise ValueError(
                    "skill UI descriptions sidecar has an invalid skill name"
                )
            if not isinstance(description, str):
                raise ValueError("skill UI descriptions must be strings")
            if len(description) > MAX_UI_DESCRIPTION_CHARS:
                raise ValueError("skill UI description exceeds the supported size")
            if description:
                descriptions[skill_name] = description
        if descriptions:
            profiles[profile_key] = descriptions

    # v1 contained only strings. Preserve those entries byte-for-byte, but mark
    # their Profile buckets explicitly unbound until a management read verifies
    # the stable default Profile or a controlled save binds current bytes.
    raw_legacy_unbound = (
        {profile_key: sorted(descriptions) for profile_key, descriptions in profiles.items()}
        if source_version == LEGACY_SCHEMA_VERSION
        else value.get("legacy_unbound", {})
    )
    if not isinstance(raw_legacy_unbound, dict):
        raise ValueError("skill UI descriptions legacy bindings must be an object")
    legacy_unbound: dict[str, list[str]] = {}
    for profile_key, raw_names in raw_legacy_unbound.items():
        if not isinstance(profile_key, str) or not profile_key:
            raise ValueError("skill UI descriptions legacy profile key is invalid")
        if not isinstance(raw_names, list) or any(
            not isinstance(name, str) or not name for name in raw_names
        ):
            raise ValueError("skill UI descriptions legacy skill list is invalid")
        names = sorted(
            name for name in set(raw_names) if name in profiles.get(profile_key, {})
        )
        if names:
            legacy_unbound[profile_key] = names

    raw_bindings = value.get("bindings", {})
    if not isinstance(raw_bindings, dict):
        raise ValueError("skill UI descriptions sidecar bindings must be an object")
    bindings: dict[str, dict] = {}
    for profile_key, raw_binding in raw_bindings.items():
        if not isinstance(profile_key, str) or not profile_key:
            raise ValueError("skill UI descriptions binding has an invalid profile key")
        if not isinstance(raw_binding, dict):
            raise ValueError("skill UI descriptions profile binding must be an object")
        generation = raw_binding.get("profile_generation")
        raw_skills = raw_binding.get("skills", {})
        if not isinstance(generation, str) or not generation.strip():
            raise ValueError("skill UI descriptions binding has an invalid generation")
        if not isinstance(raw_skills, dict):
            raise ValueError("skill UI descriptions binding skills must be an object")
        skills: dict[str, str] = {}
        for skill_name, digest in raw_skills.items():
            if not isinstance(skill_name, str) or not skill_name:
                raise ValueError("skill UI descriptions binding has an invalid skill name")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
            ):
                raise ValueError("skill UI descriptions binding has an invalid digest")
            if skill_name in profiles.get(profile_key, {}):
                skills[skill_name] = digest
        if profile_key in profiles and skills:
            bindings[profile_key] = {
                "profile_generation": generation.strip(),
                "skills": skills,
            }
    return {
        "version": SCHEMA_VERSION,
        "profiles": profiles,
        "bindings": bindings,
        "legacy_unbound": legacy_unbound,
    }


def _runtime_sha256(runtime_path: Path) -> str:
    """Hash one regular, non-symlink Skill index file."""
    path = Path(runtime_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Skill runtime path must be a regular file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _drop_profile_binding(payload: dict, profile_key: str) -> None:
    payload["bindings"].pop(profile_key, None)


def _drop_legacy_skill(payload: dict, profile_key: str, skill_name: str) -> None:
    names = payload["legacy_unbound"].get(profile_key)
    if not isinstance(names, list):
        return
    remaining = [name for name in names if name != skill_name]
    if remaining:
        payload["legacy_unbound"][profile_key] = remaining
    else:
        payload["legacy_unbound"].pop(profile_key, None)


def _drop_profile_legacy(payload: dict, profile_key: str) -> None:
    payload["legacy_unbound"].pop(profile_key, None)


def _drop_skill_binding(payload: dict, profile_key: str, skill_name: str) -> None:
    profile_binding = payload["bindings"].get(profile_key)
    if not isinstance(profile_binding, dict):
        return
    skills = profile_binding.get("skills")
    if isinstance(skills, dict):
        skills.pop(skill_name, None)
    if not skills:
        _drop_profile_binding(payload, profile_key)


def _set_skill_binding(
    payload: dict,
    profile_key: str,
    skill_name: str,
    *,
    profile_generation: str,
    runtime_digest: str,
) -> None:
    generation = str(profile_generation or "").strip()
    if not generation:
        raise ValueError("profile generation is required for a Skill UI binding")
    current = payload["bindings"].get(profile_key)
    if not isinstance(current, dict) or current.get("profile_generation") != generation:
        current = {"profile_generation": generation, "skills": {}}
        payload["bindings"][profile_key] = current
    current["skills"][skill_name] = runtime_digest
    _drop_legacy_skill(payload, profile_key, skill_name)


def _migrate_renamed_profile_bucket(
    payload: dict, profile_key: str, profile_generation: str
) -> bool:
    """Move one orphaned same-generation bucket after a Profile rename.

    A generation UUID is preserved by rename but stripped from clone/import/install.
    Migration is therefore allowed only when the destination has no bucket, exactly
    one old binding carries the generation, and that old Profile path no longer
    exists. Duplicate live generations fail closed instead of guessing.
    """
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    if (
        profile_generation == DEFAULT_PROFILE_GENERATION
        or profile_key in payload["profiles"]
    ):
        return False
    candidates = []
    for old_key, binding in payload["bindings"].items():
        if old_key == profile_key or not isinstance(binding, dict):
            continue
        if binding.get("profile_generation") != profile_generation:
            continue
        try:
            old_path_exists = Path(old_key).exists()
        except OSError:
            old_path_exists = True
        if not old_path_exists and old_key in payload["profiles"]:
            candidates.append(old_key)
    if len(candidates) != 1:
        return False

    old_key = candidates[0]
    payload["profiles"][profile_key] = payload["profiles"].pop(old_key)
    payload["bindings"][profile_key] = payload["bindings"].pop(old_key)
    legacy_names = payload["legacy_unbound"].pop(old_key, None)
    if legacy_names:
        payload["legacy_unbound"][profile_key] = legacy_names
    return True


def _load_unlocked(*, strict: bool) -> dict:
    path = sidecar_path()
    try:
        if not path.exists():
            return _empty_payload()
        if path.is_symlink():
            raise ValueError("skill UI descriptions sidecar cannot be a symlink")
        if path.stat().st_size > _MAX_SIDECAR_BYTES:
            raise ValueError("skill UI descriptions sidecar is too large")
        return _validated_payload(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        if strict:
            raise
        logger.warning("Could not read skill UI descriptions from %s: %s", path, exc)
        return _empty_payload()


def _atomic_write_unlocked(payload: dict) -> None:
    path = sidecar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("skill UI descriptions sidecar cannot be a symlink")

    normalized = _validated_payload(payload)
    serialized = (
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if len(serialized.encode("utf-8")) > _MAX_SIDECAR_BYTES:
        raise ValueError("skill UI descriptions sidecar is too large")
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_profile_descriptions(
    profile_key: str, *, strict: bool = False
) -> dict[str, str]:
    """Read raw UI text for compatibility-only callers.

    Management routes use :func:`read_profile_description_states` so separately
    persisted text is never paired with a different Profile incarnation or
    different ``SKILL.md`` bytes after a crash.
    """
    key = str(profile_key or "").strip()
    if not key:
        return {}
    with _LOCK:
        payload = _load_unlocked(strict=strict)
        descriptions = payload["profiles"].get(key, {})
        return dict(descriptions) if isinstance(descriptions, dict) else {}


@_profile_sidecar_mutation
def read_profile_description_states(
    profile_key: str,
    runtime_paths: dict[str, Path],
    *,
    profile_generation: str,
    strict: bool = False,
) -> dict[str, dict[str, object]]:
    """Return crash-reconciled UI metadata for explicit management reads.

    Runtime content is authoritative. A description is exposed only when its
    binding matches both the current Profile generation and the SHA-256 of the
    resolved Skill index file. Legacy unbound entries are migrated only when
    the sidecar publication is not older than the runtime file; otherwise they
    remain visible solely as an explicit ``stale`` state with empty text.
    """
    key = str(profile_key or "").strip()
    generation = str(profile_generation or "").strip()
    if not key:
        return {}
    if not generation:
        raise ValueError("profile generation is required")
    normalized_paths = {
        str(name or "").strip(): Path(path)
        for name, path in dict(runtime_paths or {}).items()
        if str(name or "").strip()
    }
    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=strict)
            renamed = _migrate_renamed_profile_bucket(payload, key, generation)
            descriptions = dict(payload["profiles"].get(key, {}))
            profile_binding = payload["bindings"].get(key)
            binding_generation = (
                profile_binding.get("profile_generation")
                if isinstance(profile_binding, dict)
                else None
            )
            bound_skills = (
                profile_binding.get("skills", {})
                if isinstance(profile_binding, dict)
                else {}
            )
            replaced_incarnation = (
                profile_binding is not None and binding_generation != generation
            )
            migrated = False
            states: dict[str, dict[str, object]] = {}
            for name, runtime_path in normalized_paths.items():
                description = descriptions.get(name, "")
                if not description:
                    states[name] = {"ui_description": "", "stale": False}
                    continue
                try:
                    runtime_stat = os.stat(runtime_path, follow_symlinks=False)
                    if not stat.S_ISREG(runtime_stat.st_mode):
                        raise ValueError("Skill runtime path must be a regular file")
                    runtime_digest = _runtime_sha256(runtime_path)
                except Exception:
                    if strict:
                        raise
                    states[name] = {"ui_description": "", "stale": True}
                    continue

                expected_digest = (
                    bound_skills.get(name)
                    if binding_generation == generation and isinstance(bound_skills, dict)
                    else None
                )
                legacy_names = payload["legacy_unbound"].get(key, [])
                legacy_migration_allowed = (
                    generation == "default-profile" and name in legacy_names
                )
                if expected_digest is not None:
                    stale = expected_digest != runtime_digest
                elif replaced_incarnation:
                    stale = True
                elif legacy_migration_allowed:
                    # The default Profile path is not reusable. Existing v1
                    # strings can therefore be bound one-by-one to the current
                    # runtime without risking same-name incarnation inheritance.
                    _set_skill_binding(
                        payload,
                        key,
                        name,
                        profile_generation=generation,
                        runtime_digest=runtime_digest,
                    )
                    migrated = True
                    stale = False
                else:
                    # New unbound values and all named-Profile v1 values are
                    # ambiguous after a crash or same-name recreation.
                    stale = True
                states[name] = {
                    "ui_description": "" if stale else description,
                    "stale": stale,
                }
            if replaced_incarnation:
                payload["profiles"].pop(key, None)
                _drop_profile_binding(payload, key)
                _drop_profile_legacy(payload, key)
            if renamed or migrated or replaced_incarnation:
                _atomic_write_unlocked(payload)
            return states


@_profile_sidecar_mutation
def prepare_ui_description_for_runtime_change(
    profile_key: str,
    skill_name: str,
    *,
    profile_generation: str,
    runtime_path: Path | None,
) -> str:
    """Fence existing UI text before publishing different runtime bytes.

    Existing v2 bindings are left untouched: after the runtime replacement they
    naturally become stale until a new description is published. A verified v1
    default-Profile entry is first bound to the currently visible runtime. If no
    runtime exists yet, every old binding/legacy migration claim is invalidated
    so a recreated Skill cannot inherit text from a deleted object.
    """
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    key = str(profile_key or "").strip()
    name = str(skill_name or "").strip()
    generation = str(profile_generation or "").strip()
    if not key:
        raise ValueError("profile key is required")
    if not name:
        raise ValueError("skill name is required")
    if not generation:
        raise ValueError("profile generation is required")

    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=True)
            description = str(payload["profiles"].get(key, {}).get(name, ""))
            if not description:
                return ""

            changed = False
            safe_description = ""
            if runtime_path is None:
                profile_binding = payload["bindings"].get(key)
                before_binding = (
                    dict(profile_binding.get("skills", {}))
                    if isinstance(profile_binding, dict)
                    else None
                )
                before_legacy = list(payload["legacy_unbound"].get(key, []))
                _drop_skill_binding(payload, key, name)
                _drop_legacy_skill(payload, key, name)
                profile_binding = payload["bindings"].get(key)
                after_binding = (
                    dict(profile_binding.get("skills", {}))
                    if isinstance(profile_binding, dict)
                    else None
                )
                changed = (
                    before_binding != after_binding
                    or before_legacy != payload["legacy_unbound"].get(key, [])
                )
            else:
                runtime_digest = _runtime_sha256(Path(runtime_path))
                profile_binding = payload["bindings"].get(key)
                binding_generation = (
                    profile_binding.get("profile_generation")
                    if isinstance(profile_binding, dict)
                    else None
                )
                bound_skills = (
                    profile_binding.get("skills", {})
                    if isinstance(profile_binding, dict)
                    else {}
                )
                expected_digest = (
                    bound_skills.get(name)
                    if binding_generation == generation and isinstance(bound_skills, dict)
                    else None
                )
                legacy_names = payload["legacy_unbound"].get(key, [])
                if expected_digest == runtime_digest:
                    safe_description = description
                elif (
                    expected_digest is None
                    and generation == DEFAULT_PROFILE_GENERATION
                    and name in legacy_names
                ):
                    _set_skill_binding(
                        payload,
                        key,
                        name,
                        profile_generation=generation,
                        runtime_digest=runtime_digest,
                    )
                    changed = True
                    safe_description = description

            if changed:
                _atomic_write_unlocked(payload)
            return safe_description


def get_ui_description_state(
    profile_key: str,
    skill_name: str,
    *,
    profile_generation: str,
    runtime_path: Path,
    strict: bool = False,
) -> dict[str, object]:
    name = str(skill_name or "").strip()
    if not name:
        return {"ui_description": "", "stale": False}
    return read_profile_description_states(
        profile_key,
        {name: Path(runtime_path)},
        profile_generation=profile_generation,
        strict=strict,
    ).get(name, {"ui_description": "", "stale": False})


def get_ui_description(
    profile_key: str,
    skill_name: str,
    *,
    strict: bool = False,
    profile_generation: str | None = None,
    runtime_path: Path | None = None,
) -> str:
    if profile_generation is None and runtime_path is None:
        return read_profile_descriptions(profile_key, strict=strict).get(
            str(skill_name or ""), ""
        )
    if profile_generation is None or runtime_path is None:
        raise ValueError("profile generation and runtime path must be provided together")
    state = get_ui_description_state(
        profile_key,
        skill_name,
        profile_generation=profile_generation,
        runtime_path=runtime_path,
        strict=strict,
    )
    if state.get("stale"):
        raise SkillUIDescriptionStale(
            "Skill UI description is stale after an interrupted or external runtime update"
        )
    return str(state.get("ui_description") or "")


@_profile_sidecar_mutation
def set_ui_description(profile_key: str, skill_name: str, description: str) -> str:
    key = str(profile_key or "").strip()
    name = str(skill_name or "").strip()
    text = str(description or "").strip()
    if not key:
        raise ValueError("profile key is required")
    if not name:
        raise ValueError("skill name is required")
    if len(text) > MAX_UI_DESCRIPTION_CHARS:
        raise ValueError(
            f"UI description must be {MAX_UI_DESCRIPTION_CHARS} characters or fewer"
        )

    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=True)
            profiles = payload["profiles"]
            descriptions = dict(profiles.get(key, {}))
            binding_context = _BINDING_CONTEXT.get()
            if text:
                descriptions[name] = text
                profiles[key] = descriptions
                if binding_context is not None:
                    generation, runtime_path = binding_context
                    _set_skill_binding(
                        payload,
                        key,
                        name,
                        profile_generation=generation,
                        runtime_digest=_runtime_sha256(runtime_path),
                    )
                else:
                    # A direct compatibility write cannot claim to describe a
                    # particular runtime revision. Remove every prior claim so
                    # explicit management reads fail closed until re-bound.
                    _drop_skill_binding(payload, key, name)
                    _drop_legacy_skill(payload, key, name)
            else:
                descriptions.pop(name, None)
                _drop_skill_binding(payload, key, name)
                _drop_legacy_skill(payload, key, name)
                if descriptions:
                    profiles[key] = descriptions
                else:
                    profiles.pop(key, None)
            _atomic_write_unlocked(payload)
    return text


@_profile_sidecar_mutation
def pop_ui_description(profile_key: str, skill_name: str) -> str:
    """Remove and return one description, writing only when an entry existed."""
    key = str(profile_key or "").strip()
    name = str(skill_name or "").strip()
    if not key:
        raise ValueError("profile key is required")
    if not name:
        raise ValueError("skill name is required")

    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=True)
            profiles = payload["profiles"]
            descriptions = dict(profiles.get(key, {}))
            previous = descriptions.pop(name, "")
            if not previous:
                return ""
            profile_binding = payload["bindings"].get(key)
            binding_generation = (
                profile_binding.get("profile_generation")
                if isinstance(profile_binding, dict)
                else None
            )
            bound_skills = (
                profile_binding.get("skills", {})
                if isinstance(profile_binding, dict)
                else {}
            )
            snapshot = _SkillUIDescriptionSnapshot(
                previous,
                profile_generation=binding_generation,
                runtime_digest=(
                    bound_skills.get(name) if isinstance(bound_skills, dict) else None
                ),
                legacy_unbound=name in payload["legacy_unbound"].get(key, []),
            )
            _drop_skill_binding(payload, key, name)
            _drop_legacy_skill(payload, key, name)
            if descriptions:
                profiles[key] = descriptions
            else:
                profiles.pop(key, None)
            _atomic_write_unlocked(payload)
            return snapshot


@_profile_sidecar_mutation
def pop_profile_descriptions(profile_key: str) -> dict[str, str]:
    """Atomically remove and return one Profile's complete UI metadata bucket."""
    key = str(profile_key or "").strip()
    if not key:
        raise ValueError("profile key is required")

    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=True)
            previous = payload["profiles"].pop(key, {})
            if not previous:
                return {}
            snapshot = _ProfileDescriptionsSnapshot(
                previous,
                binding=payload["bindings"].get(key),
                legacy_unbound=payload["legacy_unbound"].get(key, []),
            )
            _drop_profile_binding(payload, key)
            _drop_profile_legacy(payload, key)
            _atomic_write_unlocked(payload)
            return snapshot


@_profile_sidecar_mutation
def restore_profile_descriptions(
    profile_key: str, descriptions: dict[str, str]
) -> None:
    """Restore a previously popped Profile bucket without merging stale values."""
    key = str(profile_key or "").strip()
    if not key:
        raise ValueError("profile key is required")
    if not isinstance(descriptions, dict):
        raise ValueError("profile descriptions must be an object")

    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=True)
            if descriptions:
                payload["profiles"][key] = dict(descriptions)
            else:
                payload["profiles"].pop(key, None)
            _drop_profile_binding(payload, key)
            _drop_profile_legacy(payload, key)
            if isinstance(descriptions, _ProfileDescriptionsSnapshot):
                if descriptions.binding:
                    payload["bindings"][key] = descriptions.binding
                if descriptions.legacy_unbound:
                    payload["legacy_unbound"][key] = list(
                        descriptions.legacy_unbound
                    )
            _atomic_write_unlocked(payload)


@_profile_sidecar_mutation
def restore_ui_description(
    profile_key: str, skill_name: str, description: str
) -> None:
    """Restore one popped entry, preserving v2 binding data when available."""
    key = str(profile_key or "").strip()
    name = str(skill_name or "").strip()
    text = str(description or "")
    if not key:
        raise ValueError("profile key is required")
    if not name:
        raise ValueError("skill name is required")
    if not text:
        return

    with _LOCK:
        with _sidecar_file_lock():
            payload = _load_unlocked(strict=True)
            descriptions = dict(payload["profiles"].get(key, {}))
            descriptions[name] = text
            payload["profiles"][key] = descriptions
            _drop_skill_binding(payload, key, name)
            _drop_legacy_skill(payload, key, name)
            if isinstance(description, _SkillUIDescriptionSnapshot):
                if description.profile_generation and description.runtime_digest:
                    _set_skill_binding(
                        payload,
                        key,
                        name,
                        profile_generation=description.profile_generation,
                        runtime_digest=description.runtime_digest,
                    )
                elif description.legacy_unbound:
                    names = set(payload["legacy_unbound"].get(key, []))
                    names.add(name)
                    payload["legacy_unbound"][key] = sorted(names)
            _atomic_write_unlocked(payload)


def delete_ui_description(profile_key: str, skill_name: str) -> None:
    pop_ui_description(profile_key, skill_name)
