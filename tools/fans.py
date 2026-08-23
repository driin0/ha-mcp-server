import httpx

from tools._base import mcp, HA_URL, HEADERS, envelope, error, observe_actuation


@mcp.tool()
def list_fans() -> dict:
    """
    List all fan entities with state and speed.

    Returns: {total, returned, offset, note?, fans: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    fans = []
    for s in r.json():
        if not s["entity_id"].startswith("fan."):
            continue
        attrs = s.get("attributes", {})
        fans.append({
            "entity_id": s["entity_id"],
            "name": attrs.get("friendly_name", s["entity_id"]),
            "state": s["state"],
            "percentage": attrs.get("percentage"),
            "preset_mode": attrs.get("preset_mode"),
            "preset_modes": attrs.get("preset_modes", []),
            "oscillating": attrs.get("oscillating"),
            "direction": attrs.get("direction"),
        })
    return envelope(sorted(fans, key=lambda x: x["name"]), key="fans")


@mcp.tool()
def fan_control(
    entity_id: str,
    command: str,
    percentage: int = None,
    preset_mode: str = "",
    oscillating: bool = None,
    direction: str = "",
) -> dict:
    """
    Control a fan entity.

    command: turn_on | turn_off | toggle | set_percentage | set_preset_mode | oscillate | set_direction
    percentage: 0–100, speed percentage
    preset_mode: e.g. 'auto', 'sleep'
    oscillating: true/false
    direction: forward | reverse

    Returns: {entity_id, command, verified, state, ...} on a call Home
    Assistant accepted, or {error: "entity_not_found", ...} when entity_id
    has no state at all.

    `verified` is true only when the fan's own state (turn_on/turn_off), or
    the relevant attribute (percentage/preset_mode/oscillating/direction),
    read back after the call, matches what was requested. 'toggle' has no
    fixed target, so it counts as verified when the state differs from what
    the fan reported just before the call.
    """
    service_map = {
        "turn_on": "turn_on",
        "turn_off": "turn_off",
        "toggle": "toggle",
        "set_percentage": "set_percentage",
        "set_preset_mode": "set_preset_mode",
        "oscillate": "oscillate",
        "set_direction": "set_direction",
    }
    service = service_map.get(command)
    if not service:
        raise ValueError(f"Unknown command '{command}'. Use: {', '.join(service_map)}")
    data: dict = {"entity_id": entity_id}
    if percentage is not None:
        data["percentage"] = percentage
    if preset_mode:
        data["preset_mode"] = preset_mode
    if oscillating is not None:
        data["oscillating"] = oscillating
    if direction:
        data["direction"] = direction

    prior = None
    if command == "toggle":
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code != 404:
            r.raise_for_status()
            prior = r.json()["state"]

    with httpx.Client() as client:
        r = client.post(f"{HA_URL}/api/services/fan/{service}", headers=HEADERS, json=data, timeout=10)
        r.raise_for_status()

    if command == "turn_on":
        satisfied = lambda s: s["state"] == "on"
    elif command == "turn_off":
        satisfied = lambda s: s["state"] == "off"
    elif command == "toggle":
        satisfied = lambda s: isinstance(prior, str) and s["state"] != prior
    elif command == "set_percentage":
        satisfied = lambda s: s.get("attributes", {}).get("percentage") == percentage
    elif command == "set_preset_mode":
        satisfied = lambda s: s.get("attributes", {}).get("preset_mode") == preset_mode
    elif command == "oscillate":
        satisfied = lambda s: s.get("attributes", {}).get("oscillating") == oscillating
    else:  # set_direction
        satisfied = lambda s: s.get("attributes", {}).get("direction") == direction

    obs = observe_actuation(entity_id, satisfied)
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
    attrs = obs["state"].get("attributes", {})
    if command == "set_percentage":
        out["percentage"] = attrs.get("percentage")
    elif command == "set_preset_mode":
        out["preset_mode"] = attrs.get("preset_mode")
    elif command == "oscillate":
        out["oscillating"] = attrs.get("oscillating")
    elif command == "set_direction":
        out["direction"] = attrs.get("direction")
    return out
