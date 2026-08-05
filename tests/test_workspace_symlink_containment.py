import pytest

from api.workspace import (
    EscapeUploadAuthorizationExpiredError,
    authorize_escape_upload,
    authorize_escape_target,
    prepare_escape_upload,
    list_authorized_escape_dir,
    list_dir,
    open_anchored_create_fd,
    read_file_content,
    resolve_authorized_escape_request,
    resolve_authorized_escape_upload_request,
    safe_resolve_ws,
)


def test_anchored_create_rejects_replaced_root_identity(tmp_path):
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    original_stat = original.stat()
    expected = (int(original_stat.st_dev), int(original_stat.st_ino))

    original.rmdir()
    replacement.rename(original)

    with pytest.raises(FileNotFoundError):
        open_anchored_create_fd(
            original,
            original / "blocked.txt",
            expected_root_identity=expected,
        )
    assert not (original / "blocked.txt").exists()


def test_safe_resolve_blocks_external_symlink_directory(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    # The read/list gate still blocks navigation through the escape symlink.
    with pytest.raises(ValueError, match="Path traversal blocked"):
        safe_resolve_ws(workspace, "escape")

    with pytest.raises(ValueError, match="Path traversal blocked"):
        list_dir(workspace, "escape")

    # The escape symlink is now emitted (display-only) with target_outside_workspace=True.
    entries = {e["name"]: e for e in list_dir(workspace, ".")}
    assert "escape" in entries
    assert entries["escape"]["type"] == "symlink"
    assert entries["escape"]["target_outside_workspace"] is True
    # #4581 hardening: display-only escape rows are uniformly is_dir=False — the
    # target's real dir/file nature is target-derived metadata we don't disclose
    # (the row is non-navigable regardless).
    assert entries["escape"]["is_dir"] is False
    assert "target" not in entries["escape"]


def test_read_file_blocks_external_symlink_file(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (workspace / "secret-link.txt").symlink_to(outside / "secret.txt")

    # The read gate still blocks reading through the escape symlink.
    with pytest.raises(ValueError, match="Path traversal blocked"):
        read_file_content(workspace, "secret-link.txt")

    # The escape symlink is now emitted (display-only) with target_outside_workspace=True.
    entries = {e["name"]: e for e in list_dir(workspace, ".")}
    assert "secret-link.txt" in entries
    assert entries["secret-link.txt"]["type"] == "symlink"
    assert entries["secret-link.txt"]["target_outside_workspace"] is True
    assert entries["secret-link.txt"]["is_dir"] is False


def test_internal_symlink_still_resolves_within_workspace(tmp_path):
    import api.workspace as w

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "inside.txt").write_text("inside", encoding="utf-8")
    (workspace / "inside-link.txt").symlink_to(nested / "inside.txt")

    resolved = safe_resolve_ws(workspace, "inside-link.txt")

    assert resolved == (nested / "inside.txt").resolve()
    assert read_file_content(workspace, "inside-link.txt")["content"] == "inside"
    if not w._DIR_FD_OK:
        pytest.skip("internal symlink listing is platform-dependent without dir_fd")
    assert "inside-link.txt" in {entry["name"] for entry in list_dir(workspace, ".")}


def test_authorized_escape_request_reanchors_descendants(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    (outside / "nested").mkdir(parents=True)
    (outside / "nested" / "inside.txt").write_text("inside", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    grant = authorize_escape_target(workspace, "sess-1", "escape")
    resolved = resolve_authorized_escape_request(
        workspace,
        "sess-1",
        grant["token"],
        "escape/nested/inside.txt",
    )

    assert resolved["surface_path"] == "escape"
    assert resolved["external_rel"] == "nested/inside.txt"
    assert resolved["target"] == outside / "nested" / "inside.txt"


def test_authorized_escape_request_expires_when_surface_target_changes(tmp_path):
    workspace = tmp_path / "workspace"
    outside_a = tmp_path / "outside-a"
    outside_b = tmp_path / "outside-b"
    workspace.mkdir()
    outside_a.mkdir()
    outside_b.mkdir()
    escape = workspace / "escape"
    escape.symlink_to(outside_a)

    grant = authorize_escape_target(workspace, "sess-1", "escape")

    escape.unlink()
    escape.symlink_to(outside_b)

    with pytest.raises(ValueError, match="expired"):
        resolve_authorized_escape_request(workspace, "sess-1", grant["token"], "escape")


def test_upload_capability_mint_rejects_target_swapped_after_resolution(tmp_path, monkeypatch):
    import api.workspace as w

    if not w._DIR_FD_OK:
        pytest.skip("race-safe external upload authorization requires dir_fd support")

    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    reports = external / "reports"
    sibling = external / "sibling"
    workspace.mkdir()
    reports.mkdir(parents=True)
    sibling.mkdir()
    (workspace / "escape").symlink_to(external)

    read_grant = authorize_escape_target(workspace, "sess-1", "escape")
    real_safe_resolve = w.safe_resolve_ws
    swapped = {"done": False}

    def racing_safe_resolve(root, requested):
        resolved = real_safe_resolve(root, requested)
        if not swapped["done"] and str(requested) == "reports":
            swapped["done"] = True
            reports.rmdir()
            reports.symlink_to(sibling)
        return resolved

    monkeypatch.setattr(w, "safe_resolve_ws", racing_safe_resolve)
    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        prepare_escape_upload(workspace, "sess-1", read_grant["token"], "escape/reports")


def test_upload_capability_activation_rejects_real_directory_rename_replacement(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    reports = external / "reports"
    substitute = external / "substitute"
    moved_original = external / "reports-original"
    workspace.mkdir()
    reports.mkdir(parents=True)
    substitute.mkdir()
    (workspace / "escape").symlink_to(external)

    read_grant = authorize_escape_target(workspace, "sess-1", "escape")
    prepared = prepare_escape_upload(
        workspace, "sess-1", read_grant["token"], "escape/reports"
    )

    reports.rename(moved_original)
    substitute.rename(reports)

    with pytest.raises(EscapeUploadAuthorizationExpiredError, match="expired"):
        authorize_escape_upload(
            workspace,
            "sess-1",
            read_grant["token"],
            "escape/reports",
            prepared["prepare_token"],
        )


def _activate_upload(workspace, session_id, read_token, rel):
    prepared = prepare_escape_upload(workspace, session_id, read_token, rel)
    return authorize_escape_upload(
        workspace, session_id, read_token, rel, prepared["prepare_token"]
    )


def test_upload_prepare_token_is_single_use(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "escape").symlink_to(outside)

    read_grant = authorize_escape_target(workspace, "sess-1", "escape")
    prepared = prepare_escape_upload(workspace, "sess-1", read_grant["token"], "escape")
    upload_grant = authorize_escape_upload(
        workspace,
        "sess-1",
        read_grant["token"],
        "escape",
        prepared["prepare_token"],
    )
    assert upload_grant["capability"] == "upload"

    with pytest.raises(EscapeUploadAuthorizationExpiredError, match="expired"):
        authorize_escape_upload(
            workspace,
            "sess-1",
            read_grant["token"],
            "escape",
            prepared["prepare_token"],
        )


def test_upload_capability_expires_and_revalidates_surface_target(tmp_path):
    import api.workspace as workspace_api

    workspace = tmp_path / "workspace"
    outside_a = tmp_path / "outside-a"
    outside_b = tmp_path / "outside-b"
    workspace.mkdir()
    outside_a.mkdir()
    outside_b.mkdir()
    escape = workspace / "escape"
    escape.symlink_to(outside_a)

    read_grant = authorize_escape_target(workspace, "sess-1", "escape")
    upload_grant = _activate_upload(workspace, "sess-1", read_grant["token"], "escape")

    with workspace_api._ESCAPE_AUTH_LOCK:
        workspace_api._ESCAPE_UPLOAD_AUTH_TOKENS[upload_grant["token"]]["expires_at"] = 0
    with pytest.raises(EscapeUploadAuthorizationExpiredError, match="expired"):
        resolve_authorized_escape_upload_request(
            workspace, "sess-1", upload_grant["token"], "escape"
        )

    upload_grant = _activate_upload(workspace, "sess-1", read_grant["token"], "escape")
    escape.unlink()
    escape.symlink_to(outside_b)
    with pytest.raises(EscapeUploadAuthorizationExpiredError, match="expired"):
        resolve_authorized_escape_upload_request(
            workspace, "sess-1", upload_grant["token"], "escape"
        )


def test_authorized_escape_request_keeps_other_live_grants(tmp_path):
    workspace = tmp_path / "workspace"
    outside_a = tmp_path / "outside-a"
    outside_b = tmp_path / "outside-b"
    workspace.mkdir()
    outside_a.mkdir()
    outside_b.mkdir()
    (outside_a / "alpha.txt").write_text("alpha", encoding="utf-8")
    (outside_b / "beta.txt").write_text("beta", encoding="utf-8")
    (workspace / "escape-a").symlink_to(outside_a)
    (workspace / "escape-b").symlink_to(outside_b)

    grant_a = authorize_escape_target(workspace, "sess-1", "escape-a")
    grant_b = authorize_escape_target(workspace, "sess-1", "escape-b")

    resolved_a = resolve_authorized_escape_request(
        workspace,
        "sess-1",
        grant_a["token"],
        "escape-a/alpha.txt",
    )
    resolved_b = resolve_authorized_escape_request(
        workspace,
        "sess-1",
        grant_b["token"],
        "escape-b/beta.txt",
    )

    assert resolved_a["target"] == outside_a / "alpha.txt"
    assert resolved_b["target"] == outside_b / "beta.txt"


def test_authorized_listing_keeps_nested_child_escape_display_only(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    second_outside = tmp_path / "second-outside"
    workspace.mkdir()
    outside.mkdir()
    second_outside.mkdir()
    (workspace / "escape").symlink_to(outside)
    (outside / "nested-escape").symlink_to(second_outside)

    grant = authorize_escape_target(workspace, "sess-1", "escape")
    payload = list_authorized_escape_dir(workspace, "sess-1", grant["token"], "escape")
    entries = {entry["name"]: entry for entry in payload["entries"]}

    assert entries["nested-escape"]["path"] == "escape/nested-escape"
    assert entries["nested-escape"]["target_outside_workspace"] is True
    assert entries["nested-escape"]["escape_read_only"] is True
    assert "target" not in entries["nested-escape"]


# ── TOCTOU hardening (#3398): a path that passes safe_resolve_ws() but is then
#    swapped to an external symlink before the open must not read/list/write
#    outside the workspace. The read/list/write paths use a portable anchored
#    openat-walk (openat + O_NOFOLLOW per component, dir_fd where supported). ──


def test_read_file_toctou_swap_to_external_symlink_blocked(tmp_path, monkeypatch):
    """If the resolved path is swapped to an external symlink AFTER the
    safe_resolve_ws() check, read_file_content must refuse, not follow the
    symlink and leak external content."""
    import api.workspace as w
    if not w._DIR_FD_OK:
        pytest.skip("TOCTOU symlink-swap hardening requires dir_fd support")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.txt").write_text("LEGIT", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET-LEAK", encoding="utf-8")

    real_resolve = w.safe_resolve_ws

    def racing_resolve(root, rel):
        p = real_resolve(root, rel)
        if rel == "data.txt":
            try:
                p.unlink()
            except OSError:
                pass
            p.symlink_to(outside / "secret.txt")
        return p

    monkeypatch.setattr(w, "safe_resolve_ws", racing_resolve)
    try:
        result = w.read_file_content(workspace, "data.txt")
        assert "SECRET" not in result["content"], "TOCTOU symlink swap leaked external content"
    except (FileNotFoundError, ValueError):
        pass  # refused — the correct outcome


def test_list_dir_toctou_swap_to_external_symlink_blocked(tmp_path, monkeypatch):
    """If a checked directory path is swapped to an external symlink after
    safe_resolve_ws(), list_dir must refuse rather than enumerate the external
    directory."""
    import api.workspace as w
    if not w._DIR_FD_OK:
        pytest.skip("TOCTOU symlink-swap hardening requires dir_fd support")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sub").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")

    real_resolve = w.safe_resolve_ws

    def racing_resolve(root, rel):
        p = real_resolve(root, rel)
        if rel == "sub":
            try:
                p.rmdir()
            except OSError:
                pass
            p.symlink_to(outside)
        return p

    monkeypatch.setattr(w, "safe_resolve_ws", racing_resolve)
    try:
        entries = w.list_dir(workspace, "sub")
        names = {e["name"] for e in entries}
        assert "secret.txt" not in names, "TOCTOU symlink swap leaked external dir listing"
    except (FileNotFoundError, ValueError):
        pass  # refused — the correct outcome


def test_anchored_create_blocks_symlinked_component(tmp_path):
    """open_anchored_create_fd must refuse to write through a symlinked path
    component (the upload / archive-extraction write race), landing nothing
    outside the workspace."""
    import api.workspace as w
    if not w._DIR_FD_OK:
        pytest.skip("anchored symlink-component rejection requires dir_fd support")
    from api.workspace import open_anchored_create_fd

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "evil").symlink_to(outside)  # symlinked intermediate dir

    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        open_anchored_create_fd(workspace, (workspace / "evil" / "pwned.txt"))
    assert not (outside / "pwned.txt").exists()


def test_anchored_create_no_fd_leak_on_rejection(tmp_path):
    """Repeated rejected anchored creates must not leak file descriptors."""
    import os

    from api.workspace import open_anchored_create_fd

    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("fd-count check requires /proc/self/fd")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "evil").symlink_to(outside)

    before = len(os.listdir("/proc/self/fd"))
    for _ in range(200):
        try:
            open_anchored_create_fd(workspace, (workspace / "evil" / "x.txt"))
        except Exception:
            pass
    after = len(os.listdir("/proc/self/fd"))
    assert after <= before + 2, f"fd leak: before={before} after={after}"


def test_anchored_create_nested_autocreates_dirs(tmp_path):
    """A normal (non-escaping) nested create works and lands under the workspace."""
    import os

    from api.workspace import open_anchored_create_fd

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fd = open_anchored_create_fd(workspace, workspace / "a" / "b" / "file.txt")
    os.write(fd, b"hello")
    os.close(fd)
    assert (workspace / "a" / "b" / "file.txt").read_text() == "hello"


def test_rename_anchored_reports_destination_traversal(tmp_path):
    from api.workspace import rename_anchored

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    source = workspace / "inside.txt"
    source.write_text("inside", encoding="utf-8")
    dest = outside / "outside.txt"

    with pytest.raises(ValueError) as exc_info:
        rename_anchored(workspace, source, dest)
    assert str(dest) in str(exc_info.value)


def test_external_capability_create_fails_closed_without_dir_fd(tmp_path, monkeypatch):
    import api.workspace as w

    monkeypatch.setattr(w, "_DIR_FD_OK", False)
    external = tmp_path / "external"
    external.mkdir()
    identity = external.stat()

    with pytest.raises(OSError, match="(?i)race-safe anchored creation is unavailable"):
        w.open_anchored_create_fd(
            external,
            external / "payload.txt",
            expected_root_identity=(int(identity.st_dev), int(identity.st_ino)),
        )
    assert not (external / "payload.txt").exists()


def test_list_read_create_work_on_no_dir_fd_fallback(tmp_path, monkeypatch):
    """The no-dir_fd portability fallback (Windows path) must still list, read,
    and create within the workspace, and still hide/block external symlinks via
    the static safe_resolve_ws guard — no fd-relative API that would brick on
    platforms without os.supports_dir_fd."""
    import os

    import api.workspace as w

    monkeypatch.setattr(w, "_DIR_FD_OK", False)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "internal").symlink_to(workspace / "sub")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("x", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    names = {e["name"] for e in w.list_dir(workspace, ".")}
    assert "a.txt" in names
    if w._DIR_FD_OK:
        assert "internal" in names          # legit internal symlink listed
    assert "escape" in names            # external symlink emitted (display-only)
    escape_entry = next(e for e in w.list_dir(workspace, ".") if e["name"] == "escape")
    assert escape_entry["target_outside_workspace"] is True
    assert w.read_file_content(workspace, "a.txt")["content"] == "hi"

    fd = w.open_anchored_create_fd(workspace, workspace / "new" / "f.txt")
    os.write(fd, b"ok")
    os.close(fd)
    assert (workspace / "new" / "f.txt").read_text() == "ok"


def test_read_blocked_when_workspace_root_raced_to_symlink(tmp_path):
    """If the workspace root itself is swapped to an external symlink after
    resolve() but before the anchored open, read_file_content must refuse
    (O_NOFOLLOW on the root open), not follow it and leak external content."""
    import os
    import shutil

    import api.workspace as w

    if not w._DIR_FD_OK:
        pytest.skip("anchored root-open race only applies on dir_fd platforms")

    outside = tmp_path / "evil"
    outside.mkdir()
    (outside / "f.txt").write_text("SECRET-LEAK", encoding="utf-8")
    wsroot = tmp_path / "wsroot"
    wsroot.mkdir()
    (wsroot / "f.txt").write_text("LEGIT", encoding="utf-8")

    real_open = os.open
    state = {"swapped": False}

    def racing_open(path, *args, **kwargs):
        if (not state["swapped"]) and "dir_fd" not in kwargs and str(path) == str(wsroot.resolve()):
            state["swapped"] = True
            shutil.rmtree(str(wsroot))
            os.symlink(str(outside), str(wsroot))
        return real_open(path, *args, **kwargs)

    os.open = racing_open
    try:
        try:
            result = w.read_file_content(wsroot, "f.txt")
            assert "SECRET" not in result["content"], "root-swap race leaked external content"
        except (FileNotFoundError, ValueError, NotADirectoryError, OSError):
            pass  # refused — correct
    finally:
        os.open = real_open


# ── #4510: escape-target symlinks are now emitted as display-only rows ──────
#    The escape filter was widened (not removed): symlinks whose resolved target
#    sits outside the workspace root are now emitted with
#    target_outside_workspace=True instead of being silently dropped. The
#    read/list gate (safe_resolve_ws / open_anchored_fd) is unchanged and still
#    blocks navigation through them. ──────────────────────────────────────────


def test_list_dir_in_workspace_symlink_shape(tmp_path):
    """In-workspace symlinks emit type='symlink' with target_outside_workspace=False."""
    import api.workspace as w

    if not w._DIR_FD_OK:
        pytest.skip("symlink listing requires dir_fd support")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.txt").write_text("hello", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "data.txt")

    entries = {e["name"]: e for e in w.list_dir(workspace, ".")}
    assert "link.txt" in entries
    assert entries["link.txt"]["type"] == "symlink"
    assert entries["link.txt"]["target_outside_workspace"] is False
    assert entries["link.txt"]["is_dir"] is False
    assert entries["link.txt"]["target"] == str((workspace / "data.txt").resolve())


def test_list_dir_outside_workspace_symlink_emitted_with_flag(tmp_path):
    """Escape-target symlinks are emitted with target_outside_workspace=True."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "file.txt").write_text("external", encoding="utf-8")
    (workspace / "ext-link.txt").symlink_to(outside / "file.txt")

    entries = {e["name"]: e for e in list_dir(workspace, ".")}
    assert "ext-link.txt" in entries
    assert entries["ext-link.txt"]["type"] == "symlink"
    assert entries["ext-link.txt"]["target_outside_workspace"] is True
    assert entries["ext-link.txt"]["is_dir"] is False
    # #4581 hardening: a display-only escape-target row must NOT disclose where it
    # points — no resolved outside path, no target-derived size, no target-derived
    # metadata. Only the link name/path + the display-only flag are emitted.
    assert "target" not in entries["ext-link.txt"]
    assert "size" not in entries["ext-link.txt"]


def test_list_dir_external_symlink_blocked_system_path_unchanged(tmp_path):
    """Symlinks to blocked system paths (/etc, /usr) are still filtered out."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "etc-link").symlink_to("/etc")
    (workspace / "usr-link").symlink_to("/usr")

    names = {e["name"] for e in list_dir(workspace, ".")}
    assert "etc-link" not in names
    assert "usr-link" not in names


def test_list_dir_escape_symlink_read_still_blocked(tmp_path):
    """Listing shows the escape symlink (display-only) but read_file_content
    on the same target still raises ValueError — proving the read gate is intact."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(outside / "secret.txt")

    # Listing emits the entry with target_outside_workspace=True
    entries = {e["name"]: e for e in list_dir(workspace, ".")}
    assert "escape.txt" in entries
    assert entries["escape.txt"]["target_outside_workspace"] is True

    # But reading through it is still blocked
    with pytest.raises(ValueError, match="Path traversal blocked"):
        read_file_content(workspace, "escape.txt")
