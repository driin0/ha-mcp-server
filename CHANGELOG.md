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
  unregistered command. **This class of fault is not closed.** `.get("result",
  [])` and its variants (`.get("result", {})`, `.get("result", r)`, …) remain
  at 41 sites across 16 files — all inside tools that already returned
  `dict` before this branch, and so fell outside this sweep, which only
  touched tools returning a bare list or a list literal on failure. `areas.py`
  is the largest single remainder, with 10 sites, even after this release
  separately fixed four of its worst cases in `list_devices`, `list_labels`,
  `list_floors` and `get_entity_registry`.
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

- A pytest suite (156 tests) and a GitHub Actions CI workflow
  (`.github/workflows/test.yml`). The repository previously had no tests.

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
