import httpx

from tools._base import (
    mcp, HA_URL, HEADERS, _slug, _ws, confirm_entity_exists, envelope, error,
    observe_actuation, wait_for_entity, ws_error,
)


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
    overwrite: bool = False,
) -> dict:
    """
    Create or update an automation. The automation ID is derived from the name.

    trigger, condition and action must be valid HA trigger/condition/action objects.

    overwrite: the automation id is derived from `name` through a lossy
      slug (see _slug()) - "Morning lights" and "Morning, lights!" both
      become "morning_lights", so two different names can collide on one
      id. By default a name that collides with an existing automation
      under a *different* alias is refused ("id_collision") rather than
      silently replacing its definition - the id cannot be made unique
      without changing the scheme, so refusing is the honest default. Pass
      overwrite=True to replace it deliberately. Calling again with the
      exact same `name` is treated as an intentional update, not a
      collision, and always succeeds without this flag.

    Example — turn on a light at sunset:
      name: "Turn on light at sunset"
      trigger: [{"platform": "sun", "event": "sunset"}]
      action: [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}]

    Example — notify when door opens:
      name: "Notify door open"
      trigger: [{"platform": "state", "entity_id": "binary_sensor.front_door", "to": "on"}]
      action: [{"service": "notify.mobile_app_myphone", "data": {"message": "Door open!"}}]

    Returns: {automation_id, entity_id, enabled, verified, state, result} on
    a config Home Assistant accepted, or an error() envelope - "id_collision"
    (see `overwrite` above) or "automation_not_registered"/
    "automation_not_disabled"/"automation_state_unverified" when the
    entity's state after creation could not confirm what was requested.

    `enabled` and `state` report what was actually observed after creation,
    not the request. Home Assistant arms every new automation ("on") the
    instant it registers, and disabling it is a second, separate service
    call - sending that call before the entity has registered lands on an
    entity_id that does not exist yet and is accepted as a 200 [] no-op,
    the same way any call at a target that is not there yet is (see
    confirm_entity_exists()), leaving the automation silently armed.
    Measured live: ten automations created with enabled=False, config POST
    and turn_off sent back-to-back with no wait - 9 of 10 stayed "on". This
    tool waits for the entity to register before sending turn_off, then
    reads its state back to confirm the request actually landed; `verified`
    is true only when it did. A safety-relevant automation created disabled
    must be checked by its actual state, not assumed from the request - so
    a state that cannot be confirmed is an error() return, never a bare
    success.
    """
    automation_id = _slug(name)
    entity_id = f"automation.{automation_id}"

    if not overwrite:
        # A transient failure reading the existing config should not block
        # a legitimate create, so this check only acts on a confirmed 200 -
        # anything else (404 "no such id" included) falls through as "no
        # collision" rather than raising.
        with httpx.Client() as client:
            existing = client.get(
                f"{HA_URL}/api/config/automation/config/{automation_id}",
                headers=HEADERS, timeout=10,
            )
        if existing.status_code == 200:
            existing_alias = existing.json().get("alias", "")
            if existing_alias != name:
                return error(
                    "id_collision",
                    f"{entity_id!r} already holds a different automation "
                    f"({existing_alias!r}) - {name!r} slugs to the same id "
                    "and would silently replace its definition. Pass "
                    "overwrite=True to replace it deliberately, or choose a "
                    "name that slugs differently.",
                    automation_id=automation_id, entity_id=entity_id,
                    existing_alias=existing_alias, requested_name=name,
                )

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
        create_result = r.json()

    if not enabled:
        # Wait for the entity to register before disabling it - see
        # docstring and wait_for_entity()'s own docstring for the race this
        # closes.
        if not wait_for_entity(entity_id):
            return error(
                "automation_not_registered",
                f"{entity_id} was created but never registered a state, so "
                "it could not be disabled - it may still be armed. Check "
                "manually before relying on it.",
                automation_id=automation_id, entity_id=entity_id,
                result=create_result,
            )
        with httpx.Client() as client:
            client.post(
                f"{HA_URL}/api/services/automation/turn_off",
                headers=HEADERS,
                json={"entity_id": entity_id},
                timeout=10,
            )

    expected = "off" if not enabled else "on"
    obs = observe_actuation(entity_id, lambda s: s["state"] == expected)
    if not obs["exists"]:
        return error(
            "automation_not_registered",
            f"{entity_id} was created but never registered a state.",
            automation_id=automation_id, entity_id=entity_id,
            result=create_result,
        )
    if not obs["verified"]:
        return error(
            "automation_not_disabled" if not enabled else "automation_state_unverified",
            f"{entity_id} was created, but its state could not be confirmed "
            f"as {expected!r} - observed {obs['state']['state']!r}. Treat "
            "its enabled/disabled state as unknown until verified manually.",
            automation_id=automation_id, entity_id=entity_id,
            enabled=obs["state"]["state"] == "on", state=obs["state"]["state"],
            result=create_result,
        )
    return {
        "automation_id": automation_id,
        "entity_id": entity_id,
        "enabled": obs["state"]["state"] == "on",
        "verified": True,
        "state": obs["state"]["state"],
        "result": create_result,
    }


@mcp.tool()
def delete_automation(entity_id: str) -> dict:
    """
    Delete an automation by entity_id (e.g. 'automation.turn_on_light_at_sunset').

    Home Assistant's delete endpoint is keyed by the automation's own config
    id, not by entity_id. An automation created by create_automation() in
    this tool has the two equal - the same slug is used as both - but one
    created through the Home Assistant UI editor gets a numeric timestamp
    config id that is independent of its entity_id's object_id, since the
    UI derives the entity_id from the alias separately. Measured live: an
    automation saved from the UI with alias "Morning Lights UI Style" got
    entity_id 'automation.morning_lights_ui_style' and config id
    '1690221234567' - deleting by the entity_id's own slug answers 400
    ("Resource not found") even though the automation exists; deleting by
    the numeric id read from its `id` attribute succeeds. This tool reads
    that attribute from the entity's own state before deleting, so it works
    for automations created via the HA UI too, not only ones this tool
    created itself.

    Only works for automations stored in the UI-editable automation
    registry. A YAML-defined automation has no config id, and Home
    Assistant refuses to delete it via this API - measured live, with a
    400 ("Resource not found"), not a 404: this endpoint answers 400 both
    for "no such config id" and for "this automation cannot be deleted
    here", so the two are reported the same way below.

    ⚠️ This is irreversible.

    Returns: {deleted: entity_id, status: <HTTP status code>} on success,
    or an error() envelope ("entity_not_found" or "not_deletable") on
    failure.
    """
    with httpx.Client() as client:
        state_r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if state_r.status_code == 404:
            return error("entity_not_found",
                         f"{entity_id} does not exist on this Home Assistant instance.",
                         entity_id=entity_id)
        state_r.raise_for_status()
        automation_id = (state_r.json().get("attributes", {}).get("id")
                        or entity_id.removeprefix("automation."))

        r = client.delete(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 400:
            return error(
                "not_deletable",
                "Home Assistant refused the delete (400) - this automation "
                "is likely defined in YAML, which has no config id and "
                "cannot be deleted via this API. Only automations editable "
                "in the HA UI can be deleted with this tool.",
                entity_id=entity_id, automation_id=automation_id,
                ha_response=r.text[:300],
            )
        r.raise_for_status()
        return {"deleted": entity_id, "status": r.status_code}


@mcp.tool()
def trigger_automation(entity_id: str) -> dict:
    """Manually trigger an automation regardless of its trigger conditions.

    Returns: {entity_id, accepted: true, verified: null, detail} once Home
    Assistant accepts the call, or {error: "entity_not_found", ...} when
    entity_id has no state at all. What the automation's action sequence
    actually does has no single state here to confirm it happened - check
    the entities it acts on, or get_automation_trace() for its own record
    of the run.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/automation/trigger",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the trigger; what the "
                  "automation's actions do has no single state here to "
                  "confirm - check get_automation_trace() or the entities "
                  "it acts on.",
    }


@mcp.tool()
def toggle_automation(entity_id: str) -> dict:
    """Enable or disable an automation.

    Returns: {entity_id, new_state} once the entity's state is read back
    after the toggle - "on" (armed) or "off" (disabled) - or
    {entity_id, new_state: null, detail} when the entity has no state yet
    (it may not exist, or may still be registering; see create_automation()'s
    docstring for the registration race this can surface).
    """
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

    Returns: {automation_id, entity_id, alias, blueprint, result} once Home
    Assistant accepts the config. Unlike create_automation(), this does not
    wait for the entity to register or read its state back - a blueprint
    automation is armed the instant it registers, the same way any new
    automation is; check get_states_by_domain('automation') afterward if
    you need to confirm it exists.
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
    Import a blueprint from a URL (GitHub, HA Community, etc.) and save it
    to disk, ready to use.

    url: direct URL to the blueprint YAML file.
    Examples:
      'https://raw.githubusercontent.com/user/repo/main/blueprints/automation/my_blueprint.yaml'
      'https://community.home-assistant.io/t/some-blueprint/123456'

    Home Assistant's `blueprint/import` command is only the preview step
    the blueprint editor uses before showing you what it found - it parses
    and validates the YAML but writes nothing to disk. Saving is a second,
    separate command, `blueprint/save`. Verified live: blueprint/list was
    unchanged immediately after blueprint/import alone, and only showed the
    new path once blueprint/save was also called with the id and raw YAML
    the import step returned. This tool performs both steps, so a caller
    does not have to know that split exists or do it themselves.

    After importing, use list_blueprints() to see it and
    create_automation_from_blueprint() to use it.

    Returns: {imported, saved, url, path, domain, name, overrides_existing}
    once both steps succeed, or an error() envelope - the ws_error() from
    whichever WebSocket command failed, or "invalid_blueprint" when Home
    Assistant's own parser reports validation errors on the imported YAML
    (nothing is saved in that case), or "incomplete_import" when the import
    step succeeded but did not return enough information (domain or a
    suggested path) to save it.
    """
    imported = _ws({"type": "blueprint/import", "url": url})
    if err := ws_error(imported):
        return err
    data = imported["result"] or {}

    validation_errors = data.get("validation_errors")
    if validation_errors:
        return error("invalid_blueprint",
                     "Home Assistant could not validate this blueprint - nothing was saved.",
                     url=url, validation_errors=validation_errors)

    metadata = data.get("blueprint", {}).get("metadata", {})
    domain = metadata.get("domain", "")
    path = data.get("suggested_filename") or data.get("path", "")
    if not domain or not path:
        return error("incomplete_import",
                     "Home Assistant's import did not return enough information "
                     "to save this blueprint (missing domain or suggested path).",
                     url=url, result=data)

    saved = _ws({
        "type": "blueprint/save",
        "domain": domain,
        "path": path,
        "yaml": data.get("raw_data", ""),
        "source_url": url,
    })
    if err := ws_error(saved):
        return err
    save_result = saved["result"] or {}

    return {
        "imported": True,
        "saved": True,
        "url": url,
        "path": path,
        "domain": domain,
        "name": metadata.get("name", ""),
        "overrides_existing": save_result.get("overrides_existing", False),
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
