"""Static WebUI contracts for Profile-incarnation CAS tokens."""

from pathlib import Path


PANELS_JS = (
    Path(__file__).resolve().parents[1] / "static" / "panels.js"
).read_text(encoding="utf-8")


def _function_block(name: str) -> str:
    markers = (f"async function {name}(", f"function {name}(")
    start = next((PANELS_JS.find(marker) for marker in markers if PANELS_JS.find(marker) >= 0), -1)
    assert start >= 0, f"{name} not found"
    brace = PANELS_JS.find("{", start)
    assert brace >= 0
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(PANELS_JS)):
        char = PANELS_JS[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return PANELS_JS[brace + 1 : index]
    raise AssertionError(f"{name} block did not terminate")


def test_skills_panel_caches_generation_from_management_reads():
    assert "let _skillsProfileGeneration = null" in PANELS_JS
    assert "data.profile_generation" in _function_block("loadSkills")
    assert "data.profile_generation" in _function_block("openSkill")


def test_every_skills_panel_mutation_forwards_captured_generation():
    assert "profile_generation: _skillsProfileGeneration" in _function_block(
        "_skillMutationPayload"
    )
    for function_name in ("toggleSkill", "saveSkillForm", "deleteCurrentSkill"):
        block = _function_block(function_name)
        assert "_skillMutationPayload(" in block, function_name


def test_profiles_panel_loads_generation_and_delete_uses_captured_token():
    assert "/api/profiles?include_generation=1" in _function_block("loadProfilesPanel")
    for function_name in ("deleteCurrentProfile", "deleteProfile"):
        block = _function_block(function_name)
        assert "profile_generation" in block, function_name
        assert "include_generation=1" not in block, (
            f"{function_name} must not refresh to a replacement Profile token after confirmation"
        )


def test_profile_delete_never_falls_back_to_tokenless_request():
    helper = _function_block("_profileGenerationForDelete")
    assert "profile_generation" in helper
    assert "throw new Error" in helper
    assert "/api/profiles" not in helper
