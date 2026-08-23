import httpx

from tools._base import mcp, HA_URL, HEADERS, envelope, error, observe_actuation

# HA service names (alarm_disarm, alarm_arm_home, ...) already match
# f"alarm_{command}"; the state each settles into is not the same string
# for "disarm" (service alarm_disarm, state "disarmed") but is for every
# arm_* command (service alarm_arm_home, state "armed_home").
_ALARM_EXPECTED_STATE = {
    "disarm": "disarmed",
    "arm_home": "armed_home",
    "arm_away": "armed_away",
    "arm_night": "armed_night",
    "arm_vacation": "armed_vacation",
    "arm_custom_bypass": "armed_custom_bypass",
}


@mcp.tool()
def alarm_control(entity_id: str, command: str, code: str = "") -> dict:
    """
    Arm or disarm an alarm control panel (Alarmo and others).

    command: disarm | arm_home | arm_away | arm_night | arm_vacation | arm_custom_bypass
    code: optional alarm code (required if the panel is configured to need one)

    ⚠️ SAFETY: This controls a physical alarm system. Always confirm the entity and
    command with the user before executing.

    Returns: {entity_id, command, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all.

    `verified` is true only when the panel's own state, read back after the
    call, matches the command (e.g. arm_home -> "armed_home"). Arming
    passes through a transient "arming"/"pending" state first — measured
    live, a panel with a short exit delay settles within about a second, so
    the read-back retries once before concluding the effect was not
    observed. A wrong or missing code raises rather than returning a value
    (Home Assistant answers it with a non-2xx status), which surfaces the
    same way any other refused call in this codebase does.
    """
    if command not in _ALARM_EXPECTED_STATE:
        return error("invalid_command",
                     f"Invalid command. Use one of: {sorted(_ALARM_EXPECTED_STATE)}")
    service = f"alarm_{command}"  # HA service names: alarm_disarm, alarm_arm_home, etc.
    data: dict = {"entity_id": entity_id}
    if code:
        data["code"] = code
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/alarm_control_panel/{service}",
            headers=HEADERS,
            json=data,
            timeout=15,
        )
        r.raise_for_status()
    expected = _ALARM_EXPECTED_STATE[command]
    obs = observe_actuation(entity_id, lambda s: s["state"] == expected, retries=2, delay=1.0)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    return {
        "entity_id": entity_id,
        "command": command,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }


@mcp.tool()
def get_alarm_state() -> dict:
    """
    Get the current state of all alarm control panels (Alarmo and others).

    Returns: {total, returned, offset, note?, alarms: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        alarms = []
        for s in r.json():
            if not s["entity_id"].startswith("alarm_control_panel."):
                continue
            attrs = s.get("attributes", {})
            alarms.append({
                "entity_id": s["entity_id"],
                "name": attrs.get("friendly_name", s["entity_id"]),
                "state": s["state"],
                "code_format": attrs.get("code_format"),
                "changed_by": attrs.get("changed_by"),
                "open_sensors": attrs.get("open_sensors", {}),
                "bypassed_sensors": attrs.get("bypassed_sensors", []),
                "last_changed": s.get("last_changed", ""),
            })
        return envelope(sorted(alarms, key=lambda x: x["name"]), key="alarms")
