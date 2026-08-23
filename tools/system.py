import httpx

from tools._base import mcp, HA_URL, HEADERS, _ws, _ws_multi, envelope, error, observe_actuation, ws_error


@mcp.tool()
def restart_homeassistant() -> dict:
    """
    Restart Home Assistant. Use with caution — all automations and integrations
    will be unavailable for ~30–60 seconds during restart.

    Returns: {accepted: true, verified: null, detail}. There is no entity
    to check beforehand (this targets the whole instance, not a single
    entity) and no bounded read-back that could confirm a restart that
    takes tens of seconds — waiting for it here would be exactly the long
    polling loop this codebase avoids. `verified` stays null rather than
    claiming a completed restart this tool never actually observes; use
    get_config() a while later to confirm Home Assistant came back up.
    """
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/homeassistant/restart",
            headers=HEADERS,
            json={},
            timeout=15,
        )
        r.raise_for_status()
    return {
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the restart request. It takes "
                  "roughly 30-60 seconds; check back with get_config() "
                  "afterward to confirm it came back up.",
    }


@mcp.tool()
def list_config_entries(domain: str = "") -> dict:
    """
    List installed integrations (config entries).

    domain: optional exact-match filter (e.g. 'telegram_bot', 'shelly', 'reolink')

    Returns: {total, returned, offset, note?, entries: [{entry_id, domain,
             title, state, disabled_by}]}

    An empty `entries` with total 0 means the filter matched nothing. A failed
    call returns {error, detail} instead — the two used to look identical.
    """
    result = _ws({"type": "config_entries/get"})
    if err := ws_error(result):
        return err
    out = [
        {
            "entry_id": e.get("entry_id"),
            "domain": e.get("domain"),
            "title": e.get("title"),
            "state": e.get("state"),
            "disabled_by": e.get("disabled_by"),
        }
        for e in result["result"]
        if not domain or e.get("domain") == domain
    ]
    # Home Assistant permits title: null, which used to raise TypeError here.
    out.sort(key=lambda x: (x["domain"] or "", x["title"] or ""))
    return envelope(out, key="entries")


@mcp.tool()
def list_repairs() -> dict:
    """
    List active repair issues in Home Assistant.

    Returns: {total, returned, offset, note?, repairs: [{issue_id, domain,
             severity, title, ignored, created}]}

    An empty `repairs` with total 0 means there are no open, non-ignored
    issues. A failed call returns {error, detail} instead.
    """
    result = _ws({"type": "repairs/list_issues"})
    if err := ws_error(result):
        return err
    issues = result["result"].get("issues", [])
    out = [
        {
            "issue_id": i.get("issue_id"),
            "domain": i.get("domain"),
            "severity": i.get("severity"),
            "title": i.get("translation_key"),
            "ignored": i.get("ignored", False),
            "created": i.get("created"),
        }
        for i in issues
        if not i.get("ignored", False)
    ]
    return envelope(out, key="repairs")


@mcp.tool()
def list_backups() -> dict:
    """
    List available backups in Home Assistant.

    Returns: {total, returned, offset, note?, backups: [{backup_id, name,
             date, size_mb, type, protected, homeassistant_version}]}

    An empty `backups` with total 0 means there are no backups. A failed
    call returns {error, detail} instead.
    """
    result = _ws({"type": "backup/info"})
    if err := ws_error(result):
        return err
    backups = result["result"].get("backups", [])
    out = [
        {
            "backup_id": b.get("backup_id") or b.get("slug"),
            "name": b.get("name"),
            "date": b.get("date"),
            "size_mb": round(b.get("size", 0) / 1048576, 1) if b.get("size") else None,
            "type": b.get("type", "full"),
            "protected": b.get("protected", False),
            "homeassistant_version": b.get("homeassistant_version") or b.get("homeassistant"),
        }
        for b in sorted(backups, key=lambda x: x.get("date", ""), reverse=True)
    ]
    return envelope(out, key="backups")


@mcp.tool()
def create_backup(name: str = "", agent_ids: list = None) -> dict:
    """
    Create a new full backup of Home Assistant.

    name: optional backup name (defaults to HA's auto-generated name)
    agent_ids: optional list of backup agent ids to store the backup with
               (e.g. ['backup.local']). Home Assistant now requires at
               least one agent_id on backup/generate; when omitted, every
               agent reported by the backup integration is used. Use
               list_backups() afterward to confirm the backup landed —
               creation is asynchronous, so this call returns as soon as
               the job is queued, not when it finishes.

    Returns: {backup_job_id, agent_ids} once Home Assistant accepts the
    job, or an error() envelope ("no_backup_agents" when none is
    configured, or Home Assistant's own error otherwise) on failure. A
    returned backup_job_id confirms only that the job was queued, not that
    it completed — use list_backups() to confirm.
    """
    if not agent_ids:
        agents_result = _ws({"type": "backup/agents/info"})
        if err := ws_error(agents_result):
            return err
        agent_ids = [a["agent_id"] for a in agents_result["result"].get("agents", [])]
        if not agent_ids:
            return error(
                "no_backup_agents",
                "No backup agents are configured on this Home Assistant "
                "instance - backup/generate requires at least one agent_id, "
                "and none is available to default to.",
            )
    msg: dict = {"type": "backup/generate", "agent_ids": agent_ids}
    if name:
        msg["name"] = name
    result = _ws(msg)
    if err := ws_error(result):
        return err
    return {"backup_job_id": result["result"].get("backup_job_id"), "agent_ids": agent_ids}


@mcp.tool()
def reload_integration(entry_id: str) -> dict:
    """
    Reload a config entry (integration) without restarting Home Assistant.
    entry_id: use list_config_entries() to find the entry_id.

    Returns: {entry_id, reloaded: true} once Home Assistant confirms the
    reload, or an error() envelope with Home Assistant's actual error
    code/message on failure — `reloaded: true` is only ever returned once
    that has actually been confirmed, never asserted alongside a failure.
    """
    result = _ws({"type": "config_entries/reload", "entry_id": entry_id})
    if err := ws_error(result):
        return err
    return {"entry_id": entry_id, "reloaded": bool(result["result"])}


@mcp.tool()
def apply_update(entity_id: str, backup: bool = True) -> dict:
    """
    Install a pending update (HA core, add-on, HACS integration, firmware, etc.).

    entity_id: the update.* entity to install (use list_updates() to find them)
    backup: create a backup before updating (default: True, recommended)

    ⚠️ Some updates require a restart. Confirm with the user before proceeding.

    Returns: {entity_id, verified, state, installed_version, latest_version}
    on a call Home Assistant accepted, or {error: "entity_not_found", ...}
    when entity_id has no state at all.

    `verified` is true only when the update entity's own state, read back
    after the call, is "off" (Home Assistant's update domain: "on" means an
    update is pending, "off" means installed_version already matches
    latest_version) — not merely that the install call returned 2xx. An
    install that is genuinely long-running (a firmware flash, a core
    restart) may still show `verified: false` here since this does one
    short bounded read-back, not a wait for the job to finish; check
    list_updates() again afterward for anything that takes longer than
    that. Calling this on an entity with no pending update, or a wrong
    entity_id shape, raises rather than returning a value — Home Assistant
    answers both with a non-2xx status.
    """
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/update/install",
            headers=HEADERS,
            json={"entity_id": entity_id, "backup": backup},
            timeout=30,
        )
        r.raise_for_status()
    obs = observe_actuation(entity_id, lambda s: s["state"] == "off")
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id)
    attrs = obs["state"].get("attributes", {})
    return {
        "entity_id": entity_id,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
        "installed_version": attrs.get("installed_version"),
        "latest_version": attrs.get("latest_version"),
    }


@mcp.tool()
def list_updates() -> dict:
    """
    List available updates (HA core, HACS integrations, add-ons, etc.).

    Returns: {total, returned, offset, note?, updates: [{entity_id, name,
             installed_version, latest_version, release_url, skipped_version}]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        updates = []
        for s in r.json():
            if not s["entity_id"].startswith("update."):
                continue
            if s["state"] != "on":
                continue
            attrs = s.get("attributes", {})
            updates.append({
                "entity_id": s["entity_id"],
                "name": attrs.get("friendly_name", s["entity_id"]),
                "installed_version": attrs.get("installed_version"),
                "latest_version": attrs.get("latest_version"),
                "release_url": attrs.get("release_url", ""),
                "skipped_version": attrs.get("skipped_version"),
            })
        updates.sort(key=lambda x: x["name"])
        return envelope(updates, key="updates")


@mcp.tool()
def list_config_flows() -> dict:
    """
    List pending integration setup flows (config entries in progress).

    These are integrations that have been discovered or partially configured
    and are waiting for user action (e.g. approval, credentials, device selection).

    Returns: {total, returned, offset, note?, flows: [{flow_id, handler,
             step_id, context, description_placeholders}]}
    Use dismiss_config_flow() to cancel a pending flow.
    """
    def _parse(flows):
        return [
            {
                "flow_id": f.get("flow_id"),
                "handler": f.get("handler"),
                "step_id": f.get("step_id"),
                "context": f.get("context", {}),
                "description_placeholders": f.get("description_placeholders", {}),
            }
            for f in (flows if isinstance(flows, list) else [])
        ]

    # Try WS first (works across all HA setups including Supervisor)
    result = _ws({"type": "config_entries/flow/progress"})
    ws_err = ws_error(result)
    if not ws_err:
        return envelope(_parse(result["result"]), key="flows")

    # Fallback: REST (not always available via Supervisor proxy)
    rest_detail = None
    try:
        with httpx.Client() as client:
            r = client.get(
                f"{HA_URL}/api/config/config_entries/flow",
                headers=HEADERS,
                timeout=10,
            )
            if r.status_code == 200:
                return envelope(_parse(r.json()), key="flows")
            rest_detail = f"REST fallback returned {r.status_code}"
    except Exception as exc:
        # Broad on purpose: a fallback path must not itself become a new way
        # to crash. The exception is captured into rest_detail rather than
        # discarded, so this is no longer the bare except-pass that used to
        # hide it.
        rest_detail = f"REST fallback raised {exc!r}"

    # Both paths failed - report both, instead of the WebSocket error alone
    # (or, before this conversion, an empty list indistinguishable from "no
    # pending flows").
    return error(ws_err["error"], ws_err["detail"], rest_detail=rest_detail)


@mcp.tool()
def dismiss_config_flow(flow_id: str) -> dict:
    """
    Cancel and dismiss a pending integration setup flow.

    flow_id: use list_config_flows() to find the flow_id.
    This removes the flow without completing the integration setup.
    Useful for dismissing unwanted auto-discovered integrations.

    Returns: {dismissed: flow_id, success: true}. A discovery flow can
    reappear on its own next time Home Assistant rediscovers the same
    device/service — dismissing it here does not block future discovery.
    """
    with httpx.Client() as client:
        r = client.delete(
            f"{HA_URL}/api/config/config_entries/flow/{flow_id}",
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
    return {"dismissed": flow_id, "success": True}
