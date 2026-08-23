from datetime import datetime, timedelta, timezone

from tools._base import mcp, _ws, _ws_multi, envelope, ws_error


@mcp.tool()
def get_statistics(
    entity_id: str,
    period: str = "hour",
    start_time: str = "",
    end_time: str = "",
) -> dict:
    """
    Get long-term statistics for a sensor entity (energy, temperature, etc.).

    entity_id: e.g. 'sensor.energy_consumption'
    period: 'hour' | 'day' | 'month' | 'week' (default: 'hour')
    start_time: ISO8601 (default: 24h ago)
    end_time: ISO8601 (default: now)

    Returns: {total, returned, note?, statistics: [{start, end, mean, min,
             max, sum, state}]}

    A series is not paginated by offset - if it is too short or too long,
    narrow it with `start_time`/`end_time` instead.
    """
    now = datetime.now(timezone.utc)
    if not start_time:
        start_time = (now - timedelta(hours=24)).isoformat()
    if not end_time:
        end_time = now.isoformat()

    result = _ws({
        "type": "recorder/statistics_during_period",
        "start_time": start_time,
        "end_time": end_time,
        "statistic_ids": [entity_id],
        "period": period,
        "units": {},
    })
    if err := ws_error(result):
        return err
    stats = result["result"].get(entity_id, [])
    out = [
        {
            "start": s.get("start"),
            "end": s.get("end"),
            "mean": s.get("mean"),
            "min": s.get("min"),
            "max": s.get("max"),
            "sum": s.get("sum"),
            "state": s.get("state"),
        }
        for s in stats
    ]
    return envelope(out, key="statistics",
                    note="" if out else "no statistics in the window - widen `start_time`/`end_time`")


@mcp.tool()
def get_statistics_summary(
    entity_ids: list,
    period: str = "day",
    days: int = 7,
) -> dict:
    """
    Get aggregated statistics summary for one or more sensor entities.

    entity_ids: list of entity_ids to summarize, e.g. ['sensor.temp_living_room', 'sensor.temp_bedroom']
    period:     aggregation period — 'hour', 'day' (default), 'week', 'month'
    days:       how many days back to look (default: 7)

    Returns: {total, returned, note?, statistics: [{entity_id, period, days,
             samples, mean, min, max, sum_delta?}]}

    Returns per entity: mean, min, max over the period, plus delta (last - first) for
    cumulative sensors (energy, gas, water). Useful for quick "how did X behave this week?"
    questions without wading through raw hourly data.

    One entity's statistics call failing does not fail the others - that
    entity's row is {entity_id, error, detail} instead of a summary, and one
    with no recorded data reports {entity_id, error: "no_data"}.
    """
    now = datetime.now(timezone.utc)
    start_time = (now - timedelta(days=days)).isoformat()
    end_time = now.isoformat()

    msgs = [
        {
            "type": "recorder/statistics_during_period",
            "start_time": start_time,
            "end_time": end_time,
            "statistic_ids": [eid],
            "period": period,
            "units": {},
        }
        for eid in entity_ids
    ]
    results = _ws_multi(msgs)

    summaries = []
    for eid, res in zip(entity_ids, results):
        if err := ws_error(res):
            summaries.append({"entity_id": eid, **err})
            continue

        stats = res["result"].get(eid, [])
        if not stats:
            summaries.append({"entity_id": eid, "error": "no_data"})
            continue

        means = [s["mean"] for s in stats if s.get("mean") is not None]
        mins  = [s["min"]  for s in stats if s.get("min")  is not None]
        maxs  = [s["max"]  for s in stats if s.get("max")  is not None]
        sums  = [s["sum"]  for s in stats if s.get("sum")  is not None]

        summary: dict = {
            "entity_id": eid,
            "period": period,
            "days": days,
            "samples": len(stats),
            "mean": round(sum(means) / len(means), 2) if means else None,
            "min":  round(min(mins), 2) if mins else None,
            "max":  round(max(maxs), 2) if maxs else None,
        }
        # Delta for cumulative sensors (energy, gas, water)
        if sums:
            summary["sum_delta"] = round(sums[-1] - sums[0], 3)

        summaries.append(summary)

    return envelope(summaries, key="statistics")
