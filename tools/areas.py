import json

import httpx

from tools._base import mcp, HA_URL, HEADERS, _ws, _ws_multi, envelope, error, ws_error


@mcp.tool()
def list_areas() -> dict:
    """
    List all areas with their entities, floor name and floor_id.

    Returns: {total, returned, offset, note?, areas: [{area_id, name,
             floor_id, floor_name, entities: [...]}]}
    """
    ws_results = _ws_multi([
        {"type": "config/area_registry/list"},
        {"type": "config/floor_registry/list"},
    ])
    if err := ws_error(ws_results[0]):
        return err
    if err := ws_error(ws_results[1]):
        return err
    areas_raw = ws_results[0]["result"]
    floor_map = {f["floor_id"]: f["name"] for f in ws_results[1]["result"]}

    template = (
        "{%- set area_ids = areas() | list %}"
        "{%- set ns = namespace(result=[]) %}"
        "{%- for aid in area_ids %}"
        "{%- set ns.result = ns.result + [{"
        "'area_id': aid, "
        "'entities': area_entities(aid) | list"
        "}] %}"
        "{%- endfor %}"
        "{{ ns.result | tojson }}"
    )
    with httpx.Client() as client:
        r = client.post(f"{HA_URL}/api/template", headers=HEADERS,
                        json={"template": template}, timeout=15)
        r.raise_for_status()
    entities_map = {item["area_id"]: item["entities"] for item in json.loads(r.text.strip())}

    result = []
    for area in areas_raw:
        area_id = area.get("area_id", "")
        floor_id = area.get("floor_id")
        result.append({
            "area_id": area_id,
            "name": area.get("name", ""),
            "floor_id": floor_id,
            "floor_name": floor_map.get(floor_id, "") if floor_id else "",
            "entities": entities_map.get(area_id, []),
        })
    result.sort(key=lambda x: x["name"])
    return envelope(result, key="areas")


@mcp.tool()
def create_area(name: str, icon: str = "") -> dict:
    """Create a new area. icon: MDI icon, e.g. 'mdi:sofa'.

    Returns the created area object (area_id, name, floor_id, icon, ...)
    from Home Assistant, or an error() envelope on failure.
    """
    msg: dict = {"type": "config/area_registry/create", "name": name}
    if icon:
        msg["icon"] = icon
    r = _ws(msg)
    if err := ws_error(r):
        return err
    return r["result"]


@mcp.tool()
def update_area(area_id: str, name: str = "", icon: str = "") -> dict:
    """
    Update an existing area's name and/or icon.
    Use list_areas() to find area_ids.

    Returns the updated area object from Home Assistant, or an error()
    envelope on failure.
    """
    msg: dict = {"type": "config/area_registry/update", "area_id": area_id}
    if name:
        msg["name"] = name
    if icon:
        msg["icon"] = icon
    r = _ws(msg)
    if err := ws_error(r):
        return err
    return r["result"]


@mcp.tool()
def delete_area(area_id: str) -> dict:
    """Delete an area by area_id.

    ⚠️ This is irreversible. Entities and devices assigned to this area lose
    that assignment; the area itself cannot be recovered.

    Returns: {deleted: area_id, success: true}, or an error() envelope with
    Home Assistant's actual error code/message on failure.
    """
    r = _ws({"type": "config/area_registry/delete", "area_id": area_id})
    if err := ws_error(r):
        return err
    return {"deleted": area_id, "success": True}


@mcp.tool()
def list_devices(area_id: str = "", search: str = "", limit: int = 50, offset: int = 0) -> dict:
    """
    List devices from the device registry with pagination.

    area_id: filter by area (use list_areas() to find IDs)
    search:  filter by name substring (case-insensitive)
    limit:   max devices to return (default 50, 0 for no limit)
    offset:  skip first N devices (for pagination)

    Returns: {total, returned, offset, note?, devices: [{id, name, manufacturer, model, area_id, labels}]}
    """
    r = _ws({"type": "config/device_registry/list"})
    if err := ws_error(r):
        return err
    devices = r["result"]
    if area_id:
        devices = [d for d in devices if d.get("area_id") == area_id]
    trimmed = [
        {
            "id": d.get("id"),
            "name": d.get("name_by_user") or d.get("name") or "",
            "manufacturer": d.get("manufacturer") or "",
            "model": d.get("model") or "",
            "area_id": d.get("area_id"),
            "labels": list(d.get("labels", [])),
        }
        for d in devices
    ]
    trimmed.sort(key=lambda x: x["name"].lower())
    if search:
        trimmed = [d for d in trimmed if search.lower() in d["name"].lower()]
    return envelope(trimmed, key="devices", limit=limit, offset=offset,
                    offset_paginated=True)


@mcp.tool()
def get_device(device_id: str) -> dict:
    """Get full details of a device by device_id."""
    r = _ws({"type": "config/device_registry/list"})
    for d in r.get("result", []):
        if d.get("id") == device_id:
            return d
    return {"error": f"Device not found: {device_id}"}


@mcp.tool()
def rename_entity(entity_id: str, name: str) -> dict:
    """
    Set a custom display name for an entity (overrides the default name).
    Pass name='' to reset to the original integration-provided name.

    Returns: {entity_id, name, success: true} on success, or an error()
    envelope with Home Assistant's actual error code/message on failure.
    """
    r = _ws({
        "type": "config/entity_registry/update",
        "entity_id": entity_id,
        "name": name or None,
    })
    if err := ws_error(r):
        return err
    entry = r["result"].get("entity_entry", {})
    return {
        "entity_id": entity_id,
        "name": entry.get("name") or entry.get("original_name", ""),
        "success": True,
    }


@mcp.tool()
def list_labels() -> dict:
    """
    List all labels defined in Home Assistant, sorted by name.

    Returns: {total, returned, offset, note?, labels: [...]}
    """
    r = _ws({"type": "config/label_registry/list"})
    if err := ws_error(r):
        return err
    labels = sorted(r["result"], key=lambda x: x.get("name", "").lower())
    return envelope(labels, key="labels")


@mcp.tool()
def create_label(name: str, color: str = "", icon: str = "") -> dict:
    """
    Create a new label.

    color: CSS color string, e.g. '#ff5733' or 'red'
    icon:  MDI icon, e.g. 'mdi:star'

    Returns the created label object from Home Assistant, or an error()
    envelope on failure.
    """
    msg: dict = {"type": "config/label_registry/create", "name": name}
    if color:
        msg["color"] = color
    if icon:
        msg["icon"] = icon
    r = _ws(msg)
    if err := ws_error(r):
        return err
    return r["result"]


@mcp.tool()
def update_label(label_id: str, name: str = "", color: str = "", icon: str = "") -> dict:
    """
    Update an existing label's name, color and/or icon.

    label_id: the label to update (use list_labels() to find it)
    name:     new display name (leave empty to keep current)
    color:    CSS color string, e.g. '#ff5733' or 'red'
    icon:     MDI icon, e.g. 'mdi:star'

    Returns the updated label object from Home Assistant, or an error()
    envelope on failure.
    """
    msg: dict = {"type": "config/label_registry/update", "label_id": label_id}
    if name:
        msg["name"] = name
    if color:
        msg["color"] = color
    if icon:
        msg["icon"] = icon
    r = _ws(msg)
    if err := ws_error(r):
        return err
    return r["result"]


@mcp.tool()
def delete_label(label_id: str) -> dict:
    """Delete a label by label_id.

    ⚠️ This is irreversible. The label is removed from every entity it was
    assigned to; it cannot be recovered.

    Returns: {deleted: label_id, success: true}, or an error() envelope
    with Home Assistant's actual error code/message on failure.
    """
    r = _ws({"type": "config/label_registry/delete", "label_id": label_id})
    if err := ws_error(r):
        return err
    return {"deleted": label_id, "success": True}


@mcp.tool()
def get_entity_labels(entity_id: str) -> dict:
    """
    Get the labels assigned to an entity.

    Returns: {entity_id, labels: [label_id, ...]}
    """
    r = _ws({"type": "config/entity_registry/get", "entity_id": entity_id})
    if err := ws_error(r):
        return err
    return {"entity_id": entity_id, "labels": list(r["result"].get("labels", []))}


@mcp.tool()
def set_entity_labels(entity_id: str, labels: list) -> dict:
    """
    Set labels on an entity (replaces existing labels).

    labels: list of label_id strings, e.g. ["energia", "illuminazione"]
    Use list_labels() to discover available label IDs.

    Returns: {entity_id, labels, success: true} on success, or an error()
    envelope with Home Assistant's actual error code/message on failure.
    """
    r = _ws({
        "type": "config/entity_registry/update",
        "entity_id": entity_id,
        "labels": labels,
    })
    if err := ws_error(r):
        return err
    entry = r["result"].get("entity_entry", {})
    return {
        "entity_id": entity_id,
        "labels": list(entry.get("labels", labels)),
        "success": True,
    }


# config/entity_registry/update commands are sent over one shared WebSocket
# connection (_ws_multi), all before any reply is read back - see
# _ws_commands()'s docstring in tools/_base.py. Home Assistant's own queue
# depth for unacknowledged commands on one connection is not published and
# has not been measured against a real instance, so this is a conservative
# bound rather than a discovered limit: a caller with more entities than
# this gets a clear, actionable error instead of a batch that silently
# stalls or drops replies mid-write on a large enough list.
_BULK_LABEL_MAX = 200


@mcp.tool()
def bulk_set_entity_labels(entity_ids: list, labels: list) -> dict:
    """
    Assign labels to multiple entities at once (replaces existing labels on each entity).

    entity_ids: list of entity_id strings (max 200 per call - split a larger
                list into batches; see the error this returns above that)
    labels: list of label_id strings to assign to all entities

    Returns: {total, succeeded, failed: [...]} on a batch that was sent, or
    an error() envelope ("too_many_entities") without sending anything when
    entity_ids exceeds the 200-entity limit.
    """
    if len(entity_ids) > _BULK_LABEL_MAX:
        return error(
            "too_many_entities",
            f"{len(entity_ids)} entity_ids given, {_BULK_LABEL_MAX} max per "
            "call - all commands in a batch are sent before any reply is "
            "read back, so a larger batch is split into multiple calls "
            "rather than sent in one.",
            entity_count=len(entity_ids), max_entities=_BULK_LABEL_MAX,
        )
    msgs = [
        {"type": "config/entity_registry/update", "entity_id": eid, "labels": labels}
        for eid in entity_ids
    ]
    results = _ws_multi(msgs)
    succeeded, failed = 0, []
    for eid, r in zip(entity_ids, results):
        if r.get("success"):
            succeeded += 1
        else:
            failed.append(eid)
    return {"total": len(entity_ids), "succeeded": succeeded, "failed": failed}


@mcp.tool()
def list_floors() -> dict:
    """
    List all floors defined in Home Assistant, sorted by level.

    Returns: {total, returned, offset, note?, floors: [...]}
    """
    r = _ws({"type": "config/floor_registry/list"})
    if err := ws_error(r):
        return err
    floors = sorted(r["result"], key=lambda x: x.get("level", 0))
    return envelope(floors, key="floors")


@mcp.tool()
def create_floor(name: str, level: int = 0, icon: str = "") -> dict:
    """
    Create a new floor.

    level: integer floor level (0 = ground floor, 1 = first floor, -1 = basement, …)
    icon:  MDI icon, e.g. 'mdi:home-floor-0'

    Returns the created floor object from Home Assistant, or an error()
    envelope on failure.
    """
    msg: dict = {"type": "config/floor_registry/create", "name": name, "level": level}
    if icon:
        msg["icon"] = icon
    r = _ws(msg)
    if err := ws_error(r):
        return err
    return r["result"]


@mcp.tool()
def delete_floor(floor_id: str) -> dict:
    """Delete a floor by floor_id.

    ⚠️ This is irreversible. Areas assigned to this floor lose that
    assignment; the floor itself cannot be recovered.

    Returns: {deleted: floor_id, success: true}, or an error() envelope
    with Home Assistant's actual error code/message on failure.
    """
    r = _ws({"type": "config/floor_registry/delete", "floor_id": floor_id})
    if err := ws_error(r):
        return err
    return {"deleted": floor_id, "success": True}


@mcp.tool()
def get_entity_registry(entity_id: str) -> dict:
    """
    Get full entity registry info for an entity: area, platform, unique_id,
    disabled_by, hidden_by, aliases, icon, device_id, and more.

    Useful for diagnosing entity configuration or finding the device an entity belongs to.
    """
    r = _ws({"type": "config/entity_registry/get", "entity_id": entity_id})
    if err := ws_error(r):
        return err
    result = r["result"]

    # Resolve area_id: entity's own area, or fall back to device's area
    area_id = result.get("area_id")
    device_id = result.get("device_id")
    if not area_id and device_id:
        device_r = _ws({"type": "config/device_registry/list"})
        if err := ws_error(device_r):
            return err
        for device in device_r.get("result", []):
            if device.get("id") == device_id:
                area_id = device.get("area_id")
                break

    return {
        "entity_id": result.get("entity_id"),
        "name": result.get("name") or result.get("original_name"),
        "platform": result.get("platform"),
        "device_id": device_id,
        "area_id": area_id,
        "unique_id": result.get("unique_id"),
        "disabled_by": result.get("disabled_by"),
        "hidden_by": result.get("hidden_by"),
        "icon": result.get("icon") or result.get("original_icon"),
        "labels": list(result.get("labels", [])),
        "aliases": list(result.get("aliases", [])),
        "has_entity_name": result.get("has_entity_name", False),
    }


@mcp.tool()
def list_zones() -> dict:
    """
    List all zone entities (home, work, school, etc.) with GPS coordinates and radius.
    Zones are used for presence detection and location-based automations.

    Returns: {total, returned, offset, note?, zones: [{entity_id, name,
             latitude, longitude, radius, icon, passive}]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        zones = []
        for s in r.json():
            if not s["entity_id"].startswith("zone."):
                continue
            attrs = s.get("attributes", {})
            zones.append({
                "entity_id": s["entity_id"],
                "name": attrs.get("friendly_name", s["entity_id"]),
                "latitude": attrs.get("latitude"),
                "longitude": attrs.get("longitude"),
                "radius": attrs.get("radius"),
                "icon": attrs.get("icon", ""),
                "passive": attrs.get("passive", False),
            })
    zones.sort(key=lambda x: x["name"])
    return envelope(zones, key="zones")


@mcp.tool()
def disable_entity(entity_id: str) -> dict:
    """
    Disable an entity in the entity registry.
    Disabled entities are hidden from HA and stop reporting state.
    Use enable_entity() to re-enable.

    Returns: {entity_id, disabled: true, success: true} once Home Assistant
    accepts the change, or an error() envelope with Home Assistant's actual
    error code/message on failure — `disabled: true` is only ever returned
    once that has actually been confirmed, never asserted alongside a
    failure.
    """
    r = _ws({
        "type": "config/entity_registry/update",
        "entity_id": entity_id,
        "disabled_by": "user",
    })
    if err := ws_error(r):
        return err
    return {"entity_id": entity_id, "disabled": True, "success": True}


@mcp.tool()
def enable_entity(entity_id: str) -> dict:
    """
    Re-enable a previously disabled entity in the entity registry.

    Returns: {entity_id, enabled: true, success: true} once Home Assistant
    accepts the change, or an error() envelope with Home Assistant's actual
    error code/message on failure.
    """
    r = _ws({
        "type": "config/entity_registry/update",
        "entity_id": entity_id,
        "disabled_by": None,
    })
    if err := ws_error(r):
        return err
    return {"entity_id": entity_id, "enabled": True, "success": True}


@mcp.tool()
def set_area_floor(area_id: str, floor_id: str) -> dict:
    """
    Assign an area to a floor (pass floor_id='' to remove the assignment).
    Use list_areas() for area_ids and list_floors() for floor_ids.

    Returns: {area_id, floor_id, success: true} on success, or an error()
    envelope with Home Assistant's actual error code/message on failure.
    """
    r = _ws({
        "type": "config/area_registry/update",
        "area_id": area_id,
        "floor_id": floor_id or None,
    })
    if err := ws_error(r):
        return err
    entry = r["result"]
    return {
        "area_id": area_id,
        "floor_id": entry.get("floor_id"),
        "success": True,
    }


@mcp.tool()
def set_entity_area(entity_id: str, area_id: str) -> dict:
    """
    Assign an entity to an area (or remove it from any area).

    entity_id: entity to update, e.g. 'light.living_room'
    area_id:   area to assign it to (use list_areas() to find IDs).
               Pass '' (empty string) to remove the entity from its current area.

    Note: this overrides the device-level area for this specific entity.

    Returns: {entity_id, area_id, success: true} on success, or an error()
    envelope with Home Assistant's actual error code/message on failure.
    """
    r = _ws({
        "type": "config/entity_registry/update",
        "entity_id": entity_id,
        "area_id": area_id or None,
    })
    if err := ws_error(r):
        return err
    entry = r["result"].get("entity_entry", {})
    return {
        "entity_id": entity_id,
        "area_id": entry.get("area_id"),
        "success": True,
    }
