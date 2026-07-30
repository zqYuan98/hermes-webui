"""Contract tests for the unified Hermes agent identity icon."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
INDEX = STATIC / "index.html"
UI = STATIC / "ui.js"
STYLE = STATIC / "style.css"
SHARE_HTML = STATIC / "share.html"
SHARE_JS = STATIC / "share.js"
MANIFEST = STATIC / "manifest.json"
SW = STATIC / "sw.js"


def test_agent_icon_source_is_the_supplied_embedded_png_svg():
    source = (STATIC / "favicon.svg").read_text(encoding="utf-8")
    assert 'width="1024" height="1024" viewBox="0 0 1024 1024"' in source
    assert 'xlink:href="data:image/png;base64,' in source


def test_every_agent_identity_surface_uses_the_shared_icon_asset():
    index = INDEX.read_text(encoding="utf-8")
    ui = UI.read_text(encoding="utf-8")
    share_html = SHARE_HTML.read_text(encoding="utf-8")
    share_js = SHARE_JS.read_text(encoding="utf-8")

    assert '<img class="agent-brand-icon" src="static/favicon.svg"' in index
    assert '<img class="agent-profile-icon" src="static/favicon.svg"' in index
    assert index.count('<img class="agent-profile-icon" src="static/favicon.svg"') == 2
    assert '<img class="agent-welcome-icon" src="static/favicon.svg"' in index
    assert '<img class="agent-avatar-image" src="static/favicon.svg"' in ui
    assert '<img class="agent-brand-icon" src="/static/favicon.svg"' in share_html
    assert 'share-role-badge agent-avatar' in share_js
    assert '<img class="agent-avatar-image" src="/static/favicon.svg"' in share_js


def test_agent_icon_styles_cover_small_and_large_identity_surfaces():
    css = STYLE.read_text(encoding="utf-8")
    for selector in (
        ".agent-brand-icon",
        ".agent-welcome-icon",
        ".agent-avatar-image",
        '.share-message[data-role="assistant"] .share-role-badge',
    ):
        assert selector in css
    assert "object-fit:cover" in css.replace(" ", "")


def test_compact_viewports_keep_the_welcome_avatar_fully_visible():
    css = STYLE.read_text(encoding="utf-8")
    compact_css = css.replace(" ", "")
    marker = "@media(max-height:700px)"
    assert marker in compact_css
    compact = compact_css[compact_css.index(marker) :]
    assert ".empty-state" in compact
    assert ".agent-welcome-icon" in compact


def test_pwa_and_notifications_keep_using_regenerated_icon_family():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    icon_sources = {icon["src"] for icon in manifest["icons"]}
    assert {
        "static/favicon.svg",
        "static/favicon-32.png",
        "static/favicon-192.png",
        "static/favicon-512.png",
    }.issubset(icon_sources)

    sw = SW.read_text(encoding="utf-8")
    for asset in (
        "./static/favicon.svg",
        "./static/favicon-32.png",
        "./static/favicon-192.png",
        "./static/favicon-512.png",
    ):
        assert asset in sw

    for name in ("favicon-32.png", "favicon-192.png", "favicon-512.png"):
        payload = (STATIC / name).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
