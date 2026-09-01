import importlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_profiles_module(base_home: Path):
    os.environ["HERMES_BASE_HOME"] = str(base_home)
    os.environ["HERMES_HOME"] = str(base_home)

    # Save the original module references so we can restore them after the test.
    # Permanently deleting api.config / api.profiles from sys.modules breaks
    # subsequent tests that import these modules and expect consistent state.
    _saved = {
        name: sys.modules[name]
        for name in ["api.config", "api.profiles"]
        if name in sys.modules
    }

    for name in ["api.config", "api.profiles"]:
        if name in sys.modules:
            del sys.modules[name]

    profiles = importlib.import_module("api.profiles")

    # Restore original modules and package attributes so the cache stays
    # consistent for the rest of the suite.
    sys.modules.update(_saved)
    api_pkg = sys.modules.get("api")
    if api_pkg is not None:
        for name, module in _saved.items():
            setattr(api_pkg, name.rsplit(".", 1)[-1], module)

    return profiles


def test_switch_profile_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        base = temp_root / ".hermes"
        (base / "profiles").mkdir(parents=True)
        (temp_root / "escape-target").mkdir()

        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.switch_profile("../../escape-target")


def test_delete_profile_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        base = temp_root / ".hermes"
        (base / "profiles").mkdir(parents=True)
        (temp_root / "escape-target").mkdir()

        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.delete_profile_api("../../escape-target")


def test_switch_profile_allows_valid_profile_name():
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        base = temp_root / ".hermes"
        profile_dir = base / "profiles" / "demo"
        profile_dir.mkdir(parents=True)

        profiles = _reload_profiles_module(base)
        result = profiles.switch_profile("demo")

        assert result["active"] == "demo"
        assert Path(os.environ["HERMES_HOME"]).resolve() == profile_dir.resolve()


def test_profile_generation_changes_when_same_name_directory_is_recreated(tmp_path):
    import shutil

    from api.profile_generation import ensure_profile_generation

    profile_dir = tmp_path / ".hermes" / "profiles" / "demo"
    profile_dir.mkdir(parents=True)
    first = ensure_profile_generation(profile_dir)

    shutil.rmtree(profile_dir)
    profile_dir.mkdir(parents=True)
    second = ensure_profile_generation(profile_dir)

    assert first != second


def test_clone_create_uses_root_source_destination_transaction(
    tmp_path, monkeypatch
):
    import contextlib

    import api.profiles as profiles
    import api.skill_ui_descriptions as descriptions
    import hermes_cli.profiles as cli_profiles

    base = (tmp_path / ".hermes").resolve()
    source = base / "profiles" / "source"
    destination = base / "profiles" / "clone"
    source.mkdir(parents=True)

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [])

    transaction_keys = []
    state = {"active": False}

    @contextlib.contextmanager
    def tracked_transaction(keys):
        transaction_keys.append(tuple(str(Path(key).resolve()) for key in keys))
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    def fake_create(name, **_kwargs):
        assert state["active"], "Agent create ran outside the lifecycle transaction"
        assert name == "clone"
        destination.mkdir(parents=True, exist_ok=False)
        return destination

    monkeypatch.setattr(descriptions, "profile_transaction", tracked_transaction)
    monkeypatch.setattr(cli_profiles, "create_profile", fake_create)

    result = profiles.create_profile_api("clone", clone_from="source")

    assert result["name"] == "clone"
    assert transaction_keys == [
        (str(base), str(source.resolve()), str(destination.resolve()))
    ]


def test_clone_root_alias_is_resolved_only_inside_profile_transaction(
    tmp_path, monkeypatch
):
    import contextlib

    import api.profiles as profiles
    import api.skill_ui_descriptions as descriptions
    import hermes_cli.profiles as cli_profiles

    base = (tmp_path / ".hermes").resolve()
    alias_candidate = base / "profiles" / "renamed-root"
    destination = base / "profiles" / "clone"
    base.mkdir(parents=True)
    state = {"active": False}
    transaction_keys = []

    @contextlib.contextmanager
    def tracked_transaction(keys):
        transaction_keys.append(tuple(str(Path(key).resolve()) for key in keys))
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    def tracked_is_root(name):
        if name != "default":
            assert state["active"], "live root-alias lookup escaped the transaction"
        return name in {"default", "renamed-root"}

    def fake_create(name, **kwargs):
        assert state["active"]
        assert name == "clone"
        assert kwargs["clone_from"] == "renamed-root"
        destination.mkdir(parents=True, exist_ok=False)

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(profiles, "_is_root_profile", tracked_is_root)
    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [])
    monkeypatch.setattr(descriptions, "profile_transaction", tracked_transaction)
    monkeypatch.setattr(cli_profiles, "create_profile", fake_create)

    result = profiles.create_profile_api("clone", clone_from="renamed-root")

    assert result["name"] == "clone"
    assert transaction_keys == [
        (str(base), str(alias_candidate.resolve()), str(destination.resolve()))
    ]


def _patch_profile_delete_environment(tmp_path, monkeypatch):
    import api.profiles as profiles
    import api.skill_ui_descriptions as descriptions
    import hermes_cli.profiles as cli_profiles

    base = tmp_path / ".hermes"
    profile_dir = base / "profiles" / "demo"
    profile_dir.mkdir(parents=True)
    sidecar = tmp_path / "webui" / "skill-ui-descriptions.json"
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(profiles, "_active_profile", "default")
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    monkeypatch.setattr(
        profiles, "_resolve_named_profile_home", lambda _name: profile_dir
    )
    monkeypatch.setattr(descriptions, "sidecar_path", lambda: sidecar)
    return profiles, descriptions, cli_profiles, profile_dir


def test_delete_uses_root_and_profile_transaction(tmp_path, monkeypatch):
    import contextlib
    import shutil

    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    base = profiles._DEFAULT_HERMES_HOME.resolve()
    transaction_keys = []
    state = {"active": False}

    @contextlib.contextmanager
    def tracked_transaction(keys):
        transaction_keys.append(tuple(str(Path(key).resolve()) for key in keys))
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    def fake_delete(_name, yes=True):
        assert state["active"], "Agent delete ran outside the lifecycle transaction"
        assert yes is True
        shutil.rmtree(profile_dir)

    monkeypatch.setattr(descriptions, "profile_transaction", tracked_transaction)
    monkeypatch.setattr(cli_profiles, "delete_profile", fake_delete)

    result = profiles.delete_profile_api("demo")

    assert result == {"ok": True, "name": "demo"}
    assert transaction_keys == [(str(base), str(profile_dir.resolve()))]


def test_delete_root_alias_check_occurs_inside_profile_transaction(
    tmp_path, monkeypatch
):
    import contextlib
    import shutil

    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    state = {"active": False}

    @contextlib.contextmanager
    def tracked_transaction(keys):
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    def tracked_is_root(name):
        if name != "default":
            assert state["active"], "live root-alias lookup escaped the transaction"
        return name == "default"

    def fake_delete(_name, yes=True):
        assert state["active"]
        shutil.rmtree(profile_dir)

    monkeypatch.setattr(descriptions, "profile_transaction", tracked_transaction)
    monkeypatch.setattr(profiles, "_is_root_profile", tracked_is_root)
    monkeypatch.setattr(cli_profiles, "delete_profile", fake_delete)

    assert profiles.delete_profile_api("demo") == {"ok": True, "name": "demo"}
    assert not state["active"]
    assert not profile_dir.exists()


def test_delete_profile_prunes_ui_description_bucket(tmp_path, monkeypatch):
    import shutil

    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    profile_key = str(profile_dir.resolve())
    descriptions.set_ui_description(profile_key, "demo-skill", "旧中文说明")
    monkeypatch.setattr(
        cli_profiles,
        "delete_profile",
        lambda _name, yes=True: shutil.rmtree(profile_dir),
    )

    result = profiles.delete_profile_api("demo")

    assert result == {"ok": True, "name": "demo"}
    assert not profile_dir.exists()
    assert descriptions.read_profile_descriptions(profile_key) == {}


def test_delete_profile_rejects_stale_generation_after_same_name_recreate(
    tmp_path, monkeypatch
):
    import shutil

    from api.profile_generation import ensure_profile_generation

    profiles, _descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    stale_generation = ensure_profile_generation(profile_dir)
    shutil.rmtree(profile_dir)
    profile_dir.mkdir(parents=True)
    current_generation = ensure_profile_generation(profile_dir)
    deleted = []
    monkeypatch.setattr(
        cli_profiles,
        "delete_profile",
        lambda *_args, **_kwargs: deleted.append(True),
    )

    with pytest.raises(RuntimeError, match="generation"):
        profiles.delete_profile_api(
            "demo", expected_generation=stale_generation
        )

    assert current_generation != stale_generation
    assert profile_dir.is_dir()
    assert deleted == []


def test_delete_profile_restores_ui_bucket_when_directory_delete_fails(
    tmp_path, monkeypatch
):
    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    profile_key = str(profile_dir.resolve())
    expected = {"demo-skill": "旧中文说明"}
    descriptions.set_ui_description(profile_key, "demo-skill", "旧中文说明")
    monkeypatch.setattr(
        cli_profiles,
        "delete_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    with pytest.raises(OSError, match="busy"):
        profiles.delete_profile_api("demo")

    assert profile_dir.exists()
    assert descriptions.read_profile_descriptions(profile_key) == expected


def test_delete_profile_failure_restores_ui_runtime_bindings(tmp_path, monkeypatch):
    from api.profile_generation import ensure_profile_generation

    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    skill_file = profile_dir / "skills" / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\ndescription: Runtime\n---\n\n# Original\n",
        encoding="utf-8",
    )
    generation = ensure_profile_generation(profile_dir)
    profile_key = str(profile_dir.resolve())
    with descriptions.bind_ui_description_to_runtime(generation, skill_file):
        descriptions.set_ui_description(profile_key, "demo-skill", "旧中文说明")
    monkeypatch.setattr(
        cli_profiles,
        "delete_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    with pytest.raises(OSError, match="busy"):
        profiles.delete_profile_api(
            "demo", expected_generation=generation, require_generation=True
        )

    assert profile_dir.exists()
    assert descriptions.get_ui_description_state(
        profile_key,
        "demo-skill",
        profile_generation=generation,
        runtime_path=skill_file,
        strict=True,
    ) == {"ui_description": "旧中文说明", "stale": False}


def test_same_name_profile_create_waits_for_delete_transaction(
    tmp_path, monkeypatch
):
    import shutil

    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    delete_at_cli = threading.Event()
    allow_delete = threading.Event()
    create_entered = threading.Event()
    errors = []

    def delayed_delete(_name, yes=True):
        delete_at_cli.set()
        assert allow_delete.wait(timeout=5)
        shutil.rmtree(profile_dir)

    def fake_create(_name, **_kwargs):
        create_entered.set()
        profile_dir.mkdir(parents=True, exist_ok=False)

    monkeypatch.setattr(cli_profiles, "delete_profile", delayed_delete)
    monkeypatch.setattr(cli_profiles, "create_profile", fake_create)
    monkeypatch.setattr(cli_profiles, "seed_profile_skills", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [])

    def run_delete():
        try:
            profiles.delete_profile_api("demo")
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    def run_create():
        try:
            profiles.create_profile_api("demo")
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    deleting = threading.Thread(target=run_delete)
    creating = threading.Thread(target=run_create)
    deleting.start()
    assert delete_at_cli.wait(timeout=5)
    creating.start()

    assert not create_entered.wait(timeout=0.3)
    allow_delete.set()
    deleting.join(timeout=5)
    creating.join(timeout=5)

    assert not errors
    assert not deleting.is_alive() and not creating.is_alive()
    assert create_entered.is_set()
    assert profile_dir.is_dir()


def test_delete_profile_is_fail_closed_when_ui_bucket_cleanup_fails(
    tmp_path, monkeypatch
):
    profiles, descriptions, cli_profiles, profile_dir = (
        _patch_profile_delete_environment(tmp_path, monkeypatch)
    )
    deleted = []
    monkeypatch.setattr(
        descriptions,
        "pop_profile_descriptions",
        lambda _key: (_ for _ in ()).throw(OSError("sidecar unavailable")),
    )
    monkeypatch.setattr(
        cli_profiles,
        "delete_profile",
        lambda *_args, **_kwargs: deleted.append(True),
    )

    with pytest.raises(OSError, match="sidecar unavailable"):
        profiles.delete_profile_api("demo")

    assert profile_dir.exists()
    assert deleted == []


class _ProfileDeleteHandler:
    def __init__(self):
        self.status = None
        self.body = bytearray()
        self.wfile = self
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)


def test_profile_delete_route_maps_sidecar_io_failure_to_500(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "name": "demo",
            "profile_generation": "11111111-1111-4111-8111-111111111111",
        },
    )
    monkeypatch.setattr(
        routes,
        "_guard_request_session_visibility",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(profiles, "_validate_profile_name", lambda _name: None)
    monkeypatch.setattr(
        profiles,
        "delete_profile_api",
        lambda _name, **_kwargs: (_ for _ in ()).throw(OSError("sidecar unavailable")),
    )

    handler = _ProfileDeleteHandler()
    routes.handle_post(handler, urlparse("/api/profile/delete"))

    assert handler.status == 500
    assert json.loads(handler.body) == {"error": "Could not delete profile"}


def test_profile_delete_route_forwards_generation(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    captured = {}
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "name": "demo",
            "profile_generation": "11111111-1111-4111-8111-111111111111",
        },
    )
    monkeypatch.setattr(
        routes,
        "_guard_request_session_visibility",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(profiles, "_validate_profile_name", lambda _name: None)

    def fake_delete(name, *, expected_generation=None, require_generation=False):
        captured.update(
            name=name,
            expected_generation=expected_generation,
            require_generation=require_generation,
        )
        return {"ok": True, "name": name}

    monkeypatch.setattr(profiles, "delete_profile_api", fake_delete)

    handler = _ProfileDeleteHandler()
    routes.handle_post(handler, urlparse("/api/profile/delete"))

    assert handler.status == 200
    assert captured == {
        "name": "demo",
        "expected_generation": "11111111-1111-4111-8111-111111111111",
        "require_generation": True,
    }
