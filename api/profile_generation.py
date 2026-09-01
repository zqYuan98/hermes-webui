"""Stable, non-reusable identity for a Hermes Profile incarnation.

A named Profile path can be deleted and recreated with the same name.  The path
therefore cannot, by itself, identify the Profile that a browser page or queued
request originally observed.  Each named Profile receives a private UUID file
in its Profile root.  The file is outside ``skills/`` and is never loaded into
Agent context.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import uuid
from pathlib import Path

from api.paths import _fsync_directory

PROFILE_GENERATION_FILENAME = ".webui-profile-generation"
DEFAULT_PROFILE_GENERATION = "default-profile"
_MAX_GENERATION_BYTES = 128


class ProfileGenerationError(RuntimeError):
    """The persisted Profile generation could not be trusted."""


class ProfileGenerationMismatch(ProfileGenerationError):
    """A request belongs to a different incarnation of the same Profile path."""


def is_named_profile_home(profile_home: Path) -> bool:
    """Return whether *profile_home* has the standard ``profiles/<name>`` shape."""
    home = Path(profile_home).expanduser()
    return home.parent.name == "profiles"


def profile_generation_path(profile_home: Path) -> Path:
    return Path(profile_home).expanduser() / PROFILE_GENERATION_FILENAME


def profile_home_identity(profile_home: Path) -> tuple[int, int]:
    """Return the no-follow directory identity for one Profile incarnation."""
    home = Path(profile_home).expanduser()
    info = os.stat(home, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ProfileGenerationError("Profile home must be a real directory")
    return int(info.st_dev), int(info.st_ino)


def _validate_generation_text(raw: str) -> str:
    text = str(raw or "").strip()
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProfileGenerationError("Profile generation is malformed") from exc
    canonical = str(parsed)
    if text != canonical:
        raise ProfileGenerationError("Profile generation is not canonical")
    return canonical


def read_profile_generation(profile_home: Path) -> str:
    """Read and validate an existing named Profile generation, fail-closed."""
    path = profile_generation_path(profile_home)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ProfileGenerationError("Profile generation cannot be a symlink") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ProfileGenerationError("Profile generation must be a regular file")
        if info.st_size <= 0 or info.st_size > _MAX_GENERATION_BYTES:
            raise ProfileGenerationError("Profile generation has an invalid size")
        chunks: list[bytes] = []
        remaining = _MAX_GENERATION_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 128))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_GENERATION_BYTES:
            raise ProfileGenerationError("Profile generation is too large")
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProfileGenerationError("Profile generation is not ASCII") from exc
        return _validate_generation_text(text)
    finally:
        os.close(fd)


def _publish_new_generation(profile_home: Path, generation: str) -> None:
    """Publish *generation* exactly once without replacing an existing token."""
    home = Path(profile_home).expanduser()
    if not home.is_dir():
        raise FileNotFoundError(f"Profile home does not exist: {home}")
    destination = profile_generation_path(home)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(home), prefix=f".{PROFILE_GENERATION_FILENAME}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        payload = (generation + "\n").encode("ascii")
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
        os.close(fd)
        fd = -1
        try:
            os.link(tmp_path, destination, follow_symlinks=False)
        except FileExistsError:
            return
        _fsync_directory(home)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def ensure_profile_generation(profile_home: Path) -> str:
    """Return a named Profile generation, atomically creating it when absent."""
    home = Path(profile_home).expanduser()
    profile_home_identity(home)
    try:
        return read_profile_generation(home)
    except FileNotFoundError:
        candidate = str(uuid.uuid4())
        _publish_new_generation(home, candidate)
        return read_profile_generation(home)


def reset_profile_generation(profile_home: Path) -> str:
    """Assign a fresh generation to a newly-created Profile directory.

    Profile cloning may copy hidden files.  Removing any copied token before the
    first publication prevents the new Profile from inheriting its source
    incarnation identity.  Callers must hold the Profile transaction lock and
    must use this only for a directory that was just created by that operation.
    """
    home = Path(profile_home).expanduser()
    profile_home_identity(home)
    path = profile_generation_path(home)
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode):
            raise ProfileGenerationError(
                "Copied Profile generation is not a regular file"
            )
        path.unlink()
        _fsync_directory(home)
    return ensure_profile_generation(home)


def generation_for_profile_home(
    profile_home: Path, *, named: bool | None = None
) -> str:
    """Return the stable root sentinel or a named Profile's persisted UUID."""
    home = Path(profile_home).expanduser()
    if named is None:
        named = is_named_profile_home(home)
    if not named:
        return DEFAULT_PROFILE_GENERATION
    return ensure_profile_generation(home)


def require_profile_generation(
    profile_home: Path,
    expected_generation: str,
    *,
    named: bool | None = None,
) -> str:
    """Compare the current incarnation with a request's captured generation."""
    expected = str(expected_generation or "").strip()
    current = generation_for_profile_home(profile_home, named=named)
    if not expected or expected != current:
        raise ProfileGenerationMismatch(
            "Active profile generation changed; reload and retry"
        )
    return current
