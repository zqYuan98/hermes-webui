import os
import threading
from contextlib import contextmanager

import pytest

import api.routes as routes


class _FakeHandler:
    pass


def _patch_skill_routes(monkeypatch, skills_dir):
    cap = {}
    monkeypatch.setattr(routes, "_active_skills_dir", lambda: skills_dir)
    monkeypatch.setattr(routes, "j", lambda h, o: (cap.__setitem__("ok", o), True)[1])
    monkeypatch.setattr(
        routes,
        "bad",
        lambda h, m, c=400: (cap.__setitem__("bad", (m, c)), True)[1],
    )
    return cap


def test_skill_save_rejects_symlinked_skill_file(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "demo"
    skill_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("important", encoding="utf-8")
    link = skill_dir / "SKILL.md"
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    routes._handle_skill_save(
        _FakeHandler(),
        {"name": "demo", "content": "changed"},
    )

    assert "bad" in cap, f"expected 400, got {cap}"
    assert cap["bad"][1] == 400
    assert "Cannot save to a symlinked skill file" in cap["bad"][0]
    assert outside.read_text(encoding="utf-8") == "important"


@pytest.mark.parametrize(
    "unsafe_name",
    ["..\\evil", "C:", ".", "CON", "con.txt", "NUL", "LPT1", "trailing."],
)
def test_skill_save_rejects_unsafe_windows_path_components(
    tmp_path, monkeypatch, unsafe_name
):
    skills_dir = tmp_path / "skills"
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    routes._handle_skill_save(
        _FakeHandler(),
        {"name": unsafe_name, "content": "bad"},
    )

    assert cap["bad"] == ("Invalid skill name", 400)
    assert not any(skills_dir.rglob("SKILL.md"))


def test_skill_save_real_file_still_works(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    routes._handle_skill_save(
        _FakeHandler(),
        {"name": "demo-skill", "content": "# Demo\n"},
    )

    skill_file = skills_dir / "demo-skill" / "SKILL.md"
    assert "ok" in cap, f"expected success, got {cap}"
    assert cap["ok"]["ok"] is True
    assert skill_file.read_text(encoding="utf-8") == "# Demo\n"


def test_skill_save_rejects_stale_named_profile_generation(tmp_path, monkeypatch):
    import shutil

    from api.profile_generation import ensure_profile_generation

    profile_home = tmp_path / ".hermes" / "profiles" / "demo"
    skills_dir = profile_home / "skills"
    skills_dir.mkdir(parents=True)
    stale_generation = ensure_profile_generation(profile_home)
    shutil.rmtree(profile_home)
    skills_dir.mkdir(parents=True)
    current_generation = ensure_profile_generation(profile_home)
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": "demo-skill",
            "content": "---\nname: demo-skill\n---\n\n# Demo\n",
            "ui_description": "旧页面说明",
            "profile_generation": stale_generation,
        },
    )

    assert current_generation != stale_generation
    assert cap["bad"] == ("Active profile generation changed; reload and retry", 409)
    assert not (skills_dir / "demo-skill" / "SKILL.md").exists()


def test_queued_old_save_cannot_mutate_recreated_profile(
    tmp_path, monkeypatch
):
    import shutil

    import api.profiles as profiles
    import api.skill_ui_descriptions as descriptions
    import hermes_cli.profiles as cli_profiles
    from api.profile_generation import (
        ensure_profile_generation,
        read_profile_generation,
    )

    base = (tmp_path / ".hermes").resolve()
    profile_home = base / "profiles" / "demo"
    skills_dir = profile_home / "skills"
    skill_file = skills_dir / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\n---\n\n# Incarnation A\n",
        encoding="utf-8",
    )
    generation_a = ensure_profile_generation(profile_home)
    profile_key = str(profile_home.resolve())
    state_dir = tmp_path / "webui"

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    descriptions.set_ui_description(profile_key, "demo-skill", "说明 A")

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(profiles, "_active_profile", "default")
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    monkeypatch.setattr(
        profiles, "_resolve_named_profile_home", lambda _name: profile_home
    )
    monkeypatch.setattr(profiles, "list_profiles_api", lambda *args, **kwargs: [])

    def fake_delete(_name, yes=True):
        assert yes is True
        shutil.rmtree(profile_home)

    def fake_create(_name, **_kwargs):
        profile_home.mkdir(parents=True, exist_ok=False)
        (profile_home / "skills").mkdir()
        return profile_home

    monkeypatch.setattr(cli_profiles, "delete_profile", fake_delete)
    monkeypatch.setattr(cli_profiles, "create_profile", fake_create)
    monkeypatch.setattr(cli_profiles, "seed_profile_skills", lambda *_args, **_kwargs: None)

    original_skill_transaction = descriptions.skill_transaction
    request_waiting_before_lock = threading.Event()
    release_old_request = threading.Event()
    old_request_thread = {"ident": None}

    @contextmanager
    def delayed_old_request_transaction(key):
        if threading.get_ident() == old_request_thread["ident"]:
            request_waiting_before_lock.set()
            assert release_old_request.wait(timeout=5)
        with original_skill_transaction(key):
            yield

    monkeypatch.setattr(
        descriptions, "skill_transaction", delayed_old_request_transaction
    )

    errors = []

    def old_save():
        old_request_thread["ident"] = threading.get_ident()
        try:
            routes._handle_skill_save(
                _FakeHandler(),
                {
                    "name": "demo-skill",
                    "content": (
                        "---\nname: demo-skill\n---\n\n# Stale incarnation A request\n"
                    ),
                    "ui_description": "旧请求说明",
                    "profile_generation": generation_a,
                },
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    request_thread = threading.Thread(target=old_save)
    request_thread.start()
    assert request_waiting_before_lock.wait(timeout=5), (
        "old request did not reach the lock queue after resolving its Profile path"
    )

    profiles.delete_profile_api(
        "demo",
        expected_generation=generation_a,
        require_generation=True,
    )
    created = profiles.create_profile_api("demo")
    generation_b = created["profile_generation"]
    assert generation_b != generation_a

    skill_file.parent.mkdir(parents=True, exist_ok=True)
    sentinel_content = "---\nname: demo-skill\n---\n\n# Incarnation B sentinel\n"
    skill_file.write_text(sentinel_content, encoding="utf-8")
    descriptions.set_ui_description(profile_key, "demo-skill", "说明 B")

    release_old_request.set()
    request_thread.join(timeout=10)

    assert not errors
    assert not request_thread.is_alive()
    assert cap["bad"] == (
        "Active profile generation changed; reload and retry",
        409,
    )
    assert read_profile_generation(profile_home) == generation_b
    assert skill_file.read_text(encoding="utf-8") == sentinel_content
    assert descriptions.get_ui_description(profile_key, "demo-skill") == "说明 B"


def test_interrupted_combined_save_never_pairs_new_runtime_with_old_ui(
    tmp_path, monkeypatch
):
    import api.skill_ui_descriptions as descriptions
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    profile_home = tmp_path / "profile"
    skills_dir = profile_home / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    old_content = "---\nname: demo\n---\n\n# Old runtime\n"
    new_content = "---\nname: demo\n---\n\n# New runtime\n"
    skill_file.write_text(old_content, encoding="utf-8")
    profile_key = str(profile_home.resolve())
    state_dir = tmp_path / "state"

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    descriptions.set_ui_description(profile_key, "demo", "Old UI")

    def terminate_after_runtime_publication(*_args, **_kwargs):
        raise SystemExit("simulated uncatchable process termination")

    monkeypatch.setattr(
        descriptions,
        "set_ui_description",
        terminate_after_runtime_publication,
    )

    with pytest.raises(SystemExit, match="uncatchable"):
        routes._handle_skill_save(
            _FakeHandler(),
            {
                "name": "demo",
                "content": new_content,
                "ui_description": "New UI",
            },
        )

    assert skill_file.read_text(encoding="utf-8") == new_content
    with pytest.raises(RuntimeError, match="stale|interrupted|mismatch"):
        descriptions.get_ui_description(
            profile_key,
            "demo",
            strict=True,
            profile_generation=DEFAULT_PROFILE_GENERATION,
            runtime_path=skill_file,
        )
    assert "ok" not in cap


def test_named_profile_generation_is_resolved_under_skill_transaction(
    tmp_path, monkeypatch
):
    import api.profile_generation as profile_generation
    import api.skill_ui_descriptions as descriptions

    profile_home = tmp_path / ".hermes" / "profiles" / "demo"
    skills_dir = profile_home / "skills"
    skills_dir.mkdir(parents=True)
    generation = profile_generation.ensure_profile_generation(profile_home)
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    original_transaction = descriptions.skill_transaction
    original_generation_for_home = profile_generation.generation_for_profile_home
    state = threading.local()

    @contextmanager
    def tracked_transaction(profile_key):
        with original_transaction(profile_key):
            state.held = True
            try:
                yield
            finally:
                state.held = False

    def checked_generation_for_home(profile_home_arg, *, named=None):
        assert getattr(state, "held", False), (
            "Profile generation must be captured only after the Profile "
            "transaction lock is held"
        )
        return original_generation_for_home(profile_home_arg, named=named)

    monkeypatch.setattr(descriptions, "skill_transaction", tracked_transaction)
    monkeypatch.setattr(
        profile_generation,
        "generation_for_profile_home",
        checked_generation_for_home,
    )

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": "demo-skill",
            "content": "---\nname: demo-skill\n---\n\n# Demo\n",
            "profile_generation": generation,
        },
    )

    assert cap["ok"]["ok"] is True


def test_skill_toggle_rejects_stale_named_profile_generation(tmp_path, monkeypatch):
    import shutil

    from api.profile_generation import ensure_profile_generation

    profile_home = tmp_path / ".hermes" / "profiles" / "demo"
    skills_dir = profile_home / "skills"
    skill_file = skills_dir / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\n---\n\n# Demo\n", encoding="utf-8"
    )
    stale_generation = ensure_profile_generation(profile_home)
    shutil.rmtree(profile_home)
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\n---\n\n# Replacement\n", encoding="utf-8"
    )
    current_generation = ensure_profile_generation(profile_home)
    config_path = profile_home / "config.yaml"
    config_path.write_text("skills:\n  disabled: []\n", encoding="utf-8")
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    routes._handle_skill_toggle(
        _FakeHandler(),
        {
            "name": "demo-skill",
            "enabled": False,
            "profile_generation": stale_generation,
        },
    )

    assert current_generation != stale_generation
    assert cap["bad"] == ("Active profile generation changed; reload and retry", 409)
    assert config_path.read_text(encoding="utf-8") == "skills:\n  disabled: []\n"


def test_skill_save_rolls_back_content_when_ui_sidecar_fails(tmp_path, monkeypatch):
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("original\n", encoding="utf-8")

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        routes, "_active_skill_ui_profile_key", lambda: "/profiles/default"
    )

    def _fail(*_args, **_kwargs):
        raise OSError("simulated sidecar failure")

    monkeypatch.setattr(skill_ui_descriptions, "set_ui_description", _fail)
    routes._handle_skill_save(
        _FakeHandler(),
        {"name": "demo", "content": "changed\n", "ui_description": "仅界面"},
    )

    assert cap["bad"][1] == 500
    assert "rolled back" in cap["bad"][0]
    assert skill_file.read_text(encoding="utf-8") == "original\n"


def test_skill_delete_keeps_skill_when_ui_sidecar_cleanup_fails(tmp_path, monkeypatch):
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("original\n", encoding="utf-8")

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        routes, "_active_skill_ui_profile_key", lambda: "/profiles/default"
    )

    def _fail(*_args, **_kwargs):
        raise OSError("simulated sidecar failure")

    monkeypatch.setattr(skill_ui_descriptions, "pop_ui_description", _fail)
    routes._handle_skill_delete(_FakeHandler(), {"name": "demo"})

    assert cap["bad"][1] == 500
    assert "was not deleted" in cap["bad"][0]
    assert skill_file.read_text(encoding="utf-8") == "original\n"


def test_same_skill_combined_saves_never_commit_mismatched_pairs(tmp_path, monkeypatch):
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    state_dir = tmp_path / "state"
    profile_key = str(tmp_path.resolve())
    _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        skill_ui_descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )

    original_set = skill_ui_descriptions.set_ui_description
    first_at_sidecar = threading.Event()
    second_finished = threading.Event()

    def interleaved_set(profile, name, text):
        if text == "说明 A":
            first_at_sidecar.set()
            second_finished.wait(timeout=0.4)
            return original_set(profile, name, text)
        result = original_set(profile, name, text)
        second_finished.set()
        return result

    monkeypatch.setattr(skill_ui_descriptions, "set_ui_description", interleaved_set)
    errors = []

    def save(content_label, description):
        try:
            routes._handle_skill_save(
                _FakeHandler(),
                {
                    "name": "demo",
                    "content": (
                        "---\nname: demo\ndescription: runtime\n---\n\n"
                        f"# {content_label}\n"
                    ),
                    "ui_description": description,
                },
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    first = threading.Thread(target=save, args=("正文 A", "说明 A"))
    first.start()
    assert first_at_sidecar.wait(timeout=2)
    second = threading.Thread(target=save, args=("正文 B", "说明 B"))
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    content = (skills_dir / "demo" / "SKILL.md").read_text(encoding="utf-8")
    description = skill_ui_descriptions.get_ui_description(profile_key, "demo")
    assert ("# 正文 A" in content, description) in {
        (True, "说明 A"),
        (False, "说明 B"),
    }


def test_failed_save_rollback_cannot_clobber_later_success(tmp_path, monkeypatch):
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    state_dir = tmp_path / "state"
    profile_key = str(skills_dir.parent.resolve())
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("original\n", encoding="utf-8")
    _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        skill_ui_descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    skill_ui_descriptions.set_ui_description(profile_key, "demo", "原说明")

    original_set = skill_ui_descriptions.set_ui_description
    first_at_sidecar = threading.Event()
    second_finished = threading.Event()

    def interleaved_set(profile, name, text):
        if text == "失败说明":
            first_at_sidecar.set()
            second_finished.wait(timeout=0.4)
            raise OSError("simulated sidecar failure")
        result = original_set(profile, name, text)
        second_finished.set()
        return result

    monkeypatch.setattr(skill_ui_descriptions, "set_ui_description", interleaved_set)

    first = threading.Thread(
        target=routes._handle_skill_save,
        args=(
            _FakeHandler(),
            {
                "name": "demo",
                "content": "---\nname: demo\n---\n\n# 失败正文\n",
                "ui_description": "失败说明",
            },
        ),
    )
    first.start()
    assert first_at_sidecar.wait(timeout=2)
    second = threading.Thread(
        target=routes._handle_skill_save,
        args=(
            _FakeHandler(),
            {
                "name": "demo",
                "content": "---\nname: demo\n---\n\n# 成功正文\n",
                "ui_description": "成功说明",
            },
        ),
    )
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert "# 成功正文" in skill_file.read_text(encoding="utf-8")
    assert skill_ui_descriptions.get_ui_description(profile_key, "demo") == "成功说明"


def test_skill_delete_racing_save_cannot_leave_orphan_ui_description(
    tmp_path, monkeypatch
):
    import shutil
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    state_dir = tmp_path / "state"
    profile_key = str(skills_dir.parent.resolve())
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\nname: demo\n---\n\n# Original\n", encoding="utf-8")
    _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        skill_ui_descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    skill_ui_descriptions.set_ui_description(profile_key, "demo", "原说明")

    real_rmtree = shutil.rmtree
    delete_at_rmtree = threading.Event()
    save_finished = threading.Event()

    def delayed_rmtree(path, *args, **kwargs):
        delete_at_rmtree.set()
        save_finished.wait(timeout=0.4)
        return real_rmtree(path, *args, **kwargs)

    original_set = skill_ui_descriptions.set_ui_description

    def signaling_set(profile, name, text):
        result = original_set(profile, name, text)
        if text == "新说明":
            save_finished.set()
        return result

    monkeypatch.setattr(shutil, "rmtree", delayed_rmtree)
    monkeypatch.setattr(skill_ui_descriptions, "set_ui_description", signaling_set)

    deleting = threading.Thread(
        target=routes._handle_skill_delete,
        args=(_FakeHandler(), {"name": "demo"}),
    )
    deleting.start()
    assert delete_at_rmtree.wait(timeout=2)
    saving = threading.Thread(
        target=routes._handle_skill_save,
        args=(
            _FakeHandler(),
            {
                "name": "demo",
                "content": "---\nname: demo\n---\n\n# New\n",
                "ui_description": "新说明",
            },
        ),
    )
    saving.start()
    deleting.join(timeout=5)
    saving.join(timeout=5)

    assert not deleting.is_alive() and not saving.is_alive()
    description = skill_ui_descriptions.get_ui_description(profile_key, "demo")
    assert skill_file.exists() == bool(description)
    if skill_file.exists():
        assert "# New" in skill_file.read_text(encoding="utf-8")
        assert description == "新说明"


@pytest.mark.parametrize(
    ("requested_name", "canonical_name"),
    [
        ("Demo", "demo"),
        ("demo skill", "demo-skill"),
        (" demo ", "demo"),
    ],
)
def test_skill_save_rejects_noncanonical_request_name(
    tmp_path, monkeypatch, requested_name, canonical_name
):
    skills_dir = tmp_path / "skills"
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": requested_name,
            "content": f"---\nname: {canonical_name}\n---\n\n# Demo\n",
            "ui_description": "中文说明",
        },
    )

    assert cap["bad"] == ("Skill name must use its canonical form", 400)
    assert not any(skills_dir.rglob("SKILL.md"))


def test_skill_save_rejects_frontmatter_name_mismatch(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": "folder-name",
            "content": "---\nname: canonical-name\n---\n\n# Demo\n",
            "ui_description": "中文说明",
        },
    )

    assert cap["bad"] == ("Skill frontmatter name must match the skill name", 400)
    assert not any(skills_dir.rglob("SKILL.md"))


@pytest.mark.parametrize("frontmatter_name", ["Demo", "demo skill"])
def test_skill_save_rejects_noncanonical_frontmatter_name(
    tmp_path, monkeypatch, frontmatter_name
):
    skills_dir = tmp_path / "skills"
    cap = _patch_skill_routes(monkeypatch, skills_dir)

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": "demo",
            "content": f"---\nname: {frontmatter_name}\n---\n\n# Demo\n",
            "ui_description": "中文说明",
        },
    )

    assert cap["bad"] == ("Skill frontmatter name must use its canonical form", 400)
    assert not any(skills_dir.rglob("SKILL.md"))


def test_existing_skill_uses_frontmatter_name_as_sidecar_identity(
    tmp_path, monkeypatch
):
    import api.skill_ui_descriptions as descriptions

    skills_dir = tmp_path / "skills"
    state_dir = tmp_path / "state"
    profile_key = str(tmp_path)
    skill_file = skills_dir / "legacy-folder" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: canonical-name\ndescription: runtime\n---\n\n# Old\n",
        encoding="utf-8",
    )
    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(routes, "_active_skill_ui_profile_key", lambda: profile_key)
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": "canonical-name",
            "category": "ignored-category",
            "content": (
                "---\nname: canonical-name\ndescription: runtime\n---\n\n# Updated\n"
            ),
            "ui_description": "规范名说明",
        },
    )

    assert cap["ok"]["ok"] is True
    assert "# Updated" in skill_file.read_text(encoding="utf-8")
    assert not (skills_dir / "ignored-category" / "canonical-name").exists()
    assert descriptions.read_profile_descriptions(profile_key) == {
        "canonical-name": "规范名说明"
    }


def test_skill_list_does_not_recreate_deleted_profile_home(tmp_path):
    profile_home = tmp_path / "profiles" / "deleted"
    skills_dir = profile_home / "skills"

    result = routes._skills_list_from_dir(skills_dir)

    assert result["skills"] == []
    assert not profile_home.exists()


def test_root_skill_list_still_initializes_missing_skills_dir(tmp_path):
    skills_dir = tmp_path / "default-home" / "skills"

    result = routes._skills_list_from_dir(skills_dir)

    assert result["skills"] == []
    assert skills_dir.is_dir()


def test_skill_save_does_not_recreate_deleted_profile_home(tmp_path, monkeypatch):
    import api.skill_ui_descriptions as descriptions

    profile_home = tmp_path / "profiles" / "deleted"
    skills_dir = profile_home / "skills"
    state_dir = tmp_path / "state"
    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        routes, "_active_skill_ui_profile_key", lambda: str(profile_home.resolve())
    )
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )

    routes._handle_skill_save(
        _FakeHandler(),
        {
            "name": "demo",
            "content": "---\nname: demo\n---\n\n# Demo\n",
            "ui_description": "不应写入",
        },
    )

    assert cap["bad"] == ("Active profile no longer exists", 409)
    assert not profile_home.exists()
    assert descriptions.read_profile_descriptions(str(profile_home.resolve())) == {}


def test_ui_description_save_does_not_recreate_deleted_profile_home(
    tmp_path, monkeypatch
):
    import api.skill_ui_descriptions as descriptions

    profile_home = tmp_path / "profiles" / "deleted"
    skills_dir = profile_home / "skills"
    state_dir = tmp_path / "state"
    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        routes, "_active_skill_ui_profile_key", lambda: str(profile_home.resolve())
    )
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )

    routes._handle_skill_ui_description_save(
        _FakeHandler(),
        {"name": "demo", "ui_description": "不应写入"},
    )

    assert cap["bad"] == ("Active profile no longer exists", 409)
    assert not profile_home.exists()
    assert descriptions.read_profile_descriptions(str(profile_home.resolve())) == {}


def test_legacy_single_file_delete_preserves_skills_root_and_siblings(
    tmp_path, monkeypatch
):
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    legacy_file = skills_dir / "legacy.md"
    sibling_legacy = skills_dir / "sibling.md"
    modern_file = skills_dir / "modern" / "SKILL.md"
    modern_file.parent.mkdir(parents=True)
    legacy_file.write_text("---\nname: legacy\n---\n\n# Legacy\n", encoding="utf-8")
    sibling_legacy.write_text(
        "---\nname: sibling\n---\n\n# Sibling\n", encoding="utf-8"
    )
    modern_file.write_text("---\nname: modern\n---\n\n# Modern\n", encoding="utf-8")

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        routes, "_active_skill_ui_profile_key", lambda: "/profiles/default"
    )
    monkeypatch.setattr(
        skill_ui_descriptions, "pop_ui_description", lambda *_args: ""
    )

    routes._handle_skill_delete(_FakeHandler(), {"name": "legacy"})

    assert cap["ok"] == {"ok": True, "name": "legacy"}
    assert skills_dir.is_dir()
    assert not legacy_file.exists()
    assert sibling_legacy.read_text(encoding="utf-8").endswith("# Sibling\n")
    assert modern_file.read_text(encoding="utf-8").endswith("# Modern\n")


def test_skill_delete_restores_ui_description_when_directory_delete_fails(
    tmp_path, monkeypatch
):
    import shutil
    import api.skill_ui_descriptions as skill_ui_descriptions

    skills_dir = tmp_path / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("original\n", encoding="utf-8")

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    expected_profile_key = str(skills_dir.parent.resolve())
    monkeypatch.setattr(
        skill_ui_descriptions, "pop_ui_description", lambda *_args: "原中文说明"
    )
    restored = {}
    monkeypatch.setattr(
        skill_ui_descriptions,
        "restore_ui_description",
        lambda profile, name, text: restored.update(
            profile=profile, name=name, text=text
        ),
    )
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    routes._handle_skill_delete(_FakeHandler(), {"name": "demo"})

    assert cap["bad"] == ("Could not delete skill", 500)
    assert restored == {
        "profile": expected_profile_key,
        "name": "demo",
        "text": "原中文说明",
    }
    assert skill_file.read_text(encoding="utf-8") == "original\n"


def test_skill_delete_failure_restores_runtime_binding(tmp_path, monkeypatch):
    import shutil

    import api.skill_ui_descriptions as descriptions
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    skills_dir = tmp_path / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo\ndescription: Runtime\n---\n\n# Original\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        descriptions,
        "sidecar_path",
        lambda: state_dir / "skill-ui-descriptions.json",
    )
    profile_key = str(tmp_path.resolve())
    with descriptions.bind_ui_description_to_runtime(
        DEFAULT_PROFILE_GENERATION, skill_file
    ):
        descriptions.set_ui_description(profile_key, "demo", "原中文说明")

    cap = _patch_skill_routes(monkeypatch, skills_dir)
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    routes._handle_skill_delete(_FakeHandler(), {"name": "demo"})

    assert cap["bad"] == ("Could not delete skill", 500)
    assert skill_file.is_file()
    assert descriptions.get_ui_description_state(
        profile_key,
        "demo",
        profile_generation=DEFAULT_PROFILE_GENERATION,
        runtime_path=skill_file,
        strict=True,
    ) == {"ui_description": "原中文说明", "stale": False}
