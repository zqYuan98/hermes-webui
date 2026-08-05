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


def _run_meeting_concurrent_write(base: dict, remote: dict) -> dict:
    assert NODE, "node is required for hub-store behavioural tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const base = {json.dumps(base, ensure_ascii=False)};
const remote = {json.dumps(remote, ensure_ascii=False)};
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
  .then(data => {{ data.items[0].status = 'completed'; return window.HubStore.write('meetings', data); }})
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
