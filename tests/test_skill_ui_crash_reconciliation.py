import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

import api.routes as routes


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="requires POSIX SIGKILL")
@pytest.mark.parametrize(
    ("kill_phase", "expected_runtime", "expected_ui", "expected_stale"),
    [
        ("before_runtime", "old", "Old UI", False),
        ("before_sidecar", "new", "", True),
        ("after_sidecar", "new", "New UI", False),
    ],
)
def test_sigkill_combined_save_reconciles_in_fresh_interpreter(
    tmp_path,
    kill_phase,
    expected_runtime,
    expected_ui,
    expected_stale,
):
    """Every durable kill boundary is old, new, or explicitly stale."""
    import hermes_constants
    from api.profile_generation import DEFAULT_PROFILE_GENERATION

    repo = pathlib.Path(routes.__file__).resolve().parent.parent
    agent_repo = pathlib.Path(hermes_constants.__file__).resolve().parent
    profile_home = (tmp_path / "profile").resolve()
    skills_dir = profile_home / "skills"
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    old_content = "---\nname: demo\n---\n\n# Old runtime\n"
    new_content = "---\nname: demo\n---\n\n# New runtime\n"
    skill_file.write_text(old_content, encoding="utf-8")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    profile_key = str(profile_home)
    sidecar = state_dir / "skill-ui-descriptions.json"
    sidecar.write_text(
        json.dumps(
            {
                "version": 2,
                "profiles": {profile_key: {"demo": "Old UI"}},
                "bindings": {
                    profile_key: {
                        "profile_generation": DEFAULT_PROFILE_GENERATION,
                        "skills": {
                            "demo": hashlib.sha256(
                                old_content.encode("utf-8")
                            ).hexdigest()
                        },
                    }
                },
                "legacy_unbound": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.chmod(sidecar, 0o600)

    marker = tmp_path / f"{kill_phase}.ready"
    worker = tmp_path / "sigkill_skill_save_worker.py"
    worker.write_text(
        r'''
import os
import pathlib
import signal
import sys

state_dir = pathlib.Path(sys.argv[1])
skills_dir = pathlib.Path(sys.argv[2])
marker = pathlib.Path(sys.argv[3])
phase = sys.argv[4]
new_content = sys.argv[5]
os.environ["HERMES_WEBUI_STATE_DIR"] = str(state_dir)

import api.paths as paths
import api.routes as routes
import api.skill_ui_descriptions as descriptions

routes._active_skills_dir = lambda: skills_dir
routes.j = lambda *_args, **_kwargs: True


def fail_bad(_handler, message, status=400):
    raise RuntimeError(f"unexpected HTTP {status}: {message}")


routes.bad = fail_bad
real_atomic_write = paths._atomic_write_text
real_set = descriptions.set_ui_description


def stop_at_boundary():
    marker.write_text("ready", encoding="utf-8")
    signal.pause()


if phase == "before_runtime":
    def gated_atomic_write(*args, **kwargs):
        stop_at_boundary()
        return real_atomic_write(*args, **kwargs)

    paths._atomic_write_text = gated_atomic_write
elif phase == "before_sidecar":
    def gated_set(*args, **kwargs):
        stop_at_boundary()
        return real_set(*args, **kwargs)

    descriptions.set_ui_description = gated_set
elif phase == "after_sidecar":
    def gated_set(*args, **kwargs):
        result = real_set(*args, **kwargs)
        stop_at_boundary()
        return result

    descriptions.set_ui_description = gated_set
else:
    raise RuntimeError(f"unknown phase: {phase}")


class Handler:
    pass


routes._handle_skill_save(
    Handler(),
    {
        "name": "demo",
        "content": new_content,
        "ui_description": "New UI",
    },
)
raise RuntimeError("save unexpectedly crossed the kill boundary")
'''.strip(),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HERMES_WEBUI_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(repo), str(agent_repo), env.get("PYTHONPATH", ""))
        if part
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(worker),
            str(state_dir),
            str(skills_dir),
            str(marker),
            kill_phase,
            new_content,
        ],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 15
    while not marker.exists() and time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                "worker exited before kill boundary: "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.02)
    assert marker.exists(), "worker did not reach the requested kill boundary"

    os.kill(process.pid, signal.SIGKILL)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == -signal.SIGKILL, (
        f"worker was not killed by SIGKILL: rc={process.returncode} "
        f"stdout={stdout!r} stderr={stderr!r}"
    )

    reader = subprocess.run(
        [
            sys.executable,
            "-c",
            r'''
import json
import os
import pathlib
import sys

os.environ["HERMES_WEBUI_STATE_DIR"] = sys.argv[1]
from api.profile_generation import DEFAULT_PROFILE_GENERATION
from api.skill_ui_descriptions import get_ui_description_state

state = get_ui_description_state(
    sys.argv[2],
    "demo",
    profile_generation=DEFAULT_PROFILE_GENERATION,
    runtime_path=pathlib.Path(sys.argv[3]),
    strict=True,
)
print(json.dumps(state, ensure_ascii=False, sort_keys=True))
'''.strip(),
            str(state_dir),
            profile_key,
            str(skill_file),
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert reader.returncode == 0, (
        f"fresh reader failed: stdout={reader.stdout!r} stderr={reader.stderr!r}"
    )
    state = json.loads(reader.stdout.strip().splitlines()[-1])
    runtime = skill_file.read_text(encoding="utf-8")
    assert ("# New runtime" in runtime) is (expected_runtime == "new")
    assert state == {
        "stale": expected_stale,
        "ui_description": expected_ui,
    }
