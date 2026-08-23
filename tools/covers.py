import httpx

from tools._base import mcp, HA_URL, HEADERS, envelope, error, observe_actuation


@mcp.tool()
def list_covers() -> dict:
    """
    List all cover entities (blinds, shutters, garage doors, etc.) with state and position.

    Returns: {total, returned, offset, note?, covers: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    covers = []
    for s in r.json():
        if not s["entity_id"].startswith("cover."):
            continue
        attrs = s.get("attributes", {})
        covers.append({
            "entity_id": s["entity_id"],
            "name": attrs.get("friendly_name", s["entity_id"]),
            "state": s["state"],
            "position": attrs.get("current_position"),
            "tilt_position": attrs.get("current_tilt_position"),
            "device_class": attrs.get("device_class"),
        })
    return envelope(sorted(covers, key=lambda x: x["name"]), key="covers")


@mcp.tool()
def cover_control(
    entity_id: str,
    command: str,
    position: int = None,
    tilt_position: int = None,
) -> dict:
    """
    Control a cover entity (blind, shutter, garage door, etc.).

    command: open | close | stop | set_position | set_tilt_position | toggle
    position: 0–100, used with set_position
    tilt_position: 0–100, used with set_tilt_position

    Returns: {entity_id, command, verified, state, position?, tilt_position?}
    on a call Home Assistant accepted, or {error: "entity_not_found", ...}
    when entity_id has no state at all.

    `verified` is true only when the cover's own state (or, for
    set_position/set_tilt_position, its current_position/
    current_tilt_position attribute), read back after the call, matches
    what was requested. A cover with simulated or real travel time is not
    settled the instant the service call returns — measured live, a window
    cover reaches its target position within about a second — so the
    read-back retries once. `stop` and `toggle` have no single target state
    to match: `verified` there means the cover is no longer mid-travel
    ("opening"/"closing") by the time of the read-back, not that it ended
    up in any particular position.
    """
    command_map = {
        "open": "open_cover",
        "close": "close_cover",
        "stop": "stop_cover",
        "toggle": "toggle",
        "set_position": "set_cover_position",
        "set_tilt_position": "set_cover_tilt_position",
    }
    service = command_map.get(command)
    if not service:
        raise ValueError(f"Unknown command '{command}'. Use: {', '.join(command_map)}")
    data: dict = {"entity_id": entity_id}
    if command == "set_position" and position is not None:
        data["position"] = position
    if command == "set_tilt_position" and tilt_position is not None:
        data["tilt_position"] = tilt_position

    if command == "toggle":
        # No single target state: whether "toggle" ends open or closed
        # depends on the state before the call, so that has to be read
        # first rather than guessed at.
        prior = None  # stays None (never equal to any real state) if the entity is missing
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code != 404:
            r.raise_for_status()
            prior = r.json()["state"]

    with httpx.Client() as client:
        r = client.post(f"{HA_URL}/api/services/cover/{service}", headers=HEADERS, json=data, timeout=10)
        r.raise_for_status()

    if command == "open":
        satisfied = lambda s: s["state"] == "open"
    elif command == "close":
        satisfied = lambda s: s["state"] == "closed"
    elif command == "stop":
        satisfied = lambda s: s["state"] not in ("opening", "closing")
    elif command == "toggle":
        satisfied = lambda s: isinstance(prior, str) and s["state"] != prior
    elif command == "set_position":
        satisfied = lambda s: s.get("attributes", {}).get("current_position") == position
    else:  # set_tilt_position
        satisfied = lambda s: s.get("attributes", {}).get("current_tilt_position") == tilt_position

    obs = observe_actuation(entity_id, satisfied, retries=2, delay=1.0)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    out = {
        "entity_id": entity_id,
        "command": command,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }
    if command == "set_position":
        out["position"] = obs["state"].get("attributes", {}).get("current_position")
    elif command == "set_tilt_position":
        out["tilt_position"] = obs["state"].get("attributes", {}).get("current_tilt_position")
    return out
