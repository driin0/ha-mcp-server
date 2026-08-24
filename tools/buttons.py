import httpx

from tools._base import mcp, HA_URL, HEADERS, confirm_entity_exists


@mcp.tool()
def press_button(entity_id: str) -> dict:
    """Press a button entity (domain: button or input_button).

    Returns: {entity_id, accepted: true, verified: null, detail} once Home
    Assistant accepts the call, or {error: "entity_not_found", ...} when
    entity_id has no state at all.

    A button's own state is only the timestamp of its last press, not
    evidence that whatever it triggers (a script, a physical device)
    actually happened — so existence is checked before calling, since
    that is the only thing this tool can confirm about the target, and
    `verified` stays null rather than claiming an effect this tool cannot
    observe.
    """
    domain = entity_id.split(".")[0]
    if domain not in ("button", "input_button"):
        raise ValueError("entity_id must be a button.* or input_button.* entity")
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/{domain}/press",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the press; what it triggers has "
                  "no state here to confirm it happened.",
    }
