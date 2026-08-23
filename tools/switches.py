import httpx

from tools._base import mcp, HA_URL, HEADERS, envelope, error, observe_actuation


@mcp.tool()
def list_switches() -> dict:
    """
    List all switch entities with their current state.

    Returns: {total, returned, offset, note?, switches: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    switches = sorted([
        {
            "entity_id": s["entity_id"],
            "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
            "state": s["state"],
        }
        for s in r.json()
        if s["entity_id"].startswith("switch.")
    ], key=lambda x: x["name"])
    return envelope(switches, key="switches")


@mcp.tool()
def toggle_entity(entity_id: str, state: str = "toggle") -> dict:
    """
    Turn on, off or toggle any entity that supports it (switch, light, fan, input_boolean, etc.).

    state: 'on' | 'off' | 'toggle' (default: toggle)

    Returns: {entity_id, state, verified, observed_state} on a call Home
    Assistant accepted, or {error: "entity_not_found", ...} when entity_id
    has no state at all.

    `verified` is true only when the entity's own state, read back after
    the call, matches — "on"/"off" match literally; 'toggle' has no fixed
    target, so it counts as verified when the state differs from what the
    entity reported just before the call.
    """
    if state not in ("on", "off", "toggle"):
        raise ValueError("state must be: on, off, or toggle")

    prior = None
    if state == "toggle":
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code != 404:
            r.raise_for_status()
            prior = r.json()["state"]

    service_map = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/homeassistant/{service_map[state]}",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()

    if state == "toggle":
        satisfied = lambda s: isinstance(prior, str) and s["state"] != prior
    else:
        satisfied = lambda s: s["state"] == state

    obs = observe_actuation(entity_id, satisfied)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, state=state)
    return {
        "entity_id": entity_id,
        "state": state,
        "verified": obs["verified"],
        "observed_state": obs["state"]["state"],
    }
