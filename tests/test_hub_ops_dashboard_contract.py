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
    local_maintenance: dict | None = None,
    local_acknowledgement: dict | None = None,
    hub_init_status: int = 200,
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
const localMaintenance = {json.dumps(local_maintenance, ensure_ascii=False)};
const localAcknowledgement = {json.dumps(local_acknowledgement, ensure_ascii=False)};
const hubInitStatus = {json.dumps(hub_init_status)};
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
  if (path === '/api/hub/init') {{
    if (hubInitStatus === 404) return Promise.reject(new Error('not found'));
    return Promise.resolve({{ok: true}});
  }}
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
  .then(data => {{
    if (localCommand) data.commands.push(localCommand);
    if (localMaintenance) data.maintenance.push(localMaintenance);
    if (localAcknowledgement) data.acknowledgements.push(localAcknowledgement);
    return {'window.HubStore.write("ops", data).then(() => ({data, saved: JSON.parse(saved)}), err => ({data, saved, writeError: err.message}))' if write_back else '({data})'};
  }})
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
def test_hub_store_keeps_existing_hub_visible_when_secure_init_endpoint_is_absent():
    result = _run_hub_store_ops(
        {"services": [{"id": "manual-site", "name": "Manual", "status": "ok"}], "commands": []},
        hub_init_status=404,
    )

    assert result["data"]["services"][0]["id"] == "manual-site"


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
def test_hub_store_preserves_manual_maintenance_and_acknowledgements_when_saving_ops():
    result = _run_hub_store_ops(
        {
            "services": [],
            "commands": [],
            "maintenance": [{
                "id": "maint-1", "entityType": "service", "entityId": "managed:host:docker",
                "startsAt": "2026-08-01T10:00:00+08:00", "endsAt": "2026-08-01T11:00:00+08:00",
                "reason": "版本升级",
            }],
            "acknowledgements": [{
                "id": "ack-1", "eventId": "event-1", "createdAt": "2026-08-01T10:01:00+08:00",
                "note": "已确认",
            }],
        },
        {
            "events": [{
                "id": "event-auto-1", "entityType": "service", "entityId": "managed:host:docker",
                "statusChangedAt": "2026-08-01T10:00:00+08:00", "lifecycleSource": "monitor",
            }],
        },
        write_back=True,
        local_command={"id": "local", "label": "Local", "command": "uptime"},
    )

    data, saved = result["data"], result["saved"]
    assert data["events"][0]["id"] == "event-auto-1"
    assert data["maintenance"][0]["reason"] == "版本升级"
    assert data["acknowledgements"][0]["eventId"] == "event-1"
    assert "events" not in saved
    assert saved["maintenance"][0]["entityId"] == "managed:host:docker"
    assert saved["acknowledgements"][0]["note"] == "已确认"


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_hub_store_three_way_merges_maintenance_and_acknowledgement_rows():
    initial = {"services": [], "commands": [], "maintenance": [], "acknowledgements": []}
    remote = {
        "services": [], "commands": [],
        "maintenance": [{"id": "remote-maint", "entityType": "machine", "entityId": "host", "reason": "机房维护"}],
        "acknowledgements": [{"id": "remote-ack", "eventId": "event-r", "note": "remote"}],
    }
    merged = _run_hub_store_ops(
        initial,
        write_back=True,
        remote_after_read=remote,
        local_maintenance={"id": "local-maint", "entityType": "service", "entityId": "svc", "reason": "deploy"},
        local_acknowledgement={"id": "local-ack", "eventId": "event-l", "note": "local"},
    )
    assert {row["id"] for row in merged["saved"]["maintenance"]} == {"local-maint", "remote-maint"}
    assert {row["id"] for row in merged["saved"]["acknowledgements"]} == {"local-ack", "remote-ack"}

    base = {
        "services": [], "commands": [],
        "maintenance": [{"id": "same-maint", "entityType": "machine", "entityId": "host", "reason": "base"}],
        "acknowledgements": [{"id": "same-ack", "eventId": "event-1", "note": "base"}],
    }
    remote_conflict = {
        "services": [], "commands": [],
        "maintenance": [{"id": "same-maint", "entityType": "machine", "entityId": "host", "reason": "remote"}],
        "acknowledgements": [{"id": "same-ack", "eventId": "event-1", "note": "base"}],
    }
    conflict = _run_hub_store_ops(
        base,
        write_back=True,
        remote_after_read=remote_conflict,
        local_maintenance={"id": "same-maint", "entityType": "machine", "entityId": "host", "reason": "local"},
    )
    assert conflict["saved"] is None
    assert "维护窗口「same-maint」已被其他页面或 Agent 修改" in conflict["writeError"]


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


def test_ops_dashboard_2_0_exposes_primary_views_filters_table_and_service_drawer():
    js = HUB_JS.read_text(encoding="utf-8")
    css = HUB_CSS.read_text(encoding="utf-8")

    for required in (
        "opsView: 'servers'",
        "opsStatus: 'all'",
        "opsKind: 'all'",
        "opsQuery: ''",
        "opsSelectedService: ''",
        "data-hub-ops-view",
        "data-hub-status-filter",
        "data-hub-kind-filter",
        "data-hub-ops-query",
        "renderServiceTable",
        "renderServiceDrawer",
        "hub-ops-view-tabs",
        "hub-service-table",
        "hub-service-drawer",
    ):
        assert required in js or required in css

    assert "服务器" in js and "服务" in js and "异常" in js
    assert "搜索服务、主机、IP、端口或 unit" in js
    assert "启动方式" in js and "监听" in js and "最近采集" in js
    assert "<th>最近变化</th>" not in js


def test_ops_dashboard_2_0_search_filter_is_local_only():
    js = HUB_JS.read_text(encoding="utf-8")

    on_input = js[js.find("function onInput") : js.find("function onSubmit")]

    assert "data-hub-ops-query" in on_input
    assert "view.opsQuery = e.target.value" in on_input
    assert "refreshOpsView();" in on_input
    assert "HubStore.write" not in on_input
    assert "save(" not in on_input


def test_ops_dashboard_2_0_filters_do_not_discard_unsaved_service_form_input():
    js = HUB_JS.read_text(encoding="utf-8")

    on_input = js[js.find("function onInput") : js.find("function onSubmit")]
    on_change = js[js.find("function onChange") : js.find("function onSubmit")]
    on_click = js[js.find("function onClick") : js.find("function onSubmitQuickCapture")]

    assert "if (view.form) return" in on_input
    assert "if (view.form)" in on_change and "请先保存或取消当前编辑" in on_change
    assert "function guardOpsForm" in js
    assert "guardOpsForm()" in on_click
    reload = js[js.find("function reload()") : js.find("function render()")]
    assert "if (view.form)" in reload
    assert "请先保存或取消当前编辑，再重新读取数据" in reload


def test_ops_dashboard_2_0_drawer_and_machine_linkage_are_accessible_read_only():
    js = HUB_JS.read_text(encoding="utf-8")

    drawer = js[js.find("function renderServiceDrawer") : js.find("function commandRow")]
    click = js[js.find("function onClick") :]
    readonly = js[js.find("function isReadOnlyCommand") : js.find("function readOnlyCommandsForService")]

    assert 'role="dialog"' in drawer
    assert 'aria-modal="true"' in drawer
    assert 'aria-labelledby="hubServiceDrawerTitle"' in drawer
    assert 'data-hub-action="close-service-drawer"' in drawer
    assert "focusServiceDrawerClose" in js
    assert "e.key === 'Escape'" in js
    assert "restoreServiceFocus" in js
    assert "serviceId: id || ''" in js
    assert "document.querySelectorAll('[data-hub-id]')" in js
    assert "document.querySelectorAll('[data-hub-service-row]')" in js
    assert "data-hub-action=\"machine-services\"" in js
    assert "view.opsMachine = id || ''" in click
    assert "view.opsView = 'services'" in click
    assert "clear-machine-filter" in click
    assert "copy-service-command" in drawer and "copyText(btn.getAttribute('data-hub-copy')" in click
    assert "systemctl status " in readonly
    assert "docker ps" in readonly
    assert "pm2 status " in readonly
    assert "journalctl " not in js[js.find("function readOnlyCommandsForService") : js.find("function renderServiceFields")]
    assert "docker logs" not in readonly
    assert "docker inspect" not in readonly
    assert "pm2 logs" not in readonly
    assert "systemctl restart" not in readonly
    assert "systemctl stop" not in readonly
    assert "systemctl start" not in readonly


def test_ops_dashboard_2_0_service_links_do_not_also_open_the_drawer():
    js = HUB_JS.read_text(encoding="utf-8")

    on_keydown = js[js.find("function onKeydown") : js.find("function onInput")]
    on_click = js[js.find("function onClick") : js.find("function onSubmitQuickCapture")]

    assert "e.target.closest('a')" in on_click
    assert "e.target.closest('a, button, input, select, textarea')" in on_keydown


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


def test_ops_dashboard_static_contract_displays_discovery_startup_and_listener_metadata():
    js = HUB_JS.read_text(encoding="utf-8")

    card = js[js.find("function serviceCard") : js.find("function commandRow")]
    assert "启动：" in card
    assert "监听：" in card
    assert "s.startup" in card
    assert "s.listen" in card
    assert "s.kind" in card


def test_ops_lifecycle_contract_exposes_fact_only_events_acknowledgements_and_maintenance():
    js = HUB_JS.read_text(encoding="utf-8")
    store = HUB_STORE_JS.read_text(encoding="utf-8")
    css = HUB_CSS.read_text(encoding="utf-8")

    for required in (
        "events: (automatic.events || [])",
        "maintenance: (manual.maintenance || [])",
        "acknowledgements: (manual.acknowledgements || [])",
        "mergeRows(base.maintenance",
        "mergeRows(base.acknowledgements",
        "renderOpsLifecycle",
        "renderLifecycleEvent",
        "data-hub-action=\"ack-event\"",
        "data-hub-action=\"new-maintenance\"",
        "data-hub-form=\"maintenance\"",
        "statusChangedAt",
        "incidentOpenedAt",
        "lifecycleSource",
        "hub-lifecycle",
        "hub-maintenance",
    ):
        assert required in js or required in store or required in css

    assert "最近变化" not in js
    assert "window.api(" not in js[js.find("function renderOpsLifecycle") : js.find("function onSubmit")]
    assert "systemctl restart" not in js[js.find("function renderOpsLifecycle") :]
    assert "docker logs" not in js[js.find("function renderOpsLifecycle") :]
    assert "docker inspect" not in js[js.find("function renderOpsLifecycle") :]


def test_hub_scaffold_readme_documents_new_ops_contract():
    store = HUB_STORE_JS.read_text(encoding="utf-8")

    assert "machines:[{id,name,ownership,role,host,region,os,resources,status,checks}]" in store
    assert "generatedAt" in store
    assert "source" in store
    assert "hub-ops-auto.json" in store
    assert "界面永不写入" in store
