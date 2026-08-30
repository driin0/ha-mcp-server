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


# At most this many entity ids per integration. The COUNT is the finding -
# "40 of this platform's entities are down" - and the ids are there to act
# on, not to enumerate. Unbounded they are the 678 KB list_orphan_entities
# shipped in 2.0.0: a correct answer that never reached the caller because
# it was too large to deliver. `unavailable` always carries the full count.
_SAMPLE_SIZE = 10


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


# How urgent an integration's trouble is, most urgent first. The verdict is
# Home Assistant's own - the config entry's state - not a guess made here
# from how many entities are involved.
#
# Size is how loud a fault is, not how wrong it is. Measured on a real
# instance: ibeacon had 1424 of 1424 entities unavailable with a healthy
# entry, which for beacons out of range is their ordinary resting state,
# and ranking by count put it above reolink 32/32 in setup_retry - an
# actual camera that had stopped working.
#
# setup_error before not_loaded before setup_retry, by decreasing chance of
# fixing itself: a retry may recover unattended, setup_error is typically
# expired authentication and stays broken until a person acts.
_ENTRY_TROUBLE = {"setup_error": 0, "migration_error": 0,
                  "not_loaded": 1, "setup_retry": 2}


def _tier(group: dict) -> int:
    """0-2: Home Assistant says the entry is in trouble.
    3: every entity is down but Home Assistant considers the entry fine.
    4: only some entities are down."""
    trouble = _ENTRY_TROUBLE.get(group["config_entry_state"])
    if trouble is not None:
        return trouble
    return 3 if group["all_unavailable"] else 4


@mcp.tool()
def instance_health(unavailable_hours: int = 0, limit: int = 20,
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
      unavailable entity has been that way for at least this many hours.
      **Defaults to 0 - no filtering - because the age it filters on is
      largely fiction.** See `oldest_unavailable_hours` below. This narrows
      the LISTING only; `summary` always counts the whole population, and
      `excluded_below_threshold` reports how many rows were held back, so a
      filter can never make the instance look healthier than it is. An
      integration whose age cannot be read is listed whatever this is set
      to: unknown is not recent.
    limit, offset: page over the listed integrations. Bounded by default
      like every other collection here - a real instance has thousands of
      registry entries, and an unbounded aggregate over them is a response
      that never arrives.

    Returns: {total, returned, offset, note?, summary, hours_are_a_lower_bound,
    integrations: [{platform, unavailable, unknown, total, all_unavailable,
    config_entry_state, oldest_unavailable_hours, sample_entity_ids}],
    config_entries: [...], repairs: [...], sections_unavailable: [...],
    excluded_below_threshold}. Integrations are sorted with the wholly-down
    ones first, then by how many entities are down.

    `all_unavailable` - every entity this integration owns has no state - is
    the signal that carries, and it is the incident's own signature: not
    "some devices are flaky" but "this integration is not working".
    `config_entry_state` joins Home Assistant's own verdict onto the same
    row, matching the entry's domain to the entity's platform, so the two
    halves of the diagnosis arrive together instead of in two sections a
    reader has to cross-reference by hand. It is null only when the
    integration has no config entry at all - a helper platform such as
    `automation` or `group` - so "Home Assistant considers this healthy"
    and "this question does not apply" stay distinguishable.

    Rows are ordered by Home Assistant's verdict, not by size: an entry in
    trouble first (`setup_error`, then `not_loaded`, then `setup_retry`),
    then everything-down-but-loaded, then partial outages, and within each
    by how many entities are affected. An earlier version called
    everything-down-with-a-loaded-entry the case to look at hardest. Real
    data says otherwise: it is usually an integration whose entities are
    transient by design - 1424 of 1424 ibeacon entities unavailable is
    beacons out of range, not a fault - and ranking it first buried the
    cameras and the NAS that had actually stopped.

    `sample_entity_ids` is at most ten ids per integration, not the whole
    set: the count in `unavailable` is the finding, and the ids are there
    to act on. Every part of this response is bounded, including the parts
    inside a row - an unbounded row is how a correct answer comes to be too
    large to deliver.

    `oldest_unavailable_hours` is a LOWER BOUND and usually a useless one.
    It derives from `last_changed`, which a Home Assistant restart resets -
    and measured on a real instance three hours after a restart, all 29
    integrations holding 1857 unavailable entities reported the same 3.1
    hours. It collapses to the instance's uptime, so do not rank or filter
    on it and do not read it as "how long this has been broken". It is kept
    because on an instance with a long uptime it does carry the signal, and
    dropping it would remove the only direct evidence of duration there is.
    `hours_are_a_lower_bound` is in the response so a reader cannot miss
    this, and the value is null when no age could be read at all.

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

    The config-entry read uses `config_entries/get`, the command
    list_config_entries() has been sending against real instances since
    1.0 - not `config_entries/list`, which appears in this codebase only
    as an example inside a docstring and was never sent by anything.

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
            "oldest_unavailable_hours": None, "sample_entity_ids": [],
            "all_unavailable": False, "config_entry_state": None,
        })
        group["total"] += 1

        value = row.get("state")
        if value == "unavailable":
            group["unavailable"] += 1
            unavailable_total += 1
            if len(group["sample_entity_ids"]) < _SAMPLE_SIZE:
                group["sample_entity_ids"].append(entity_id)
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
        {"type": "config_entries/get"},
        {"type": "repairs/list_issues"},
    ])
    entry_state: dict[str, str] = {}
    if ws_error(ws_results[0]):
        unreadable.append("config_entries")
    else:
        all_entries = [
            {"entry_id": e.get("entry_id"), "domain": e.get("domain"),
             "title": e.get("title"), "state": e.get("state")}
            for e in ws_results[0]["result"]
        ]
        # Built from EVERY entry, before the filter below. Built from the
        # filtered list instead, a loaded entry came back as None - the same
        # value a platform with no entry at all gets, like automation or
        # group. One field cannot mean both "Home Assistant considers this
        # healthy" and "this question does not apply here": a reader cannot
        # act on a difference it can no longer see.
        entry_state = {e["domain"]: e["state"] for e in all_entries if e["domain"]}
        config_entries = [e for e in all_entries if e["state"] != "loaded"]
    if ws_error(ws_results[1]):
        unreadable.append("repairs")
    else:
        repairs = [
            {"issue_id": i.get("issue_id"), "domain": i.get("domain"),
             "severity": i.get("severity"), "title": i.get("translation_key")}
            for i in ws_results[1]["result"].get("issues", [])
            if not i.get("ignored", False)
        ]

    # --- join the two halves of the diagnosis onto one row --------------
    #
    # "Every entity down" says something is wrong; the config entry's own
    # state says what. Home Assistant keys entries by domain and the entity
    # registry keys entities by platform; for an integration that owns its
    # own entities those are the same string, which is what this join
    # assumes. Only a platform with no entry at all keeps None.
    for group in groups.values():
        group["all_unavailable"] = (
            group["unavailable"] > 0 and group["unavailable"] == group["total"])
        group["config_entry_state"] = entry_state.get(group["platform"])

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
        "integrations_entirely_unavailable": sum(
            1 for g in groups.values() if g["all_unavailable"]),
        "config_entries_not_loaded": len(config_entries),
        "repairs_open": len(repairs),
    }

    # --- the listing ----------------------------------------------------
    affected = [g for g in groups.values() if g["unavailable"]]
    listed = sorted(
        (g for g in affected
         if g["oldest_unavailable_hours"] is None
         or g["oldest_unavailable_hours"] >= unavailable_hours),
        # Home Assistant's verdict first (see _tier), then size within it.
        # Deliberately NOT by age: see oldest_unavailable_hours in the
        # docstring - ranking on a number that is the same for every row is
        # ranking on nothing.
        key=lambda g: (_tier(g), -g["unavailable"], g["platform"]),
    )
    excluded = len(affected) - len(listed)

    out = envelope(listed, key="integrations", limit=limit, offset=offset,
                   offset_paginated=True)
    out["excluded_below_threshold"] = excluded
    out["summary"] = summary
    out["hours_are_a_lower_bound"] = True
    out["config_entries"] = config_entries
    out["repairs"] = repairs
    out["sections_unavailable"] = unreadable

    if excluded:
        hidden = (
            f"{excluded} integration(s) with unavailable entities are not "
            f"listed: their longest outage reads as shorter than "
            f"unavailable_hours={unavailable_hours}. That age resets on a "
            "Home Assistant restart, so this filter hides real faults on a "
            "recently restarted instance - `summary` still counts them all."
        )
        out["note"] = f"{hidden} {out['note']}" if out.get("note") else hidden

    if unreadable:
        warning = (
            "This report is INCOMPLETE: "
            + ", ".join(unreadable)
            + " could not be read on this connection. Those checks did not "
            "pass - they did not run."
        )
        out["note"] = f"{warning} {out['note']}" if out.get("note") else warning

    return out
