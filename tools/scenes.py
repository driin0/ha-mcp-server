import httpx

from tools._base import mcp, HA_URL, HEADERS, _slug, confirm_entity_exists, envelope


@mcp.tool()
def list_scenes() -> dict:
    """
    List all scenes with their entity list and current states.

    Returns: {total, returned, offset, note?, scenes: [...]}
    """
    with httpx.Client() as client:
        states_r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        states_r.raise_for_status()
        all_states = {s["entity_id"]: s["state"] for s in states_r.json()}
        scenes = []
        for s in states_r.json():
            if not s["entity_id"].startswith("scene."):
                continue
            attrs = s.get("attributes", {})
            entity_ids = attrs.get("entity_id", [])
            scenes.append({
                "entity_id": s["entity_id"],
                "name": attrs.get("friendly_name", s["entity_id"]),
                "entities": {eid: all_states.get(eid, "unknown") for eid in entity_ids},
            })
        return envelope(sorted(scenes, key=lambda x: x["name"]), key="scenes")


@mcp.tool()
def activate_scene(entity_id: str) -> dict:
    """Activate a scene by entity_id (e.g. 'scene.movie_night').

    Returns: {entity_id, accepted: true, verified: null, detail} once Home
    Assistant accepts the call, or {error: "entity_not_found", ...} when
    entity_id has no state at all. A scene's own state is only the
    timestamp it was last activated, not evidence of what changed - check
    the individual entities the scene was defined over (list_scenes()
    shows them) to confirm the effect.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/scene/turn_on",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the call; a scene's own state "
                  "is only a last-activated timestamp, not evidence of "
                  "what changed - check the individual entities it was "
                  "defined over.",
    }


@mcp.tool()
def create_scene(name: str, entities: dict) -> dict:
    """
    Create or update a scene.

    entities: dict of entity_id -> state/attributes to capture.

    Example — a cinema scene:
      name: "Cinema"
      entities: {
        "light.living_room": {"state": "on", "brightness": 30},
        "switch.living_room_night_light": {"state": "on"}
      }

    ⚠️ This silently overwrites an existing scene with the same name -
    Home Assistant's config/scene/config/{id} endpoint has no separate
    create-vs-update mode, so a name that slugs the same as an existing
    scene replaces its definition with no confirmation step.

    Returns: {scene_id, entity_id, result} - `result` is Home Assistant's
    own JSON response from the config write, passed through unexamined
    (a non-2xx response raises rather than returning a value).
    """
    scene_id = _slug(name)
    payload = {
        "name": name,
        "entities": entities,
    }
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/scene/config/{scene_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        return {"scene_id": scene_id, "entity_id": f"scene.{scene_id}", "result": r.json()}


@mcp.tool()
def delete_scene(entity_id: str) -> dict:
    """Delete a scene by entity_id (e.g. 'scene.cinema').

    ⚠️ This is irreversible.

    Returns: {deleted: entity_id, status: <HTTP status code>}. A non-2xx
    response raises rather than returning a value, like every other REST
    config write in this codebase.
    """
    scene_id = entity_id.removeprefix("scene.")
    with httpx.Client() as client:
        r = client.delete(
            f"{HA_URL}/api/config/scene/config/{scene_id}",
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        return {"deleted": entity_id, "status": r.status_code}
