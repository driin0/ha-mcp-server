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
    Home Assistant accepted for every requested field, or an error()
    envelope - "no_changes_requested", "entity_not_found", or
    "service_call_failed" when Home Assistant refused one of the fields.

    Each requested field is its own service call — climate has no single
    "set everything" service — so a call can partially apply (e.g.
    hvac_mode accepted, temperature then refused). Fields are sent in the
    order listed above, and stop at the first one Home Assistant refuses:
    the "service_call_failed" return still reports `applied` (the fields
    that were sent and accepted before the failure) and `failed_field`/
    `not_attempted`, so a caller told "failed" is not left blind to a
    partial change already in effect — the previous behaviour let that
    exception propagate and discarded which fields had already landed.
    `verified` covers what was requested as a whole: true only when every
    field in `applied` matches what the entity's own state and attributes
    report on read-back; `attributes` there always shows what was actually
    observed, which is what to check when only part of a multi-field call
    took effect.
    """
    field_calls = []
    if hvac_mode:
        field_calls.append(("hvac_mode", "climate/set_hvac_mode",
                            {"entity_id": entity_id, "hvac_mode": hvac_mode}))
    if temperature is not None:
        field_calls.append(("temperature", "climate/set_temperature",
                            {"entity_id": entity_id, "temperature": temperature}))
    if fan_mode:
        field_calls.append(("fan_mode", "climate/set_fan_mode",
                            {"entity_id": entity_id, "fan_mode": fan_mode}))
    if swing_mode:
        field_calls.append(("swing_mode", "climate/set_swing_mode",
                            {"entity_id": entity_id, "swing_mode": swing_mode}))

    if not field_calls:
        return error("no_changes_requested",
                     "None of hvac_mode, temperature, fan_mode or swing_mode were given.",
                     entity_id=entity_id)

    applied = {}
    with httpx.Client() as client:
        for field, service, data in field_calls:
            r = client.post(f"{HA_URL}/api/services/{service}", headers=HEADERS,
                            json=data, timeout=10)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError:
                not_attempted = [f for f, _, _ in field_calls
                                if f not in applied and f != field]
                return error(
                    "service_call_failed",
                    f"Home Assistant refused {field} ({r.status_code}): {r.text[:200]}",
                    entity_id=entity_id, applied=applied, failed_field=field,
                    not_attempted=not_attempted,
                )
            applied[field] = data[field]

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
