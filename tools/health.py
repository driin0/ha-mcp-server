"""One call for the state an instance is actually in.

The founding incident's diagnostic signature was not subtle and was not
visible: every entity of one device had been unavailable for weeks while
its own ping still reported `on`. A live system with a dead integration.
Nothing here returned that in one call - it took five, assembled by hand.

What makes it a signature is the grouping. Twenty-five unavailable entities
that all belong to one integration is ONE fault; reported as twenty-five
rows it reads as an instance falling apart, and the actual finding - that
they share a platform - is the part a list of rows does not say.
"""
import datetime

from tools._base import mcp, envelope, _ws_multi, ws_error
from tools.validation import _live_snapshot


def _hours_since(timestamp: str | None, now: datetime.datetime) -> float | None:
    """Hours between `timestamp` and now, or None when it cannot be read.

    None is not zero. An entity whose last_changed is missing or malformed
    has an UNKNOWN age, and reporting that as 0.0 would file it at the
    "just happened" end of exactly the ordering this tool exists to expose -
    where the duration filter would then hide it.
    """
    if not timestamp:
        return None
    try:
        when = datetime.datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return (now - when).total_seconds() / 3600.0


@mcp.tool()
def instance_health(unavailable_hours: int = 24, limit: int = 20,
                    offset: int = 0) -> dict:
    """
    One read-only report on what is wrong with this Home Assistant instance:
    entities that have no usable state, grouped by the integration that owns
    them, plus config entries that are not loaded and open repair issues.

    The grouping is the point. Twenty-five unavailable entities from one
    integration is one fault, and seeing that they share a platform is what
    distinguishes "an integration is down" from "twenty-five devices have
    problems". In the incident this server was built after, that single
    distinction was the whole diagnosis, and it took five separate calls to
    assemble by hand.

    unavailable_hours: only list integrations whose longest-running
      unavailable entity has been that way for at least this many hours
      (default 24). This narrows the LISTING only - `summary` always counts
      the whole population, so a filter can never make the instance look
      healthier than it is. Pass 0 to list everything. An integration whose
      age cannot be read is listed whatever this is set to: unknown is not
      recent.
    limit, offset: page over the listed integrations. Bounded by default
      like every other collection here - a real instance has thousands of
      registry entries, and an unbounded aggregate over them is a response
      that never arrives.

    Returns: {total, returned, offset, note?, summary, hours_are_a_lower_bound,
    integrations: [{platform, unavailable, unknown, total,
    oldest_unavailable_hours, entity_ids}], config_entries: [...],
    repairs: [...], sections_unavailable: [...]}, integrations sorted with
    the longest-running outage first.

    `oldest_unavailable_hours` is a LOWER BOUND, never the true duration:
    it derives from the entity's `last_changed`, which a Home Assistant
    restart resets. A fault that survived a restart reads as newer than it
    is, never as older. `hours_are_a_lower_bound` is in the response so a
    reader cannot miss it, and is null for an integration whose age could
    not be read at all.

    `sections_unavailable` names any section that could not be read -
    typically `repairs` or `config_entries` on a connection that will not
    forward those commands, the same Supervisor-proxy limitation
    get_system_health() already handles. Such a section is reported as
    unread, never as empty: a health report that quietly omits a check it
    could not run is a report saying "all clear" about something it never
    looked at.

    Returns an error() envelope only when the core snapshot - the entity
    registry and the live states - could not be read at all. Never for
    finding a healthy instance, which is simply `integrations: []`.

    Two WebSocket round trips, not one: the registries come from
    _live_snapshot(), shared with the validation tools, and the two
    sections below are batched separately rather than by widening a helper
    every other caller would then pay for.
    """
    states, entity_registry, _devices, err = _live_snapshot()
    if err:
        return err

    now = datetime.datetime.now(datetime.timezone.utc)
    unreadable: list[str] = []

    # --- grouping, over EVERY entity, before any filter -----------------
    groups: dict[str, dict] = {}
    unavailable_total = 0
    unknown_total = 0

    for entity_id, row in states.items():
        platform = (entity_registry.get(entity_id) or {}).get("platform") or "unknown"
        group = groups.setdefault(platform, {
            "platform": platform, "unavailable": 0, "unknown": 0, "total": 0,
            "oldest_unavailable_hours": None, "entity_ids": [],
        })
        group["total"] += 1

        value = row.get("state")
        if value == "unavailable":
            group["unavailable"] += 1
            unavailable_total += 1
            group["entity_ids"].append(entity_id)
            hours = _hours_since(row.get("last_changed"), now)
            if hours is not None:
                current = group["oldest_unavailable_hours"]
                group["oldest_unavailable_hours"] = (
                    hours if current is None else max(current, hours))
        elif value == "unknown":
            group["unknown"] += 1
            unknown_total += 1

    # --- the other two sections, each allowed to fail on its own --------
    config_entries: list[dict] = []
    repairs: list[dict] = []

    ws_results = _ws_multi([
        {"type": "config_entries/list"},
        {"type": "repairs/list_issues"},
    ])
    if ws_error(ws_results[0]):
        unreadable.append("config_entries")
    else:
        config_entries = [
            {"entry_id": e.get("entry_id"), "domain": e.get("domain"),
             "title": e.get("title"), "state": e.get("state")}
            for e in ws_results[0]["result"]
            if e.get("state") != "loaded"
        ]
    if ws_error(ws_results[1]):
        unreadable.append("repairs")
    else:
        repairs = [
            {"issue_id": i.get("issue_id"), "domain": i.get("domain"),
             "severity": i.get("severity"), "title": i.get("translation_key")}
            for i in ws_results[1]["result"].get("issues", [])
            if not i.get("ignored", False)
        ]

    # --- the summary, computed over everything above --------------------
    #
    # Built BEFORE the filter below touches `groups`. Filtering first and
    # counting afterwards would make the counts shrink with the listing,
    # which is how a checker comes to report "nothing found" about a
    # population it stopped looking at.
    summary = {
        "checked_entities": len(states),
        "unavailable": unavailable_total,
        "unknown": unknown_total,
        "integrations_with_unavailable": sum(
            1 for g in groups.values() if g["unavailable"]),
        "config_entries_not_loaded": len(config_entries),
        "repairs_open": len(repairs),
    }

    # --- the listing ----------------------------------------------------
    listed = sorted(
        (g for g in groups.values()
         if g["unavailable"]
         and (g["oldest_unavailable_hours"] is None
              or g["oldest_unavailable_hours"] >= unavailable_hours)),
        key=lambda g: (-(g["oldest_unavailable_hours"] or 0), g["platform"]),
    )

    out = envelope(listed, key="integrations", limit=limit, offset=offset,
                   offset_paginated=True)
    out["summary"] = summary
    out["hours_are_a_lower_bound"] = True
    out["config_entries"] = config_entries
    out["repairs"] = repairs
    out["sections_unavailable"] = unreadable

    if unreadable:
        warning = (
            "This report is INCOMPLETE: "
            + ", ".join(unreadable)
            + " could not be read on this connection. Those checks did not "
            "pass - they did not run."
        )
        out["note"] = f"{warning} {out['note']}" if out.get("note") else warning

    return out
