import httpx

from tools._base import mcp, HA_URL, HEADERS, _ws, confirm_entity_exists, envelope, error, observe_actuation


@mcp.tool()
def list_groups(search: str = "") -> dict:
    """
    List all entity groups (group.* domain) with their members.

    search: optional substring filter on group name (case-insensitive)

    Returns: {total, returned, offset, note?, groups: [{entity_id, name, state,
             entities: [...], all_entities: bool}]}

    Note: these are logical groups (group.*) used for grouping entity states.
    For device/area grouping, use list_areas(). For light groups, use list_lights().
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    groups = []
    for s in r.json():
        if not s["entity_id"].startswith("group."):
            continue
        attrs = s.get("attributes", {})
        name = attrs.get("friendly_name", s["entity_id"])
        if search and search.lower() not in name.lower():
            continue
        groups.append({
            "entity_id": s["entity_id"],
            "name": name,
            "state": s["state"],
            "entities": attrs.get("entity_id", []),
            "all_entities": attrs.get("all", False),
            "icon": attrs.get("icon", ""),
        })
    return envelope(sorted(groups, key=lambda x: x["name"]), key="groups")


@mcp.tool()
def create_group(
    name: str,
    entities: list,
    all_entities: bool = False,
    icon: str = "",
) -> dict:
    """
    Create or update a logical group (group.*).

    name:        group name — also used to derive the entity_id (e.g. 'Living Room Lights'
                 → 'group.living_room_lights')
    entities:    list of entity_ids to include in the group,
                 e.g. ['light.living_room', 'light.kitchen', 'switch.lamp']
    all_entities: if True, group state is 'on' only when ALL entities are on
                  (default False: 'on' when ANY entity is on)
    icon:        MDI icon, e.g. 'mdi:lightbulb-group' (optional)

    Returns: {entity_id, name, entities, verified, state} on a call Home
    Assistant accepted. `verified` is true only when group.<object_id>
    exists afterward with a member list matching `entities` exactly, read
    back rather than assumed. Note that Home Assistant does not validate
    membership: an entity_id in `entities` that does not itself exist is
    still accepted and stored as a member as-is (measured live), so
    `verified: true` here confirms the group was created with the member
    list requested, not that every member is real.
    """
    object_id = name.lower().replace(" ", "_")
    entity_id = f"group.{object_id}"
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/group/set",
            headers=HEADERS,
            json={
                "object_id": object_id,
                "name": name,
                "entities": ",".join(entities),
                "all": all_entities,
                **({"icon": icon} if icon else {}),
            },
            timeout=15,
        )
        r.raise_for_status()

    wanted = set(entities)
    obs = observe_actuation(entity_id, lambda s: set(s.get("attributes", {}).get("entity_id", [])) == wanted)
    actual_entities = obs["state"].get("attributes", {}).get("entity_id", []) if obs["exists"] else []
    return {
        "entity_id": entity_id,
        "name": name,
        "entities": actual_entities,
        "verified": obs["verified"],
        "state": obs["state"]["state"] if obs["exists"] else None,
    }


@mcp.tool()
def update_group(
    entity_id: str,
    entities: list = None,
    name: str = "",
    all_entities: bool = None,
    icon: str = "",
) -> dict:
    """
    Update an existing group's members, name, or icon.

    entity_id:   e.g. 'group.living_room_lights'
    entities:    new list of entity_ids (replaces current members)
    name:        new display name
    all_entities: change the 'all' behavior (True = all must be on, False = any)
    icon:        new MDI icon

    Only non-None/non-empty fields are updated; others keep their current value.

    Returns: {entity_id, verified, state, entities} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all — checked BEFORE calling group/set, not after: that
    service does not distinguish create from update, so pointed at an
    object_id with no existing group it creates a new one instead of
    failing (measured live) - without this check, "update" a group that
    was never there would silently create it and report success.

    `verified` is true only when every field actually given
    (entities/name/all_entities — icon is not exposed on the state object,
    so it cannot be checked here) matches on read-back, not merely that
    the call returned 2xx.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing

    object_id = entity_id.removeprefix("group.")
    payload: dict = {"object_id": object_id}
    if entities is not None:
        payload["entities"] = ",".join(entities)
    if name:
        payload["name"] = name
    if all_entities is not None:
        payload["all"] = all_entities
    if icon:
        payload["icon"] = icon
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/group/set",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()

    def matches(s: dict) -> bool:
        attrs = s.get("attributes", {})
        if entities is not None and set(attrs.get("entity_id", [])) != set(entities):
            return False
        if name and attrs.get("friendly_name") != name:
            return False
        if all_entities is not None and attrs.get("all") != all_entities:
            return False
        return True

    obs = observe_actuation(entity_id, matches)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id)
    return {
        "entity_id": entity_id,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
        "entities": obs["state"].get("attributes", {}).get("entity_id", []),
    }


@mcp.tool()
def delete_group(entity_id: str) -> dict:
    """
    Delete a logical group (group.*).

    entity_id: e.g. 'group.living_room_lights'. Use list_groups() to find entity_ids.

    Note: only groups created via the 'group.set' service can be deleted this way.
    YAML-defined groups (in groups.yaml) must be removed manually from the file.

    Returns: {entity_id, verified} on a call Home Assistant accepted, or
    {error: "entity_not_found", ...} when entity_id had no state even
    before the call — nothing to delete. `verified` is true only when
    entity_id no longer has a state afterward — a YAML-defined group is
    accepted by group/remove without effect, since the service only
    removes storage-backed groups, which `verified: false` reports rather
    than a blanket claimed success.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id)
    r.raise_for_status()

    object_id = entity_id.removeprefix("group.")
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/group/remove",
            headers=HEADERS,
            json={"object_id": object_id},
            timeout=15,
        )
        r.raise_for_status()

    # observe_actuation()'s predicate is never satisfied by a state that
    # still exists, so a group that survives the delete falls through to
    # exists=True/verified=False after the retry; one that is gone by
    # then returns exists=False immediately - which here IS the success.
    obs = observe_actuation(entity_id, lambda s: False)
    return {"entity_id": entity_id, "verified": not obs["exists"]}
