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
