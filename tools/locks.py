import httpx

from tools._base import (
    mcp, HA_URL, HEADERS, envelope, error, observe_actuation,
    verified_allowing_transit,
)

_LOCK_EXPECTED_STATE = {"lock": "locked", "unlock": "unlocked", "open": "open"}
# The state Home Assistant's lock domain defines for "not there yet" for
# each command (LockState.LOCKING/UNLOCKING/OPENING). Not reproduced live
# on this project's demo lock, which jumps straight to its target state
# with no observable delay — this is defensive, for the real locks (many
# Z-Wave/Zigbee locks included) that do take a moment to actuate.
_LOCK_TRANSITIONAL_STATE = {"lock": "locking", "unlock": "unlocking", "open": "opening"}


@mcp.tool()
def list_locks() -> dict:
    """
    List all lock entities with their state.

    Returns: {total, returned, offset, note?, locks: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
    locks = []
    for s in r.json():
        if not s["entity_id"].startswith("lock."):
            continue
        attrs = s.get("attributes", {})
        locks.append({
            "entity_id": s["entity_id"],
            "name": attrs.get("friendly_name", s["entity_id"]),
            "state": s["state"],
            "changed_by": attrs.get("changed_by"),
        })
    return envelope(sorted(locks, key=lambda x: x["name"]), key="locks")


@mcp.tool()
def lock_control(entity_id: str, command: str, code: str = "") -> dict:
    """
    Control a lock entity.

    ⚠️ SAFETY: This physically actuates a lock. ALWAYS ask the user for explicit
    confirmation before calling this tool — show the entity name and command,
    then wait for the user to confirm before proceeding.

    command: lock | unlock | open
    code: optional PIN/code if required by the lock

    Returns: {entity_id, command, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all — Home Assistant accepts and 200s a service call aimed at
    an entity that does not exist, so this check happens after the call, by
    reading the entity back, not before it.

    `verified` is true only when the entity's own state, read back after
    the call, matches the command (locked / unlocked / open). A lock that
    jams, or one that never received the command, still answers the
    service call with 200 — `verified: false` with `state` set to whatever
    was actually observed (e.g. "jammed") is how that is told apart from a
    real success. Not reproduced live on this project's test lock, which
    settles with no observable delay, but many real locks pass through a
    transient "locking"/"unlocking"/"opening" state first; when the
    read-back still shows that state for the command just sent,
    `verified` is `None` — accepted and still actuating, neither confirmed
    nor denied — rather than the `False` a lock that is not moving at all
    (or has jammed) still gets. A non-2xx response (wrong code, a refused
    command) raises rather than returning a value at all.
    """
    if command not in _LOCK_EXPECTED_STATE:
        raise ValueError("command must be: lock, unlock, or open")
    data: dict = {"entity_id": entity_id}
    if code:
        data["code"] = code
    with httpx.Client() as client:
        r = client.post(f"{HA_URL}/api/services/lock/{command}", headers=HEADERS, json=data, timeout=10)
        r.raise_for_status()
    expected = _LOCK_EXPECTED_STATE[command]
    obs = observe_actuation(entity_id, lambda s: s["state"] == expected)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    return {
        "entity_id": entity_id,
        "command": command,
        "verified": verified_allowing_transit(obs, {_LOCK_TRANSITIONAL_STATE[command]}),
        "state": obs["state"]["state"],
    }
