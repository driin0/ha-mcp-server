import pytest

from tools._base import envelope, error, ws_error


def test_wraps_a_collection_under_a_named_key():
    result = envelope([{"a": 1}, {"a": 2}], key="lights")

    assert result == {"total": 2, "returned": 2, "offset": 0,
                      "lights": [{"a": 1}, {"a": 2}]}


def test_empty_collection_says_so_instead_of_vanishing():
    result = envelope([], key="repairs")

    assert result["total"] == 0
    assert result["repairs"] == []
    assert result["note"] == "no repairs found"


def test_truncation_is_announced():
    rows = [{"n": n} for n in range(140)]

    result = envelope(rows, key="automations", limit=3)

    assert result["total"] == 140
    assert result["returned"] == 3
    assert [row["n"] for row in result["automations"]] == [0, 1, 2]
    assert "3 of 140" in result["note"]


def test_offset_pages_without_losing_the_total():
    rows = [{"n": n} for n in range(10)]

    result = envelope(rows, key="rows", limit=3, offset=6)

    assert result["total"] == 10
    assert result["offset"] == 6
    assert [row["n"] for row in result["rows"]] == [6, 7, 8]


def test_limit_zero_means_no_limit():
    rows = [{"n": n} for n in range(10)]

    result = envelope(rows, key="rows", limit=0)

    assert result["returned"] == 10
    assert "note" not in result


def test_explicit_total_means_the_caller_already_paginated():
    """Home Assistant sometimes applies the limit server-side."""
    result = envelope([{"n": 1}], key="rows", total=99)

    assert result["total"] == 99
    assert result["returned"] == 1
    assert "1 of 99" in result["note"]


def test_a_caller_supplied_note_wins():
    result = envelope([], key="rows", note="the Scheduler component is absent")

    assert result["note"] == "the Scheduler component is absent"


def test_error_has_no_collection_key():
    result = error("not_found", "No such automation")

    assert result == {"error": "not_found", "detail": "No such automation"}


def test_error_carries_extra_context():
    result = error("not_found", "gone", entity_id="automation.x")

    assert result["entity_id"] == "automation.x"


def test_ws_error_passes_a_successful_frame_through():
    frame = {"id": 1, "type": "result", "success": True, "result": [1, 2]}

    assert ws_error(frame) is None


def test_ws_error_reports_a_home_assistant_failure():
    frame = {"id": 1, "type": "result", "success": False,
             "error": {"code": "not_found", "message": "Unknown command"}}

    assert ws_error(frame) == {"error": "not_found", "detail": "Unknown command"}


def test_ws_error_reports_a_transport_failure():
    """_base._ws returns this shape when authentication fails."""
    frame = {"error": "Auth failed: {'type': 'auth_invalid'}"}

    result = ws_error(frame)

    assert result["error"] == "websocket_error"
    assert "auth_invalid" in result["detail"]


def test_ws_error_reports_a_missing_frame():
    """ws_error defends against a malformed or absent response."""
    result = ws_error(None)

    assert result["error"] == "bad_response"


def test_the_last_page_is_not_reported_as_truncated():
    rows = [{"n": n} for n in range(10)]

    result = envelope(rows, key="rows", limit=5, offset=8)

    assert result["returned"] == 2
    assert result["total"] == 10
    assert "note" not in result


def test_offset_past_the_end_is_announced():
    """An offset beyond the collection used to fall through both note
    branches: count != 0 skips "no {key} found", and offset + len(page) <
    count is false once page is empty (offset + 0 is not less than count).
    The result was an empty page with no note at all - indistinguishable
    from a bug rather than a caller-supplied offset with nothing left."""
    rows = [{"n": n} for n in range(3)]

    result = envelope(rows, key="rows", offset=10)

    assert result["total"] == 3
    assert result["returned"] == 0
    assert result["rows"] == []
    assert "note" in result
    assert "offset 10" in result["note"]


def test_total_and_limit_together_are_refused():
    with pytest.raises(ValueError):
        envelope([{"n": 1}], key="rows", total=99, limit=10)


def test_success_without_result_key_is_an_error():
    frame = {"id": 1, "type": "result", "success": True}

    result = ws_error(frame)

    assert result["error"] == "bad_response"
    assert "result" in result["detail"]
