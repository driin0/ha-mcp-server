"""Shared plumbing for every tool in tools/*.py, plus the write-result convention.

## The write-result convention

A read tool wraps a collection in envelope() and a single object in a plain
dict; the read half of this file has always been unambiguous about shape. The
write half was not — an audit of 97 write tools across an earlier revision of
this codebase found 21 different acknowledgement keys, 17 tools that returned
Home Assistant's raw object with no marker of any kind, and one bug
(areas.py, since fixed) that could return {"disabled": true, "success":
false} in the same dict — an unconditional claim of the effect alongside a
denial that it happened, with Home Assistant's actual error code discarded
in favour of a bare False. The convention below is what replaced that: every
write tool in this codebase now returns exactly one of the following.

1. error(code, detail, **extra) — the call failed, or the target does not
   exist. Never returned inside a list: a failure must not be reachable by
   iterating results as if it were a record.

2. An **actuation** result, for a service call that changes an entity's own
   state: observe_actuation() reads the entity back after the call and
   reports {..., "verified": bool, "state": <observed>} — never the service
   call's own response body, which Home Assistant answers with 200 and an
   empty list for both a successful idempotent call and a call aimed at an
   entity that does not exist (see confirm_entity_exists()'s docstring).
   When the entity has nothing to read back (press_button, a notification,
   an alert acknowledgement), confirm_entity_exists() is called first, and
   the result is {..., "accepted": True, "verified": None, "detail": "..."}
   — "verified: None" here is a claim of ignorance, not of success; the
   detail says what could and could not be confirmed.

3. A **registry write** result, for a config/registry command (create,
   update, delete an area, tag, person, helper, dashboard, pipeline...):
   gated by ws_error() (or an HTTP status check, for the handful of REST
   config endpoints) BEFORE anything is built from it — a dict returned
   from a write tool with no "error" key is therefore unambiguously a
   success, by construction, not by a "success" flag that could disagree
   with the rest of the dict. Past that gate:
     - create/update returns the object Home Assistant handed back. That
       object is its own acknowledgement — it carries the very fields that
       just changed — so nothing is layered on top of it; a redundant
       "success": true is exactly the kind of field that went stale in the
       areas.py bug.
     - delete, or any write with no natural content payload, returns a
       small dict naming what was done, e.g. {"deleted": id}. A literal
       "success": True here is not contradictable the way it used to be:
       the line that builds it is only reached once ws_error() has already
       confirmed there is nothing to contradict it with.

4. A **bulk** result: {"total", "succeeded", "failed": [...]} — one entry
   per item, since a batch tolerates partial failure a single call cannot.

Never a bare bool, a bare string, or None: a caller — nearly always a
language model with no access to this source — is reading a JSON value with
no schema, and a bare scalar carries no field names to hang a question on
("did true mean the light is on, or that the call was accepted?"). The three
tools that legitimately return a plain string (get_addon_logs, get_error_log,
render_template) return a string because their output IS the payload — a log,
a template render — not an acknowledgement of anything.

tests/test_conformance.py enforces the mechanically-checkable parts of this:
every tool returns dict (except the three string tools above); no tool
returns a bare list literal, a bare scalar literal, or defaults a missing WS
"success" key with .get("success", <literal>) — that pattern is exactly what
let a transport failure or a real HA error pass through as a false success,
or (in the other direction) let a real failure's detail get discarded in
favour of a bare False. Route both around ws_error() instead.

## The registration gate

A confirmation protocol cannot be enforced from inside this server: nothing
here can make a calling model ask the user before acting. The one guardrail
that server-side code CAN enforce is not registering a tool at all, so it
never appears in the model's menu in the first place. See
GATED_TOOL_GROUPS/disabled_tool_names()/apply_registration_gate() below, and
list_disabled_tools() for how that absence stays discoverable rather than a
silent capability gap.
"""
import asyncio
import json
import os
import re
import time

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

HA_URL = os.getenv("HA_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
MCP_PORT = int(os.getenv("MCP_PORT", "47821"))
MCP_SECRET = os.getenv("MCP_SECRET", "")
MCP_ALLOW_NO_AUTH = os.getenv("MCP_ALLOW_NO_AUTH", "").lower() in ("1", "true", "yes")
UI_SECRET = os.getenv("UI_SECRET", "") or MCP_SECRET
HA_INGRESS_MODE = os.getenv("HA_INGRESS_MODE", "").lower() in ("1", "true", "yes")

if not HA_URL or not HA_TOKEN:
    raise RuntimeError("HA_URL and HA_TOKEN must be set in .env")

if not MCP_SECRET and not MCP_ALLOW_NO_AUTH:
    raise RuntimeError(
        "MCP_SECRET is not set. Set it to a strong random token (openssl rand -base64 32) "
        "or set MCP_ALLOW_NO_AUTH=true to explicitly run without authentication (trusted networks only)."
    )

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

# host, port and the HTTP path moved out of the constructor in MCP SDK 2.0:
# they are arguments of streamable_http_app(), see server.py.
mcp = MCPServer("Home Assistant Advanced")

HELPER_DOMAINS = {
    "input_boolean", "input_number", "input_text",
    "input_select", "input_datetime", "counter", "timer", "input_button",
}


def _slug(name: str) -> str:
    """Convert a human name to a valid HA slug."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _parse_remote_prefixes(raw: str) -> dict:
    """Parse a remote-instance list into {group_name: entity_id_prefix}.

    Several Home Assistant instances are often joined by exposing the remote
    entities on a main one, where they show up under a shared entity_id prefix.
    Tools that group by location use this map to report those entities under the
    instance they come from instead of the local area registry.

    Format: a comma-separated list where each item is either "name" — the prefix
    then defaults to "sensor.<name>_" — or "name=prefix" when the entity_id
    prefix does not follow that convention. Example:

        HA_REMOTE_PREFIXES="annex,workshop=sensor.ws_"

    Empty by default: with nothing configured no entity is treated as remote,
    and grouping falls back entirely to the area registry.
    """
    prefixes: dict = {}
    for item in raw.split(","):
        name, sep, prefix = item.partition("=")
        name = name.strip()
        prefix = prefix.strip()
        if not name:
            continue
        prefixes[name] = prefix if sep and prefix else f"sensor.{_slug(name)}_"
    return prefixes


REMOTE_PREFIXES = _parse_remote_prefixes(os.getenv("HA_REMOTE_PREFIXES", ""))

# Substrings that mark a media_player as an Amazon Echo, which is announced to
# through alexa_media rather than the regular TTS service. The two defaults match
# how the Alexa Media Player integration names its entities; add your own when a
# speaker group is named after the room or the household instead.
ALEXA_KEYWORDS = tuple(
    kw.strip().lower()
    for kw in os.getenv("HA_ALEXA_KEYWORDS", "echo,alexa").split(",")
    if kw.strip()
)

CONFIGURED_LANGUAGE = os.getenv("HA_DEFAULT_LANGUAGE", "").strip()

_DEFAULT_LANGUAGE = ""


def default_language() -> str:
    """The language spoken by the TTS and conversation tools when none is given.

    Resolved once and cached, in this order:

    1. the `default_language` option, when set;
    2. the language reported by /api/config;
    3. English.

    Step 2 is only a guess, and deliberately overridable: Home Assistant derives
    entity_ids from that setting, so an instance is often kept in English to get
    English entity_ids while the people using it speak something else — the
    interface language is a per-user profile preference and is not exposed here.
    Set the option whenever the instance should speak a different language from
    the one it is configured in.
    """
    global _DEFAULT_LANGUAGE
    if CONFIGURED_LANGUAGE:
        return CONFIGURED_LANGUAGE
    if not _DEFAULT_LANGUAGE:
        try:
            import httpx
            with httpx.Client() as client:
                r = client.get(f"{HA_URL}/api/config", headers=HEADERS, timeout=10)
                r.raise_for_status()
                _DEFAULT_LANGUAGE = r.json().get("language") or "en"
        except Exception:
            _DEFAULT_LANGUAGE = "en"
    return _DEFAULT_LANGUAGE


def _run_in_new_loop(coro):
    """Run a coroutine in a fresh event loop (safe to call from inside uvicorn)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ws(msg: dict) -> dict:
    """Send one WS command over a single authenticated connection (sync, thread-safe)."""
    return _ws_multi([msg])[0]


def _ws_multi(msgs: list) -> list:
    """Send multiple WS commands over a single authenticated connection (sync, thread-safe)."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run_in_new_loop, _ws_commands(msgs)).result(timeout=30)


async def _ws_commands(msgs: list) -> list:
    """Async: open one WS connection, authenticate, send all msgs, return list of results.

    Sends all commands first, then collects result messages by id — skipping event
    messages and unsolicited frames that HA may send between command results.
    """
    import websockets
    ws_url = HA_URL.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    async with websockets.connect(ws_url, open_timeout=10, max_size=10 * 1024 * 1024) as ws:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if m.get("type") != "auth_required":
            return [{"error": f"Expected auth_required, got: {m}"}] * len(msgs)
        await ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if m.get("type") != "auth_ok":
            return [{"error": f"Auth failed: {m}"}] * len(msgs)

        # Send all commands first
        for i, msg in enumerate(msgs, start=1):
            await ws.send(json.dumps({"id": i, **msg}))

        # Collect result messages by id, skipping event/unsolicited frames
        results: list = [None] * len(msgs)
        pending = set(range(1, len(msgs) + 1))
        while pending:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if m.get("type") == "result" and m.get("id") in pending:
                results[m["id"] - 1] = m
                pending.discard(m["id"])
        return results


def envelope(items, *, key="items", total=None, offset=0, limit=None, note="",
             offset_paginated=False) -> dict:
    """Wrap a collection in the standard result shape.

    The shape exists because the MCP SDK turns a bare list return into one
    response block per element — and into no block at all for an empty list.
    A caller then cannot tell "nothing found" from "the call failed", and a
    truncated result has nowhere to say so. A dict has room for both.

    items:  the complete filtered and sorted collection. Slicing happens here,
            so pagination has one implementation rather than one per tool.
    key:    the name the collection appears under — "lights", "automations".
            Named rather than generic, because the reader is a language model.
    total:  pass it when Home Assistant applied the limit server-side and
            `items` is therefore already the page. `limit` cannot also be
            passed with `total`, since `items` is already the paginated page.
    limit:  0 or None means no limit.
    note:   overrides the generated note.
    offset_paginated: the calling tool exposes an `offset` parameter, so a
            truncation note may tell the caller to advance it. Most tools
            that call envelope() with a `limit` have no such parameter —
            offset is always 0 for them — and advising a caller to move a
            knob that does not exist produced a note that was simply wrong
            for those tools. Only pass True from the two tools (list_devices,
            list_automations) that actually have one.
    """
    rows = list(items)
    if total is None:
        count = len(rows)
        page = rows[offset:offset + limit] if limit else rows[offset:]
    else:
        if limit:
            raise ValueError(
                "envelope(total=...) means `items` is already the page, so a "
                "limit cannot also be applied - pass one or the other")
        count = total
        page = rows

    out = {"total": count, "returned": len(page), "offset": offset}
    if not note:
        if count == 0:
            note = f"no {key} found"
        elif not page:
            note = f"offset {offset} is past the end of {count} {key}"
        elif offset + len(page) < count:
            advice = ("raise limit, advance offset, or refine the filters"
                      if offset_paginated else
                      "raise limit or refine the filters")
            note = f"{len(page)} of {count} shown - {advice}"
    if note:
        out["note"] = note
    out[key] = page
    return out


def error(code: str, detail: str = "", **extra) -> dict:
    """A failed call.

    Never a list element: an error must not be reachable by iterating results,
    or a caller reads it as a record that happens to have an `error` key.
    """
    out = {"error": code, "detail": detail}
    out.update(extra)
    return out


def ws_error(result) -> dict | None:
    """Return an error envelope if a WebSocket command failed, else None.

    Two shapes reach here. Home Assistant answers a failed command with
    {"success": false, "error": {"code", "message"}}; _ws itself returns
    {"error": "..."} when the connection or the authentication fails.
    A successful frame should always have a "result" key, but this branch
    defends against a malformed response.

    Timeouts raise exceptions (asyncio.TimeoutError or
    concurrent.futures.TimeoutError) rather than returning a dict, so callers
    see an exception rather than an error envelope.

    Used as:

        r = _ws({"type": "config_entries/list"})
        if err := ws_error(r):
            return err
        entries = r["result"]

    The alternative — r.get("result", []) — turns every one of those failures
    into an empty list, which the SDK then renders as no output whatsoever.
    """
    if not isinstance(result, dict):
        return error("bad_response",
                     f"Unexpected WebSocket response: {result!r}")
    if result.get("success") is False:
        err = result.get("error") or {}
        if isinstance(err, dict):
            return error(err.get("code", "unknown"), err.get("message", ""))
        return error("websocket_error", str(err))
    if result.get("success") is True and "result" not in result:
        return error("bad_response",
                     "WebSocket response marked success but has no result key")
    if "result" not in result and "error" in result:
        return error("websocket_error", str(result["error"]))
    return None


def entity_area_map(entities: list | None = None) -> tuple[dict, dict | None]:
    """Map every entity_id to its area, the way Home Assistant resolves it.

    An entity's area is its own area_id when set, and otherwise its device's.
    Reading only the entity registry misses the second case, which in a real
    installation is most entities: integrations create devices, and people
    assign the area to the device, not to each entity individually.

    entities: entity_registry/list rows the caller already fetched (e.g. to
              also read labels or platform), so this does not repeat that
              round trip - only config/device_registry/list is then sent.
              When omitted, both registries are fetched here together in a
              single _ws_multi round trip.

    Returns (map, error_envelope_or_None) so a caller can decide whether a
    failed read is fatal - it is when the caller was asked to filter by
    area, because an empty result would be indistinguishable from "nothing
    is in that area". When the map is only used to enrich rows (grouping,
    reporting), a caller may instead degrade and keep going - but should
    say so in its own note, since a silently empty area on every row is
    the same class of fault this function exists to fix.
    """
    if entities is None:
        ws_results = _ws_multi([
            {"type": "config/entity_registry/list"},
            {"type": "config/device_registry/list"},
        ])
        if err := ws_error(ws_results[0]):
            return {}, err
        entities = ws_results[0]["result"]
        device_result = ws_results[1]
    else:
        device_result = _ws({"type": "config/device_registry/list"})
    if err := ws_error(device_result):
        return {}, err

    device_areas = {d["id"]: d.get("area_id") for d in device_result["result"]}
    area_map = {
        e["entity_id"]: e.get("area_id") or device_areas.get(e.get("device_id"))
        for e in entities
    }
    return area_map, None


def confirm_entity_exists(entity_id: str) -> dict | None:
    """Confirm entity_id currently has a state on this Home Assistant instance.

    Returns None when it exists. Returns an error() envelope when Home
    Assistant reports 404 for it.

    This exists because a Home Assistant service call does not reject a
    target that does not exist: it is accepted and answered with 200 and an
    empty list of changed states — the exact shape an idempotent no-op call
    also returns (turning off an already-off light, locking an
    already-locked lock). Measured live: `lock.unlock` on
    `lock.ghost_does_not_exist` returns 200 [], and the entity 404s on
    read. Without this check a tool has no way to tell "accepted, nothing
    to act on" from "accepted, and it worked".

    Call this BEFORE the service call, for a tool with no state of its own
    to read back afterward and reveal that same absence — press_button,
    restart_homeassistant, the notify family, alerts, todo, calendar,
    groups, media players. An actuator with observable state does not need
    it: observe_actuation() below already reports `exists: False` from the
    read-back it does anyway, one HTTP call instead of two.

    Raises like any other read in this file on a transport failure or a
    non-404 error status — only "does not exist" is reported as an
    error() return here; "could not check" surfaces as an exception.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id)
    r.raise_for_status()
    return None


def wait_for_entity(entity_id: str, *, retries: int = 4, delay: float = 1.0) -> bool:
    """Poll GET /api/states/entity_id until it stops 404ing, or give up.

    observe_actuation() below exists to tell "wrong state" from "gone" for
    an entity assumed to already be registered, so it returns exists=False
    on the FIRST 404 rather than retrying - correct for that job, wrong for
    this one. A freshly created automation's entity briefly does not exist
    at all while Home Assistant's entity platform catches up with a config
    write that already returned 200 - measured live, ten automations
    created back-to-back and immediately disabled: 9 of 10 stayed armed,
    because the disable landed on an entity_id that did not exist yet and
    was accepted as a 200 [] no-op. This is the "wait it out" loop that
    race needs, before the disabling call is sent at all - see
    create_automation()'s enabled=False path, its only caller today.

    Returns True once a non-404 read is seen (the entity may still not be
    in its final state - this only confirms it exists), False if every
    attempt within `retries` extra reads still 404s.
    """
    with httpx.Client() as client:
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(delay)
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
            if r.status_code != 404:
                return True
    return False


def observe_actuation(entity_id: str, satisfied, *, retries: int = 1, delay: float = 1.0) -> dict:
    """Read entity_id's state back after a service call and report what was
    actually observed — the discriminator between "Home Assistant accepted
    the call" and "the call did what it said".

    Never trust the service call's own response body for this: Home
    Assistant returns only the states that changed, so an idempotent call
    (locking an already-locked lock) and a call aimed at an entity_id that
    does not exist at all both legitimately return 200 with an empty list —
    measured live, `vacuum.start` on an already-cleaning vacuum and
    `lock.unlock` on a nonexistent entity both come back as 200 []. This
    function ignores that body entirely and always goes back to the source
    of truth: the entity's own state, read fresh.

    entity_id: the entity the service call targeted.
    satisfied:  callable(state: dict) -> bool, given the raw Home Assistant
                state object (`{"entity_id", "state", "attributes", ...}`).
                Decide whether it counts as the actuation having taken
                effect — e.g. `lambda s: s["state"] == "locked"`, or
                `lambda s: s["attributes"].get("fan_speed") == "turbo"`.
    retries:    extra reads after the first, `delay` seconds apart (default:
                one retry, one second later). This is not a polling loop —
                Home Assistant applies most service calls synchronously
                (toggle_automation, tools/automations.py, is the one-shot
                precedent this generalises). Measured live: a lock and a
                garage-door cover already show their final state by the
                time the POST returns; an alarm panel and a window cover
                with simulated travel settle within about a second. This is
                a short bounded margin for that case, not a substitute for
                a real job-completion API.

    Returns exactly one of:
      {"exists": False, "verified": False, "state": None}
          entity_id has no state at all. The service call was still
          accepted — see the confirm_entity_exists() docstring — so this is
          the only way to learn the target never existed.
      {"exists": True, "verified": True, "state": <raw HA state dict>}
          `satisfied` matched a read-back.
      {"exists": True, "verified": False, "state": <raw HA state dict>}
          the entity exists but `satisfied` never matched within `retries`
          — accepted, but unverified (a jammed lock, an offline device, a
          value silently ignored: measured live, `vacuum.set_fan_speed`
          with a value outside the entity's own fan_speed_list returns 200
          [] and leaves the attribute untouched — no error, no effect).
    """
    state = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(delay)
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return {"exists": False, "verified": False, "state": None}
        r.raise_for_status()
        state = r.json()
        if satisfied(state):
            return {"exists": True, "verified": True, "state": state}
    return {"exists": True, "verified": False, "state": state}


# ---------------------------------------------------------------------------
# Registration gate
# ---------------------------------------------------------------------------
# Each group maps an env var to the tool names it controls and the default
# when that env var is unset. Every group defaults to *enabled* except
# "user_management": creating, editing or deleting a Home Assistant login
# account is a different risk tier from moving a cover or deleting a scene —
# it is the exact shape of a disguised-privileged-action problem (a
# plausible-sounding request that is actually an account takeover vector),
# almost nobody needs a language model to have that capability, and it is
# rare enough in ordinary use that disabling it by default costs little.
# Every other destructive or actuating tool — every delete_*, lock_control,
# restart_homeassistant, apply_update — stays enabled by default: someone
# upgrading from v1.1.0 must not find capabilities gone with no error, only
# absence. See list_disabled_tools() below for how the absence that DOES
# happen (user_management, until explicitly enabled) stays discoverable.
GATED_TOOL_GROUPS: dict[str, dict] = {
    "user_management": {
        "tools": {"create_user", "update_user", "delete_user"},
        "env": "MCP_ENABLE_USER_MANAGEMENT",
        "default": False,
        "reason": (
            "creates, edits or deletes Home Assistant login accounts — a "
            "different risk tier from controlling entities, and the tier "
            "where a disguised request (\"add a maintenance user\") is "
            "really a privileged account-management action."
        ),
    },
}


def _group_enabled(spec: dict) -> bool:
    flag = os.getenv(spec["env"], "").strip().lower()
    if flag:
        return flag in ("1", "true", "yes")
    return spec["default"]


def disabled_tool_names() -> set[str]:
    """The tool names GATED_TOOL_GROUPS says should not be registered, given
    the environment right now.

    A pure function of os.environ - no side effect on the tool registry
    itself - so it can be called freely (by apply_registration_gate() below,
    by list_disabled_tools(), by a test) without touching global state.
    """
    disabled = set()
    for spec in GATED_TOOL_GROUPS.values():
        if not _group_enabled(spec):
            disabled |= spec["tools"]
    return disabled


def apply_registration_gate() -> set[str]:
    """Deregister every tool disabled_tool_names() names, and return them.

    Every tools/*.py module still declares its tools with the same bare
    @mcp.tool() every conformance sweep in tests/test_conformance.py scans
    for by source - gating happens here, after the fact, by removing an
    already-registered tool from mcp._tool_manager, not by changing how or
    whether a module decorates its functions. That keeps a gated tool fully
    visible to every static check (it still returns dict, still has no bare
    list/scalar return, still carries its Returns: docstring) while making
    it invisible to an MCP client's tools/list - the actual guardrail, since
    nothing server-side can make a calling model ask before acting; not
    registering the tool is the one thing this server CAN enforce.

    Call once, after every tools.* module has imported (server.py does this
    right after its import block) - a tool has to be registered before it
    can be removed, so calling this before all modules have imported would
    silently do nothing for whichever ones import later.
    """
    removed = set()
    for name in disabled_tool_names():
        if mcp._tool_manager.get_tool(name) is not None:
            mcp._tool_manager.remove_tool(name)
            removed.add(name)
    return removed


@mcp.tool()
def list_disabled_tools() -> dict:
    """List which tool groups are gated behind an env var, and their current
    on/off state on this running instance.

    Always registered, regardless of what it reports - the one fixed point
    a caller can use to discover that a tool it expected (create_user,
    update_user, delete_user, by default) is not a bug or a version
    mismatch, but a deliberate opt-in gate, and how to turn it on.

    Returns: {total, returned, offset, note?, groups: [{group, enabled,
    tools, env, reason}]}. `enabled` re-reads the env var on every call
    rather than caching what apply_registration_gate() saw at startup - in
    the ordinary case (the env var is fixed for the process's lifetime,
    normal for a container) the two always agree. They can only disagree if
    something changes the env var after this process already started
    without restarting it, in which case this tool reports the live value
    while the actual registration - decided once, at startup - has not
    moved: restart the server for a changed flag to take effect.

    call_service, fire_event and call_addon_api are deliberately absent
    from every group and from this list: they are generic passthroughs
    (any HA service, any event, any add-on's own HTTP API) that a
    named-tool gate cannot cover without also restricting them specifically
    - gating create_user while leaving call_service enabled would be
    decorative if call_service could reach the same WebSocket command, so
    this was checked directly rather than assumed: Home Assistant's user
    and person registries (config/auth/*, person/*) are WebSocket-only
    config commands, not services, and call_service only proxies POST
    /api/services/{domain}/{service} - there is no domain/service that
    creates, edits or deletes a login account or a person for it to reach.
    fire_event and call_addon_api touch neither registry. A future group
    gating something call_service CAN reach (e.g. lock_control, which
    lock.unlock duplicates exactly) would need to restrict call_service
    too, or it would be exactly that decorative gate.
    """
    groups = []
    for group, spec in sorted(GATED_TOOL_GROUPS.items()):
        groups.append({
            "group": group,
            "enabled": _group_enabled(spec),
            "tools": sorted(spec["tools"]),
            "env": spec["env"],
            "reason": spec["reason"],
        })
    return envelope(groups, key="groups")
