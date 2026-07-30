import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HUB_STORE_JS = ROOT / "extensions" / "hub" / "hub-store.js"
HUB_JS = ROOT / "extensions" / "hub" / "hub.js"
HUB_CSS = ROOT / "extensions" / "hub" / "hub.css"

NODE = shutil.which("node")


def _run_hub_store_ops(
    manual,
    automatic: dict | None = None,
    write_back: bool = False,
    remote_after_read: dict | None = None,
    local_command: dict | None = None,
) -> dict:
    assert NODE, "node is required for hub-store behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const manualText = {json.dumps(manual if isinstance(manual, str) else json.dumps(manual, ensure_ascii=False), ensure_ascii=False)};
const automatic = {json.dumps(automatic or {}, ensure_ascii=False)};
const remoteAfterRead = {json.dumps(remote_after_read, ensure_ascii=False)};
const localCommand = {json.dumps(local_command, ensure_ascii=False)};
let manualReads = 0;
let saved = null;
const storage = {{'hermes-hub.session': 'sid-1', 'hermes-hub.root': '/tmp/hub'}};
global.localStorage = {{
  getItem(key) {{ return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null; }},
  setItem(key, value) {{ storage[key] = String(value); }},
  removeItem(key) {{ delete storage[key]; }}
}};
global.window = {{}};
window.api = function(path, opts) {{
  if (path === '/api/hub/init') return Promise.resolve({{ok: true}});
  if (path.startsWith('/api/list?')) return Promise.resolve({{items: []}});
  if (path.startsWith('/api/file?')) {{
    const decoded = decodeURIComponent(path);
    let content;
    if (decoded.includes('hub-ops-auto.json')) content = JSON.stringify(automatic);
    else {{ manualReads += 1; content = remoteAfterRead !== null && manualReads > 1 ? JSON.stringify(remoteAfterRead) : manualText; }}
    return Promise.resolve({{content}});
  }}
  if (path === '/api/file/save') {{ saved = JSON.parse(opts.body).content; return Promise.resolve({{ok:true}}); }}
  return Promise.reject(new Error('unexpected api path: ' + path));
}};
vm.runInThisContext(src, {{filename: 'hub-store.js'}});
window.HubStore.init()
  .then(() => window.HubStore.read('ops'))
  .then(data => {{ if (localCommand) data.commands.push(localCommand); return {'window.HubStore.write("ops", data).then(() => ({data, saved: JSON.parse(saved)}), err => ({data, saved, writeError: err.message}))' if write_back else '({data})'}; }})
  .then(result => process.stdout.write(JSON.stringify(result)))
  .catch(err => {{ console.error(err && err.stack || err); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_hub_store_normalizes_legacy_ops_without_losing_manual_rows():
    ops = _run_hub_store_ops(
        {
            "services": [
                {
                    "id": "manual-site",
                    "name": "Manual site",
                    "env": "prod",
                    "status": "watch",
                    "notes": "keep me editable",
                }
            ],
            "commands": [{"id": "tail-log", "label": "Tail log", "command": "tail -f app.log"}],
        }
    )

    data = ops["data"]
    assert data["services"][0]["id"] == "manual-site"
    assert data["commands"][0]["id"] == "tail-log"
    assert data["machines"] == []
    assert "generatedAt" in data
    assert data["source"]["kind"] == "manual"



@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_hub_store_merges_auto_snapshot_and_persists_only_manual_data():
    result = _run_hub_store_ops(
        {
            "services": [
                {"id": "manual-site", "name": "Manual", "status": "watch"},
                {"id": "managed:host:docker", "managed": True, "notes": "人工备注"},
            ],
            "commands": [{"id": "cmd", "label": "Uptime", "command": "uptime"}],
        },
        {
            "generatedAt": "2026-07-30T13:00:00+08:00",
            "source": {"kind": "automatic", "name": "monitor"},
            "machines": [{"id": "host", "status": "down"}],
            "services": [{"id": "managed:host:docker", "managed": True, "name": "Docker", "status": "down"}],
        },
        write_back=True,
    )

    data, saved = result["data"], result["saved"]
    assert data["machines"][0]["id"] == "host"
    managed = next(s for s in data["services"] if s.get("managed"))
    assert managed["status"] == "down"
    assert managed["notes"] == "人工备注"
    assert "machines" not in saved and "generatedAt" not in saved and "source" not in saved
    assert saved["commands"][0]["id"] == "cmd"
    assert {s["id"] for s in saved["services"]} == {"manual-site", "managed:host:docker"}
    assert next(s for s in saved["services"] if s["id"] == "managed:host:docker") == {
        "id": "managed:host:docker", "managed": True, "notes": "人工备注"
    }


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_hub_store_keeps_auto_snapshot_visible_but_refuses_to_overwrite_corrupt_manual_ops():
    result = _run_hub_store_ops(
        '{"services": [',
        {
            "generatedAt": "2026-07-30T13:00:00+08:00",
            "machines": [{"id": "host", "status": "down"}],
            "services": [{"id": "managed:host:docker", "managed": True, "name": "Docker", "status": "down"}],
        },
        write_back=True,
    )
    assert result["data"]["machines"][0]["id"] == "host"
    assert "writeError" in result and "禁止覆盖" in result["writeError"]
    assert result["saved"] is None

    empty = _run_hub_store_ops(
        "",
        {
            "machines": [{"id": "host", "status": "down"}],
            "services": [{"id": "managed:host:docker", "managed": True, "name": "Docker", "status": "down"}],
        },
        write_back=True,
    )
    assert empty["data"]["services"][0]["id"] == "managed:host:docker"
    assert "禁止覆盖" in empty["writeError"]
    assert empty["saved"] is None


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_hub_store_three_way_merges_concurrent_rows_and_rejects_same_id_conflict():
    initial = {"services": [], "commands": []}
    remote = {"services": [], "commands": [{"id": "remote", "label": "Remote", "command": "date"}]}
    local = {"id": "local", "label": "Local", "command": "uptime"}
    merged = _run_hub_store_ops(initial, write_back=True, remote_after_read=remote, local_command=local)
    assert {row["id"] for row in merged["saved"]["commands"]} == {"local", "remote"}

    base = {"services": [], "commands": [{"id": "same", "label": "Base", "command": "date"}]}
    remote_conflict = {"services": [], "commands": [{"id": "same", "label": "Remote", "command": "date"}]}
    local_conflict = {"id": "same", "label": "Local", "command": "uptime"}
    conflict = _run_hub_store_ops(
        base, write_back=True, remote_after_read=remote_conflict, local_command=local_conflict
    )
    assert conflict["saved"] is None
    assert "已被其他页面或 Agent 修改" in conflict["writeError"]


def test_hub_init_endpoint_creates_and_tightens_private_data(test_server, cleanup_test_sessions, tmp_path):
    from tests.test_sprint4 import make_session_tracked, post

    hub = tmp_path / "new-hub"
    hub.mkdir(mode=0o755)
    existing = hub / "hub-ops.json"
    existing.write_text('{"services":[{"id":"keep"}],"commands":[]}', encoding="utf-8")
    existing.chmod(0o644)
    _, add_status = post("/api/workspaces/add", {"path": str(hub), "name": "Hub init test"})
    assert add_status == 200
    sid, _ = make_session_tracked(cleanup_test_sessions, ws=hub)

    result, status = post("/api/hub/init", {"session_id": sid})

    assert status == 200 and result["ok"] is True
    assert hub.stat().st_mode & 0o777 == 0o700
    assert json.loads(existing.read_text(encoding="utf-8"))["services"][0]["id"] == "keep"
    expected = {
        "hub-profile.json",
        "hub-design.json",
        "hub-ops.json",
        "hub-ops-auto.json",
        "hub-resources.json",
        "hub-inbox.json",
    }
    assert expected <= {path.name for path in hub.iterdir()}
    assert all((hub / name).stat().st_mode & 0o777 == 0o600 for name in expected)
    auto = json.loads((hub / "hub-ops-auto.json").read_text(encoding="utf-8"))
    assert auto["machines"] == [] and auto["services"] == []


def test_hub_init_endpoint_rejects_symlinked_fixed_file(test_server, cleanup_test_sessions, tmp_path):
    from tests.test_sprint4 import make_session_tracked, post

    hub = tmp_path / "symlink-hub"
    hub.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep":true}', encoding="utf-8")
    (hub / "hub-ops.json").symlink_to(outside)
    _, add_status = post("/api/workspaces/add", {"path": str(hub), "name": "Hub symlink test"})
    assert add_status == 200
    sid, _ = make_session_tracked(cleanup_test_sessions, ws=hub)

    result, status = post("/api/hub/init", {"session_id": sid})

    assert status == 400
    assert "symlinked Hub file" in result["error"]
    assert outside.read_text(encoding="utf-8") == '{"keep":true}'


def test_ops_dashboard_static_contract_exposes_machine_visualization_and_filtering():
    js = HUB_JS.read_text(encoding="utf-8")
    css = HUB_CSS.read_text(encoding="utf-8")

    for required in (
        "opsOwner: 'personal'",
        "data-hub-owner-filter",
        "renderOpsOverview",
        "renderMachineCard",
        "renderResourceBars",
        "hub-machine-grid",
        "hub-ops-metrics",
        "hub-resource-bar",
    ):
        assert required in js or required in css

    assert "machineId" in js
    assert "generatedAt" in js
    assert "最近同步" in js
    assert "个人" in js and "公司" in js and "全部" in js


def test_ops_dashboard_static_contract_marks_stale_and_unknown_as_not_healthy():
    js = HUB_JS.read_text(encoding="utf-8")
    css = HUB_CSS.read_text(encoding="utf-8")

    assert "function isStale" in js
    assert "statusToHub" in js
    assert "unknown" in js and "critical" in js and "warning" in js
    assert js.find("return 'down';", js.find("function statusToHub")) < js.find("if (stale", js.find("function statusToHub"))
    assert "过期" in js
    assert ".hub-status.stale" in css
    assert ".hub-dot.stale" in css
    assert "AUTO_REFRESH_MS" in js and "visibilitychange" in js and "window.addEventListener('focus'" in js
    assert "if (view.form) return" in js
    assert "Number.isFinite(itemAge)" in js and "itemAge > 0" in js


def test_ops_dashboard_static_contract_protects_automatic_services_and_enriches_agent_prompt():
    js = HUB_JS.read_text(encoding="utf-8")

    assert "managed" in js
    assert "isManagedService" in js
    assert "自动登记" in js
    assert "del-service" in js
    assert "编辑自动服务备注" in js
    assert "servicePrompt" in js
    assert "machineId" in js[js.find("function servicePrompt") : js.find("function onInput")]
    assert "最近采集" in js[js.find("function servicePrompt") : js.find("function onInput")]
    assert "详情" in js[js.find("function servicePrompt") : js.find("function onInput")]


def test_hub_scaffold_readme_documents_new_ops_contract():
    store = HUB_STORE_JS.read_text(encoding="utf-8")

    assert "machines:[{id,name,ownership,role,host,region,os,resources,status,checks}]" in store
    assert "generatedAt" in store
    assert "source" in store
    assert "hub-ops-auto.json" in store
    assert "界面永不写入" in store
