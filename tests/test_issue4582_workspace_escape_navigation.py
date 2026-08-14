from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import urllib.parse
import uuid
import io
import zipfile

import pytest

from api.routes import _project_os_workspace_read
from tests._pytest_port import BASE


ROOT = pathlib.Path(__file__).resolve().parents[1]
UI_JS = ROOT / "static" / "ui.js"
WORKSPACE_JS = ROOT / "static" / "workspace.js"
NODE = shutil.which("node")


def _get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.loads(response.read())


def _get_bytes(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return response.read()


def _browser_headers() -> dict[str, str]:
    parsed = urllib.parse.urlparse(BASE)
    return {"Origin": f"{parsed.scheme}://{parsed.netloc}"}


def _referer_only_headers() -> dict[str, str]:
    parsed = urllib.parse.urlparse(BASE)
    return {"Referer": f"{parsed.scheme}://{parsed.netloc}/workspace"}


def _post_json(path: str, body: dict | None = None, headers: dict[str, str] | None = None) -> tuple[dict, int]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _post_multipart(
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
    headers: dict[str, str] | None = None,
) -> tuple[dict, int]:
    boundary = uuid.uuid4().hex.encode()
    body = b""
    for name, value in fields.items():
        body += b"--" + boundary + b"\r\n"
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += value.encode() + b"\r\n"
    for name, (filename, data) in files.items():
        body += b"--" + boundary + b"\r\n"
        body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
    body += b"--" + boundary + b"--\r\n"
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _make_session(workspace: pathlib.Path) -> str:
    _post_json("/api/workspaces/add", {"path": str(workspace)})
    payload, status = _post_json("/api/session/new", {"workspace": str(workspace)})
    assert status == 200, payload
    return payload["session"]["session_id"]


def _prepare_upload_grant(sid: str, path: str, read_token: str) -> tuple[dict, int]:
    return _post_json(
        "/api/escape/upload-authorize",
        {"session_id": sid, "path": path, "token": read_token, "phase": "prepare"},
        headers=_browser_headers(),
    )


def _activate_upload_grant(
    sid: str, path: str, read_token: str, prepare_token: str
) -> tuple[dict, int]:
    return _post_json(
        "/api/escape/upload-authorize",
        {
            "session_id": sid,
            "path": path,
            "token": read_token,
            "phase": "activate",
            "prepare_token": prepare_token,
        },
        headers=_browser_headers(),
    )


def _read_workspace_js() -> str:
    return WORKSPACE_JS.read_text(encoding="utf-8")


def _workspace_escape_helper_block() -> str:
    src = _read_workspace_js()
    start = src.find("function _escapeGrantStore(){")
    assert start >= 0, "escape grant helper block start not found in static/workspace.js"
    end = src.find("let _workspacePanelActiveTab = 'files';", start)
    assert end >= 0, "escape grant helper block end not found in static/workspace.js"
    return src[start:end]


def _run_node(js: str) -> dict:
    assert NODE is not None, "node not on PATH"
    completed = subprocess.run(
        [NODE, "-e", js],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


class TestIssue4582EscapeNavigationLive:
    def test_authorized_file_symlink_reads_and_raws_through_parent_anchor(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        workspace.mkdir()
        outside.mkdir()
        (outside / "note.txt").write_text("outside file", encoding="utf-8")
        (workspace / "escape-file.txt").symlink_to(outside / "note.txt")

        sid = _make_session(workspace)
        root_listing = _get_json(f"/api/list?session_id={sid}&path=.")
        escape_row = {entry["name"]: entry for entry in root_listing["entries"]}["escape-file.txt"]
        assert escape_row["target_outside_workspace"] is True

        denied, denied_status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape-file.txt"},
        )
        assert denied_status == 403, denied

        referer_only, referer_only_status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape-file.txt"},
            headers=_referer_only_headers(),
        )
        assert referer_only_status == 403, referer_only

        auth, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape-file.txt"},
            headers=_browser_headers(),
        )
        assert status == 200, auth
        assert auth["path"] == "escape-file.txt"
        assert auth["is_dir"] is False
        assert auth["read_only"] is True

        text = _get_json(
            f"/api/escape/file/read?session_id={sid}&token={auth['token']}&path=escape-file.txt"
        )
        assert text["path"] == "escape-file.txt"
        assert text["content"] == "outside file"
        assert text["escape_read_only"] is True

        raw = _get_bytes(
            f"/api/escape/file/raw?session_id={sid}&token={auth['token']}&path=escape-file.txt"
        )
        assert raw == b"outside file"

        try:
            _get_json(
                f"/api/escape/list?session_id={sid}&token={auth['token']}&path=escape-file.txt"
            )
            assert False, "file escape grants should not list as directories"
        except urllib.error.HTTPError as exc:
            assert exc.code in (403, 404)

    def test_authorized_dir_list_read_and_raw_stay_virtualized(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        workspace.mkdir()
        outside.mkdir()
        (outside / "note.txt").write_text("outside note", encoding="utf-8")
        (workspace / "escape").symlink_to(outside)

        sid = _make_session(workspace)
        root_listing = _get_json(f"/api/list?session_id={sid}&path=.")
        escape_row = {entry["name"]: entry for entry in root_listing["entries"]}["escape"]
        assert escape_row["target_outside_workspace"] is True

        denied, denied_status = _post_json("/api/escape/authorize", {"session_id": sid, "path": "escape"})
        assert denied_status == 403, denied

        referer_only, referer_only_status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_referer_only_headers(),
        )
        assert referer_only_status == 403, referer_only

        auth, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        assert status == 200, auth
        assert auth["path"] == "escape"
        assert auth["is_dir"] is True
        assert auth["read_only"] is True

        listed = _get_json(
            f"/api/escape/list?session_id={sid}&token={auth['token']}&path=escape"
        )
        entries = {entry["name"]: entry for entry in listed["entries"]}
        assert listed["path"] == "escape"
        assert listed["read_only"] is True
        assert entries["note.txt"]["path"] == "escape/note.txt"
        assert entries["note.txt"]["escape_read_only"] is True
        assert str(outside) not in json.dumps(listed)

        text = _get_json(
            f"/api/escape/file/read?session_id={sid}&token={auth['token']}&path=escape/note.txt"
        )
        assert text["path"] == "escape/note.txt"
        assert text["content"] == "outside note"
        assert text["escape_read_only"] is True

        raw = _get_bytes(
            f"/api/escape/file/raw?session_id={sid}&token={auth['token']}&path=escape/note.txt"
        )
        assert raw == b"outside note"
        assert _project_os_workspace_read(pathlib.Path(workspace), "escape/note.txt") is None

    def test_nested_escape_row_stays_display_only_and_non_browsable(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        second_outside = tmp_path / "second-outside"
        workspace.mkdir()
        outside.mkdir()
        second_outside.mkdir()
        (second_outside / "secret.txt").write_text("secret", encoding="utf-8")
        (outside / "nested-escape").symlink_to(second_outside)
        (outside / "nested-file-escape.txt").symlink_to(second_outside / "secret.txt")
        (workspace / "escape").symlink_to(outside)

        sid = _make_session(workspace)
        auth, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        assert status == 200, auth

        nested_auth, nested_status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape/nested-escape"},
            headers=_browser_headers(),
        )
        assert nested_status in (403, 404), nested_auth

        listed = _get_json(
            f"/api/escape/list?session_id={sid}&token={auth['token']}&path=escape"
        )
        entries = {entry["name"]: entry for entry in listed["entries"]}
        nested = entries["nested-escape"]
        assert nested["target_outside_workspace"] is True
        assert nested["escape_read_only"] is True
        assert "target" not in nested

        try:
            _get_json(
                f"/api/escape/list?session_id={sid}&token={auth['token']}&path=escape/nested-escape"
            )
            assert False, "nested escape traversal should stay blocked"
        except urllib.error.HTTPError as exc:
            assert exc.code in (403, 404)

        try:
            _get_bytes(
                f"/api/escape/file/raw?session_id={sid}&token={auth['token']}&path=escape/nested-file-escape.txt"
            )
            assert False, "nested file escape raw read should stay blocked"
        except urllib.error.HTTPError as exc:
            assert exc.code in (403, 404)

    def test_external_upload_requires_separate_browser_confirmed_capability(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        workspace.mkdir()
        outside.mkdir()
        (outside / "reports").mkdir()
        (workspace / "escape").symlink_to(outside)
        sid = _make_session(workspace)

        read_grant, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        assert status == 200, read_grant
        assert read_grant["read_only"] is True

        missing_origin, missing_origin_status = _post_json(
            "/api/escape/upload-authorize",
            {"session_id": sid, "path": "escape", "token": read_grant["token"]},
        )
        assert missing_origin_status == 403, missing_origin

        referer_only, referer_only_status = _post_json(
            "/api/escape/upload-authorize",
            {"session_id": sid, "path": "escape", "token": read_grant["token"]},
            headers=_referer_only_headers(),
        )
        assert referer_only_status == 403, referer_only

        denied, denied_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape", "upload_token": read_grant["token"]},
            {"file": ("denied.txt", b"read grants are not upload grants")},
            headers=_browser_headers(),
        )
        assert denied_status == 403, denied
        assert not (outside / "denied.txt").exists()

        prepared, prepare_status = _prepare_upload_grant(
            sid, "escape/reports", read_grant["token"]
        )
        assert prepare_status == 200, prepared
        upload_grant, grant_status = _activate_upload_grant(
            sid, "escape/reports", read_grant["token"], prepared["prepare_token"]
        )
        assert grant_status == 200, upload_grant
        assert upload_grant["capability"] == "upload"
        assert upload_grant["path"] == "escape/reports"
        assert upload_grant["expires_in"] <= 120

        uploaded, upload_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/reports", "upload_token": upload_grant["token"]},
            {"file": ("minutes.md", b"# Minutes")},
            headers=_browser_headers(),
        )
        assert upload_status == 200, uploaded
        assert (outside / "reports" / "minutes.md").read_bytes() == b"# Minutes"
        assert not (workspace / "escape" / "reports" / "minutes.md").is_symlink()
        assert str(outside) not in json.dumps(uploaded)

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("notes/day-one.md", "day one")
        uploaded_archive, archive_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/reports", "upload_token": upload_grant["token"]},
            {"file": ("bundle.zip", archive_buffer.getvalue())},
            headers=_browser_headers(),
        )
        assert archive_status == 200, uploaded_archive
        assert uploaded_archive["path"] == "escape/reports/bundle.zip"
        assert uploaded_archive["extracted"] is False
        assert (outside / "reports" / "bundle.zip").read_bytes() == archive_buffer.getvalue()
        assert not (outside / "reports" / "bundle").exists()
        assert str(outside) not in json.dumps(uploaded_archive)

        uploaded_office, office_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/reports", "upload_token": upload_grant["token"]},
            {"file": ("review.docx", b"not-a-real-docx")},
            headers=_browser_headers(),
        )
        assert office_status == 200, uploaded_office
        assert uploaded_office["path"] == "escape/reports/review.docx"
        assert "sidecar" not in uploaded_office
        assert "sidecar_error" not in uploaded_office
        assert not (outside / "reports" / "review.docx.md").exists()

    def test_external_upload_rejects_dangling_leaf_symlink_without_creating_nested_path(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        reports = outside / "reports"
        workspace.mkdir()
        reports.mkdir(parents=True)
        (workspace / "escape").symlink_to(outside)
        (reports / "alias.txt").symlink_to("nested/payload.txt")
        sid = _make_session(workspace)

        read_grant, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        assert status == 200, read_grant
        prepared, status = _prepare_upload_grant(
            sid, "escape/reports", read_grant["token"]
        )
        assert status == 200, prepared
        upload_grant, status = _activate_upload_grant(
            sid, "escape/reports", read_grant["token"], prepared["prepare_token"]
        )
        assert status == 200, upload_grant

        uploaded, status = _post_multipart(
            "/api/workspace/upload",
            {
                "session_id": sid,
                "path": "escape/reports",
                "upload_token": upload_grant["token"],
            },
            {"file": ("alias.txt", b"must-not-follow")},
            headers=_browser_headers(),
        )
        assert status in (403, 409), uploaded
        assert (reports / "alias.txt").is_symlink()
        assert not (reports / "nested").exists()

    def test_external_upload_rejects_existing_leaf_without_deduplicating_or_overwriting(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        reports = outside / "reports"
        workspace.mkdir()
        reports.mkdir(parents=True)
        (workspace / "escape").symlink_to(outside)
        (reports / "existing.txt").write_bytes(b"original")
        sid = _make_session(workspace)

        read_grant, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        assert status == 200, read_grant
        prepared, status = _prepare_upload_grant(
            sid, "escape/reports", read_grant["token"]
        )
        assert status == 200, prepared
        upload_grant, status = _activate_upload_grant(
            sid, "escape/reports", read_grant["token"], prepared["prepare_token"]
        )
        assert status == 200, upload_grant

        uploaded, status = _post_multipart(
            "/api/workspace/upload",
            {
                "session_id": sid,
                "path": "escape/reports",
                "upload_token": upload_grant["token"],
            },
            {"file": ("existing.txt", b"replacement")},
            headers=_browser_headers(),
        )
        assert status == 409, uploaded
        assert (reports / "existing.txt").read_bytes() == b"original"
        assert not (reports / "existing-1.txt").exists()

    def test_external_upload_activation_rejects_real_directory_replacement(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        reports = outside / "reports"
        substitute = outside / "substitute"
        original = outside / "reports-original"
        workspace.mkdir()
        reports.mkdir(parents=True)
        substitute.mkdir()
        (workspace / "escape").symlink_to(outside)
        sid = _make_session(workspace)

        read_grant, status = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        assert status == 200, read_grant
        prepared, status = _prepare_upload_grant(
            sid, "escape/reports", read_grant["token"]
        )
        assert status == 200, prepared

        reports.rename(original)
        substitute.rename(reports)

        activated, status = _activate_upload_grant(
            sid, "escape/reports", read_grant["token"], prepared["prepare_token"]
        )
        assert status == 403, activated
        assert not (reports / "payload.txt").exists()
        assert not (original / "payload.txt").exists()

    def test_external_upload_capability_rejects_wrong_session_traversal_and_nested_escape(self, tmp_path):
        workspace = tmp_path / "workspace"
        second_workspace = tmp_path / "workspace-2"
        outside = tmp_path / "outside"
        second_outside = tmp_path / "second-outside"
        workspace.mkdir()
        second_workspace.mkdir()
        outside.mkdir()
        second_outside.mkdir()
        (outside / "reports").mkdir()
        (outside / "other").mkdir()
        (workspace / "escape").symlink_to(outside)
        (outside / "nested-escape").symlink_to(second_outside)
        sid = _make_session(workspace)
        other_sid = _make_session(second_workspace)

        read_grant, _ = _post_json(
            "/api/escape/authorize",
            {"session_id": sid, "path": "escape"},
            headers=_browser_headers(),
        )
        prepared, prepare_status = _prepare_upload_grant(
            sid, "escape/reports", read_grant["token"]
        )
        assert prepare_status == 200, prepared
        upload_grant, status = _activate_upload_grant(
            sid, "escape/reports", read_grant["token"], prepared["prepare_token"]
        )
        assert status == 200, upload_grant

        wrong_session, wrong_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": other_sid, "path": "escape/reports", "upload_token": upload_grant["token"]},
            {"file": ("wrong.txt", b"wrong")},
            headers=_browser_headers(),
        )
        assert wrong_status == 403, wrong_session

        traversal, traversal_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/../outside", "upload_token": upload_grant["token"]},
            {"file": ("traversal.txt", b"traversal")},
            headers=_browser_headers(),
        )
        assert traversal_status == 403, traversal

        nested, nested_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/nested-escape", "upload_token": upload_grant["token"]},
            {"file": ("nested.txt", b"nested")},
            headers=_browser_headers(),
        )
        assert nested_status == 403, nested
        assert not (second_outside / "nested.txt").exists()

        sibling, sibling_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/other", "upload_token": upload_grant["token"]},
            {"file": ("sibling.txt", b"sibling")},
            headers=_browser_headers(),
        )
        assert sibling_status == 403, sibling
        assert not (outside / "other" / "sibling.txt").exists()

        missing_dir, missing_dir_status = _post_multipart(
            "/api/workspace/upload",
            {"session_id": sid, "path": "escape/reports/new-dir", "upload_token": upload_grant["token"]},
            {"file": ("new.txt", b"new")},
            headers=_browser_headers(),
        )
        assert missing_dir_status == 403, missing_dir
        assert not (outside / "reports" / "new-dir").exists()


pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


class TestIssue4582EscapeNavigationFrontend:
    def test_authorization_flow_switches_routes_and_marks_subtree_read_only(self):
        helper_block = _workspace_escape_helper_block()
        js = (
            "const helperBlock = "
            + json.dumps(helper_block)
            + ";\n"
            + r"""
const S = { session: { session_id: 'sess-1' }, currentDir: '.', _escapeGrants: Object.create(null) };
const confirmCalls = [];
const apiCalls = [];
const toasts = [];
const showConfirmDialog = async (opts) => { confirmCalls.push(opts); return true; };
const api = async (path, opts) => {
  apiCalls.push({ path, method: opts && opts.method, body: opts && opts.body });
  return {
    token: 'tok-123',
    path: 'escape',
    expires_at: 4102444800,
    is_dir: true,
    read_only: true,
  };
};
const showToast = (...args) => { toasts.push(args); };
const t = (key) => key;
const runner = new Function(
  'S', 'showConfirmDialog', 'api', 'showToast', 't', 'URLSearchParams',
  helperBlock + '; return { authorizeWorkspaceEscapeNavigation, _workspaceRouteForPath, _workspacePathIsReadOnly, _workspaceEscapeGrantForPath };'
);
const apiFns = runner(S, showConfirmDialog, api, showToast, t, URLSearchParams);
(async () => {
  const beforeRead = apiFns._workspaceRouteForPath('escape/note.txt', 'read');
  const beforeList = apiFns._workspaceRouteForPath('escape', 'list');
  const grant = await apiFns.authorizeWorkspaceEscapeNavigation({ path: 'escape', name: 'escape' });
  const afterRead = apiFns._workspaceRouteForPath('escape/note.txt', 'read');
  const afterRaw = apiFns._workspaceRouteForPath('escape/note.txt', 'raw', { inline: true });
  const grantLookup = apiFns._workspaceEscapeGrantForPath('escape/note.txt');
  const readOnly = apiFns._workspacePathIsReadOnly('escape/note.txt');
  console.log(JSON.stringify({
    beforeRead,
    beforeList,
    afterRead,
    afterRaw,
    grant,
    grantLookup,
    readOnly,
    confirmCalls,
    apiCalls,
    toasts,
  }));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
"""
        )
        result = _run_node(js)
        assert result["beforeRead"] == "/api/file?session_id=sess-1&path=escape%2Fnote.txt"
        assert result["beforeList"] == "/api/list?session_id=sess-1&path=escape"
        assert result["afterRead"] == "/api/escape/file/read?session_id=sess-1&path=escape%2Fnote.txt&token=tok-123"
        assert result["afterRaw"] == "/api/escape/file/raw?session_id=sess-1&path=escape%2Fnote.txt&token=tok-123&inline=1"
        assert result["grant"]["path"] == "escape"
        assert result["grantLookup"]["token"] == "tok-123"
        assert result["readOnly"] is True
        assert result["confirmCalls"][0]["message"] == "external_link_open_confirm"
        assert result["apiCalls"] == [
            {
                "path": "/api/escape/authorize",
                "method": "POST",
                "body": "{\"session_id\":\"sess-1\",\"path\":\"escape\"}",
            }
        ]
        assert result["toasts"][0][0] == "external_link_read_only_upload"

    def test_exact_grant_click_reauthorizes_without_reprompt(self):
        helper_block = _workspace_escape_helper_block()
        js = (
            "const helperBlock = "
            + json.dumps(helper_block)
            + ";\n"
            + r"""
const S = {
  session: { session_id: 'sess-1' },
  currentDir: '.',
  _escapeGrants: {
    escape: {
      sessionId: 'sess-1',
      path: 'escape',
      token: 'tok-old',
      expiresAt: Date.now() + 60_000,
      isDir: true,
    },
  },
};
const confirmCalls = [];
const apiCalls = [];
const toasts = [];
const showConfirmDialog = async (opts) => { confirmCalls.push(opts); return true; };
const api = async (path, opts) => {
  apiCalls.push({ path, method: opts && opts.method, body: opts && opts.body });
  return {
    token: 'tok-new',
    path: 'escape',
    expires_at: 4102444800,
    is_dir: true,
    read_only: true,
  };
};
const showToast = (...args) => { toasts.push(args); };
const t = (key) => key;
const runner = new Function(
  'S', 'showConfirmDialog', 'api', 'showToast', 't', 'URLSearchParams',
  helperBlock + '; return { authorizeWorkspaceEscapeNavigation, _workspaceEscapeExactGrant };'
);
const apiFns = runner(S, showConfirmDialog, api, showToast, t, URLSearchParams);
(async () => {
  const grant = await apiFns.authorizeWorkspaceEscapeNavigation({ path: 'escape', name: 'escape' });
  console.log(JSON.stringify({
    grant,
    stored: apiFns._workspaceEscapeExactGrant('escape'),
    confirmCalls,
    apiCalls,
    toasts,
  }));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
"""
        )
        result = _run_node(js)
        assert result["grant"]["token"] == "tok-new"
        assert result["stored"]["token"] == "tok-new"
        assert result["confirmCalls"] == []
        assert result["apiCalls"] == [
            {
                "path": "/api/escape/authorize",
                "method": "POST",
                "body": "{\"session_id\":\"sess-1\",\"path\":\"escape\"}",
            }
        ]
        assert result["toasts"][0][0] == "external_link_read_only_upload"

    def test_external_rows_authorize_then_open(self):
        src = UI_JS.read_text(encoding="utf-8")
        assert "authorizeWorkspaceEscapeNavigation(item)" in src
        assert "if(grant.isDir) await loadDir(item.path);" in src
        assert "else await openFile(item.path);" in src

    def test_read_only_affordances_stay_suppressed(self):
        ui_src = UI_JS.read_text(encoding="utf-8")
        ws_src = _read_workspace_js()
        assert "if(!isReadOnlyEscape){" in ui_src
        assert "_workspacePathIsReadOnly(_previewCurrentPath)" in ws_src
        assert "_workspacePathIsReadOnly(S.currentDir || '.')" in ws_src

    def test_external_upload_uses_explicit_upload_only_confirmation_for_click_and_drop(self):
        ws_src = _read_workspace_js()

        assert "authorizeWorkspaceEscapeUpload" in ws_src
        assert "/api/escape/upload-authorize" in ws_src
        assert "external_upload_confirm" in ws_src
        assert "external_upload_granted" in ws_src
        assert "formData.append('upload_token'" in ws_src
        assert "await authorizeWorkspaceEscapeUpload" in ws_src
        assert "uploadOsDropToWorkspace" in ws_src

        # Read-only is still the default for every non-upload mutation surface.
        assert "function _workspacePathIsReadOnly" in ws_src
        for fn_name in (
            "saveCurrentFile",
            "createNewFile",
            "createNewFolder",
            "moveWorkspacePath",
        ):
            start = ws_src.find(f"function {fn_name}")
            if start >= 0:
                body = ws_src[start : start + 1800]
                assert "_workspacePathIsReadOnly" in body

    def test_external_view_disables_non_upload_mutation_buttons(self):
        ws_src = _read_workspace_js()
        start = ws_src.index("function syncWorkspaceMutationButtons")
        end = ws_src.index("\n\nasync function loadDir", start)
        helper = ws_src[start:end]
        js = (
            "const helper = "
            + json.dumps(helper)
            + ";\n"
            + r"""
const buttons = {
  btnNewFile: {disabled: false}, btnNewFolder: {disabled: false},
  btnRefreshPanel: {disabled: false}, btnUploadWorkspace: {disabled: false}
};
const $ = (id) => buttons[id] || null;
const S = {session: {session_id: 'sess-1'}, currentDir: 'external-dir'};
const _workspacePathIsReadOnly = (path) => path === 'external-dir';
const runner = new Function('$', 'S', '_workspacePathIsReadOnly', helper + '; return syncWorkspaceMutationButtons;');
const sync = runner($, S, _workspacePathIsReadOnly);
sync('external-dir');
const external = JSON.parse(JSON.stringify(buttons));
sync('.');
const normal = JSON.parse(JSON.stringify(buttons));
console.log(JSON.stringify({external, normal}));
"""
        )
        result = _run_node(js)
        assert result["external"]["btnNewFile"]["disabled"] is True
        assert result["external"]["btnNewFolder"]["disabled"] is True
        assert result["external"]["btnRefreshPanel"]["disabled"] is False
        assert result["external"]["btnUploadWorkspace"]["disabled"] is False
        assert result["normal"]["btnNewFile"]["disabled"] is False
        assert result["normal"]["btnNewFolder"]["disabled"] is False

        load_start = ws_src.index("async function loadDir")
        load_end = ws_src.index("\n\nfunction refreshWorkspacePanel", load_start)
        load_body = ws_src[load_start:load_end]
        assert "renderFileTree();" in load_body
        assert (
            "if(typeof syncWorkspaceMutationButtons==='function')"
            "syncWorkspaceMutationButtons(S.currentDir);"
        ) in load_body

        boot_src = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
        sync_start = boot_src.index("function syncWorkspacePanelUI")
        sync_end = boot_src.index("\n\nfunction toggleMobileSidebar", sync_start)
        sync_body = boot_src[sync_start:sync_end]
        assert "syncWorkspaceMutationButtons(S.currentDir||'.')" in sync_body

    def test_external_upload_confirmation_stores_only_the_upload_capability(self):
        helper_block = _workspace_escape_helper_block()
        js = (
            "const helperBlock = "
            + json.dumps(helper_block)
            + ";\n"
            + r"""
const S = {
  session: { session_id: 'sess-1' }, currentDir: 'escape/reports',
  _escapeGrants: {
    escape: { sessionId: 'sess-1', path: 'escape', token: 'read-token', expiresAt: Date.now() + 60_000, isDir: true }
  }
};
const confirms = [];
const calls = [];
const toasts = [];
const showConfirmDialog = async (opts) => { confirms.push(opts); return true; };
const api = async (path, opts) => {
  calls.push({path, method: opts.method, body: opts.body});
  const body = JSON.parse(opts.body);
  if(body.phase === 'prepare'){
    return {prepare_token: 'prepare-token', path: 'escape/reports', expires_at: 4102444800, capability: 'upload-prepare'};
  }
  return {token: 'upload-token', path: 'escape/reports', expires_at: 4102444800, capability: 'upload'};
};
const showToast = (...args) => toasts.push(args);
const t = (key) => key;
const runner = new Function(
  'S', 'showConfirmDialog', 'api', 'showToast', 't', 'URLSearchParams',
  helperBlock + '; return { authorizeWorkspaceEscapeUpload, _workspacePathIsReadOnly, _workspaceEscapeUploadGrantForPath };'
);
const fns = runner(S, showConfirmDialog, api, showToast, t, URLSearchParams);
(async () => {
  const granted = await fns.authorizeWorkspaceEscapeUpload('escape/reports');
  console.log(JSON.stringify({
    granted,
    readOnly: fns._workspacePathIsReadOnly('escape/reports'),
    upload: fns._workspaceEscapeUploadGrantForPath('escape/reports'),
    confirms, calls, toasts,
  }));
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
"""
        )
        result = _run_node(js)
        assert result["readOnly"] is True
        assert result["upload"]["token"] == "read-token"
        assert result["upload"]["uploadToken"] == "upload-token"
        assert result["upload"]["uploadPath"] == "escape/reports"
        assert result["confirms"][0]["message"] == "external_upload_confirm\n\nescape/reports"
        assert len(result["calls"]) == 2
        prepared_body = json.loads(result["calls"][0]["body"])
        activated_body = json.loads(result["calls"][1]["body"])
        assert prepared_body == {
            "session_id": "sess-1",
            "path": "escape/reports",
            "token": "read-token",
            "phase": "prepare",
        }
        assert activated_body == {
            "session_id": "sess-1",
            "path": "escape/reports",
            "token": "read-token",
            "phase": "activate",
            "prepare_token": "prepare-token",
        }

    def test_external_upload_cancel_stops_after_prepare_without_caching_authority(self):
        helper_block = _workspace_escape_helper_block()
        js = (
            "const helperBlock = "
            + json.dumps(helper_block)
            + ";\n"
            + r"""
const S = {
  session: { session_id: 'sess-1' }, currentDir: 'escape/reports',
  _escapeGrants: {
    escape: {sessionId:'sess-1', path:'escape', token:'read-token', expiresAt:Date.now()+60_000, isDir:true}
  }
};
const calls = [];
const showConfirmDialog = async () => false;
const api = async (path, opts) => {
  calls.push(JSON.parse(opts.body));
  return {prepare_token:'prepare-token', path:'escape/reports', expires_at:4102444800, capability:'upload-prepare'};
};
const showToast = () => {};
const t = (key) => key;
const runner = new Function(
  'S','showConfirmDialog','api','showToast','t','URLSearchParams',
  helperBlock + '; return {authorizeWorkspaceEscapeUpload,_workspaceEscapeUploadGrantForPath};'
);
const fns = runner(S,showConfirmDialog,api,showToast,t,URLSearchParams);
(async () => {
  const granted = await fns.authorizeWorkspaceEscapeUpload('escape/reports');
  console.log(JSON.stringify({granted,calls,cached:fns._workspaceEscapeUploadGrantForPath('escape/reports')}));
})().catch(err => {console.error(err && err.stack || err); process.exit(1);});
"""
        )
        result = _run_node(js)
        assert result["granted"] is None
        assert result["cached"] is None
        assert len(result["calls"]) == 1
        assert result["calls"][0]["phase"] == "prepare"

    def test_external_upload_token_is_not_reused_for_a_sibling_directory(self):
        helper_block = _workspace_escape_helper_block()
        js = (
            "const helperBlock = "
            + json.dumps(helper_block)
            + ";\n"
            + r"""
const S = {
  session: { session_id: 'sess-1' }, currentDir: 'escape/reports',
  _escapeGrants: {
    escape: {
      sessionId: 'sess-1', path: 'escape', token: 'read-token', expiresAt: Date.now() + 60_000,
      isDir: true, uploadToken: 'reports-token', uploadExpiresAt: Date.now() + 60_000,
      uploadPath: 'escape/reports'
    }
  }
};
const confirms = [];
const calls = [];
const showConfirmDialog = async (opts) => { confirms.push(opts); return true; };
const api = async (path, opts) => {
  calls.push({path, body: opts.body});
  const body = JSON.parse(opts.body);
  if(body.phase === 'prepare'){
    return {prepare_token: 'other-prepare', path: 'escape/other', expires_at: 4102444800, capability: 'upload-prepare'};
  }
  return {token: 'other-token', path: 'escape/other', expires_at: 4102444800, capability: 'upload'};
};
const showToast = () => {};
const t = (key) => key;
const runner = new Function(
  'S', 'showConfirmDialog', 'api', 'showToast', 't', 'URLSearchParams',
  helperBlock + '; return { authorizeWorkspaceEscapeUpload, _workspaceEscapeUploadGrantForPath };'
);
const fns = runner(S, showConfirmDialog, api, showToast, t, URLSearchParams);
(async () => {
  const before = fns._workspaceEscapeUploadGrantForPath('escape/other');
  const granted = await fns.authorizeWorkspaceEscapeUpload('escape/other');
  console.log(JSON.stringify({before, granted, confirms, calls}));
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
"""
        )
        result = _run_node(js)
        assert result["before"] is None
        assert len(result["confirms"]) == 1
        assert len(result["calls"]) == 2
        assert json.loads(result["calls"][0]["body"])["phase"] == "prepare"
        assert json.loads(result["calls"][1]["body"])["phase"] == "activate"
        assert result["granted"]["uploadToken"] == "other-token"
        assert result["granted"]["uploadPath"] == "escape/other"

    def test_open_in_browser_reuses_workspace_route_helper(self):
        ws_src = _read_workspace_js()
        match = re.search(
            r"function openInBrowser\(\)\{\s*if\(!_previewCurrentPath\|\|!S\.session\) return;\s*const url=(.*?);\s*window\.open\(url,'_blank','noopener'\);\s*\}",
            ws_src,
            re.DOTALL,
        )
        assert match, "openInBrowser helper not found"
        assert "_workspaceRouteForPath(_previewCurrentPath, 'raw', {inline:true})" in match.group(1)
