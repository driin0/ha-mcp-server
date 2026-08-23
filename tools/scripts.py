import httpx

from tools._base import mcp, HA_URL, HEADERS, _slug, envelope, error


@mcp.tool()
def list_scripts() -> dict:
    """
    List all scripts with their state (on = running, off = idle).

    Returns: {total, returned, offset, note?, scripts: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        scripts = [
            {
                "entity_id": s["entity_id"],
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                "state": s["state"],
            }
            for s in r.json()
            if s["entity_id"].startswith("script.")
        ]
        return envelope(sorted(scripts, key=lambda x: x["name"]), key="scripts")


@mcp.tool()
def run_script(entity_id: str, variables: dict = None) -> dict:
    """
    Run a script by entity_id (e.g. 'script.restart_mqtt_broker').
    Optionally pass variables as a dict.
    """
    data = {"entity_id": entity_id}
    if variables:
        data["variables"] = variables
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/script/turn_on",
            headers=HEADERS,
            json=data,
            timeout=15,
        )
        r.raise_for_status()
        return {"triggered": entity_id}


@mcp.tool()
def create_script(name: str, sequence: list, description: str = "", overwrite: bool = False) -> dict:
    """
    Create or update a script.

    overwrite: the script id is derived from `name` through a lossy slug
      (see _slug()) - "Turn everything off" and "Turn, everything off!"
      both become "turn_everything_off", so two different names can
      collide on one id. By default a name that collides with an existing
      script under a *different* alias is refused ("id_collision") rather
      than silently replacing its definition - the id cannot be made
      unique without changing the scheme, so refusing is the honest
      default. Pass overwrite=True to replace it deliberately. Calling
      again with the exact same `name` is treated as an intentional
      update, not a collision, and always succeeds without this flag.

    Example — script that turns off all lights:
      name: "Turn everything off"
      sequence: [{"service": "light.turn_off", "target": {"entity_id": "all"}}]
    """
    script_id = _slug(name)
    entity_id = f"script.{script_id}"

    if not overwrite:
        # A transient failure reading the existing config should not block
        # a legitimate create, so this check only acts on a confirmed 200 -
        # anything else (404 "no such id" included) falls through as "no
        # collision" rather than raising.
        with httpx.Client() as client:
            existing = client.get(
                f"{HA_URL}/api/config/script/config/{script_id}",
                headers=HEADERS, timeout=10,
            )
        if existing.status_code == 200:
            existing_alias = existing.json().get("alias", "")
            if existing_alias != name:
                return error(
                    "id_collision",
                    f"{entity_id!r} already holds a different script "
                    f"({existing_alias!r}) - {name!r} slugs to the same id "
                    "and would silently replace its definition. Pass "
                    "overwrite=True to replace it deliberately, or choose a "
                    "name that slugs differently.",
                    script_id=script_id, entity_id=entity_id,
                    existing_alias=existing_alias, requested_name=name,
                )

    payload = {
        "alias": name,
        "description": description,
        "sequence": sequence,
        "mode": "single",
    }
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/script/config/{script_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        return {"script_id": script_id, "entity_id": entity_id, "result": r.json()}


@mcp.tool()
def delete_script(entity_id: str) -> dict:
    """Delete a script by entity_id (e.g. 'script.turn_everything_off')."""
    script_id = entity_id.removeprefix("script.")
    with httpx.Client() as client:
        r = client.delete(
            f"{HA_URL}/api/config/script/config/{script_id}",
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        return {"deleted": entity_id, "status": r.status_code}


@mcp.tool()
def get_script(entity_id: str) -> dict:
    """
    Get the full config (sequence, mode, description) of a script by entity_id.
    Works for scripts managed via the HA UI editor.
    """
    script_id = entity_id.removeprefix("script.")
    with httpx.Client() as client:
        r = client.get(
            f"{HA_URL}/api/config/script/config/{script_id}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 404:
            return {
                "error": "not_found",
                "entity_id": entity_id,
                "detail": "Script not found via HA config API.",
            }
        r.raise_for_status()
        return r.json()
