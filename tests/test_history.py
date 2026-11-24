"""get_history() returns the window it was asked for.

Home Assistant's GET /api/history/period/<start> answers for start + 1 day
when no end_time is sent - not up to now. get_history() used not to send
one, so any window longer than 24 h returned only its oldest day: stale
rows for a little over 24 h, nothing at all once the window began before
recorder retention (hours=2160 on casa, 2026-11-24). tests/fakeha.py
reproduces that default, so these tests fail against the old code.
"""
from datetime import datetime, timedelta, timezone

from tools.diagnostics import get_history


def _rows(fake_ha, entity_id, hours_ago_states):
    now = datetime.now(timezone.utc)
    fake_ha.history[entity_id] = [
        {"entity_id": entity_id, "state": state,
         "last_changed": (now - timedelta(hours=h)).isoformat()}
        for h, state in hours_ago_states
    ]
    return now


def test_a_window_longer_than_a_day_includes_recent_rows(fake_ha):
    _rows(fake_ha, "sensor.x", [(70, "a"), (30, "b"), (2, "c")])

    r = get_history("sensor.x", hours=72)

    assert [row["state"] for row in r["history"]] == ["a", "b", "c"]


def test_a_window_reaching_past_retention_returns_what_exists(fake_ha):
    now = _rows(fake_ha, "sensor.x", [(200, "old"), (5, "new")])
    fake_ha.history_oldest = now - timedelta(hours=240)

    r = get_history("sensor.x", hours=2160)

    assert r["total"] == 2


def test_end_time_is_always_sent(fake_ha):
    _rows(fake_ha, "sensor.x", [(1, "a")])

    get_history("sensor.x", hours=48)

    sent = [c for c in fake_ha.rest_calls if "/api/history/period/" in c.url.path]
    assert "end_time" in sent[-1].url.params


def test_explicit_start_end_overrides_hours(fake_ha):
    now = _rows(fake_ha, "sensor.x", [(100, "a"), (50, "b"), (10, "c")])

    r = get_history("sensor.x", hours=1,
                    start=(now - timedelta(hours=60)).isoformat(),
                    end=(now - timedelta(hours=20)).isoformat())

    assert [row["state"] for row in r["history"]] == ["b"]


def test_naive_start_and_end_are_read_as_utc(fake_ha):
    now = _rows(fake_ha, "sensor.x", [(50, "b")])
    naive = (now - timedelta(hours=60)).replace(tzinfo=None).isoformat()

    r = get_history("sensor.x", start=naive)

    assert r["window"]["start"].endswith("+00:00")
    assert [row["state"] for row in r["history"]] == ["b"]


def test_end_in_the_future_is_clamped_to_now(fake_ha):
    now = _rows(fake_ha, "sensor.x", [(1, "a")])

    r = get_history("sensor.x", end=(now + timedelta(days=3)).isoformat())

    assert datetime.fromisoformat(r["window"]["end"]) <= datetime.now(timezone.utc)


def test_start_not_before_end_is_refused(fake_ha):
    t = datetime.now(timezone.utc).isoformat()

    r = get_history("sensor.x", start=t, end=t)

    assert r["error"] == "invalid_window"
    assert not [c for c in fake_ha.rest_calls if "/api/history/" in c.url.path]


def test_an_unparseable_start_is_refused(fake_ha):
    assert get_history("sensor.x", start="yesterday")["error"] == "invalid_window"


def test_non_positive_hours_is_refused(fake_ha):
    assert get_history("sensor.x", hours=0)["error"] == "invalid_window"
    assert get_history("sensor.x", hours=-5)["error"] == "invalid_window"


def test_limit_keeps_the_most_recent_rows(fake_ha):
    _rows(fake_ha, "sensor.x", [(10 - i, f"s{i}") for i in range(10)])

    r = get_history("sensor.x", limit=3)

    assert r["total"] == 10
    assert r["returned"] == 3
    assert [row["state"] for row in r["history"]] == ["s7", "s8", "s9"]
    assert "most recent" in r["note"]


def test_limit_zero_means_no_limit(fake_ha):
    _rows(fake_ha, "sensor.x", [(10 - i, f"s{i}") for i in range(10)])

    assert get_history("sensor.x", limit=0)["returned"] == 10


def test_entity_id_survives_truncation(fake_ha):
    _rows(fake_ha, "sensor.x", [(3, "a"), (2, "b"), (1, "c")])

    assert get_history("sensor.x", limit=1)["entity_id"] == "sensor.x"


def test_empty_window_note_names_the_bounds(fake_ha):
    r = get_history("sensor.x", hours=1)

    assert r["total"] == 0
    assert r["window"]["start"] in r["note"]
    assert r["window"]["end"] in r["note"]
