"""Contract and behavioral coverage for the first-class Hermes Hub meeting module."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HUB_STORE_JS = ROOT / "extensions" / "hub" / "hub-store.js"
HUB_JS = ROOT / "extensions" / "hub" / "hub.js"
HUB_CSS = ROOT / "extensions" / "hub" / "hub.css"
ROUTES_PY = ROOT / "api" / "routes.py"
EXTENSIONS_README = ROOT / "extensions" / "README.md"
NODE = shutil.which("node")


def _run_meeting_store_round_trip(meetings: dict) -> dict:
    assert NODE, "node is required for hub-store behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const original = {json.dumps(meetings, ensure_ascii=False)};
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
    if (decoded.includes('hub-meetings.json')) return Promise.resolve({{content: JSON.stringify(original)}});
    return Promise.reject(new Error('unexpected read: ' + decoded));
  }}
  if (path === '/api/file/save') {{ saved = JSON.parse(opts.body).content; return Promise.resolve({{ok:true}}); }}
  return Promise.reject(new Error('unexpected api path: ' + path));
}};
vm.runInThisContext(src, {{filename: 'hub-store.js'}});
window.HubStore.init()
  .then(() => window.HubStore.read('meetings'))
  .then(data => {{
    data.items[0].actionItems[0].status = 'done';
    data.items.push({{
      id: 'meeting-2', title: '新增评审', type: 'review', status: 'planned',
      participants: [], projectLinks: [], decisions: [], actionItems: [], risks: [], openQuestions: []
    }});
    return window.HubStore.write('meetings', data).then(() => ({{data, saved: JSON.parse(saved)}}));
  }})
  .then(result => process.stdout.write(JSON.stringify(result)))
  .catch(err => {{ console.error(err && err.stack || err); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _run_meeting_store_normalize(meetings: dict) -> dict:
    assert NODE, "node is required for hub-store behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const original = {json.dumps(meetings, ensure_ascii=False)};
const storage = {{'hermes-hub.session': 'sid-1', 'hermes-hub.root': '/tmp/hub'}};
global.localStorage = {{
  getItem(key) {{ return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null; }},
  setItem(key, value) {{ storage[key] = String(value); }},
  removeItem(key) {{ delete storage[key]; }}
}};
global.window = {{}};
window.api = function(path) {{
  if (path === '/api/hub/init') return Promise.resolve({{ok: true}});
  if (path.startsWith('/api/list?')) return Promise.resolve({{items: []}});
  if (path.startsWith('/api/file?')) return Promise.resolve({{content: JSON.stringify(original)}});
  return Promise.reject(new Error('unexpected api path: ' + path));
}};
vm.runInThisContext(src, {{filename: 'hub-store.js'}});
window.HubStore.init()
  .then(() => window.HubStore.read('meetings'))
  .then(data => process.stdout.write(JSON.stringify(data)))
  .catch(err => {{ console.error(err && err.stack || err); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_round_trips_structured_minutes_without_flattening_actions():
    original = {
        "schemaVersion": 2,
        "agentMetadata": {"source": "teams-import"},
        "items": [
            {
                "id": "meeting-1",
                "title": "发布评审",
                "type": "decision",
                "status": "completed",
                "startAt": "2026-08-05T09:00:00+08:00",
                "endAt": "2026-08-05T10:00:00+08:00",
                "participants": ["林晓", "周宁"],
                "projectLinks": ["https://example.test/project/1"],
                "summary": "确认发布范围",
                "decisions": ["先灰度 10%"],
                "actionItems": [
                    {
                        "id": "action-1",
                        "title": "准备灰度清单",
                        "owner": "林晓",
                        "due": "2026-08-06",
                        "deliverable": "灰度用户 CSV",
                        "acceptance": "产品与运维共同签字",
                        "dependencies": "运维先确认回滚脚本",
                        "status": "open",
                        "agentTraceId": "trace-123",
                        "labels": ["release", "priority"],
                    }
                ],
                "risks": ["回滚窗口较短"],
                "openQuestions": ["海外区域是否同步"],
                "transcriptFile": "records/review.txt",
                "minutesFile": "minutes/review.md",
                "nextReviewAt": "2026-08-06T16:00:00+08:00",
            }
        ]
    }

    result = _run_meeting_store_round_trip(original)

    assert result["data"]["items"][0]["actionItems"][0]["status"] == "done"
    assert result["saved"]["items"][0]["decisions"] == ["先灰度 10%"]
    assert result["saved"]["items"][0]["actionItems"][0]["acceptance"] == "产品与运维共同签字"
    assert result["saved"]["items"][0]["actionItems"][0]["dependencies"] == "运维先确认回滚脚本"
    assert result["saved"]["items"][0]["actionItems"][0]["agentTraceId"] == "trace-123"
    assert result["saved"]["items"][0]["actionItems"][0]["labels"] == ["release", "priority"]
    assert result["saved"]["schemaVersion"] == 2
    assert result["saved"]["agentMetadata"] == {"source": "teams-import"}
    assert result["saved"]["items"][1]["id"] == "meeting-2"


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_normalizes_legacy_and_agent_edited_nested_fields():
    normalized = _run_meeting_store_normalize(
        {
            "items": [
                {
                    "id": 123,
                    "title": 456,
                    "participants": "林晓, 周宁",
                    "projectLinks": None,
                    "decisions": "先灰度",
                    "actionItems": [None, {"id": 9, "title": 7, "status": "unknown"}],
                    "risks": {"bad": True},
                    "openQuestions": ["海外区域？", None],
                }
            ]
        }
    )
    meeting = normalized["items"][0]
    assert meeting["id"] == "123"
    assert meeting["title"] == "456"
    assert meeting["participants"] == ["林晓", "周宁"]
    assert meeting["projectLinks"] == []
    assert meeting["decisions"] == ["先灰度"]
    assert meeting["risks"] == []
    assert meeting["openQuestions"] == ["海外区域？"]
    assert meeting["actionItems"] == [
        {
            "id": "9",
            "title": "7",
            "owner": "",
            "due": "",
            "deliverable": "",
            "acceptance": "",
            "dependencies": "",
            "status": "open",
            "updatedAt": "",
        }
    ]


def _run_meeting_concurrent_write(
    base: dict, remote: dict, *, local_root_updates: dict | None = None
) -> dict:
    assert NODE, "node is required for hub-store behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const base = {json.dumps(base, ensure_ascii=False)};
const remote = {json.dumps(remote, ensure_ascii=False)};
const localRootUpdates = {json.dumps(local_root_updates or {}, ensure_ascii=False)};
let fileReads = 0;
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
    fileReads += 1;
    return Promise.resolve({{content: JSON.stringify(fileReads === 1 ? base : remote)}});
  }}
  if (path === '/api/file/save') {{ saved = JSON.parse(opts.body).content; return Promise.resolve({{ok:true}}); }}
  return Promise.reject(new Error('unexpected api path: ' + path));
}};
vm.runInThisContext(src, {{filename: 'hub-store.js'}});
window.HubStore.init()
  .then(() => window.HubStore.read('meetings'))
  .then(data => {{
    if (data.items.length) data.items[0].status = 'completed';
    Object.assign(data, localRootUpdates);
    return window.HubStore.write('meetings', data);
  }})
  .then(() => process.stdout.write(JSON.stringify({{ok:true, saved:JSON.parse(saved)}})))
  .catch(err => process.stdout.write(JSON.stringify({{ok:false, error:String(err && err.message || err)}})));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_three_way_merges_unrelated_remote_rows():
    base = {"items": [{"id": "m1", "title": "周会", "status": "planned"}]}
    remote = {
        "items": [
            {"id": "m1", "title": "周会", "status": "planned"},
            {"id": "m2", "title": "Agent 新增会议", "status": "planned"},
        ]
    }
    result = _run_meeting_concurrent_write(base, remote)
    assert result["ok"] is True
    by_id = {item["id"]: item for item in result["saved"]["items"]}
    assert by_id["m1"]["status"] == "completed"
    assert by_id["m2"]["title"] == "Agent 新增会议"


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_rejects_conflicting_remote_edit_of_same_row():
    base = {"items": [{"id": "m1", "title": "周会", "status": "planned"}]}
    remote = {"items": [{"id": "m1", "title": "Agent 改名", "status": "planned"}]}
    result = _run_meeting_concurrent_write(base, remote)
    assert result["ok"] is False
    assert "已被其他页面或 Agent 修改" in result["error"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_merges_root_metadata_and_rejects_same_field_conflicts():
    base = {
        "schemaVersion": 2,
        "items": [{"id": "m1", "title": "周会", "status": "planned"}],
    }
    remote = {
        "schemaVersion": 2,
        "agentMetadata": {"source": "teams"},
        "items": [{"id": "m1", "title": "周会", "status": "planned"}],
    }
    result = _run_meeting_concurrent_write(base, remote)
    assert result["ok"] is True
    assert result["saved"]["schemaVersion"] == 2
    assert result["saved"]["agentMetadata"] == {"source": "teams"}

    conflict_base = dict(base, schemaVersion=2)
    conflict_remote = dict(remote, schemaVersion=3)
    result = _run_meeting_concurrent_write(
        conflict_base, conflict_remote, local_root_updates={"schemaVersion": 4}
    )
    assert result["ok"] is False
    assert "会议文件字段「schemaVersion」已被其他页面或 Agent 修改" in result["error"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_rejects_duplicate_row_ids_instead_of_silently_collapsing_them():
    base = {"items": [{"id": "m1", "title": "周会", "status": "planned"}]}
    remote = {"items": [{"id": "m1", "title": "周会", "status": "planned"}]}
    result = _run_meeting_concurrent_write(base, remote)
    assert result["ok"] is True

    duplicate_local = {
        "items": [
            {"id": "m1", "title": "周会", "status": "completed"},
            {"id": "m1", "title": "重复周会", "status": "planned"},
        ]
    }
    result = _run_meeting_concurrent_write(duplicate_local, duplicate_local)
    assert result["ok"] is False
    assert "重复 id" in result["error"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_store_rejects_duplicate_action_ids_on_save():
    duplicate_actions = {
        "items": [
            {
                "id": "m1",
                "title": "周会",
                "status": "planned",
                "actionItems": [
                    {"id": "a1", "title": "事项一"},
                    {"id": "a1", "title": "事项二"},
                ],
            }
        ]
    }
    result = _run_meeting_concurrent_write(duplicate_actions, duplicate_actions)
    assert result["ok"] is False
    assert "行动项存在重复 id" in result["error"]


def _run_strict_meeting_read_on_corrupt_json() -> dict:
    assert NODE, "node is required for hub-store behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const storage = {{'hermes-hub.session':'sid-1','hermes-hub.root':'/tmp/hub'}};
global.localStorage = {{
  getItem(key) {{ return Object.prototype.hasOwnProperty.call(storage,key) ? storage[key] : null; }},
  setItem(key,value) {{ storage[key]=String(value); }},
  removeItem(key) {{ delete storage[key]; }}
}};
global.window = {{}};
window.api = function(path) {{
  if (path === '/api/hub/init') return Promise.resolve({{ok:true}});
  if (path.startsWith('/api/list?')) return Promise.resolve({{items:[]}});
  if (path.startsWith('/api/file?')) return Promise.resolve({{content:'{{broken json'}});
  return Promise.reject(new Error('unexpected api path: '+path));
}};
vm.runInThisContext(src, {{filename:'hub-store.js'}});
let normal = null;
window.HubStore.init()
  .then(() => window.HubStore.read('meetings'))
  .then(data => {{ normal = data; window.HubStore.invalidate(); return window.HubStore.read('meetings', {{strict:true}}); }})
  .then(() => process.stdout.write(JSON.stringify({{normal, strictRejected:false}})))
  .catch(err => process.stdout.write(JSON.stringify({{normal, strictRejected:true, error:String(err && err.message || err)}})));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_strict_meeting_reload_rejects_corrupt_json_while_normal_read_stays_safe():
    result = _run_strict_meeting_read_on_corrupt_json()
    assert result["normal"] == {"items": []}
    assert result["strictRejected"] is True
    assert "hub-meetings.json" in result["error"]


def test_meeting_store_corruption_guard_and_merge_contract_are_present():
    store = HUB_STORE_JS.read_text(encoding="utf-8")
    assert "meetingsBase" in store
    assert "mergeRows(meetingsBase.items, localMeetings.items, remote.items, '会议')" in store
    assert "hub-meetings.json 无法解析，已禁止覆盖" in store


def test_meeting_scaffold_and_secure_init_allowlist_are_additive_and_backward_compatible():
    store = HUB_STORE_JS.read_text(encoding="utf-8")
    routes = ROUTES_PY.read_text(encoding="utf-8")

    assert "meetings: 'hub-meetings.json'" in store
    assert "meetings: function () { return { items: [] }; }" in store
    assert "`hub-meetings.json`" in store
    assert '"hub-meetings.json": {"items": []}' in routes

    for existing in (
        "hub-profile.json",
        "hub-design.json",
        "hub-ops.json",
        "hub-ops-auto.json",
        "hub-resources.json",
        "hub-inbox.json",
    ):
        assert existing in store
        assert existing in routes


def _run_meeting_ui_draft_preservation() -> dict:
    assert NODE, "node is required for Hub meeting UI behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync({json.dumps(str(HUB_JS))}, 'utf8');
const marker = '\\n}})();';
const exportAt = src.lastIndexOf(marker);
const testExport = `
window.__hermesHubTest = {{
  setMeetingState(meetings, id) {{
    view.module = 'meetings';
    view.data = {{
      profile: {{}}, design: {{items:[]}}, meetings,
      ops: {{services:[],commands:[],events:[],maintenance:[],acknowledgements:[]}},
      resources: {{items:[]}}, inbox: {{items:[]}}
    }};
    view.form = {{kind:'meeting', id:id || ''}};
    const meeting = findById(meetings.items || [], id);
    view.meetingDraft = Object.assign({{}}, meeting || {{}});
    view.meetingActionDrafts = meeting && Array.isArray(meeting.actionItems)
      ? meeting.actionItems.map(action => Object.assign(blankMeetingAction(), action)) : [];
  }},
  addMeetingActionDraft() {{ syncMeetingFormDrafts(); view.meetingActionDrafts.push(blankMeetingAction()); }},
  meetingForm,
  getMeetingState() {{ return {{draft:view.meetingDraft, actionCount:(view.meetingActionDrafts || []).length}}; }}
}};`;
src = src.slice(0, exportAt) + testExport + src.slice(exportAt);
const values = {{
  title: '未保存标题', type: 'review', status: 'in_progress', startAt: '2026-08-05T09:00',
  endAt: '2026-08-05T10:00', nextReviewAt: '2026-08-06T09:00', participants: '甲,乙',
  projectLinks: 'https://example.test/p', transcriptFile: 'draft.txt', minutesFile: 'draft.md',
  summary: '未保存摘要', decisions: '未保存决策', risks: '未保存风险', openQuestions: '未保存问题'
}};
const form = {{}};
global.FormData = class {{
  constructor(node) {{ if (node !== form) throw new Error('unexpected form'); }}
  get(key) {{ return values[key] || ''; }}
  getAll() {{ return []; }}
}};
global.navigator = {{}};
global.document = {{
  readyState: 'loading',
  addEventListener() {{}},
  querySelector(selector) {{ return selector === '[data-hub-form="meeting"]' ? form : null; }},
  querySelectorAll() {{ return []; }},
  getElementById() {{ return null; }}
}};
global.window = {{__HERMES_HUB_TEST__: true}};
vm.runInThisContext(src, {{filename: 'hub.js'}});
const api = window.__hermesHubTest;
if (!api) throw new Error('missing Hub test interface');
api.setMeetingState({{items:[{{id:'m1',title:'已保存标题',type:'sync',status:'planned',actionItems:[]}}]}}, 'm1');
api.addMeetingActionDraft();
process.stdout.write(JSON.stringify({{html:api.meetingForm(), state:api.getMeetingState()}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_add_action_preserves_all_unsaved_meeting_form_fields():
    result = _run_meeting_ui_draft_preservation()
    html = result["html"]
    assert 'value="未保存标题"' in html
    assert '未保存摘要' in html
    assert '未保存决策' in html
    assert '未保存风险' in html
    assert '未保存问题' in html
    assert 'value="draft.txt"' in html
    assert 'value="draft.md"' in html
    assert result["state"]["actionCount"] == 1


def _run_meeting_ui_failed_save() -> dict:
    assert NODE, "node is required for Hub meeting UI behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync({json.dumps(str(HUB_JS))}, 'utf8');
const marker = '\\n}})();';
const exportAt = src.lastIndexOf(marker);
const testExport = `
window.__hermesHubTest = {{
  setMeetingState(meetings, id) {{
    view.module = 'meetings';
    view.data = {{
      profile: {{}}, design: {{items:[]}}, meetings,
      ops: {{services:[],commands:[],events:[],maintenance:[],acknowledgements:[]}},
      resources: {{items:[]}}, inbox: {{items:[]}}
    }};
    view.form = {{kind:'meeting', id:id || ''}};
    const meeting = findById(meetings.items || [], id);
    view.meetingDraft = Object.assign({{}}, meeting || {{}});
    view.meetingActionDrafts = meeting && Array.isArray(meeting.actionItems)
      ? meeting.actionItems.map(action => Object.assign(blankMeetingAction(), action)) : [];
  }},
  setMeetingDraft(draft) {{
    view.meetingDraft = Object.assign({{}}, draft || {{}});
    view.meetingActionDrafts = (draft && draft.actionItems || []).map(action => Object.assign(blankMeetingAction(), action));
  }},
  saveMeetingCandidate(candidate) {{ return saveMeetingsCandidate(candidate, '', true); }},
  getMeetingState() {{
    return {{
      draft:view.meetingDraft,
      actionCount:(view.meetingActionDrafts || []).length,
      persistedTitle:view.data.meetings.items[0] && view.data.meetings.items[0].title,
      formOpen:!!view.form
    }};
  }}
}};`;
src = src.slice(0, exportAt) + testExport + src.slice(exportAt);
const remote = {{items:[{{id:'m1',title:'Agent 远端版本',type:'sync',status:'planned',actionItems:[]}}]}};
let invalidated = false;
global.navigator = {{}};
global.document = {{readyState:'loading', addEventListener() {{}}, querySelector() {{return null;}}, querySelectorAll() {{return [];}}, getElementById() {{return null;}}}};
global.window = {{__HERMES_HUB_TEST__: true, showToast() {{}}}};
global.HubStore = {{
  write() {{ return Promise.reject(new Error('并发冲突')); }},
  invalidate() {{ invalidated = true; }},
  read() {{ return Promise.resolve(remote); }}
}};
vm.runInThisContext(src, {{filename:'hub.js'}});
const api = window.__hermesHubTest;
api.setMeetingState({{items:[{{id:'m1',title:'原始版本',type:'sync',status:'planned',actionItems:[]}}]}}, 'm1');
api.setMeetingDraft({{id:'m1',title:'我的未保存版本',type:'review',status:'in_progress',actionItems:[]}});
api.saveMeetingCandidate({{items:[{{id:'m1',title:'我的未保存版本',type:'review',status:'in_progress',actionItems:[]}}]}})
  .then(() => process.stdout.write(JSON.stringify({{invalidated, state:api.getMeetingState()}})))
  .catch(err => {{ console.error(err && err.stack || err); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_failed_meeting_save_reloads_remote_without_losing_user_draft():
    result = _run_meeting_ui_failed_save()
    assert result["invalidated"] is True
    assert result["state"]["persistedTitle"] == "Agent 远端版本"
    assert result["state"]["draft"]["title"] == "我的未保存版本"
    assert result["state"]["formOpen"] is True


def _run_meeting_ui_failed_save_and_failed_reload() -> dict:
    assert NODE, "node is required for Hub meeting UI behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync({json.dumps(str(HUB_JS))}, 'utf8');
const marker = '\\n}})();';
const exportAt = src.lastIndexOf(marker);
const testExport = `
window.__hermesHubTest = {{
  setMeetingState(meetings, id) {{
    view.module = 'meetings';
    view.data = {{profile:{{}}, design:{{items:[]}}, meetings,
      ops:{{services:[],commands:[],events:[],maintenance:[],acknowledgements:[]}},
      resources:{{items:[]}}, inbox:{{items:[]}}}};
    view.form = {{kind:'meeting', id:id || ''}};
    const meeting = findById(meetings.items || [], id);
    view.meetingDraft = Object.assign({{}}, meeting || {{}});
    view.meetingActionDrafts = [];
  }},
  saveMeetingCandidate(candidate) {{ return saveMeetingsCandidate(candidate, '', true); }},
  getMeetingState() {{ return {{
    persistedTitle:view.data.meetings.items[0] && view.data.meetings.items[0].title,
    draftTitle:view.meetingDraft && view.meetingDraft.title,
    formOpen:!!view.form
  }}; }}
}};`;
src = src.slice(0, exportAt) + testExport + src.slice(exportAt);
const toasts = [];
global.navigator = {{}};
global.document = {{readyState:'loading', addEventListener() {{}}, querySelector() {{return null;}}, querySelectorAll() {{return [];}}, getElementById() {{return null;}}}};
global.window = {{showToast(message) {{toasts.push(message);}}}};
global.HubStore = {{
  write() {{ return Promise.reject(new Error('保存冲突')); }},
  invalidate() {{}},
  read(key, options) {{
    if (options && options.strict) return Promise.reject(new Error('磁盘暂时不可读'));
    return Promise.resolve({{items:[]}});
  }}
}};
vm.runInThisContext(src, {{filename:'hub.js'}});
const api = window.__hermesHubTest;
api.setMeetingState({{items:[{{id:'m1',title:'最后可信版本',actionItems:[]}}]}}, 'm1');
api.saveMeetingCandidate({{items:[{{id:'m1',title:'未保存候选',actionItems:[]}}]}})
  .then(() => process.stdout.write(JSON.stringify({{state:api.getMeetingState(), toasts}})))
  .catch(err => {{ console.error(err && err.stack || err); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_failed_meeting_save_keeps_last_trusted_list_when_reload_also_fails():
    result = _run_meeting_ui_failed_save_and_failed_reload()
    assert result["state"]["persistedTitle"] == "最后可信版本"
    assert result["state"]["draftTitle"] == "最后可信版本"
    assert result["state"]["formOpen"] is True
    assert any("重新读取磁盘数据也失败" in message for message in result["toasts"])


def _run_metadata_only_action_ui_round_trip() -> dict:
    assert NODE, "node is required for Hub meeting UI behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync({json.dumps(str(HUB_JS))}, 'utf8');
const marker = '\\n}})();';
const exportAt = src.lastIndexOf(marker);
const testExport = `
window.__hermesHubTest = {{
  setMeetingState(meetings) {{
    view.module = 'meetings';
    view.data = {{profile:{{}}, design:{{items:[]}}, meetings,
      ops:{{services:[],commands:[],events:[],maintenance:[],acknowledgements:[]}},
      resources:{{items:[]}}, inbox:{{items:[]}}}};
    view.form = {{kind:'meeting', id:'m1'}};
    view.meetingDraft = Object.assign({{}}, meetings.items[0]);
    view.meetingActionDrafts = meetings.items[0].actionItems.map(action => Object.assign(blankMeetingAction(), action));
  }},
  submit(form) {{ onSubmit({{target:form, preventDefault() {{}}}}); }}
}};`;
src = src.slice(0, exportAt) + testExport + src.slice(exportAt);
const values = {{title:'周会', type:'sync', status:'planned'}};
const arrays = {{
  action_id:['a-meta','a-id-only',''], action_title:['','',''], action_owner:['','',''], action_due:['','',''],
  action_deliverable:['','',''], action_acceptance:['','',''], action_dependencies:['','',''], action_status:['open','blocked','open']
}};
const form = {{
  closest() {{ return this; }},
  getAttribute(name) {{ return name === 'data-hub-form' ? 'meeting' : ''; }}
}};
global.FormData = class {{
  constructor(node) {{ if (node !== form) throw new Error('unexpected form'); }}
  get(key) {{ return values[key] || ''; }}
  getAll(key) {{ return arrays[key] || []; }}
}};
global.navigator = {{}};
global.document = {{readyState:'loading', addEventListener() {{}}, querySelector() {{return null;}}, querySelectorAll() {{return [];}}, getElementById() {{return null;}}}};
let saved = null;
global.window = {{showToast() {{}}}};
global.HubStore = {{
  newId() {{ return 'generated'; }},
  write(key, candidate) {{ saved = JSON.parse(JSON.stringify(candidate)); return Promise.resolve(); }},
  read() {{ return Promise.resolve(saved); }},
  invalidate() {{}}
}};
vm.runInThisContext(src, {{filename:'hub.js'}});
const api = window.__hermesHubTest;
api.setMeetingState({{items:[{{id:'m1',title:'周会',type:'sync',status:'planned',actionItems:[
  {{id:'a-meta',agentTraceId:'trace-only',labels:['agent']}},
  {{id:'a-id-only',status:'blocked'}},
  {{id:'',status:'open'}}
]}}]}});
api.submit(form);
setTimeout(() => process.stdout.write(JSON.stringify(saved)), 0);
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_meeting_ui_preserves_metadata_only_action_when_editing_and_saving():
    saved = _run_metadata_only_action_ui_round_trip()
    actions = saved["items"][0]["actionItems"]
    assert len(actions) == 2
    assert actions[0]["id"] == "a-meta"
    assert actions[0]["agentTraceId"] == "trace-only"
    assert actions[0]["labels"] == ["agent"]
    assert actions[1]["id"] == "a-id-only"
    assert actions[1]["status"] == "blocked"


def test_meeting_save_is_transactional_in_source_contract():
    js = HUB_JS.read_text(encoding="utf-8")
    helper = js[js.index("function saveMeetingsCandidate") : js.index("/* ── 与 Agent 联动")]
    submit = js[js.index("if (kind === 'meeting')") : js.index("if (kind === 'service'")]
    delete = js[js.index("case 'del-meeting':") : js.index("case 'del-service':")]

    assert "HubStore.write('meetings', candidate)" in helper
    assert "return HubStore.read('meetings')" in helper
    assert "view.data.meetings = persisted" in helper
    assert "HubStore.invalidate()" in helper
    assert "view.data.meetings = remote" in helper
    assert "var meetingCandidate = JSON.parse(JSON.stringify(d.meetings))" in submit
    assert "Object.assign(currentMeeting, meetingPayload)" not in submit
    assert "Object.assign({}, (view.meetingActionDrafts || [])[index] || {}, {" in submit
    assert "var deleteCandidate = JSON.parse(JSON.stringify(d.meetings))" in delete
    assert "d.meetings.items =" not in delete


def test_meeting_ui_exposes_full_crud_minutes_fields_and_structured_action_editor():
    js = HUB_JS.read_text(encoding="utf-8")
    css = HUB_CSS.read_text(encoding="utf-8")

    for required in (
        "{ id: 'meetings', label: '会议'",
        "renderMeetings",
        "meetingForm",
        'data-hub-form="meeting"',
        'data-hub-action="new-meeting"',
        "edit-meeting",
        "del-meeting",
        "participants",
        "projectLinks",
        "summary",
        "decisions",
        "actionItems",
        "risks",
        "openQuestions",
        "transcriptFile",
        "minutesFile",
        "nextReviewAt",
        'data-hub-meeting-action',
        "field('action_owner'",
        "field('action_due'",
        "field('action_deliverable'",
        "field('action_acceptance'",
        "field('action_dependencies'",
        "selectField('action_status'",
        "add-meeting-action",
        "remove-meeting-action",
        "hub-meeting",
        "hub-action-item",
    ):
        assert required in js or required in css

    meeting_form = js[js.find("function meetingForm") : js.find("/* ── 项目运维")]
    meeting_render = js[js.find("function meetingCard") : js.find("function blankMeetingAction")]
    assert 'name="actionItems"' not in meeting_form
    assert "JSON.stringify" not in meeting_form
    assert "safeUrl(link)" in meeting_render
    for field in ("meeting.title", "meeting.summary", "meeting.transcriptFile", "meeting.minutesFile"):
        assert f"esc({field})" in meeting_render


def test_home_includes_meeting_action_stats_and_meeting_timeline_entries():
    js = HUB_JS.read_text(encoding="utf-8")
    home = js[js.find("function renderHome") : js.find("function renderDesign")]

    assert "d.meetings.items" in home
    assert "待办行动项" in home
    assert "逾期" in home
    assert "近期会议" in home
    assert "会议" in home[home.find("function collectRecent") :]


def test_hub_public_docs_describe_meeting_minutes_schema_and_files():
    readme = EXTENSIONS_README.read_text(encoding="utf-8")
    store = HUB_STORE_JS.read_text(encoding="utf-8")

    assert "hub-meetings.json" in readme
    assert "会议" in readme
    for field in (
        "participants",
        "projectLinks",
        "decisions",
        "actionItems",
        "deliverable",
        "acceptance",
        "dependencies",
        "transcriptFile",
        "minutesFile",
        "nextReviewAt",
    ):
        assert field in store
