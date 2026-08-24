import httpx

from tools._base import mcp, HA_URL, HEADERS, _ws, confirm_entity_exists, envelope, rest_error, ws_error


@mcp.tool()
def list_todo_lists() -> dict:
    """
    List all todo list entities.

    Returns: {total, returned, offset, note?, lists: [{entity_id, name, item_count}]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    out = [
        {
            "entity_id": s["entity_id"],
            "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
            "item_count": s.get("attributes", {}).get("todo_items"),
        }
        for s in r.json()
        if s["entity_id"].startswith("todo.")
    ]
    return envelope(out, key="lists")


def _todo_items(entity_id: str):
    """The current items on a todo list, or a ws_error() dict.

    Shared plumbing for get_todo_items() and the write tools below, which
    use this same call_service/return_response round trip as their
    read-back — todo items are not entities of their own with a state to
    GET; they only exist inside this response.
    """
    result = _ws({
        "type": "call_service",
        "domain": "todo",
        "service": "get_items",
        "service_data": {"entity_id": entity_id},
        "return_response": True,
    })
    if err := ws_error(result):
        return err
    # Response result: {"response": {"todo.shopping_list": {"items": [...]}}}
    response = result["result"].get("response", {})
    return response.get(entity_id, {}).get("items", [])


@mcp.tool()
def get_todo_items(entity_id: str) -> dict:
    """
    Get all items from a todo list.

    entity_id: e.g. 'todo.shopping_list'

    Returns: {total, returned, offset, note?, items: [...]}

    ⚠️ third-party-settable: an item's summary comes from whoever added it -
    for a shared list, that can be anyone with access to it, not just this
    installation's owner. See tools/_base.py's "Third-party-settable
    fields" note.
    """
    items = _todo_items(entity_id)
    if isinstance(items, dict):  # ws_error()
        return items
    return envelope(items, key="items")


@mcp.tool()
def add_todo_item(entity_id: str, item: str, description: str = "", due_date: str = "") -> dict:
    """
    Add an item to a todo list.

    entity_id: e.g. 'todo.shopping_list'
    item: item summary/name
    description: optional longer description
    due_date: optional due date in YYYY-MM-DD format

    Returns: {entity_id, item, verified} on a call Home Assistant accepted,
    or an error() envelope - {error: "entity_not_found", ...} when
    entity_id has no state at all, or {error: "home_assistant_error",
    status, detail} when Home Assistant rejects the call itself (e.g. a
    due_date it does not accept). `verified` is true only when an item
    with this summary is present in get_todo_items() read back after the
    call - not merely that the call returned 2xx.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    data: dict = {"entity_id": entity_id, "item": item}
    if description:
        data["description"] = description
    if due_date:
        data["due_date"] = due_date
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/todo/add_item",
            headers=HEADERS,
            json=data,
            timeout=10,
        )
        if err := rest_error(r):
            return err

    items = _todo_items(entity_id)
    if isinstance(items, dict):  # ws_error()
        return items
    return {
        "entity_id": entity_id,
        "item": item,
        "verified": any(i.get("summary") == item for i in items),
    }


@mcp.tool()
def update_todo_item(entity_id: str, item: str, status: str = "", rename: str = "") -> dict:
    """
    Update a todo item's status or name.

    entity_id: e.g. 'todo.shopping_list'
    item: current item name (uid or summary)
    status: needs_action | completed
    rename: new name for the item

    Returns: {entity_id, item, verified} on a call Home Assistant accepted,
    or an error() envelope — {error: "entity_not_found", ...} when
    entity_id has no state at all, or {error: "home_assistant_error",
    status, detail} when Home Assistant rejects the call itself (an item
    that does not exist under `item`, or a `status` value outside
    needs_action/completed both 4xx/5xx there rather than being validated
    here first — HA's own rejection message does not distinguish the two,
    so neither does this). `verified` is true only when the item — looked
    up by its new name if `rename` was given, else by `item` — is present
    with the requested `status` (when given) in get_todo_items() read
    back after the call.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    data: dict = {"entity_id": entity_id, "item": item}
    if status:
        data["status"] = status
    if rename:
        data["rename"] = rename
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/todo/update_item",
            headers=HEADERS,
            json=data,
            timeout=10,
        )
        if err := rest_error(r):
            return err

    items = _todo_items(entity_id)
    if isinstance(items, dict):  # ws_error()
        return items
    # `item` (and `rename`) may be either the item's uid or its summary -
    # the update_item service itself accepts either, so the read-back
    # match has to check both to avoid a false "unverified" on a uid.
    look_for = rename or item
    match = next((i for i in items if look_for in (i.get("uid"), i.get("summary"))), None)
    verified = match is not None and (not status or match.get("status") == status)
    return {"entity_id": entity_id, "item": look_for, "verified": verified}


@mcp.tool()
def remove_todo_item(entity_id: str, item: str) -> dict:
    """Remove an item from a todo list. item: item name or uid.

    ⚠️ This is irreversible.

    Returns: {entity_id, item, verified} on a call Home Assistant accepted,
    or an error() envelope - {error: "entity_not_found", ...} when
    entity_id has no state at all, or {error: "home_assistant_error",
    status, detail} when Home Assistant rejects the call itself (e.g. an
    item that does not exist under `item`). `verified` is true only when
    no item with this summary remains in get_todo_items() read back after
    the call.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/todo/remove_item",
            headers=HEADERS,
            json={"entity_id": entity_id, "item": item},
            timeout=10,
        )
        if err := rest_error(r):
            return err

    items = _todo_items(entity_id)
    if isinstance(items, dict):  # ws_error()
        return items
    return {
        "entity_id": entity_id,
        "item": item,
        "verified": not any(item in (i.get("uid"), i.get("summary")) for i in items),
    }
