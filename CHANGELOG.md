# Changelog

## Unreleased

Released as 2.0.0 when it ships. The entries below cover the result-envelope
sweep on this branch only — plans 2 and 3 (automation-editing and
validation work) are not included.

Every list-returning tool changes shape. If your client code, prompts, or
saved conversations assume a bare list, they need updating — see below.

### Changed — breaking

- Every tool that used to return a bare list now returns an object instead.
  52 of the 54 converted tools get the shape
  `{total, returned, offset, note?, <collection>: [...]}`; the other two are
  exceptions with their own single-record shape — see two bullets below. A
  list return made the MCP SDK emit one response block per element, and none
  at all for an empty list, so a caller could not tell "no results" from "the
  call failed", and a truncated result had nowhere to say so. Failures now
  arrive as `{error, detail}` at the top level — never as an element of a
  result list. This applies to 54 tools across 29 files (not counting
  `tools/_base.py`, which holds `_ws_commands` and `_ws_multi` — helpers, not
  tools); the full inventory is enforced by a test that fails if any tool is
  missed.
- `list_automations` gains `search`, `label`, `limit` (default 50, 0 for no
  limit) and `offset`, and reports each automation's `labels`. A prompt or
  script that called `list_automations()` expecting the complete set now
  gets a 50-item page; pass `limit=0` for the old "everything" behaviour, and
  check `total` against `returned` (or the `note` field) to detect a
  truncated page.
- `get_entity_labels` returns `{entity_id, labels}` instead of a bare list of
  labels; `call_service` returns `{result}` instead of the raw response body.
- `create_helper` now rejects any `config` key outside a per-domain
  whitelist mirroring each helper integration's own schema, before ever
  sending it to Home Assistant. A caller that was relying on an
  unrecognised key being silently forwarded now gets `error(
  "invalid_config_keys", ...)` instead — see the privilege-escalation fix
  below for why. `create_helper` also no longer invents `helper_id`/
  `entity_id` from the name when Home Assistant's response carries no id.

### Fixed

- Three tools stopped collecting results the moment they hit their own
  `limit`, so they could never report a `total` larger than the page they
  returned — a caller had no way to learn that more results existed:
  `search_entities`, `list_entities_by_integration`, `list_sensors`.
- `search_hacs` sliced its results down to 20 *before* counting them, so
  `total` could never exceed 20 no matter how many repositories actually
  matched. It now reports the true match count and gained a `limit`
  parameter (default 20) so the page size is explicit rather than baked in.
- `get_statistics_summary` collapsed a real per-entity WebSocket failure into
  the same `"no_data"` result used for an entity with nothing recorded — a
  tool asserting a cause it had not established. The two are now
  distinguished.
- `list_schedules` reported *any* WebSocket failure as "the Scheduler custom
  component is not installed," including failures that had nothing to do
  with whether Scheduler is installed. Only the specific error codes that
  mean "no such command" are now diagnosed that way; anything else surfaces
  as itself.
- `list_config_flows` discarded the failure of its REST fallback entirely
  (`except Exception: pass`), so a double failure (WebSocket down *and* REST
  down) silently reported "no pending flows." It now reports the underlying
  error.
- `list_config_entries` raised `TypeError` when a config entry had a null
  `title` (which Home Assistant permits) — a null-safe sort key fixes it.
- WebSocket failures were swallowed into empty lists across `system.py`,
  `hacs.py`, `tags.py`, `users.py`, `dashboards.py` and `automations.py`. In
  one documented case this was read as "the integration was removed" during
  an incident and produced a wrong diagnosis; it was actually an
  unregistered command. Separately, 24 more WebSocket call sites had the
  mirror-image bug — `if not result.get("success", True)` reads a
  transport/auth failure (`_ws()` returns `{"error": "Auth failed: ..."}`,
  no `"success"` key at all) as a default success — across `assist.py` (5
  sites), `dashboards.py` (7), `users.py` (3), `persons.py` (3), `tags.py`
  (3), `hacs.py`'s shared `_hacs_check` (1), `diagnostics.py`'s
  `get_system_health` (1) and `media_players.py`'s `browse_media` (1); e.g.
  `update_dashboard_config` could report `{"saved": true}` for a dashboard
  that was never written. All 24 now route through `ws_error()`.
  **This class of fault is smaller than it was, but not closed.**
  `.get("result", ...)` and its variants (`.get("result", {})`,
  `.get("result", r)`, …) — the read-side shape of the same bug — now stand
  at **8 sites across 5 files** (an AST count taken for this release, not
  re-derived from the previous one): `addons.py` ×3, `hacs.py` ×2,
  `areas.py` ×1, `helpers.py` ×1, `sensors.py` ×1. The previously-stated "41
  sites across 16 files" was never re-verified after the sweep that
  produced it and does not reproduce; it is retracted rather than corrected
  in place, since there is no way to know now what the real number was on
  the day it was written. Six of the eight current sites are already inert:
  `addons.py`'s three and `hacs.py`'s two are gated by `raise_for_status()`/
  `_hacs_check()` on the same read, and `areas.py`'s one sits two lines
  below a `ws_error()` check on that same read (dead code, not a live bug).
  Two are not: `helpers.py`'s `create_template_sensor` reads a config-flow
  response that can legitimately answer 200 with no `"result"` key for a
  real validation failure, not only for a success of a different shape, so
  `entry_id` can default to `""` with no top-level error marker (the raw
  response is still echoed back in full, for a caller that checks it); and
  `sensors.py`'s `get_energy_summary` has no gate at all on its own
  `config/area_registry/list` read, unlike the `entity_area_map()` call two
  lines below it, which does report a degraded note on failure — a failed
  read here silently groups every power sensor under "other" with nothing
  to say so. Neither is fixed in this release. `areas.py`'s one remaining
  site, inside `get_entity_registry`, is no longer this file's largest: its
  worst case, `get_device` reading back a dead connection as "Device not
  found", is fixed this release (see below).
- `get_device` iterated `r.get("result", [])`, so a failed device-registry
  read — a dead connection, a revoked token — fell through the empty
  default straight to `{"error": "Device not found: <id>"}`, byte-for-byte
  the fault already fixed in `_dashboard_id()` one file over, and bypassed
  `error()` entirely, so the response carried no `detail`. Now routes
  through `ws_error()`, the same as `_dashboard_id()`.
- Ten tools returned their error as an element of the result list instead of
  as the top-level response, so a caller iterating results could read a
  failure as if it were a record: `list_device_triggers`,
  `list_device_conditions`, `list_device_actions`, `list_schedules`,
  `list_lovelace_resources`, `list_hacs_repos`, `search_hacs`, `list_addons`,
  `list_tags`, `list_users`.
- `get_automation_trace` now documents that traces live in memory and are
  lost on restart, so an empty result is not evidence that an automation
  never ran.

### Added

- A pytest suite and a GitHub Actions CI workflow
  (`.github/workflows/test.yml`). The repository previously had no tests.

### Fixed — a privilege escalation and a tool that never worked

The most severe items in this release, called out ahead of the
mechanical sweeps below:

- **`create_helper` allowed a caller to run an arbitrary WebSocket command
  instead of creating a helper.** `config` was spread into the WebSocket
  message after the reserved `type`/`name` keys, so a `config` carrying its
  own `"type"` silently replaced `{domain}/create` with whatever command
  that value named — reordering the spread would only have stopped that one
  key from winning a collision, not stopped it, or any other unexpected
  key, from reaching the command at all. Verified live against a throwaway
  Home Assistant instance: `create_helper(domain="input_boolean",
  config={"type": "config/auth/create", "name": "PortaSulRetro",
  "group_ids": ["system-admin"]})` created a real `system-admin` user
  account. `config` is now checked against a per-domain whitelist mirroring
  each helper integration's own schema before any WebSocket command is
  sent — `type` and `name` are never in an allowed set, so the hijack is
  refused with `error("invalid_config_keys", ...)` the same way any other
  unexpected key now is, not special-cased. `create_helper` also stopped
  inventing `helper_id`/`entity_id` from the name when Home Assistant's
  response carries no id: it now reports `no_id_in_response`, or verifies
  the constructed entity_id actually exists before claiming it.
- **`create_backup` never produced a backup.** Home Assistant's
  `backup/generate` requires `agent_ids`; the tool sent none, so every call
  failed — and the failure itself was hidden: `result.get("result") or
  result` read the failed `{"success": false, "error": {...}}` frame two
  levels removed from an `error()` envelope, so no caller ever saw why. It
  now defaults to every agent `backup/agents/info` reports (overridable
  with a new `agent_ids` parameter) and routes the response through
  `ws_error()`. Verified live: `create_backup()` with no arguments now
  produces a real backup, confirmed via `list_backups()`.

### Fixed — write tools stop treating a transport failure as success

See the `.get("result", ...)` bullet above for the read-side shape of this
same bug. The write side had 24 sites where `if not result.get("success",
True)` — or an equivalent hand-rolled check — read a transport/auth
failure as a default success, all now routed through `ws_error()`:
`assist.py` (5 sites: the assist-pipeline tools), `dashboards.py` (7:
`update_dashboard_config` in particular used to report `{"saved": true}`
for a dashboard that was never written), `users.py` (3: `create_user`'s and
`update_user`'s own fallback chains additionally returned a bare `{}` on
the same failure — no error, no data), `persons.py` (3), `tags.py` (3),
`hacs.py`'s shared `_hacs_check` (1, covering all five HACS tools —
`install_hacs_repo` used to report `{"installed": true}` for a write that
never reached Home Assistant), `diagnostics.py`'s `get_system_health` (1 —
it also stopped blaming every failure, including an authorisation failure,
on "not available via Supervisor proxy", a cause it had not established),
and `media_players.py`'s `browse_media` (1 — it used to fall through to an
empty-looking browse result, every field `None`, instead of an error).

### Changed — actuation reporting (package C)

34 tools called a Home Assistant service and returned a hardcoded
`{"ok": true}` regardless of whether the target entity existed or the
command took effect — indistinguishable from a real success, a call aimed
at a nonexistent entity_id (Home Assistant answers both with 200 and an
empty list of changed states), and a call that was accepted but physically
failed (a jammed lock, a value silently clamped or ignored). Two new
`tools/_base.py` helpers replace it: `confirm_entity_exists()`, for a tool
with nothing to read back afterward, and `observe_actuation()`, which
re-reads the entity after the call and reports what was actually observed,
with one short bounded retry for an entity that has not settled yet.

- `lock_control`, `alarm_control`, `cover_control`, `vacuum_control`,
  `vacuum_room` and `apply_update` now report `verified: true` only when
  the entity's own state (or, for set_position/set_tilt_position/
  fan_speed, the relevant attribute) matches what was requested, and
  `verified: false` with the actual observed state otherwise.
  `vacuum_control`'s `locate` has no state of its own to confirm (it only
  plays a sound), so it reports `verified: null` — accepted, but
  unverifiable. `vacuum_control` and `vacuum_room` also now require
  `entity_id`: they used to silently actuate whichever `vacuum.*` entity
  was first in `/api/states` when it was omitted.
- `set_light`, `set_climate`, `toggle_entity`, `fan_control` and
  `helpers.py`'s `set_helper`/`set_number`/`set_select`/`set_text`/
  `timer_control`/`counter_control` get the same treatment for their own
  state or attribute. `set_light` also stopped silently turning the light
  on for any `state` string that is not exactly `'off'` or `'toggle'` while
  echoing back the bogus requested value as if it had been applied — an
  unrecognised state is now rejected with `error("invalid_state", ...)`.
- `press_button`, `restart_homeassistant`, and the notification/alert/todo/
  calendar/group/media-player families return `{accepted: true, verified:
  null, detail}` where genuinely nothing can be verified (a notification
  actually delivered, an announcement actually heard), and a real
  read-back where one exists: `create_persistent_notification`/
  `dismiss_persistent_notification` now confirm against
  `persistent_notification/get`; `add_todo_item`/`update_todo_item`/
  `remove_todo_item` confirm against the list; `add_calendar_event` checks
  the calendar back; `create_group`/`update_group`/`delete_group` confirm
  membership. This surfaced a real defect: `update_group`'s `group/set`
  service does not distinguish create from update, so it silently created
  a new group when pointed at an entity_id with none — `update_group` now
  checks the target exists first, like every other write in this package.

### Fixed — creation, deletion and import correctness (package D)

Four tools reported success without confirming Home Assistant actually did
what was asked:

- `create_automation(enabled=False)` sent the disabling `automation/
  turn_off` immediately after the config POST, with no wait for the new
  entity to register — the disable often landed on an entity_id that did
  not exist yet and was accepted as a 200 `[]` no-op. Measured live: ten
  automations created back-to-back with `enabled=False`, 9 of 10 stayed
  armed. It now waits for the entity to register (`wait_for_entity()`, a
  new `_base.py` helper — distinct from `observe_actuation()`, which bails
  on the first 404 rather than retrying through it), then reads the state
  back and reports what was actually observed — never a bare success.
- `create_automation()` and `create_script()` derive their id from a lossy
  slug of the name, so two differently-named automations or scripts can
  collide on the same id and silently replace each other's definition.
  Refused by default with `error("id_collision", ...)`; pass
  `overwrite=True` to replace one deliberately.
- `delete_automation()` checked for HTTP 404 on a nonexistent automation;
  Home Assistant answers 400. It also resolved the automation's config id
  from the entity_id's own slug, which only works for an automation this
  tool itself created — a UI-created automation's config id is an
  independent numeric timestamp. It now reads the `id` attribute from the
  entity's own state before deleting.
- `import_blueprint()` called `blueprint/import` and stopped — that is only
  the preview step the blueprint editor's UI uses; nothing is written to
  disk without a second command, `blueprint/save`. Verified live:
  `blueprint/list` was unchanged after an import that had already reported
  success. `import_blueprint()` now performs both steps. (A separate,
  earlier fix in this same function is already covered above: it also used
  to treat a transport failure on the `blueprint/import` call itself as a
  success.)

### Fixed — silent substitution and partial-failure reporting (package E)

- `set_helper()` folded an unrecognised command to a default instead of
  rejecting it: a timer command of `'stop'` silently started the timer,
  and a typo'd `'decremnt'` silently incremented a counter instead.
  `input_boolean`, `counter` and `timer` now reject anything outside their
  fixed command vocabulary with an error naming what is accepted;
  `input_number`/`text`/`select`/`datetime` take free-form values with
  nothing to validate against, so they are unchanged.
- `set_climate()` sends up to four sequential service calls and used to let
  an exception from a later one propagate, discarding which fields had
  already been accepted. It now stops at the first refusal and reports
  `applied` (what landed), `failed_field` and `not_attempted`, so a caller
  told "failed" is not left blind to a partial change already in effect.
- `broadcast_tts()` reported `ok: true` for every player when the TTS
  engine entity did not exist — `tts.speak` accepts a nonexistent engine
  target the same way any nonexistent target is accepted, a 200 `[]`
  no-op. Measured live against 8 players with no `tts.*` entity
  registered: 8 of 8 used to report `ok: true`, 0 were announced. It now
  checks the engine once up front and marks every non-Alexa player's
  result accordingly instead of attempting a call that cannot work — the
  same check `send_tts` gains in package F, below.
- A template/YAML-injection audit of `create_template_sensor` found no real
  problem: config fields reach Home Assistant's config-flow API as
  discrete JSON values, never string-concatenated, so a value cannot break
  out of its own field, and the schema-key filter already limits which
  fields are sent — confirmed live with an SSTI-shaped payload (Home
  Assistant's own sandbox blocked it) and a quote/brace-laden name and
  template, both stored and rendered verbatim with no extra keys smuggled
  in.

### Changed — write tools (package F)

The read half of this sweep gave every list-returning tool one shape
(`envelope()`, above). The write half — the ~100 tools that create, update,
delete or actuate something — went through the same process and is now
documented in `tools/_base.py`'s module docstring: an actuation result
(`verified`/`state`, or `accepted`/`verified: null` when there is nothing to
read back), a `ws_error()`-gated registry write, a bulk result, or an
`error()` — never a bare bool, string, or None. A new test enforces the
scalar half mechanically; the existing `.get("success", True)` test now also
bans `.get("success", False)`, the same fault in the other direction.

- `areas.py`'s 11 write tools built their response from `r.get("success",
  False)` without checking `ws_error()` first, which could return
  self-contradicting payloads — `disable_entity()` returned `{"disabled":
  true, "success": false}`, asserting the effect while denying it happened,
  with Home Assistant's actual error code discarded. All 11 now gate on
  `ws_error()`; `reload_integration()` (`system.py`) had the same fault and
  the same fix.
- `send_tts` gains the engine-existence check package E already added to
  `broadcast_tts`: `tts.speak` answers a nonexistent engine the same 200 `[]`
  as a real announcement queued, so a missing `engine` used to report
  `accepted: true` regardless.
- `trigger_webhook` no longer reports `triggered: true` — Home Assistant
  answers a registered and an unregistered webhook_id with an identical 2xx,
  so that field asserted something HTTP cannot establish. Replaced with
  `accepted` (the same computation, an honest name) and a `detail` explaining
  the limit.
- `bulk_set_entity_labels` sends every command before reading any reply; a
  batch over 200 entities now returns `error("too_many_entities", ...)`
  instead of risking an overflow mid-write.
- `activate_scene` and `run_script` now check the target exists first and
  return the `accepted`/`verified: null` shape used everywhere else in this
  codebase for an effect with nothing to read back, instead of an ad-hoc
  single-key dict with no existence check.
- Every write tool now documents its return shape (`Returns:` in its
  docstring), and every `delete_*`/`remove_*` tool but `delete_user` — which
  already had one — now says plainly that the action cannot be undone.

### Added — registration gate (package F)

`create_user`, `update_user` and `delete_user` — the tools that manage Home
Assistant login accounts, a different risk tier from controlling entities —
are no longer registered unless `MCP_ENABLE_USER_MANAGEMENT=true` is set.
Nothing server-side can make an MCP client confirm before calling a tool;
not registering it at all is the one guardrail this server can actually
enforce. Every other tool, including every other destructive one, keeps its
v1.1.0 default of being registered — upgrading must not make capabilities
disappear with no error. The always-registered `list_disabled_tools()` tool
reports which groups are gated and why. `call_service`, `fire_event` and
`call_addon_api` are explicitly *not* covered by this or any gate: see the
README section on this for why a name-based filter cannot reach them.

### Fixed — calibration of `verified` itself

Package F's convention says what `verified` means; these two fixes are
about it actually meaning that, not just having the right shape (a third,
`get_device` folding a dead connection into "not found", is in the
`.get("result", ...)` bullets above — same session, same theme, different
shape of bug):

- `set_light` checked only `state == "on"` for any turn_on/attribute call,
  so `brightness_pct`/`color_temp_k`/`rgb_color`/`effect` all reported
  `verified: true` regardless of whether Home Assistant applied them.
  Measured live: `effect="Disco"` on a light with no `effect_list`, and
  `color_temp_k` on an rgbw-only light, both came back `verified: true`
  while nothing changed. Each requested attribute is now checked against
  its own read-back value and echoed under its own key; `rgb_color`
  compares by hue/saturation rather than exact value, since Home Assistant
  does not preserve an rgb_color's value/brightness component for a light
  whose native color mode is `hs`.
- `cover_control` and `alarm_control` read back with a ~2.1s budget, but
  measured live a window cover can take ten seconds for a full open or
  close and an alarm panel's exit delay took about five — both far past
  that budget, so a perfectly successful close or arm that simply had not
  settled yet came back `verified: false`, indistinguishable from a real
  mismatch (a jammed cover, a blocked arm). Home Assistant's own
  transitional states (`"closing"`/`"opening"` for a cover, `"arming"` for
  an alarm panel) now report `verified: null` — not confirmed, not denied —
  instead, via a new `verified_allowing_transit()` helper in
  `tools/_base.py`; `stop`/`toggle` (cover) and `disarm` (alarm) are
  unaffected, since neither has a transitional state of its own. The retry
  budget also grew slightly (~2.1s to ~3.1s) for the common short-travel
  case; `vacuum_control`'s `return` had the same fault in the opposite
  direction — it counted the transient `"returning"` state itself as
  `verified: true` — and `lock_control` gets the same null-for-transitional
  treatment defensively, for real locks that pass through `"locking"`/
  `"unlocking"` (not reproduced on this project's own test lock, which
  settles instantly).

## 1.1.0

The project moves into its own repository: **github.com/driin0/ha-mcp-server**,
AGPL-3.0.

Nothing about the server changes with this release — the code is the one that
had been developed inside the Home Assistant app repository, which was the
version actually in use. What changes is where it lives and how it is shipped.

### Why

The code existed in two places: a private Docker repository and a copy inside
the Home Assistant app. They had drifted badly — **23 files differed**, and the
copy in the app was two and a half months ahead, holding fixes the other never
received. Whoever had deployed the Docker one was running a version with five
broken tools.

There is now a single source. The Home Assistant app carries only its packaging
and consumes the image published here.

### What this brings

* **A container image**, `ghcr.io/driin0/ha-mcp-server`, multi-architecture
  (amd64, arm64), 119 MB — built in two stages so the compiler toolchain stays
  out of the final image
* **`compose.yaml`, `deploy.sh` and `.env.sample`**, so the server can be run
  outside Home Assistant
* **AGPL-3.0**, with the licence text shipped inside the image

### For the Home Assistant app

The app no longer installs dependencies: it copies them, already compiled, from
this image. Its build no longer needs `gcc`, and both distributions are
guaranteed to run the *same* packages rather than the same `requirements.txt`
resolved twice.

This works because both images are Alpine with the same Python minor. The
Dockerfile imports the native extensions at build time, so a future base image
that breaks the ABI fails the build instead of failing at first start.
