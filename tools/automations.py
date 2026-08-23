import httpx

from tools._base import mcp, HA_URL, HEADERS, _slug, _ws, envelope, error, ws_error


@mcp.tool()
def list_automations(search: str = "", label: str = "", limit: int = 50,
                     offset: int = 0) -> dict:
    """
    List automations with their state and last triggered time.

    search: optional substring filter on automation name (case-insensitive)
    label:  filter by label ID (use list_labels() to find label IDs)
    limit:  max automations to return (default 50, 0 for no limit)
    offset: skip the first N (for pagination)

    Returns: {total, returned, offset, note?, automations: [{entity_id, name,
             state, last_triggered, labels}]}

    `total` counts what matched the filters, not what was returned: when it
    exceeds `returned`, `note` says so. An instance with a few hundred
    automations is normal, so filter by label before raising the limit.
    """
    r = _ws({"type": "config/entity_registry/list"})
    registry_err = ws_error(r)
    if registry_err and label:
        # Filtering by a label we could not read would silently return
        # nothing, which is the failure mode this work removes.
        return registry_err
    registry = ({} if registry_err
                else {e["entity_id"]: e for e in r["result"]})

    with httpx.Client() as client:
        resp = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        resp.raise_for_status()

    automations = []
    for s in resp.json():
        if not s["entity_id"].startswith("automation."):
            continue
        attrs = s.get("attributes", {})
        name = attrs.get("friendly_name", s["entity_id"])
        if search and search.lower() not in name.lower():
            continue
        labels = list(registry.get(s["entity_id"], {}).get("labels", []))
        if label and label not in labels:
            continue
        automations.append({
            "entity_id": s["entity_id"],
            "name": name,
            "state": s["state"],
            "last_triggered": attrs.get("last_triggered"),
            "labels": labels,
        })

    automations.sort(key=lambda x: x["name"])
    return envelope(automations, key="automations", limit=limit, offset=offset,
                    offset_paginated=True)


@mcp.tool()
def create_automation(
    name: str,
    trigger: list,
    action: list,
    condition: list = None,
    description: str = "",
    enabled: bool = True,
) -> dict:
    """
    Create or update an automation. The automation ID is derived from the name.

    trigger, condition and action must be valid HA trigger/condition/action objects.

    Example — turn on a light at sunset:
      name: "Turn on light at sunset"
      trigger: [{"platform": "sun", "event": "sunset"}]
      action: [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}]

    Example — notify when door opens:
      name: "Notify door open"
      trigger: [{"platform": "state", "entity_id": "binary_sensor.front_door", "to": "on"}]
      action: [{"service": "notify.mobile_app_myphone", "data": {"message": "Door open!"}}]
    """
    automation_id = _slug(name)
    payload = {
        "alias": name,
        "description": description,
        "trigger": trigger,
        "condition": condition or [],
        "action": action,
        "mode": "single",
    }
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        if not enabled:
            client.post(
                f"{HA_URL}/api/services/automation/turn_off",
                headers=HEADERS,
                json={"entity_id": f"automation.{automation_id}"},
                timeout=10,
            )
        return {"automation_id": automation_id, "entity_id": f"automation.{automation_id}", "result": r.json()}


@mcp.tool()
def delete_automation(entity_id: str) -> dict:
    """
    Delete an automation by entity_id (e.g. 'automation.turn_on_light_at_sunset').
    Only works for automations managed via the HA UI editor.
    YAML-defined automations cannot be deleted via API.
    """
    automation_id = entity_id.removeprefix("automation.")
    with httpx.Client() as client:
        r = client.delete(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 404:
            return {
                "error": "not_found",
                "entity_id": entity_id,
                "detail": (
                    "This automation is defined in YAML and cannot be deleted via API. "
                    "Only UI-managed automations can be deleted with this tool."
                ),
            }
        r.raise_for_status()
        return {"deleted": entity_id, "status": r.status_code}


@mcp.tool()
def trigger_automation(entity_id: str) -> dict:
    """Manually trigger an automation regardless of its trigger conditions."""
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/automation/trigger",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
        return {"triggered": entity_id}


@mcp.tool()
def toggle_automation(entity_id: str) -> dict:
    """Enable or disable an automation."""
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/automation/toggle",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
        # The service call succeeds even for an entity that does not exist, and a
        # freshly created automation takes a moment to register, so report the
        # missing state instead of raising on the read-back.
        s = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if s.status_code == 404:
            return {"entity_id": entity_id, "new_state": None,
                    "detail": "Toggle sent, but the entity has no state yet — it may "
                              "not exist, or may still be registering."}
        s.raise_for_status()
        return {"entity_id": entity_id, "new_state": s.json().get("state")}


@mcp.tool()
def get_automation(entity_id: str) -> dict:
    """
    Get the full config (triggers, conditions, actions) of an automation by entity_id.
    Resolves the numeric id from entity attributes and calls the HA config API directly,
    falling back to slug if no numeric id is available.
    """
    automation_slug = entity_id.removeprefix("automation.")
    with httpx.Client() as client:
        # Resolve numeric id from entity attributes (GUI automations use a timestamp id)
        automation_id = automation_slug
        state_r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if state_r.status_code == 200:
            numeric_id = state_r.json().get("attributes", {}).get("id")
            if numeric_id:
                automation_id = numeric_id

        r = client.get(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 404:
            r.raise_for_status()
            return r.json()

        # Fallback: try slug if numeric id didn't work
        if automation_id != automation_slug:
            r2 = client.get(
                f"{HA_URL}/api/config/automation/config/{automation_slug}",
                headers=HEADERS,
                timeout=10,
            )
            if r2.status_code != 404:
                r2.raise_for_status()
                return r2.json()

    return {
        "error": "not_found",
        "entity_id": entity_id,
        "detail": "Automation not found via HA config API.",
    }


@mcp.tool()
def get_automation_trace(entity_id: str, limit: int = 5) -> dict:
    """
    Get the latest execution traces for an automation.
    Useful for debugging why an automation did or did not trigger.

    entity_id: e.g. 'automation.living_room_lights'
    limit: number of recent traces to return (default 5)

    Returns: {total, returned, offset, note?, traces: [...]}

    Traces are held in memory by Home Assistant and are lost on restart, so an
    empty result means "no traces available", never "the automation never ran".
    """
    result = _ws({
        "type": "trace/list",
        "domain": "automation",
        "item_id": entity_id.replace("automation.", ""),
    })
    if err := ws_error(result):
        return err
    traces = [
        {
            "run_id": t.get("run_id"),
            "state": t.get("state"),
            "timestamp": t.get("timestamp"),
            "last_step": t.get("last_step"),
            "error": t.get("error"),
            "script_execution": t.get("script_execution"),
        }
        for t in (result["result"] or [])
    ]
    note = ("no traces available - Home Assistant keeps them in memory and "
            "loses them on restart") if not traces else ""
    return envelope(traces, key="traces", limit=limit, note=note)


@mcp.tool()
def list_blueprints(domain: str = "automation") -> dict:
    """
    List available blueprints.

    domain: 'automation' (default) or 'script'
    Returns: {total, returned, offset, note?, blueprints: [...]}
    """
    result = _ws({"type": "blueprint/list", "domain": domain})
    if err := ws_error(result):
        return err
    blueprints = result["result"] or {}
    rows = [
        {
            "path": path,
            "name": data.get("metadata", {}).get("name", path),
            "description": data.get("metadata", {}).get("description", ""),
            "domain": data.get("metadata", {}).get("domain", domain),
            "input": list((data.get("metadata", {}).get("input") or {}).keys()),
        }
        for path, data in blueprints.items()
    ]
    return envelope(rows, key="blueprints")


@mcp.tool()
def create_automation_from_blueprint(
    blueprint_path: str,
    alias: str,
    input_values: dict,
) -> dict:
    """
    Create an automation from a blueprint.

    blueprint_path: e.g. 'homeassistant/motion_trigger.yaml'
    alias: name for the new automation
    input_values: dict of blueprint input variables

    Home Assistant has no WebSocket command for saving an automation config -
    only a REST endpoint, the same one create_automation() uses.
    """
    automation_id = _slug(alias)
    payload = {
        "alias": alias,
        "use_blueprint": {
            "path": blueprint_path,
            "input": input_values,
        },
    }
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        return {
            "automation_id": automation_id,
            "entity_id": f"automation.{automation_id}",
            "alias": alias,
            "blueprint": blueprint_path,
            "result": r.json(),
        }


@mcp.tool()
def list_device_triggers(device_id: str) -> dict:
    """
    List the triggers a device offers (device automation).

    device_id: from list_devices()
    Returns: {total, returned, offset, note?, triggers: [...]}
    """
    result = _ws({"type": "device_automation/trigger/list", "device_id": device_id})
    if err := ws_error(result):
        return err
    return envelope(result["result"], key="triggers")


@mcp.tool()
def list_device_conditions(device_id: str) -> dict:
    """
    List the conditions a device offers (device automation).

    device_id: from list_devices()
    Returns: {total, returned, offset, note?, conditions: [...]}
    """
    result = _ws({"type": "device_automation/condition/list", "device_id": device_id})
    if err := ws_error(result):
        return err
    return envelope(result["result"], key="conditions")


@mcp.tool()
def list_device_actions(device_id: str) -> dict:
    """
    List the actions a device offers (device automation).

    device_id: from list_devices()
    Returns: {total, returned, offset, note?, actions: [...]}
    """
    result = _ws({"type": "device_automation/action/list", "device_id": device_id})
    if err := ws_error(result):
        return err
    return envelope(result["result"], key="actions")


@mcp.tool()
def import_blueprint(url: str) -> dict:
    """
    Import a blueprint from a URL (GitHub, HA Community, etc.).

    url: direct URL to the blueprint YAML file.
    Examples:
      'https://raw.githubusercontent.com/user/repo/main/blueprints/automation/my_blueprint.yaml'
      'https://community.home-assistant.io/t/some-blueprint/123456'

    After importing, use list_blueprints() to see it and
    create_automation_from_blueprint() to use it.
    """
    result = _ws({"type": "blueprint/import", "url": url})
    if not result.get("success", True):
        err = result.get("error", {})
        return {"error": err.get("code", "unknown"), "detail": err.get("message", str(err))}
    data = result.get("result") or {}
    return {
        "imported": True,
        "url": url,
        "path": data.get("suggested_filename") or data.get("path", ""),
        "name": data.get("blueprint", {}).get("metadata", {}).get("name", ""),
        "domain": data.get("blueprint", {}).get("metadata", {}).get("domain", ""),
    }


@mcp.tool()
def list_schedules() -> dict:
    """
    List all schedules from the Scheduler integration (HACS custom component).

    Returns: {total, returned, offset, note?, schedules: [...]}
    Returns an error if the Scheduler custom component is not installed, or if
    the call otherwise fails — the two are not the same thing and are reported
    differently.
    """
    r = _ws({"type": "scheduler/items"})
    if err := ws_error(r):
        # HA answers an unregistered command type when the custom component
        # is not loaded; any other failure is a different problem and must
        # not be reported as a missing integration (see tools/hacs.py's
        # _hacs_check for the same distinction made for HACS).
        if err.get("error") in ("unknown_command", "not_found"):
            return error("scheduler_not_available",
                         "The Scheduler custom component is not installed.",
                         detail_from_ha=err.get("detail", ""))
        return err
    items = r["result"]
    rows = [
        {
            "schedule_id": item.get("schedule_id"),
            "entity_id": item.get("entity_id"),
            "name": item.get("name", ""),
            "enabled": item.get("enabled", True),
            "next_trigger": item.get("next_trigger"),
            "timeslots": item.get("timeslots", []),
            "actions": item.get("actions", []),
        }
        for item in items
    ]
    rows.sort(key=lambda x: x.get("name", ""))
    return envelope(rows, key="schedules")
