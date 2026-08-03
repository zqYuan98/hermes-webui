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


def test_hub_init_creates_private_meeting_store(test_server, cleanup_test_sessions, tmp_path):
    from tests.test_sprint4 import make_session_tracked, post

    hub = tmp_path / "meeting-hub"
    hub.mkdir(mode=0o755)
    _, add_status = post("/api/workspaces/add", {"path": str(hub), "name": "Meeting Hub"})
    assert add_status == 200
    sid, _ = make_session_tracked(cleanup_test_sessions, ws=hub)

    result, status = post("/api/hub/init", {"session_id": sid})

    meetings = hub / "hub-meetings.json"
    assert status == 200 and result["ok"] is True
    assert meetings.name in result["files"]
    assert json.loads(meetings.read_text(encoding="utf-8")) == {"items": []}
    assert meetings.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_hub_store_persists_meetings_and_reuses_media_endpoints():
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(HUB_STORE_JS))}, 'utf8');
const storage = {{'hermes-hub.session': 'sid-meeting', 'hermes-hub.root': '/tmp/hub'}};
const calls = [];
let saved = null;
global.localStorage = {{
  getItem(key) {{ return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null; }},
  setItem(key, value) {{ storage[key] = String(value); }},
  removeItem(key) {{ delete storage[key]; }}
}};
global.File = class File {{
  constructor(parts, name, opts) {{ this.parts = parts; this.name = name; this.type = (opts || {{}}).type || ''; this.size = 4; }}
}};
global.FormData = class FormData {{
  constructor() {{ this.rows = []; }}
  append() {{ this.rows.push(Array.from(arguments)); }}
}};
global.window = {{}};
window.api = function(path, opts) {{
  calls.push({{path, method: (opts || {{}}).method || 'GET', body: (opts || {{}}).body}});
  if (path === '/api/hub/init') return Promise.resolve({{ok: true}});
  if (path.startsWith('/api/list?')) return Promise.resolve({{items: []}});
  if (path.startsWith('/api/file?')) {{
    const decoded = decodeURIComponent(path);
    if (decoded.includes('hub-meetings.json')) return Promise.resolve({{content: JSON.stringify({{items: []}})}});
    return Promise.reject(new Error('missing'));
  }}
  if (path === '/api/file/save') {{ saved = JSON.parse(opts.body).content; return Promise.resolve({{ok: true}}); }}
  if (path === '/api/workspace/upload') return Promise.resolve({{filename: 'meeting.webm', size: 4, mime: 'audio/webm'}});
  if (path === '/api/transcribe') return Promise.resolve({{ok: true, transcript: '讨论了发布计划'}});
  if (path === '/api/file/delete') return Promise.resolve({{ok: true}});
  return Promise.reject(new Error('unexpected api path: ' + path));
}};
vm.runInThisContext(src, {{filename: 'hub-store.js'}});
window.HubStore.init()
  .then(() => window.HubStore.read('meetings'))
  .then(data => {{
    data.items.push({{id: 'm1', title: '发布会', transcript: '讨论了发布计划'}});
    return window.HubStore.write('meetings', data);
  }})
  .then(() => window.HubStore.uploadRecording(new File(['data'], 'meeting.webm', {{type: 'audio/webm'}})))
  .then(uploaded => window.HubStore.transcribeRecording(new File(['data'], 'meeting.webm', {{type: 'audio/webm'}})).then(transcript => ({{uploaded, transcript}})))
  .then(result => window.HubStore.deleteFile('hub-recordings/meeting.webm').then(() => result))
  .then(result => process.stdout.write(JSON.stringify({{saved: JSON.parse(saved), calls: calls.map(c => c.path), result}})))
  .catch(err => {{ console.error(err && err.stack || err); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["saved"]["items"][0]["id"] == "m1"
    assert "/api/workspace/upload" in payload["calls"]
    assert "/api/transcribe" in payload["calls"]
    assert "/api/file/delete" in payload["calls"]
    assert payload["result"]["uploaded"]["path"] == "hub-recordings/meeting.webm"
    assert payload["result"]["transcript"] == "讨论了发布计划"


def test_meeting_workspace_exposes_record_transcribe_summarize_and_history_views():
    js = HUB_JS.read_text(encoding="utf-8")
    css = HUB_CSS.read_text(encoding="utf-8")

    for required in (
        "id: 'meetings'",
        "renderMeetings",
        "renderMeetingForm",
        "renderMeetingDetail",
        "startMeetingRecording",
        "stopMeetingRecording",
        "MediaRecorder",
        "audioBitsPerSecond: 32000",
        "summarizeMeeting",
        "'/api/btw'",
        "api/file/raw",
        "data-hub-meeting-field",
        "hub-meeting-recorder",
        "hub-meeting-detail",
        "hub-meeting-summary",
        "renderMeetingSummary",
        "window.renderMd",
    ):
        assert required in js or required in css

    assert "会议纪要" in js
    assert "开始录音" in js and "停止录音" in js
    assert "导入音频" in js and "AI 总结" in js
    assert "EventSource" in js
    assert "scrollHubTop" in js
    assert "onclick=\"switchPanel(\\'hub\\',{fromRailClick:true})\"" in js
    assert "closeMobileHubSidebar" in js


def test_meeting_recording_lifecycle_releases_media_and_cleans_replaced_files():
    js = HUB_JS.read_text(encoding="utf-8")

    cleanup = js[js.find("function cleanupMeetingMedia") : js.find("function startMeetingRecording")]
    submit = js[js.find("function saveMeetingForm") : js.find("function onInput")]
    deletion = js[js.find("function deleteMeeting") : js.find("function summarizeMeeting")]

    assert "getTracks().forEach" in cleanup and ".stop()" in cleanup
    assert "clearInterval" in cleanup
    assert "originalAudioPath" in submit and "deleteFile" in submit
    assert "deleteFile" in deletion
    assert "previousSummaryUpdatedAt" in js
    assert "beforeunload" in js


def test_meeting_layout_has_mobile_and_recording_states():
    css = HUB_CSS.read_text(encoding="utf-8")

    assert ".hub-meeting-recorder.recording" in css
    assert ".hub-meeting-grid" in css
    assert ".hub-meeting-audio" in css
    mobile = css[css.find("@media (max-width: 720px)") :]
    assert ".hub-meeting-grid" in mobile
    assert ".hub-meeting-actions" in mobile
