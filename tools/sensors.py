import httpx

from tools._base import mcp, HA_URL, HEADERS, REMOTE_PREFIXES, entity_area_map, _ws, envelope


@mcp.tool()
def get_energy(include_zero: bool = False) -> dict:
    """
    Get current power consumption (W) for all power-measuring sensor entities,
    sorted from highest to lowest consumption.

    include_zero: if True, also include devices reporting 0W (default: False)

    Returns: {total, returned, offset, note?, consumers: [{entity_id, name,
             power_w, unit}]}

    Useful to answer "what is consuming the most power right now?"
    Requires smart plugs or energy monitors with power (W) sensors.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()

    results = []
    for s in r.json():
        if not s["entity_id"].startswith("sensor."):
            continue
        attrs = s.get("attributes", {})
        if attrs.get("device_class") != "power":
            continue
        if s["state"] in ("unavailable", "unknown", ""):
            continue
        try:
            value = float(s["state"])
        except (ValueError, TypeError):
            continue
        if not include_zero and value == 0:
            continue
        results.append({
            "entity_id": s["entity_id"],
            "name": attrs.get("friendly_name", s["entity_id"]),
            "power_w": round(value, 1),
            "unit": attrs.get("unit_of_measurement", "W"),
        })

    results.sort(key=lambda x: x["power_w"], reverse=True)
    return envelope(results, key="consumers")


@mcp.tool()
def get_energy_summary() -> dict:
    """
    Get a power consumption summary grouped by location.

    Groups sensors by:
    - Remote instances detected by entity_id prefix, when any are configured
      through HA_REMOTE_PREFIXES (none by default)
    - Local areas (using the area registry)
    - Ungrouped local entities

    Returns total watts per group, sorted by consumption descending.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        all_states = r.json()

    # Collect power sensors
    power_sensors = []
    for s in all_states:
        if not s["entity_id"].startswith("sensor."):
            continue
        attrs = s.get("attributes", {})
        if attrs.get("device_class") != "power":
            continue
        if s["state"] in ("unavailable", "unknown", ""):
            continue
        try:
            value = float(s["state"])
        except (ValueError, TypeError):
            continue
        power_sensors.append({
            "entity_id": s["entity_id"],
            "name": attrs.get("friendly_name", s["entity_id"]),
            "power_w": round(value, 1),
        })

    # Get area registry for local grouping
    area_result = _ws({"type": "config/area_registry/list"})
    areas = {a["area_id"]: a["name"] for a in area_result.get("result", [])}

    # The map is only used to group sensors, not to filter - this tool has
    # no area_id parameter - so a failed read degrades to "other" for every
    # sensor rather than aborting the whole summary. It still has to say
    # so: a silently-empty grouping is the same class of fault this map
    # exists to fix.
    entity_areas, area_map_err = entity_area_map()
    degraded_note = ""
    if area_map_err:
        entity_areas = {}
        degraded_note = (
            "entity/device registry unavailable - power sensors could not "
            "be matched to an area and were placed under 'other'"
        )

    # Configured remote prefixes (empty unless HA_REMOTE_PREFIXES is set)
    remote_prefixes = REMOTE_PREFIXES

    groups: dict = {}

    for sensor in power_sensors:
        eid = sensor["entity_id"]
        watts = sensor["power_w"]

        # Check remote prefix
        group = None
        for prefix_name, prefix in remote_prefixes.items():
            if eid.startswith(prefix):
                group = prefix_name
                break

        # Local: look up area
        if group is None:
            area_id = entity_areas.get(eid)
            group = areas.get(area_id, "other") if area_id else "other"

        if group not in groups:
            groups[group] = {"group": group, "total_w": 0.0, "sensors": []}
        groups[group]["total_w"] = round(groups[group]["total_w"] + watts, 1)
        groups[group]["sensors"].append({"entity_id": eid, "name": sensor["name"], "power_w": watts})

    # Sort sensors within each group
    for g in groups.values():
        g["sensors"].sort(key=lambda x: x["power_w"], reverse=True)

    result = sorted(groups.values(), key=lambda x: x["total_w"], reverse=True)
    total = round(sum(g["total_w"] for g in result), 1)
    out = {"total_w": total, "groups": result}
    if degraded_note:
        out["note"] = degraded_note
    return out


@mcp.tool()
def list_sensors(domain: str = "sensor", search: str = "", limit: int = 100) -> dict:
    """
    List sensor or binary_sensor entities.

    domain: 'sensor' (default) or 'binary_sensor'
    search: optional substring filter on name or entity_id
    limit: max results (default 100)

    Returns: {total, returned, offset, note?, sensors: [{entity_id, name,
             state, unit, device_class, state_class}]}

    `total` counts every matching entity, not just the page returned - the
    loop used to stop collecting at `limit` and so could never report a
    total larger than it.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    results = []
    for s in r.json():
        if not s["entity_id"].startswith(f"{domain}."):
            continue
        attrs = s.get("attributes", {})
        name = attrs.get("friendly_name", s["entity_id"])
        if search and search.lower() not in name.lower() and search.lower() not in s["entity_id"].lower():
            continue
        results.append({
            "entity_id": s["entity_id"],
            "name": name,
            "state": s["state"],
            "unit": attrs.get("unit_of_measurement"),
            "device_class": attrs.get("device_class"),
            "state_class": attrs.get("state_class"),
        })
    results.sort(key=lambda x: x["name"])
    return envelope(results, key="sensors", limit=limit)
