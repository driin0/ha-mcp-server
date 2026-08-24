import httpx

from tools._base import (
    mcp, HA_URL, HEADERS, error, observe_actuation, verified_allowing_transit,
)


def _resolve_vacuum(entity_id: str) -> str:
    """Return entity_id, or the first vacuum entity found when it is empty.

    Read-only use only (get_vacuum_state): vacuum_control() and
    vacuum_room() require entity_id explicitly rather than actuating
    whichever vacuum happens to be first in /api/states, which on a
    multi-vacuum instance is silent guesswork over which robot moves.
    """
    if entity_id:
        return entity_id
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    for s in r.json():
        if s["entity_id"].startswith("vacuum."):
            return s["entity_id"]
    return ""


@mcp.tool()
def get_vacuum_state(entity_id: str = "") -> dict:
    """
    Get the current state and attributes of a vacuum robot.

    entity_id: vacuum entity; leave empty to use the first one found.
    """
    entity_id = _resolve_vacuum(entity_id)
    if not entity_id:
        return {"error": "no_vacuum_found", "detail": "No vacuum.* entity exists on this instance."}
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        r.raise_for_status()
        s = r.json()
        attrs = s.get("attributes", {})
        return {
            "entity_id": s["entity_id"],
            "state": s["state"],
            "battery_level": attrs.get("battery_level"),
            "fan_speed": attrs.get("fan_speed"),
            "status": attrs.get("status"),
            "cleaning_mode": attrs.get("cleaning_mode"),
            "current_room": attrs.get("current_room"),
            "cleaned_area": attrs.get("cleaned_area"),
            "error": attrs.get("error"),
            "last_changed": s.get("last_changed", ""),
        }


@mcp.tool()
def vacuum_control(
    command: str,
    entity_id: str,
    rooms: list = None,
    fan_speed: str = "",
) -> dict:
    """
    Control a vacuum robot.

    entity_id: vacuum entity — required. Use list_devices() / get_states_by_domain('vacuum')
               (or get_vacuum_state() with no argument) to find one; this tool never guesses
               which vacuum to actuate on a multi-vacuum instance.

    command:
      - 'start'       start cleaning (whole house)
      - 'stop'        stop cleaning
      - 'pause'       pause cleaning
      - 'return'      return to base/dock
      - 'locate'      play locate sound
      - 'fan_speed'   set fan speed (requires fan_speed — must be one of the entity's
                       own fan_speed_list, from get_vacuum_state()/list attributes)
      - 'clean_rooms' clean specific rooms (requires rooms: list of room names)

    Returns: {entity_id, command, verified, state, ...} on a call Home
    Assistant accepted, or {error: "entity_not_found"/"invalid_fan_speed",
    ...} otherwise.

    `verified` is true only when the vacuum's own state (or, for
    'fan_speed', its fan_speed attribute), read back after the call,
    matches the command. Home Assistant accepts and silently ignores a
    fan_speed value outside the entity's own fan_speed_list — no error, no
    effect — so `verified: false` there is how that is told apart from a
    real success. 'locate' has no state of its own to verify (it only
    plays a sound), so it instead checks the target exists before calling
    and reports `verified: null` — "accepted, effect unverifiable" — rather
    than a claim this tool cannot back up.

    'return' used to count the transient "returning" state as success —
    the state a vacuum is in for the entire drive back, not once it gets
    there. Measured live, a vacuum can sit in "returning" for well past the
    read-back budget (retries are short by design — see
    observe_actuation()'s docstring — a real return can take much longer
    than any budget this tool should block a call for). Only "docked"
    counts as verified now; "returning" still within the retry budget
    reports `verified: null` — accepted and genuinely in progress, neither
    confirmed nor denied — and any other state reports `verified: false`.
    """
    if not entity_id:
        raise ValueError("entity_id is required — vacuum_control no longer guesses a target")

    if command == "locate":
        # No observable state to read back: locate only plays a sound.
        # Confirm the target exists before calling, since Home Assistant
        # accepts and 200s a call aimed at a nonexistent entity_id.
        missing = error("entity_not_found",
                        f"{entity_id} does not exist on this Home Assistant instance.",
                        entity_id=entity_id, command=command)
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return missing
        r.raise_for_status()
        with httpx.Client() as client:
            r = client.post(f"{HA_URL}/api/services/vacuum/locate",
                            headers=HEADERS, json={"entity_id": entity_id}, timeout=10)
            r.raise_for_status()
        return {"entity_id": entity_id, "command": command, "verified": None,
                "detail": "Home Assistant accepted the call; a locate sound has no "
                          "observable state to confirm it played."}

    if command == "fan_speed" and not fan_speed:
        return error("invalid_command", "command 'fan_speed' requires fan_speed",
                     entity_id=entity_id, command=command)
    if command == "clean_rooms" and not rooms:
        return error("invalid_command", "command 'clean_rooms' requires rooms",
                     entity_id=entity_id, command=command)

    service_map = {
        "start": ("vacuum/start", {}),
        "stop": ("vacuum/stop", {}),
        "pause": ("vacuum/pause", {}),
        "return": ("vacuum/return_to_base", {}),
        "fan_speed": ("vacuum/set_fan_speed", {"fan_speed": fan_speed}),
        "clean_rooms": ("dreame_vacuum/vacuum_clean_segment", {"segments": rooms}),
    }
    if command not in service_map:
        return error("invalid_command", f"Unknown command or missing parameters: {command}",
                     entity_id=entity_id, command=command)
    path, extra = service_map[command]
    with httpx.Client() as client:
        r = client.post(f"{HA_URL}/api/services/{path}", headers=HEADERS,
                        json={"entity_id": entity_id, **extra}, timeout=10)
        r.raise_for_status()

    if command == "start":
        satisfied = lambda s: s["state"] == "cleaning"
    elif command == "stop":
        satisfied = lambda s: s["state"] not in ("cleaning", "paused")
    elif command == "pause":
        satisfied = lambda s: s["state"] == "paused"
    elif command == "return":
        satisfied = lambda s: s["state"] == "docked"
    elif command == "fan_speed":
        satisfied = lambda s: s.get("attributes", {}).get("fan_speed") == fan_speed
    else:  # clean_rooms
        satisfied = lambda s: s["state"] == "cleaning"

    obs = observe_actuation(entity_id, satisfied, retries=3, delay=1.0)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    transitional = {"returning"} if command == "return" else frozenset()
    out = {
        "entity_id": entity_id,
        "command": command,
        "verified": verified_allowing_transit(obs, transitional),
        "state": obs["state"]["state"],
    }
    if command == "fan_speed":
        out["fan_speed"] = obs["state"].get("attributes", {}).get("fan_speed")
    return out


@mcp.tool()
def vacuum_room(
    rooms: list,
    entity_id: str,
    repeats: int = 1,
) -> dict:
    """
    Clean one or more specific rooms (segments) with a Dreame vacuum.

    rooms:     list of segment IDs (integers), e.g. [1, 3].
               Find segment IDs from the vacuum map in the Dreame integration:
               HA → Settings → Devices → your vacuum → vacuum_clean_segment
               service, or from get_vacuum_state() attributes
               (segment_status / map_data).
    repeats:   number of cleaning passes (default 1, max typically 3)
    entity_id: vacuum entity — required; this tool never guesses which vacuum to actuate.

    Returns: {entity_id, rooms, repeats, verified, state} on a call Home
    Assistant accepted, or {error: "entity_not_found", ...} when entity_id
    has no state at all. `verified` is true only when the vacuum's state,
    read back after the call, is "cleaning".
    """
    if not entity_id:
        raise ValueError("entity_id is required — vacuum_room no longer guesses a target")
    with httpx.Client() as client:
        payload: dict = {"entity_id": entity_id, "segments": rooms}
        if repeats > 1:
            payload["repeats"] = repeats
        r = client.post(
            f"{HA_URL}/api/services/dreame_vacuum/vacuum_clean_segment",
            headers=HEADERS,
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
    obs = observe_actuation(entity_id, lambda s: s["state"] == "cleaning", retries=2, delay=1.0)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id)
    return {
        "entity_id": entity_id,
        "rooms": rooms,
        "repeats": repeats,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }
