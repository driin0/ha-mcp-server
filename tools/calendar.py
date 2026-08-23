import httpx
from datetime import datetime, timezone, timedelta

from tools._base import mcp, HA_URL, HEADERS, _ws, confirm_entity_exists, envelope


@mcp.tool()
def list_calendars() -> dict:
    """
    List all calendar entities.

    Returns: {total, returned, offset, note?, calendars: [...]}

    total 0 on an instance with no calendars: Home Assistant only registers
    /api/calendars once the calendar integration has loaded, so its absence
    means "none", not a failure.
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/calendars", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return envelope([], key="calendars")
        r.raise_for_status()
        return envelope(r.json(), key="calendars")


@mcp.tool()
def get_calendar_events(entity_id: str, start: str = "", end: str = "") -> dict:
    """
    Get events from a calendar entity.

    entity_id: e.g. 'calendar.home'
    start: ISO8601 datetime (default: now)
    end: ISO8601 datetime (default: 7 days from now)

    Returns: {total, returned, offset, note?, events: [...]}
    """
    now = datetime.now(timezone.utc)
    start_dt = start or now.isoformat()
    end_dt = end or (now + timedelta(days=7)).isoformat()
    with httpx.Client() as client:
        r = client.get(
            f"{HA_URL}/api/calendars/{entity_id}",
            headers=HEADERS,
            params={"start": start_dt, "end": end_dt},
            timeout=10,
        )
        r.raise_for_status()
        return envelope(r.json(), key="events")


@mcp.tool()
def add_calendar_event(
    entity_id: str,
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    all_day: bool = False,
) -> dict:
    """
    Create a new event on a calendar entity.

    entity_id:   e.g. 'calendar.home'
    summary:     event title
    start:       ISO8601 datetime e.g. '2026-04-15T10:00:00+02:00' (or 'YYYY-MM-DD' if all_day)
    end:         ISO8601 datetime e.g. '2026-04-15T11:00:00+02:00' (or 'YYYY-MM-DD' if all_day)
    description: optional notes
    location:    optional location string
    all_day:     if True uses date-only format (start/end as 'YYYY-MM-DD')

    Returns: {entity_id, summary, start, end, verified} on a call Home
    Assistant accepted, or {error: "entity_not_found", ...} when
    entity_id has no state at all. `verified` is true only when an event
    with this summary starting on this date is found by reading the
    calendar back (/api/calendars/<entity_id>) for a window around `start`
    — not merely that the create call returned 2xx. A search window this
    narrow can miss a genuine event moved by a recurrence rule or a
    timezone conversion on Home Assistant's side; `verified: false` there
    means "not found in that window", not "definitely not created" — check
    get_calendar_events() with a wider window before assuming the write
    failed.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    payload: dict = {"entity_id": entity_id, "summary": summary}
    if all_day:
        payload["start_date"] = start[:10]
        payload["end_date"] = end[:10]
    else:
        payload["start_date_time"] = start
        payload["end_date_time"] = end
    if description:
        payload["description"] = description
    if location:
        payload["location"] = location

    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/calendar/create_event",
            headers=HEADERS,
            json=payload,
            timeout=10,
        )
        r.raise_for_status()

        window_start = start[:10] if all_day else start
        window_end = end[:10] if all_day else end
        check = client.get(
            f"{HA_URL}/api/calendars/{entity_id}",
            headers=HEADERS,
            params={"start": window_start, "end": window_end},
            timeout=10,
        )
        check.raise_for_status()
        found = any(e.get("summary") == summary for e in check.json())

    return {
        "entity_id": entity_id,
        "summary": summary,
        "start": start,
        "end": end,
        "verified": found,
    }
