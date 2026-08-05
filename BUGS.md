# Bugs Backlog

This file tracks UI bugs and polish items. Fixed items are kept for reference.

---

## Open Bugs

*No open bugs at this time.*

---

## Known Limitations

- **Two-container Docker setup: tools run in WebUI container** — In the two-container setup (hermes-agent + hermes-webui as separate containers), WebUI-initiated agent sessions run tools in the WebUI container, not the agent container. This is a known architectural constraint. Workaround: use the combined single-image approach, or initiate sessions via the CLI in the agent container. (#681)

- **Image-in-chat vs. saved-to-workspace mismatch** — When the agent displays an inline image (from a URL) and the user asks it to save that image, the agent issues a fresh download which may return a different file if the source URL is CDN-rotated or parameterized. The WebUI correctly renders whatever URL the agent provides. Fix requires agent-side URL caching. (#641)

- **MCP tools not available in WebUI sessions** — MCP servers must be configured in the active profile's config.yaml under mcp_servers:. If MCP tools are not appearing, check that the profile is correct and the MCP server process is reachable from inside the WebUI container. (#628)

- **os.environ race condition in concurrent sessions** — Concurrent agent sessions share process-level os.environ for TERMINAL_CWD, HERMES_SESSION_KEY, and HERMES_HOME. _ENV_LOCK serializes mutations but does not fully isolate env vars during agent execution. Upstream fix pending in hermes-agent. (#195)

---

## Fixed

### ~~External directory views could not upload files~~ -- Fixed

- **Was:** Escape-target grants were correctly read-only, but the workspace Upload button and OS drag/drop were blocked along with every mutation, so an explicitly opened external directory had no safe upload workflow.
- **Fix:** Added an explicit browser-origin/CSRF-protected confirmation that mints a separate short-lived, session-bound upload-only capability. External uploads are re-anchored under that authorized root and reject traversal, nested symlink escapes, expired/wrong-session tokens, and read-only grants; edit/delete/rename/move/create/reveal remain read-only.

### ~~Hermes Hub had no first-class meeting-minutes workflow~~ -- Fixed

- **Was:** Hub could track design, operations, resources, and inbox entries, but meetings, decisions, risks, open questions, and accountable action items had no structured home.
- **Fix:** Added the Chinese `会议` module backed by additive `hub-meetings.json` storage, with meeting CRUD, structured minutes and per-action owner/due/deliverable/acceptance/status controls, plus meeting/action homepage stats and timeline entries.

### ~~Hub auto-refresh polled the file API from a hidden tab~~ -- Fixed

- **Was:** `reloadIfVisible` (extensions/hub/hub.js) gated its 60s poll only on the Hub panel being the active panel, not on `document.hidden`. A backgrounded tab left on Hub kept issuing `/api/file` reads forever — every other poll in the codebase gates on `document.hidden`.
- **Fix:** Early-return on `document.hidden`. The existing `visibilitychange`/`focus` handlers already re-run the poll on return, so no freshness is lost. Pinned by `test_hub_auto_refresh_is_gated_on_tab_visibility`.

### ~~ESLint runtime guard did not cover extensions/~~ -- Fixed

- **Was:** The `no-const-assign` brick-class guard (#3162) ran only over `static/**/*.js`. `extensions/**/*.js` ships browser JS through the same `<script>` path, so an identical bug there was unguarded.
- **Fix:** Added `extensions/` to the guard in `tests/test_static_js_runtime_lint.py`, `package.json`, TESTING.md and the config header. Tree is clean under the widened scope.

### ~~Session title truncation / hover actions~~ -- Fixed (Sprint 16)

- **Was:** Action icons reserved ~30px of space even when invisible, truncating titles.
- **Fix:** Wrapped all action buttons in a `.session-actions` overlay container with `position:absolute`. Titles now use full available width. Actions appear on hover with a gradient fade from the right edge.

### ~~Folder/project assignment interaction feels sticky~~ -- Fixed (Sprint 16)

- **Was:** Folder icon stayed permanently visible (blue, 60% opacity) when a session belonged to a project.
- **Fix:** Replaced `.has-project` persistent button with a colored left border matching the project color. The folder button now only appears in the hover overlay like all other actions.

### ~~Project picker clipping and width~~ -- Fixed (v0.17.3)

- **Was:** Picker was clipped by `overflow:hidden` on `.session-item` ancestors. With `position:fixed`, no containing block constrained width -- picker stretched to full viewport.
- **Fix:** Dynamic width calculation (min 160px, max 220px). Event listener reordering. Cleanup sequence corrected. (PR #25)

### ~~NameError crash in model discovery~~ -- Fixed (v0.17.3)

- **Was:** `logger.debug()` called in custom endpoint `except` block, but `logger` was never imported in `config.py`. Every failed endpoint fetch crashed with `NameError`.
- **Fix:** Replaced with silent `pass` -- unreachable endpoints are expected when no local LLM is configured. (PR #24)

---

## Notes

- Sprint 16 replaced all emoji HTML entities with monochrome SVG line icons (`ICONS` constant in `sessions.js`).
- All session action buttons now use the overlay pattern for consistent UX.
