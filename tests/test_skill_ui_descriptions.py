import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
import tests.conftest as _conftest
from tests._pytest_port import BASE
from tests.conftest import requires_agent_modules


def _state_dir() -> pathlib.Path:
    return pathlib.Path(os.environ["HERMES_WEBUI_TEST_STATE_DIR"])


def _remove_path(path: pathlib.Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        _conftest._rmtree_retry(path)


class _IsolatedSkillsDirs:
    def __init__(self, profile: str):
        self.profile = profile
        self.state = _state_dir()
        self.root_skills = self.state / "skills"
        self.profile_home = self.state / "profiles" / profile
        self.profile_skills = self.profile_home / "skills"
        self._root_was_symlink = False
        self._root_symlink_target = None

    def __enter__(self):
        self._root_was_symlink = self.root_skills.is_symlink()
        if self._root_was_symlink:
            self._root_symlink_target = self.root_skills.resolve()
        _remove_path(self.root_skills)
        _remove_path(self.profile_home)
        self.root_skills.mkdir(parents=True, exist_ok=True)
        self.profile_skills.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        _remove_path(self.profile_home)
        _remove_path(self.root_skills)
        if self._root_was_symlink and self._root_symlink_target is not None:
            self.root_skills.symlink_to(self._root_symlink_target)


def _write_skill(
    skills_dir: pathlib.Path, name: str, description: str, body: str
) -> pathlib.Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_md


def _get(path: str, *, profile: str | None = None):
    headers = {}
    if profile:
        headers["Cookie"] = f"hermes_profile={profile}"
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _post(path: str, body: dict, *, profile: str | None = None):
    headers = {"Content-Type": "application/json"}
    if profile:
        headers["Cookie"] = f"hermes_profile={profile}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _skill_by_name(payload: dict, name: str) -> dict:
    return next(
        skill for skill in payload.get("skills", []) if skill.get("name") == name
    )


@requires_agent_modules
def test_ui_description_roundtrip_is_profile_scoped_and_excluded_from_skill_content():
    profile = "skill-ui-description-profile"
    skill_name = "ui-description-isolation"
    root_zh = "默认配置中文解释"
    profile_zh = "独立配置中文解释"
    with _IsolatedSkillsDirs(profile) as dirs:
        root_md = _write_skill(
            dirs.root_skills,
            skill_name,
            "Root runtime description",
            "Root runtime body.",
        )
        profile_md = _write_skill(
            dirs.profile_skills,
            skill_name,
            "Profile runtime description",
            "Profile runtime body.",
        )
        profile_snapshot, profile_snapshot_status = _get(
            "/api/skills?include_ui=1", profile=profile
        )
        assert profile_snapshot_status == 200
        profile_generation = profile_snapshot["profile_generation"]

        saved_root, root_save_status = _post(
            "/api/skills/ui-description",
            {"name": skill_name, "ui_description": root_zh},
        )
        saved_profile, profile_save_status = _post(
            "/api/skills/ui-description",
            {
                "name": skill_name,
                "ui_description": profile_zh,
                "profile_generation": profile_generation,
            },
            profile=profile,
        )

        assert root_save_status == 200 and saved_root.get("ok") is True
        assert profile_save_status == 200 and saved_profile.get("ok") is True

        root_list, root_list_status = _get("/api/skills?include_ui=1")
        profile_list, profile_list_status = _get(
            "/api/skills?include_ui=1", profile=profile
        )
        assert root_list_status == 200
        assert profile_list_status == 200
        assert _skill_by_name(root_list, skill_name)["ui_description"] == root_zh
        assert _skill_by_name(profile_list, skill_name)["ui_description"] == profile_zh

        root_runtime_list, root_runtime_list_status = _get("/api/skills")
        profile_runtime_list, profile_runtime_list_status = _get(
            "/api/skills", profile=profile
        )
        assert root_runtime_list_status == 200
        assert profile_runtime_list_status == 200
        assert "ui_description" not in _skill_by_name(root_runtime_list, skill_name)
        assert "ui_description" not in _skill_by_name(profile_runtime_list, skill_name)

        root_detail, root_detail_status = _get(
            f"/api/skills/content?name={skill_name}&include_ui=1"
        )
        profile_detail, profile_detail_status = _get(
            f"/api/skills/content?name={skill_name}&include_ui=1", profile=profile
        )
        assert root_detail_status == 200
        assert profile_detail_status == 200
        assert root_detail["ui_description"] == root_zh
        assert profile_detail["ui_description"] == profile_zh
        assert root_zh not in root_detail["content"]
        assert profile_zh not in profile_detail["content"]

        root_runtime_detail, root_runtime_detail_status = _get(
            f"/api/skills/content?name={skill_name}"
        )
        profile_runtime_detail, profile_runtime_detail_status = _get(
            f"/api/skills/content?name={skill_name}", profile=profile
        )
        assert root_runtime_detail_status == 200
        assert profile_runtime_detail_status == 200
        assert "ui_description" not in root_runtime_detail
        assert "ui_description" not in profile_runtime_detail
        assert root_zh not in root_md.read_text(encoding="utf-8")
        assert profile_zh not in profile_md.read_text(encoding="utf-8")

        sidecar = _state_dir() / "skill-ui-descriptions.json"
        assert sidecar.exists()
        sidecar_text = sidecar.read_text(encoding="utf-8")
        assert root_zh in sidecar_text
        assert profile_zh in sidecar_text
        assert sidecar.resolve() not in root_md.resolve().parents
        assert sidecar.resolve() not in profile_md.resolve().parents


@requires_agent_modules
def test_ui_detail_alias_uses_frontmatter_name_for_sidecar_identity():
    skill_name = "canonical-ui-name"
    directory_name = "legacy-ui-folder"
    ui_description = "历史目录也应显示规范名说明"
    with _IsolatedSkillsDirs("unused-profile") as dirs:
        skill_dir = dirs.root_skills / directory_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Runtime only\n---\n\n# Runtime\n",
            encoding="utf-8",
        )

        saved, save_status = _post(
            "/api/skills/ui-description",
            {"name": skill_name, "ui_description": ui_description},
        )
        assert save_status == 200 and saved.get("ok") is True

        detail, detail_status = _get(
            f"/api/skills/content?name={directory_name}&include_ui=1"
        )

        assert detail_status == 200
        assert detail["name"] == skill_name
        assert detail["ui_description"] == ui_description


@requires_agent_modules
def test_clearing_ui_description_removes_only_ui_metadata():
    skill_name = "ui-description-clear"
    ui_description = "仅供界面查看"
    with _IsolatedSkillsDirs("unused-profile") as dirs:
        skill_md = _write_skill(
            dirs.root_skills,
            skill_name,
            "Runtime description stays unchanged",
            "Runtime body stays unchanged.",
        )
        original_content = skill_md.read_text(encoding="utf-8")

        saved, save_status = _post(
            "/api/skills/ui-description",
            {"name": skill_name, "ui_description": ui_description},
        )
        assert save_status == 200 and saved.get("ok") is True

        cleared, clear_status = _post(
            "/api/skills/ui-description",
            {"name": skill_name, "ui_description": ""},
        )
        assert clear_status == 200 and cleared.get("ok") is True

        listed, list_status = _get("/api/skills?include_ui=1")
        assert list_status == 200
        assert _skill_by_name(listed, skill_name)["ui_description"] == ""
        assert skill_md.read_text(encoding="utf-8") == original_content


@requires_agent_modules
def test_skill_save_persists_runtime_content_and_ui_description_together():
    skill_name = "ui-description-atomic-save"
    ui_description = "与技能正文在一次请求中保存"
    content = (
        "---\nname: ui-description-atomic-save\n"
        "description: Runtime only\n---\n\n# Runtime\n"
    )
    with _IsolatedSkillsDirs("unused-profile"):
        saved, save_status = _post(
            "/api/skills/save",
            {
                "name": skill_name,
                "content": content,
                "ui_description": ui_description,
            },
        )
        assert save_status == 200 and saved.get("ok") is True
        assert saved["ui_description"] == ui_description

        ui_detail, ui_status = _get(
            f"/api/skills/content?name={skill_name}&include_ui=1"
        )
        runtime_detail, runtime_status = _get(f"/api/skills/content?name={skill_name}")
        assert ui_status == runtime_status == 200
        assert ui_detail["ui_description"] == ui_description
        assert ui_description not in ui_detail["content"]
        assert "ui_description" not in runtime_detail


@requires_agent_modules
def test_deleting_skill_prunes_its_ui_description():
    skill_name = "ui-description-delete"
    ui_description = "删除技能后不应残留"
    with _IsolatedSkillsDirs("unused-profile") as dirs:
        _write_skill(
            dirs.root_skills, skill_name, "Runtime description", "Runtime body."
        )
        saved, save_status = _post(
            "/api/skills/ui-description",
            {"name": skill_name, "ui_description": ui_description},
        )
        assert save_status == 200 and saved.get("ok") is True

        deleted, delete_status = _post("/api/skills/delete", {"name": skill_name})
        assert delete_status == 200 and deleted.get("ok") is True

        sidecar = _state_dir() / "skill-ui-descriptions.json"
        if sidecar.exists():
            assert ui_description not in sidecar.read_text(encoding="utf-8")


@requires_agent_modules
def test_ui_description_rejects_unknown_skill_and_oversized_text():
    with _IsolatedSkillsDirs("unused-profile") as dirs:
        _write_skill(
            dirs.root_skills,
            "known-ui-description",
            "Runtime description",
            "Runtime body.",
        )

        missing, missing_status = _post(
            "/api/skills/ui-description",
            {"name": "missing-ui-description", "ui_description": "不存在"},
        )
        assert missing_status == 404
        assert "not found" in missing.get("error", "").lower()

        oversized, oversized_status = _post(
            "/api/skills/ui-description",
            {"name": "known-ui-description", "ui_description": "中" * 2001},
        )
        assert oversized_status == 400
        assert "2000" in oversized.get("error", "")


def test_webui_sidecar_writer_waits_for_agent_shared_profile_lock(tmp_path):
    from hermes_constants import profile_mutation_lock

    repo = pathlib.Path(__file__).resolve().parent.parent
    agent_repo = pathlib.Path(_conftest.HERMES_AGENT)
    worker_python = pathlib.Path(_conftest.VENV_PYTHON)
    if not worker_python.exists() or not agent_repo.exists():
        pytest.skip("Hermes Agent runtime is unavailable for cross-repository lock test")

    root = tmp_path / "hermes"
    profile_home = root / "profiles" / "demo"
    profile_home.mkdir(parents=True)
    state_dir = tmp_path / "state"
    ready_marker = tmp_path / "ready"
    go_marker = tmp_path / "go"
    done_marker = tmp_path / "done"
    worker = tmp_path / "webui_sidecar_writer.py"
    worker.write_text(
        """
import os
import pathlib
import sys
import time

state_dir = pathlib.Path(sys.argv[1])
profile_home = sys.argv[2]
ready_marker = pathlib.Path(sys.argv[3])
go_marker = pathlib.Path(sys.argv[4])
done_marker = pathlib.Path(sys.argv[5])
os.environ["HERMES_WEBUI_STATE_DIR"] = str(state_dir)

from api.skill_ui_descriptions import set_ui_description

# Import/setup may legitimately read Profile-scoped config. Complete that phase
# before the parent takes the mutation lock, then gate the actual writer.
ready_marker.write_text("ready", encoding="utf-8")
deadline = time.time() + 20
while not go_marker.exists() and time.time() < deadline:
    time.sleep(0.01)
if not go_marker.exists():
    raise RuntimeError("parent did not release writer gate")
set_ui_description(profile_home, "demo", "中文说明")
done_marker.write_text("done", encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_home)
    env["HERMES_WEBUI_AGENT_DIR"] = str(agent_repo)
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(repo), str(agent_repo), env.get("PYTHONPATH", ""))
        if part
    )

    process = subprocess.Popen(
        [
            str(worker_python),
            str(worker),
            str(state_dir),
            str(profile_home),
            str(ready_marker),
            str(go_marker),
            str(done_marker),
        ],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 15
    while not ready_marker.exists() and time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"WebUI writer exited before READY\nstdout={stdout}\nstderr={stderr}"
            )
        time.sleep(0.02)
    assert ready_marker.exists(), "WebUI writer did not reach the mutation boundary"

    with profile_mutation_lock(profile_home, timeout=2):
        go_marker.write_text("go", encoding="utf-8")
        time.sleep(0.35)
        assert process.poll() is None, "WebUI sidecar writer bypassed the shared Profile lock"
        assert not done_marker.exists()
        assert not (state_dir / "skill-ui-descriptions.json").exists()

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
    assert done_marker.is_file()
    payload = json.loads(
        (state_dir / "skill-ui-descriptions.json").read_text(encoding="utf-8")
    )
    assert payload["profiles"][str(profile_home)]["demo"] == "中文说明"


def test_sidecar_file_lock_preserves_concurrent_process_updates(tmp_path):
    repo = pathlib.Path(__file__).resolve().parent.parent
    agent_repo = pathlib.Path(_conftest.HERMES_AGENT)
    state_dir = tmp_path / "state"
    profile_home = tmp_path / "hermes"
    profile_home.mkdir()
    worker = tmp_path / "write_ui_description.py"
    worker.write_text(
        """
import os
import sys
os.environ["HERMES_WEBUI_STATE_DIR"] = sys.argv[1]
from api.skill_ui_descriptions import set_ui_description
set_ui_description(sys.argv[2], sys.argv[3], sys.argv[4])
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(repo), str(agent_repo), env.get("PYTHONPATH", ""))
        if part
    )
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(worker),
                str(state_dir),
                str(profile_home),
                f"skill-{index}",
                f"说明-{index}",
            ],
            cwd=repo,
            env=env,
        )
        for index in range(12)
    ]
    deadline = time.time() + 30
    for process in processes:
        process.wait(timeout=max(1, deadline - time.time()))
        assert process.returncode == 0

    payload = json.loads(
        (state_dir / "skill-ui-descriptions.json").read_text(encoding="utf-8")
    )
    descriptions = payload["profiles"][str(profile_home)]
    assert descriptions == {f"skill-{index}": f"说明-{index}" for index in range(12)}


def test_combined_skill_save_is_serialized_across_processes(tmp_path):
    repo = pathlib.Path(__file__).resolve().parent.parent
    agent_repo = _conftest.HERMES_AGENT
    worker_python = pathlib.Path(_conftest.VENV_PYTHON)
    if agent_repo is None or not worker_python.exists():
        pytest.skip("Hermes Agent Python is unavailable for route-level process test")
    state_dir = tmp_path / "state"
    skills_dir = tmp_path / "profile" / "skills"
    profile_key = str(tmp_path / "profile")
    pause_marker = tmp_path / "pause-a"
    release_marker = tmp_path / "release-a"
    done_b_marker = tmp_path / "done-b"
    worker = tmp_path / "combined_save_worker.py"
    worker.write_text(
        """
import json
import os
import pathlib
import sys

state_dir = pathlib.Path(sys.argv[1])
skills_dir = pathlib.Path(sys.argv[2])
profile_key = sys.argv[3]
label = sys.argv[4]
pause_marker = pathlib.Path(sys.argv[5])
release_marker = pathlib.Path(sys.argv[6])
done_marker = pathlib.Path(sys.argv[7])
os.environ["HERMES_WEBUI_STATE_DIR"] = str(state_dir)

import api.routes as routes
import api.skill_ui_descriptions as ui

routes._active_skills_dir = lambda: skills_dir
routes._active_skill_ui_profile_key = lambda: profile_key
routes.j = lambda _handler, payload: payload
routes.bad = lambda _handler, message, status=400: {
    "ok": False,
    "error": message,
    "status": status,
}

if label == "A":
    original_set = ui.set_ui_description

    def paused_set(profile, name, text):
        pause_marker.write_text("ready", encoding="utf-8")
        deadline = __import__("time").time() + 10
        while not release_marker.exists():
            if __import__("time").time() > deadline:
                raise TimeoutError("release marker not created")
            __import__("time").sleep(0.02)
        return original_set(profile, name, text)

    ui.set_ui_description = paused_set

payload = routes._handle_skill_save(
    object(),
    {
        "name": "demo",
        "content": f"---\\nname: demo\\ndescription: runtime\\n---\\n\\n# Body {label}\\n",
        "ui_description": f"Description {label}",
    },
)
done_marker.write_text(json.dumps(payload), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_WEBUI_AGENT_DIR"] = str(agent_repo)
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(repo), str(agent_repo), env.get("PYTHONPATH", ""))
        if part
    )

    done_a = tmp_path / "done-a"
    proc_a = subprocess.Popen(
        [
            str(worker_python),
            str(worker),
            str(state_dir),
            str(skills_dir),
            profile_key,
            "A",
            str(pause_marker),
            str(release_marker),
            str(done_a),
        ],
        cwd=repo,
        env=env,
    )
    deadline = time.time() + 15
    while not pause_marker.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert pause_marker.exists(), "process A did not reach the sidecar boundary"

    proc_b = subprocess.Popen(
        [
            str(worker_python),
            str(worker),
            str(state_dir),
            str(skills_dir),
            profile_key,
            "B",
            str(pause_marker),
            str(release_marker),
            str(done_b_marker),
        ],
        cwd=repo,
        env=env,
    )
    time.sleep(0.3)
    assert not done_b_marker.exists(), (
        "process B bypassed the cross-process transaction lock"
    )
    release_marker.write_text("go", encoding="utf-8")

    proc_a.wait(timeout=20)
    proc_b.wait(timeout=20)
    assert proc_a.returncode == proc_b.returncode == 0
    assert json.loads(done_a.read_text(encoding="utf-8"))["ok"] is True
    assert json.loads(done_b_marker.read_text(encoding="utf-8"))["ok"] is True
    content = (skills_dir / "demo" / "SKILL.md").read_text(encoding="utf-8")
    sidecar = json.loads(
        (state_dir / "skill-ui-descriptions.json").read_text(encoding="utf-8")
    )
    assert "# Body B" in content
    assert sidecar["profiles"][profile_key]["demo"] == "Description B"


def test_ui_detail_read_is_serialized_with_combined_save_across_processes(tmp_path):
    repo = pathlib.Path(__file__).resolve().parent.parent
    agent_repo = _conftest.HERMES_AGENT
    worker_python = pathlib.Path(_conftest.VENV_PYTHON)
    if agent_repo is None or not worker_python.exists():
        pytest.skip("Hermes Agent Python is unavailable for route-level process test")

    state_dir = tmp_path / "state"
    skills_dir = tmp_path / "profile" / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo\ndescription: runtime\n---\n\n# Body A\n",
        encoding="utf-8",
    )
    profile_key = str((tmp_path / "profile").resolve())
    state_dir.mkdir(parents=True)
    (state_dir / "skill-ui-descriptions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {profile_key: {"demo": "Description A"}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    pause_marker = tmp_path / "writer-paused"
    release_marker = tmp_path / "release-writer"
    writer_done = tmp_path / "writer-done.json"
    reader_done = tmp_path / "reader-done.json"
    worker = tmp_path / "detail_snapshot_worker.py"
    worker.write_text(
        """
import json
import os
import pathlib
import sys
import time
import urllib.parse

state_dir = pathlib.Path(sys.argv[1])
skills_dir = pathlib.Path(sys.argv[2])
profile_key = sys.argv[3]
mode = sys.argv[4]
pause_marker = pathlib.Path(sys.argv[5])
release_marker = pathlib.Path(sys.argv[6])
done_marker = pathlib.Path(sys.argv[7])
os.environ["HERMES_WEBUI_STATE_DIR"] = str(state_dir)

import api.routes as routes
import api.skill_ui_descriptions as ui

routes._active_skills_dir = lambda: skills_dir
routes._active_skill_search_dirs = lambda local: [local]
routes._active_skill_ui_profile_key = lambda: profile_key
routes.j = lambda _handler, payload, status=200: {"status": status, **payload}
routes.bad = lambda _handler, message, status=400: {
    "ok": False,
    "error": message,
    "status": status,
}

if mode == "writer":
    original_set = ui.set_ui_description

    def paused_set(profile, name, text):
        pause_marker.write_text("ready", encoding="utf-8")
        deadline = time.time() + 10
        while not release_marker.exists():
            if time.time() > deadline:
                raise TimeoutError("release marker not created")
            time.sleep(0.02)
        return original_set(profile, name, text)

    ui.set_ui_description = paused_set
    payload = routes._handle_skill_save(
        object(),
        {
            "name": "demo",
            "content": "---\\nname: demo\\ndescription: runtime\\n---\\n\\n# Body B\\n",
            "ui_description": "Description B",
        },
    )
else:
    payload = routes.handle_get(
        object(),
        urllib.parse.urlparse(
            "/api/skills/content?name=demo&include_ui=1"
        ),
    )

done_marker.write_text(json.dumps(payload), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_WEBUI_AGENT_DIR"] = str(agent_repo)
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(repo), str(agent_repo), env.get("PYTHONPATH", ""))
        if part
    )

    writer = subprocess.Popen(
        [
            str(worker_python),
            str(worker),
            str(state_dir),
            str(skills_dir),
            profile_key,
            "writer",
            str(pause_marker),
            str(release_marker),
            str(writer_done),
        ],
        cwd=repo,
        env=env,
    )
    deadline = time.time() + 15
    while not pause_marker.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert pause_marker.exists(), "writer did not reach the torn-snapshot boundary"

    reader = subprocess.Popen(
        [
            str(worker_python),
            str(worker),
            str(state_dir),
            str(skills_dir),
            profile_key,
            "reader",
            str(pause_marker),
            str(release_marker),
            str(reader_done),
        ],
        cwd=repo,
        env=env,
    )
    time.sleep(0.3)
    assert not reader_done.exists(), (
        "detail reader bypassed the cross-process Profile transaction lock"
    )
    release_marker.write_text("go", encoding="utf-8")

    writer.wait(timeout=20)
    reader.wait(timeout=20)
    assert writer.returncode == reader.returncode == 0
    assert json.loads(writer_done.read_text(encoding="utf-8"))["ok"] is True
    detail = json.loads(reader_done.read_text(encoding="utf-8"))
    assert detail["content"].endswith("# Body B\n")
    assert detail["ui_description"] == "Description B"


def test_ui_list_uses_one_profile_key_for_lock_and_sidecar_lookup(
    tmp_path, monkeypatch
):
    import api.routes as routes
    import api.skill_ui_descriptions as descriptions

    profile_a = (tmp_path / "profile-a").resolve()
    profile_b = (tmp_path / "profile-b").resolve()
    skill_a = _write_skill(
        profile_a / "skills", "demo", "Runtime A", "Body A."
    )
    skill_b = _write_skill(
        profile_b / "skills", "demo", "Runtime B", "Body B."
    )
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    with descriptions.bind_ui_description_to_runtime(
        DEFAULT_PROFILE_GENERATION, skill_a
    ):
        descriptions.set_ui_description(str(profile_a), "demo", "Description A")
    with descriptions.bind_ui_description_to_runtime(
        DEFAULT_PROFILE_GENERATION, skill_b
    ):
        descriptions.set_ui_description(str(profile_b), "demo", "Description B")

    skill_dirs = iter([profile_a / "skills", profile_b / "skills"])
    monkeypatch.setattr(routes, "_active_skills_dir", lambda: next(skill_dirs))
    monkeypatch.setattr(
        routes,
        "_skills_list_from_dir",
        lambda _skills_dir, category=None, *, profile_home=None,
        include_runtime_paths=False: {
            "skills": [
                {
                    "name": "demo",
                    "description": "Runtime",
                    **(
                        {"_ui_runtime_path": str(skill_a)}
                        if include_runtime_paths
                        else {}
                    ),
                }
            ]
        },
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"status": status, **payload},
    )

    payload = routes.handle_get(
        object(), urllib.parse.urlparse("/api/skills?include_ui=1")
    )

    assert payload["skills"][0]["ui_description"] == "Description A"


def test_profile_transaction_delegates_complete_key_set_to_agent(monkeypatch):
    import contextlib
    import hermes_constants
    import api.skill_ui_descriptions as descriptions

    entered = []

    @contextlib.contextmanager
    def fake_profile_mutation_locks(keys):
        entered.append(tuple(keys))
        yield

    monkeypatch.setattr(
        hermes_constants, "profile_mutation_locks", fake_profile_mutation_locks
    )

    with descriptions.profile_transaction(["/profiles/b", "/profiles/a", "/profiles/b"]):
        pass

    assert entered == [("/profiles/b", "/profiles/a", "/profiles/b")]


def test_ui_list_read_falls_back_when_agent_shared_lock_api_is_missing(monkeypatch):
    import hermes_constants
    import api.routes as routes

    skills_dir = pathlib.Path("/profiles/default/skills")
    expected_skills = [{"name": "demo", "description": "Runtime only"}]
    calls = []

    monkeypatch.delattr(hermes_constants, "profile_mutation_locks", raising=False)
    monkeypatch.setattr(routes, "_active_skills_dir", lambda: skills_dir)

    def fake_list(
        selected_dir,
        category=None,
        *,
        profile_home=None,
        include_runtime_paths=False,
    ):
        calls.append(
            (selected_dir, category, profile_home, include_runtime_paths)
        )
        return {"skills": expected_skills}

    monkeypatch.setattr(routes, "_skills_list_from_dir", fake_list)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"status": status, **payload},
    )

    payload = routes.handle_get(
        object(), urllib.parse.urlparse("/api/skills?include_ui=1")
    )

    assert payload == {"status": 200, "skills": expected_skills}
    assert calls == [(skills_dir, None, None, False)]
    assert "profile_generation" not in payload
    assert "ui_description" not in payload["skills"][0]


def test_ui_detail_read_falls_back_when_agent_shared_lock_api_is_missing(monkeypatch):
    import hermes_constants
    import api.routes as routes

    skills_dir = pathlib.Path("/profiles/default/skills")
    monkeypatch.delattr(hermes_constants, "profile_mutation_locks", raising=False)
    monkeypatch.setattr(routes, "_active_skills_dir", lambda: skills_dir)
    monkeypatch.setattr(
        routes,
        "_skill_view_from_active_dir",
        lambda name: {
            "name": name,
            "content": "---\nname: demo\ndescription: Runtime only\n---\n",
            "linked_files": None,
        },
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"status": status, **payload},
    )

    payload = routes.handle_get(
        object(),
        urllib.parse.urlparse(
            "/api/skills/content?name=demo&include_ui=1"
        ),
    )

    assert payload["status"] == 200
    assert payload["name"] == "demo"
    assert payload["linked_files"] == {}
    assert "profile_generation" not in payload
    assert "ui_description" not in payload


def test_missing_agent_shared_lock_api_keeps_skill_transactions_fail_closed(
    tmp_path, monkeypatch
):
    import hermes_constants
    import api.skill_ui_descriptions as descriptions

    entered = False
    monkeypatch.delattr(hermes_constants, "profile_mutation_locks", raising=False)

    with pytest.raises(
        RuntimeError, match="Hermes Agent shared Profile mutation lock is unavailable"
    ):
        with descriptions.skill_transaction(str(tmp_path)):
            entered = True

    assert entered is False


def test_ui_list_read_waits_for_profile_transaction_snapshot(tmp_path, monkeypatch):
    """A UI list read must not observe skill metadata/sidecar mid-commit."""
    import api.routes as routes
    import api.skill_ui_descriptions as descriptions

    profile_home = (tmp_path / "profile").resolve()
    skills_dir = profile_home / "skills"
    skills_dir.mkdir(parents=True)
    profile_key = str(profile_home)
    result = {}
    finished = threading.Event()

    monkeypatch.setattr(routes, "_active_skills_dir", lambda: skills_dir)
    monkeypatch.setattr(
        routes,
        "_skills_list_from_dir",
        lambda _skills_dir, category=None, *, profile_home=None,
        include_runtime_paths=False: {
            "skills": [{"name": "demo", "description": "Runtime B"}]
        },
    )
    monkeypatch.setattr(
        routes,
        "_add_skill_ui_descriptions",
        lambda skills, *, profile_key=None, profile_generation=None: [
            {**skills[0], "ui_description": "Description B"}
        ],
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"status": status, **payload},
    )
    parsed = urllib.parse.urlparse("/api/skills?include_ui=1")

    def read_list():
        result["payload"] = routes.handle_get(object(), parsed)
        finished.set()

    with descriptions.skill_transaction(profile_key):
        reader = threading.Thread(target=read_list)
        reader.start()
        assert not finished.wait(0.3), (
            "include_ui list read bypassed the Profile transaction lock"
        )

    reader.join(timeout=5)
    assert not reader.is_alive()
    assert result["payload"]["skills"] == [
        {
            "name": "demo",
            "description": "Runtime B",
            "ui_description": "Description B",
        }
    ]


def test_ui_detail_read_waits_for_profile_transaction_snapshot(tmp_path, monkeypatch):
    """A UI detail read must not observe content/sidecar mid-commit."""
    import api.routes as routes
    import api.skill_ui_descriptions as descriptions

    profile_home = (tmp_path / "profile").resolve()
    skills_dir = profile_home / "skills"
    skill_md = skills_dir / "demo" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(
        "---\nname: demo\ndescription: Runtime B\n---\n\n# Body B\n",
        encoding="utf-8",
    )
    profile_key = str(profile_home)
    state_dir = tmp_path / "state"
    result = {}
    finished = threading.Event()

    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    with descriptions.bind_ui_description_to_runtime(
        DEFAULT_PROFILE_GENERATION, skill_md
    ):
        descriptions.set_ui_description(profile_key, "demo", "Description B")
    monkeypatch.setattr(routes, "_active_skills_dir", lambda: skills_dir)
    monkeypatch.setattr(routes, "_active_skill_search_dirs", lambda local: [local])
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"status": status, **payload},
    )
    parsed = urllib.parse.urlparse(
        "/api/skills/content?name=demo&include_ui=1"
    )

    def read_detail():
        result["payload"] = routes.handle_get(object(), parsed)
        finished.set()

    with descriptions.skill_transaction(profile_key):
        reader = threading.Thread(target=read_detail)
        reader.start()
        assert not finished.wait(0.3), (
            "include_ui detail read bypassed the Profile transaction lock"
        )

    reader.join(timeout=5)
    assert not reader.is_alive()
    assert result["payload"]["content"].endswith("# Body B\n")
    assert result["payload"]["ui_description"] == "Description B"


def test_sidecar_profile_bucket_can_be_popped_and_restored(tmp_path, monkeypatch):
    import api.skill_ui_descriptions as descriptions

    sidecar = tmp_path / "state" / "skill-ui-descriptions.json"
    monkeypatch.setattr(descriptions, "sidecar_path", lambda: sidecar)
    key = str(tmp_path / "profiles" / "demo")
    descriptions.set_ui_description(key, "alpha", "说明 A")
    descriptions.set_ui_description(key, "beta", "说明 B")

    previous = descriptions.pop_profile_descriptions(key)
    assert previous == {"alpha": "说明 A", "beta": "说明 B"}
    assert descriptions.read_profile_descriptions(key) == {}

    descriptions.restore_profile_descriptions(key, previous)
    assert descriptions.read_profile_descriptions(key) == previous


def test_profile_rename_reconciles_unique_orphaned_generation_bucket(
    tmp_path, monkeypatch
):
    import api.skill_ui_descriptions as descriptions
    from api.profile_generation import ensure_profile_generation

    sidecar = tmp_path / "state" / "skill-ui-descriptions.json"
    monkeypatch.setattr(descriptions, "sidecar_path", lambda: sidecar)
    old_home = (tmp_path / "profiles" / "old-name").resolve()
    old_skill = _write_skill(
        old_home / "skills", "demo", "Runtime", "Renamed body."
    )
    generation = ensure_profile_generation(old_home)
    old_key = str(old_home)
    with descriptions.bind_ui_description_to_runtime(generation, old_skill):
        descriptions.set_ui_description(old_key, "demo", "保留的说明")

    new_home = old_home.with_name("new-name")
    old_home.rename(new_home)
    new_skill = new_home / "skills" / "demo" / "SKILL.md"
    state = descriptions.get_ui_description_state(
        str(new_home),
        "demo",
        profile_generation=generation,
        runtime_path=new_skill,
        strict=True,
    )

    assert state == {"ui_description": "保留的说明", "stale": False}
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert old_key not in payload["profiles"]
    assert old_key not in payload["bindings"]
    assert payload["profiles"][str(new_home)]["demo"] == "保留的说明"
    assert payload["bindings"][str(new_home)]["profile_generation"] == generation


def test_live_duplicate_generation_is_not_misclassified_as_profile_rename(
    tmp_path, monkeypatch
):
    import shutil

    import api.skill_ui_descriptions as descriptions
    from api.profile_generation import ensure_profile_generation

    sidecar = tmp_path / "state" / "skill-ui-descriptions.json"
    monkeypatch.setattr(descriptions, "sidecar_path", lambda: sidecar)
    source_home = (tmp_path / "profiles" / "source").resolve()
    source_skill = _write_skill(
        source_home / "skills", "demo", "Runtime", "Source body."
    )
    generation = ensure_profile_generation(source_home)
    with descriptions.bind_ui_description_to_runtime(generation, source_skill):
        descriptions.set_ui_description(str(source_home), "demo", "源说明")

    copied_home = source_home.with_name("copied")
    shutil.copytree(source_home, copied_home)
    copied_skill = copied_home / "skills" / "demo" / "SKILL.md"
    state = descriptions.get_ui_description_state(
        str(copied_home),
        "demo",
        profile_generation=generation,
        runtime_path=copied_skill,
        strict=True,
    )

    assert state == {"ui_description": "", "stale": False}
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert str(source_home) in payload["profiles"]
    assert str(copied_home) not in payload["profiles"]


def test_same_name_profile_recreation_never_exposes_previous_generation_text(
    tmp_path, monkeypatch
):
    import shutil

    import api.skill_ui_descriptions as descriptions
    from api.profile_generation import ensure_profile_generation

    sidecar = tmp_path / "state" / "skill-ui-descriptions.json"
    monkeypatch.setattr(descriptions, "sidecar_path", lambda: sidecar)
    profile_home = (tmp_path / "profiles" / "demo").resolve()
    old_skill = _write_skill(
        profile_home / "skills", "demo", "Old runtime", "Old body."
    )
    generation_a = ensure_profile_generation(profile_home)
    profile_key = str(profile_home)
    with descriptions.bind_ui_description_to_runtime(generation_a, old_skill):
        descriptions.set_ui_description(profile_key, "demo", "旧 incarnation 说明")

    shutil.rmtree(profile_home)
    new_skill = _write_skill(
        profile_home / "skills", "demo", "New runtime", "New body."
    )
    generation_b = ensure_profile_generation(profile_home)
    assert generation_b != generation_a

    state = descriptions.get_ui_description_state(
        profile_key,
        "demo",
        profile_generation=generation_b,
        runtime_path=new_skill,
        strict=True,
    )

    assert state == {"ui_description": "", "stale": True}
    assert descriptions.read_profile_descriptions(profile_key, strict=True) == {}


def test_management_description_read_fails_closed_on_malformed_sidecar(
    tmp_path, monkeypatch
):
    import api.routes as routes
    import api.skill_ui_descriptions as descriptions

    sidecar = tmp_path / "skill-ui-descriptions.json"
    sidecar.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(descriptions, "sidecar_path", lambda: sidecar)

    with pytest.raises(json.JSONDecodeError):
        routes._add_skill_ui_descriptions(
            [{"name": "demo", "description": "runtime"}],
            profile_key="/profiles/default",
        )


def test_frontend_uses_ui_description_only_for_skills_management_surface():
    repo = pathlib.Path(__file__).resolve().parent.parent
    panels = (repo / "static" / "panels.js").read_text(encoding="utf-8")
    commands = (repo / "static" / "commands.js").read_text(encoding="utf-8")

    assert "ui_description" in panels
    assert "skillUiDescription" in panels
    assert "content, ui_description: uiDescription" in panels
    assert "api('/api/skills?include_ui=1')" in panels
    assert "&include_ui=1" in panels
    assert "ui_description" not in commands
    assert (
        "detail&&typeof detail.content==='string' ? detail.content.trim() : ''"
        in commands
    )
    assert "resolve({name:match.name,directive,content:skillContent})" in commands


def test_ui_copy_explains_that_localized_description_is_not_sent_to_the_agent():
    repo = pathlib.Path(__file__).resolve().parent.parent
    i18n = (repo / "static" / "i18n.js").read_text(encoding="utf-8")

    assert "skill_ui_description" in i18n
    assert "skill_ui_description_hint" in i18n
    assert "not sent to the agent" in i18n
    assert "不会发送给 Agent" in i18n
