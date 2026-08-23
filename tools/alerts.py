import httpx

from tools._base import mcp, HA_URL, HEADERS, confirm_entity_exists, envelope


@mcp.tool()
def list_alerts() -> dict:
    """
    List all alert entities (alert.*) with their current state.

    Alert entities fire repeatedly (with configurable intervals) while a condition is active,
    until acknowledged. Useful for monitoring critical conditions like gas leaks, flooding, etc.

    Returns: {total, returned, offset, note?, alerts: [{entity_id, name, state,
             last_changed, attributes}]}
    States: 'idle' (condition inactive), 'on' (firing), 'off' (acknowledged/snoozed)
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    alerts = []
    for s in r.json():
        if not s["entity_id"].startswith("alert."):
            continue
        attrs = s.get("attributes", {})
        alerts.append({
            "entity_id": s["entity_id"],
            "name": attrs.get("friendly_name", s["entity_id"]),
            "state": s["state"],
            "last_changed": s.get("last_changed", "")[:19],
            "notification_frequency_minutes": attrs.get("notification_frequency"),
            "data": attrs.get("data", {}),
        })
    return envelope(sorted(alerts, key=lambda x: x["name"]), key="alerts")


@mcp.tool()
def acknowledge_alert(entity_id: str) -> dict:
    """
    Acknowledge a firing alert to stop repeated notifications.

    entity_id: alert entity to acknowledge (e.g. 'alert.gas_leak')
    Use list_alerts() to find active alerts.

    Acknowledged alerts will resume firing if the condition is still active
    after the configured notification interval.

    Returns: {entity_id, accepted: true, verified: null, detail} once Home
    Assistant accepts the call, or {error: "entity_not_found", ...} when
    entity_id has no state at all. Acknowledging silences repeated
    notifications rather than settling into one fixed state (an alert
    resumes firing on its own if the condition is still active), so there
    is no single expected read-back to verify against.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/alert/acknowledge",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the acknowledgement; it silences "
                  "notifications rather than settling into one fixed state "
                  "to confirm against.",
    }


@mcp.tool()
def toggle_alert(entity_id: str, action: str = "toggle") -> dict:
    """
    Turn an alert on, off, or toggle it.

    entity_id: alert entity (e.g. 'alert.gas_leak')
    action: 'on' | 'off' | 'toggle' (default: 'toggle')
            'off' silences the alert (same as acknowledge)
            'on'  re-enables a silenced alert

    Returns: {entity_id, action, accepted: true, verified: null, detail}
    once Home Assistant accepts the call, or {error: "entity_not_found",
    ...} when entity_id has no state at all. 'on' re-enables monitoring
    rather than forcing a fixed state — the alert's state right after
    still depends on whether its underlying condition is active — so
    there is no single expected read-back to verify 'on' or 'toggle'
    against; 'off' is likewise a silence, not a value.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}.get(action, "toggle")
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/alert/{service}",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "action": action,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the call; see this tool's "
                  "docstring for why the resulting state cannot be "
                  "verified against a single expected value.",
    }
