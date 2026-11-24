"""Reading the core log: Supervisor /core/logs (get_core_logs), the
structured system log (get_system_log), and get_error_log's fallback.

Home Assistant 2026.x serves no /api/error_log and writes no
home-assistant.log, so get_error_log() answered only "not available" - and
the question it was asked ("why is smartthinq_sensors loaded but every
entity unavailable?") went unanswered.
"""
import pytest

from tools import addons
from tools.diagnostics import _parse_records, get_core_logs, get_error_log, get_system_log

LOG = """\
2026-11-18 03:12:00.100 INFO (MainThread) [homeassistant.setup] Setting up hue
2026-11-18 03:12:01.200 WARNING (MainThread) [homeassistant.components.sensor] Slow update
2026-11-18 03:12:02.300 ERROR (MainThread) [homeassistant.config_entries] Error setting up entry LG ThinQ
Traceback (most recent call last):
  File "/config/custom_components/smartthinq_sensors/__init__.py", line 120, in async_setup_entry
    raise ConfigEntryNotReady
homeassistant.exceptions.ConfigEntryNotReady
2026-11-18 03:12:03.400 INFO (MainThread) [homeassistant.core] Started
2026-11-18 03:12:04.500 CRITICAL (Recorder) [homeassistant.components.recorder] Database is locked"""


@pytest.fixture
def sup(fake_ha, monkeypatch):
    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")
    fake_ha.core_log = LOG
    return fake_ha


def _core_calls(ha):
    return [c for c in ha.rest_calls if c.url.path == "/core/logs"]


def test_records_keep_their_tracebacks():
    records = _parse_records(LOG)

    assert len(records) == 5
    assert records[2].startswith("2026-11-18 03:12:02.300 ERROR")
    assert "smartthinq_sensors" in records[2]


def test_lines_before_the_first_prefix_are_one_leading_record():
    records = _parse_records("tail of a traceback\nmore\n" + LOG)

    assert records[0] == "tail of a traceback\nmore"
    assert len(records) == 6


def test_grep_returns_the_whole_record_when_only_the_traceback_matches(sup):
    r = get_core_logs(grep="SMARTTHINQ")

    assert r["matched"] == 1
    assert r["log"].startswith("2026-11-18 03:12:02.300 ERROR")
    assert "ConfigEntryNotReady" in r["log"]


def test_level_is_a_minimum(sup):
    r = get_core_logs(level="warning")

    assert r["matched"] == 3
    assert "Setting up hue" not in r["log"]
    assert "Database is locked" in r["log"]


def test_an_unknown_level_is_refused(sup):
    assert get_core_logs(level="verbose")["error"] == "invalid_level"
    assert not _core_calls(sup)


def test_lines_keeps_the_last_matching_records(sup):
    r = get_core_logs(lines=1, level="info")

    assert r["returned"] == 1
    assert r["log"].endswith("Database is locked")
    assert "1 of 5" in r["note"]


def test_the_request_asks_for_no_colours_and_the_scan_size(sup):
    r = get_core_logs(scan=50)

    call = _core_calls(sup)[-1]
    assert "no_colors" in call.url.params
    assert call.url.params["lines"] == "50"
    assert "\x1b" not in r["log"]
    assert r["scanned_lines"] == 9


def test_scan_is_clamped(sup):
    get_core_logs(scan=999999)
    assert _core_calls(sup)[-1].url.params["lines"] == "10000"
    get_core_logs(scan=0)
    assert _core_calls(sup)[-1].url.params["lines"] == "2"


def test_no_match_in_a_full_scan_says_to_look_further_back(sup):
    r = get_core_logs(grep="zigbee", scan=9)

    assert r["matched"] == 0
    assert r["log"] == ""
    assert "scan" in r["note"]


def test_without_a_supervisor_points_at_the_system_log(fake_ha):
    r = get_core_logs()

    assert r["error"] == "supervisor_not_available"
    assert "get_system_log" in r["detail"]


def test_a_supervisor_refusal_is_an_error(sup):
    sup.fail_rest("/core/logs", status=403, message="forbidden")

    assert get_core_logs()["error"] == "supervisor_error"


# ---- system log ---------------------------------------------------------------

ENTRY = {
    "name": "custom_components.smartthinq_sensors",
    "message": ["Error fetching device", "retrying"],
    "level": "ERROR",
    "source": ["custom_components/smartthinq_sensors/wideq.py", 88],
    "timestamp": 1763435522.3,
    "exception": "Traceback ...\nConnectionError",
    "count": 7,
    "first_occurred": 1763435000.0,
}


def test_system_log_entries_are_readable(fake_ha):
    fake_ha.system_log = [ENTRY, {**ENTRY, "name": "homeassistant.core", "level": "WARNING",
                                  "message": ["slow"], "exception": "",
                                  "source": ["homeassistant/core.py", 10]}]

    r = get_system_log()

    e = r["entries"][0]
    assert e["source"] == "custom_components/smartthinq_sensors/wideq.py:88"
    assert e["message"] == "Error fetching device\nretrying"
    assert e["timestamp"].startswith("2025-") or e["timestamp"].startswith("20")
    assert e["timestamp"].endswith("+00:00")
    assert e["count"] == 7
    assert r["total"] == 2


def test_system_log_filters(fake_ha):
    fake_ha.system_log = [ENTRY, {**ENTRY, "name": "homeassistant.core", "level": "WARNING",
                                  "message": ["slow"], "exception": "",
                                  "source": ["homeassistant/core.py", 10]}]

    assert get_system_log(level="error")["total"] == 1
    assert get_system_log(grep="smartthinq")["total"] == 1
    assert get_system_log(grep="ConnectionError")["total"] == 1


def test_a_long_exception_is_truncated(fake_ha):
    fake_ha.system_log = [{**ENTRY, "exception": "\n".join(f"l{i}" for i in range(100))}]

    exc = get_system_log()["entries"][0]["exception"]

    assert exc.splitlines()[-1] == "l99"
    assert len(exc.splitlines()) == 41
    assert "truncated" in exc.splitlines()[0]


def test_system_log_failure_is_an_error(fake_ha):
    fake_ha.fail_ws("system_log/list", code="unauthorized", message="admin required")

    assert get_system_log()["error"] == "unauthorized"


# ---- get_error_log fallback ---------------------------------------------------

def test_error_log_falls_back_to_the_core_log(sup):
    text = get_error_log()

    first, rest = text.split("\n", 1)
    assert "/core/logs" in first
    assert "smartthinq_sensors" in rest


def test_error_log_without_supervisor_names_the_system_log(fake_ha):
    text = get_error_log()

    assert text.startswith("error_log_not_available")
    assert "get_system_log" in text


def test_records_are_found_when_the_prefix_carries_a_utc_offset(sup):
    """The format measured on a real HA OS 2026.11 instance: the timestamp
    has a UTC offset. Without accepting it, not one line was recognised as
    a record start, the whole scan became one record, and grep returned
    691 KB for "smartthinq"."""
    sup.core_log = (
        "2026-11-24 15:26:00.109+01:00 ERROR (MainThread) [homeassistant.config_entries] "
        "Error setting up entry LG ThinQ\n"
        "Traceback (most recent call last):\n"
        '  File "/config/custom_components/smartthinq_sensors/__init__.py", line 120\n'
        "homeassistant.exceptions.ConfigEntryNotReady\n"
        "2026-11-24 15:26:02.308+01:00 WARNING (MainThread) [custom_components.remote_homeassistant] "
        "failed to fetch\n"
        "2026-11-24 15:26:07.797Z INFO (MainThread) [homeassistant.core] Started\n")

    r = get_core_logs(grep="smartthinq")

    assert r["records_scanned"] == 3
    assert r["matched"] == 1
    assert r["log"].startswith("2026-11-24 15:26:00.109+01:00 ERROR")
    assert "remote_homeassistant" not in r["log"]
    assert get_core_logs(level="warning")["matched"] == 2


def test_the_output_is_capped_keeping_the_most_recent_records(sup):
    sup.core_log = "\n".join(
        f"2026-11-24 15:26:{i:02d}.000+01:00 ERROR (MainThread) [x] e{i} " + "y" * 1000
        for i in range(60))

    r = get_core_logs(lines=100, max_chars=10_000, scan=200)

    assert len(r["log"]) <= 10_000
    assert r["log"].rstrip().endswith("y")
    assert "e59" in r["log"] and "e0 " not in r["log"]
    assert r["matched"] == 60 and r["returned"] < 60
    assert "max_chars" in r["note"]
