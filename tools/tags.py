from tools._base import mcp, _ws, envelope, ws_error


@mcp.tool()
def list_tags() -> dict:
    """
    List all NFC tags registered in Home Assistant.

    Returns: {total, returned, offset, note?, tags: [{id, name, last_scanned,
             last_scanned_by_device_id}]}
    Tags can be used to trigger automations when scanned with an NFC reader or phone.
    """
    result = _ws({"type": "tag/list"})
    if err := ws_error(result):
        return err
    tags = result["result"]
    rows = [
        {
            "id": t.get("id"),
            "name": t.get("name") or "",
            "last_scanned": t.get("last_scanned"),
            "last_scanned_by_device_id": t.get("last_scanned_by_device_id"),
        }
        for t in sorted(tags, key=lambda x: (x.get("name") or x.get("id", "")).lower())
    ]
    return envelope(rows, key="tags")


@mcp.tool()
def create_tag(name: str, tag_id: str = "") -> dict:
    """
    Create a new NFC tag in Home Assistant.

    name:   friendly name for the tag, e.g. 'Front Door', 'Desk'
    tag_id: optional custom tag ID (UUID format). Leave empty to auto-generate.

    After creating, use the tag ID to configure the NFC tag with the HA Companion App
    or write it to a physical NFC sticker.
    Use create_automation() to trigger actions when the tag is scanned:
      trigger: [{"platform": "tag", "tag_id": "<id>"}]

    Returns the created tag object from Home Assistant, or an error()
    envelope on failure.
    """
    msg: dict = {"type": "tag/create", "name": name}
    if tag_id:
        msg["tag_id"] = tag_id
    result = _ws(msg)
    if err := ws_error(result):
        return err
    return result["result"]


@mcp.tool()
def update_tag(tag_id: str, name: str) -> dict:
    """
    Rename an existing NFC tag.

    tag_id: tag ID (use list_tags() to find it)
    name:   new display name

    Returns the updated tag object from Home Assistant (or
    {tag_id, name} when Home Assistant's response is empty), or an
    error() envelope on failure.
    """
    result = _ws({"type": "tag/update", "tag_id": tag_id, "name": name})
    if err := ws_error(result):
        return err
    return result["result"] or {"tag_id": tag_id, "name": name}


@mcp.tool()
def delete_tag(tag_id: str) -> dict:
    """
    Delete an NFC tag.

    tag_id: tag ID (use list_tags() to find it)

    ⚠️ This is irreversible. Any automation triggered by this tag_id stops
    matching a scan until a new tag with that id is created.

    Returns: {deleted: tag_id, success: true} on success, or an error()
    envelope with Home Assistant's actual error code/message on failure.
    """
    result = _ws({"type": "tag/delete", "tag_id": tag_id})
    if err := ws_error(result):
        return err
    return {"deleted": tag_id, "success": True}
