"""Minor findings from the 2.6.0 reviews, fixed before release - one test each."""
import httpx
import pytest

from tests.dashboard_fixture import install
from tools import addons


# ---- dashboards ----------------------------------------------------------------

@pytest.fixture
def dash(fake_ha):
    return install(fake_ha)


def test_a_recovery_mode_save_refusal_is_not_editable(dash):
    from tools.dashboards import patch_dashboard

    dash.fail_ws("lovelace/config/save", code="error",
                 message="Saving not supported in recovery mode")

    r = patch_dashboard("casa", "views.0.title", "x")

    assert r["error"] == "not_editable"
    assert "recovery mode" in r["detail"]


def test_no_config_found_mentions_recovery_mode_too(dash):
    from tools.dashboards import patch_dashboard

    dash.lovelace["casa"] = None

    r = patch_dashboard("casa", "views.0.title", "x")

    assert r["error"] == "not_found"
    assert "recovery mode" in r["detail"]


def test_outline_flags_template_names_that_cannot_be_addressed(dash):
    from tools.dashboards import get_dashboard_outline

    dash.lovelace["casa"]["button_card_templates"]["a.b"] = {"x": 1}

    o = get_dashboard_outline("casa")

    assert o["unaddressable"] == ["button_card_templates.a.b"]


def test_a_nested_match_also_shows_its_sections_cards(dash):
    from tools.dashboards import find_dashboard_cards

    m = find_dashboard_cards("casa", entity="sensor.temperatura")["matches"][0]

    assert [s["type"] for s in m["section_cards"]] == [
        "heading", "tile", "clock", "custom:scheduler-card", "vertical-stack"]


def test_a_top_level_match_has_no_separate_section_cards(dash):
    from tools.dashboards import find_dashboard_cards

    m = find_dashboard_cards("casa", entity="sensor.lavatrice")["matches"][0]

    assert "section_cards" not in m


# ---- automations: read-back timeout after a landed write -----------------------

def test_patch_automation_read_back_timeout_says_the_write_landed(fake_ha, monkeypatch):
    from tools import automations

    real = automations._fetch_config

    def fetch(automation_id, slug, client):
        if any(c.method == "POST" for c in fake_ha.rest_calls):
            raise httpx.ReadTimeout("timed out")
        return real(automation_id, slug, client)

    monkeypatch.setattr(automations, "_fetch_config", fetch)

    r = automations.patch_automation("automation.nas_shutdown", "description", "x")

    assert r["error"] == "config_read_failed"
    assert "DID" in r["detail"]


# ---- history / logs ---------------------------------------------------------------

def test_history_docstring_describes_hours_with_end():
    from tools.diagnostics import get_history

    assert "ending at `end`" in get_history.__doc__


@pytest.fixture
def sup(fake_ha, monkeypatch):
    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")
    return fake_ha


def test_core_logs_zero_lines_means_all_and_negative_is_refused(sup):
    from tools.diagnostics import get_core_logs

    sup.core_log = "\n".join(f"2026-11-18 03:12:0{i}.000 INFO (M) [x] m{i}" for i in range(5))

    assert get_core_logs(lines=0)["returned"] == 5
    assert get_core_logs(lines=-1)["error"] == "invalid_lines"


def test_container_lines_are_records_of_their_own():
    from tools.diagnostics import _parse_records

    text = ("2026-11-18 03:12:02.300 ERROR (MainThread) [x] boom\n"
            "Traceback (most recent call last):\n"
            "  File \"a.py\", line 1\n"
            "ValueError: bad\n"
            "s6-rc: info: service legacy-services successfully stopped\n"
            "[s6-init] making user provided files available\n")

    records = _parse_records(text)

    assert len(records) == 3
    assert records[0].endswith("ValueError: bad")
    assert records[1].startswith("s6-rc:")


def test_system_log_grep_does_not_match_none(fake_ha):
    from tools.diagnostics import get_system_log

    fake_ha.system_log = [{"name": None, "message": ["m"], "level": "ERROR", "source": None,
                           "timestamp": 1.0, "exception": "", "count": 1,
                           "first_occurred": 1.0}]

    assert get_system_log(grep="none")["total"] == 0


# ---- helpers / categories / registry ---------------------------------------------

def test_list_helpers_lists_schedules(fake_ha):
    from tools.helpers import list_helpers

    fake_ha.states.append({"entity_id": "schedule.wm", "state": "off",
                           "attributes": {"friendly_name": "WM"}})

    assert "schedule.wm" in {h["entity_id"] for h in list_helpers()["helpers"]}


def test_bulk_category_counts_each_entity_once(fake_ha):
    from tools.categories import bulk_set_entity_category

    fake_ha.ws({"type": "config/category_registry/create", "scope": "automation", "name": "A"})

    r = bulk_set_entity_category(["automation.morning", "automation.morning"], "A")

    assert r["total"] == 1 and r["succeeded"] == 1
    assert len([m for m in fake_ha.ws_calls
                if m["type"] == "config/entity_registry/update"]) == 1


def test_bulk_category_registry_transport_failure_on_the_first_read(fake_ha):
    from tools.categories import bulk_set_entity_category

    fake_ha.fail_ws_transport("config/entity_registry/list")

    assert bulk_set_entity_category(["automation.morning"], "A")["error"] == "websocket_error"


def test_the_fake_drops_unknown_labels_like_home_assistant(fake_ha):
    r = fake_ha.ws({"type": "config/entity_registry/update", "entity_id": "light.kitchen",
                    "labels": ["power", "nope"]})

    assert r["result"]["entity_entry"]["labels"] == ["power"]


def test_entity_registry_shows_categories_by_name(fake_ha):
    from tools.areas import get_entity_registry

    cid = fake_ha.ws({"type": "config/category_registry/create", "scope": "automation",
                      "name": "Elettrodomestici"})["result"]["category_id"]
    fake_ha.ws({"type": "config/entity_registry/update", "entity_id": "automation.morning",
                "categories": {"automation": cid}})

    r = get_entity_registry("automation.morning")

    assert r["categories"] == {"automation": {"category_id": cid, "name": "Elettrodomestici"}}


# ---- pre-release review -------------------------------------------------------

def test_a_button_card_holding_a_card_in_custom_fields_is_a_container(dash):
    from tools.dashboards import patch_dashboard

    dash.lovelace["casa"]["views"][0]["sections"][1]["cards"][1]["custom_fields"] = {
        "inner": {"card": {"type": "tile", "entity": "sensor.x"}}}

    r = patch_dashboard("casa", "views.0.sections.1.cards.1", {"type": "markdown"},
                        expect={"type": "custom:button-card"})

    assert r["error"] == "container_target"
    assert "views.0.sections.1.cards.1.custom_fields.inner.card" in r["detail"]


def _closed(*a, **k):
    from websockets.exceptions import ConnectionClosedError
    raise ConnectionClosedError(None, None)


def test_a_dropped_websocket_does_not_break_an_unfiltered_list(fake_ha, monkeypatch):
    from tools import categories
    from tools.scripts import list_scripts

    fake_ha.states.append({"entity_id": "script.wash", "state": "off",
                           "attributes": {"friendly_name": "Wash"}})
    monkeypatch.setattr(categories, "_ws", _closed)
    monkeypatch.setattr(categories, "_ws_multi", _closed)

    r = list_scripts()

    assert [s["entity_id"] for s in r["scripts"]] == ["script.wash"]
    assert "categor" in r["note"]


def test_a_dropped_websocket_on_the_dashboard_read_back_says_the_write_landed(dash):
    from tools.dashboards import patch_dashboard

    dash.lovelace_mutate_on_read = {3: _closed}

    r = patch_dashboard("casa", "views.0.sections.0.cards.0", {"type": "markdown"}, op="insert")

    assert r["error"] == "config_write_unverified"
    assert "DID happen" in r["detail"]


def test_entity_registry_survives_a_dropped_category_read(fake_ha, monkeypatch):
    from tools import areas

    row = next(e for e in fake_ha.registry if e["entity_id"] == "automation.morning")
    row["categories"] = {"automation": "c1"}
    real = areas._ws

    def ws(msg):
        if msg["type"] == "config/category_registry/list":
            _closed()
        return real(msg)

    monkeypatch.setattr(areas, "_ws", ws)

    r = areas.get_entity_registry("automation.morning")

    assert r["categories"] == {"automation": {"category_id": "c1", "name": None}}


def test_a_history_timeout_says_to_narrow_the_window(fake_ha):
    from tools.diagnostics import get_history

    fake_ha.raise_rest("/api/history/period/", httpx.ReadTimeout("timed out"))

    r = get_history("sensor.power", hours=720)

    assert r["error"] == "history_timeout"
    assert "narrow" in r["detail"]


def test_outline_docstring_mentions_the_header():
    from tools.dashboards import get_dashboard_outline

    assert "header" in get_dashboard_outline.__doc__
