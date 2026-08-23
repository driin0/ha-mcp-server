import httpx

from tools._base import mcp, HA_URL, HEADERS, envelope, error, observe_actuation


@mcp.tool()
def list_climate() -> dict:
    """
    List all climate entities (AC, heaters, etc.) with current state.

    Returns: {total, returned, offset, note?, climate: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        result = []
        for s in r.json():
            if not s["entity_id"].startswith("climate."):
                continue
            attrs = s.get("attributes", {})
            result.append({
                "entity_id": s["entity_id"],
                "name": attrs.get("friendly_name", s["entity_id"]),
                "state": s["state"],
                "current_temperature": attrs.get("current_temperature"),
                "temperature": attrs.get("temperature"),
                "hvac_modes": attrs.get("hvac_modes", []),
                "fan_mode": attrs.get("fan_mode"),
                "fan_modes": attrs.get("fan_modes", []),
                "swing_mode": attrs.get("swing_mode"),
                "swing_modes": attrs.get("swing_modes", []),
            })
        return envelope(sorted(result, key=lambda x: x["name"]), key="climate")


@mcp.tool()
def set_climate(
    entity_id: str,
    hvac_mode: str = "",
    temperature: float = None,
    fan_mode: str = "",
    swing_mode: str = "",
) -> dict:
    """
    Control a climate entity.

    hvac_mode:   'off', 'cool', 'heat', 'dry', 'fan_only', 'auto'
    temperature: target temperature in °C
    fan_mode:    'auto', 'low', 'medium', 'high', etc. (depends on device)
    swing_mode:  'off', 'vertical', 'horizontal', 'both', etc. (depends on device)

    Returns: {entity_id, applied, verified, state, attributes} on a call
    Home Assistant accepted, or {error: "entity_not_found"/"no_changes_requested",
    ...} otherwise.

    Each requested field is its own service call — climate has no single
    "set everything" service — so a call can partially apply (e.g.
    hvac_mode accepted, temperature then refused) before a non-2xx
    response raises. `verified` covers what was requested as a whole: true
    only when every field in `applied` matches what the entity's own state
    and attributes report on read-back; `attributes` there always shows
    what was actually observed, which is what to check when only part of a
    multi-field call took effect.
    """
    with httpx.Client() as client:
        applied = {}
        if hvac_mode:
            r = client.post(f"{HA_URL}/api/services/climate/set_hvac_mode",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "hvac_mode": hvac_mode}, timeout=10)
            r.raise_for_status()
            applied["hvac_mode"] = hvac_mode
        if temperature is not None:
            r = client.post(f"{HA_URL}/api/services/climate/set_temperature",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "temperature": temperature}, timeout=10)
            r.raise_for_status()
            applied["temperature"] = temperature
        if fan_mode:
            r = client.post(f"{HA_URL}/api/services/climate/set_fan_mode",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "fan_mode": fan_mode}, timeout=10)
            r.raise_for_status()
            applied["fan_mode"] = fan_mode
        if swing_mode:
            r = client.post(f"{HA_URL}/api/services/climate/set_swing_mode",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "swing_mode": swing_mode}, timeout=10)
            r.raise_for_status()
            applied["swing_mode"] = swing_mode

    if not applied:
        return error("no_changes_requested",
                     "None of hvac_mode, temperature, fan_mode or swing_mode were given.",
                     entity_id=entity_id)

    def matches(s: dict) -> bool:
        attrs = s.get("attributes", {})
        if "hvac_mode" in applied and s["state"] != applied["hvac_mode"]:
            return False
        if "temperature" in applied and attrs.get("temperature") != applied["temperature"]:
            return False
        if "fan_mode" in applied and attrs.get("fan_mode") != applied["fan_mode"]:
            return False
        if "swing_mode" in applied and attrs.get("swing_mode") != applied["swing_mode"]:
            return False
        return True

    obs = observe_actuation(entity_id, matches)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, applied=applied)
    attrs = obs["state"].get("attributes", {})
    return {
        "entity_id": entity_id,
        "applied": applied,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
        "attributes": {k: attrs.get(k) for k in ("temperature", "fan_mode", "swing_mode") if k in applied},
    }
