"""The category of each automation, script, scene and helper in the list
tools, and filtering by it.

A filter by a category that does not exist, or that could not be read,
must be an error - never an empty list, which reads as "nothing is in it".
"""
import pytest

from tools.automations import list_automations
from tools.helpers import list_helpers
from tools.scenes import list_scenes
from tools.scripts import list_scripts


@pytest.fixture
def home(fake_ha):
    fake_ha.states += [
        {"entity_id": "script.wash", "state": "off", "attributes": {"friendly_name": "Wash"}},
        {"entity_id": "script.dry", "state": "off", "attributes": {"friendly_name": "Dry"}},
        {"entity_id": "scene.night", "state": "x", "attributes": {"friendly_name": "Night"}},
        {"entity_id": "timer.lavatrice", "state": "idle",
         "attributes": {"friendly_name": "Timer lavatrice"}},
        {"entity_id": "input_boolean.guest", "state": "off",
         "attributes": {"friendly_name": "Guest"}},
        {"entity_id": "sensor.wm_energy_today", "state": "0.4",
         "attributes": {"friendly_name": "WM energy today"}},
    ]
    fake_ha.registry += [
        {"entity_id": "script.wash", "platform": "script", "labels": []},
        {"entity_id": "script.dry", "platform": "script", "labels": []},
        {"entity_id": "scene.night", "platform": "homeassistant", "labels": []},
        {"entity_id": "timer.lavatrice", "platform": "timer", "labels": []},
        {"entity_id": "input_boolean.guest", "platform": "input_boolean", "labels": []},
        {"entity_id": "sensor.wm_energy_today", "platform": "utility_meter", "labels": []},
    ]

    def mk(scope, name):
        return fake_ha.ws({"type": "config/category_registry/create", "scope": scope,
                           "name": name})["result"]["category_id"]

    def assign(entity_id, scope, cid):
        fake_ha.ws({"type": "config/entity_registry/update", "entity_id": entity_id,
                    "categories": {scope: cid}})

    ids = {
        "auto": mk("automation", "Elettrodomestici"),
        "script": mk("script", "Elettrodomestici"),
        "scene": mk("scene", "Notte"),
        "helpers": mk("helpers", "Elettrodomestici"),
    }
    assign("automation.morning", "automation", ids["auto"])
    assign("script.wash", "script", ids["script"])
    assign("scene.night", "scene", ids["scene"])
    assign("timer.lavatrice", "helpers", ids["helpers"])
    assign("sensor.wm_energy_today", "helpers", ids["helpers"])
    return ids


def _by_id(rows):
    return {r["entity_id"]: r for r in rows}


def test_rows_carry_their_category_name(home):
    autos = _by_id(list_automations()["automations"])
    scripts = _by_id(list_scripts()["scripts"])
    scenes = _by_id(list_scenes()["scenes"])
    helpers = _by_id(list_helpers()["helpers"])

    assert autos["automation.morning"]["category"] == "Elettrodomestici"
    assert autos["automation.nas_shutdown"]["category"] is None
    assert scripts["script.wash"]["category"] == "Elettrodomestici"
    assert scripts["script.dry"]["category"] is None
    assert scenes["scene.night"]["category"] == "Notte"
    assert helpers["timer.lavatrice"]["category"] == "Elettrodomestici"


@pytest.mark.parametrize("tool,key,expected", [
    (list_automations, "automations", ["automation.morning"]),
    (list_scripts, "scripts", ["script.wash"]),
])
def test_filter_by_name(home, tool, key, expected):
    r = tool(category="elettrodomestici")

    assert [row["entity_id"] for row in r[key]] == expected


def test_filter_by_id(home):
    r = list_scenes(category=home["scene"])

    assert [row["entity_id"] for row in r["scenes"]] == ["scene.night"]


def test_an_unknown_category_is_an_error_not_an_empty_list(home):
    r = list_scripts(category="Inesistente")

    assert r["error"] == "category_not_found"


def test_the_filter_resolves_in_the_tools_own_scope(home):
    """'Notte' exists only for scenes - an automation filter must not
    quietly return nothing, it must say where the name lives."""
    r = list_automations(category="Notte")

    assert r["error"] == "category_not_found"
    assert r["exists_in_scopes"] == ["scene"]


def test_an_unreadable_category_registry_degrades_without_a_filter(home, fake_ha):
    fake_ha.fail_ws("config/category_registry/list", code="unknown_error", message="boom")

    r = list_scripts()

    assert all(row["category"] is None for row in r["scripts"])
    assert "categor" in r["note"]


def test_an_unreadable_category_registry_is_an_error_with_a_filter(home, fake_ha):
    fake_ha.fail_ws("config/category_registry/list", code="unknown_error", message="boom")

    assert list_scripts(category="Elettrodomestici")["error"] == "unknown_error"


def test_list_helpers_filtered_by_category_includes_helper_integrations(home):
    r = list_helpers(category="Elettrodomestici")

    assert sorted(row["entity_id"] for row in r["helpers"]) == [
        "sensor.wm_energy_today", "timer.lavatrice"]
    row = _by_id(r["helpers"])["sensor.wm_energy_today"]
    assert row["domain"] == "sensor"
    assert row["state"] == "0.4"
    assert row["category"] == "Elettrodomestici"


def test_list_helpers_without_a_filter_is_unchanged(home):
    ids = {row["entity_id"] for row in list_helpers()["helpers"]}

    assert "sensor.wm_energy_today" not in ids
    assert {"timer.lavatrice", "input_boolean.guest"} <= ids


def test_list_automations_keeps_its_truncation_note_with_categories(home, fake_ha):
    r = list_automations(limit=1)

    assert "1 of 2" in r["note"]


# ---- final review ---------------------------------------------------------------

def test_a_category_read_timeout_does_not_break_an_unfiltered_list(home, monkeypatch):
    """list_scripts/scenes/helpers were REST-only before categories; a
    timeout on the optional category read must not fail the listing."""
    from tools import categories
    from tools._base import WsTimeout

    def boom(*a, **k):
        raise WsTimeout(command_types=("config/category_registry/list",))

    monkeypatch.setattr(categories, "_ws", boom)
    monkeypatch.setattr(categories, "_ws_multi", boom)

    r = list_scripts()

    assert {row["entity_id"] for row in r["scripts"]} >= {"script.wash", "script.dry"}
    assert "categor" in r["note"]


def test_a_category_read_timeout_with_a_filter_still_raises(home, monkeypatch):
    from tools import categories
    from tools._base import WsTimeout

    def boom(*a, **k):
        raise WsTimeout(command_types=("config/category_registry/list",))

    monkeypatch.setattr(categories, "_ws", boom)
    monkeypatch.setattr(categories, "_ws_multi", boom)

    with pytest.raises(WsTimeout):
        list_scripts(category="Elettrodomestici")


def test_list_helpers_by_category_includes_disabled_helpers(home, fake_ha):
    row = next(e for e in fake_ha.registry if e["entity_id"] == "timer.lavatrice")
    row["disabled_by"] = "user"
    fake_ha.states = [s for s in fake_ha.states if s["entity_id"] != "timer.lavatrice"]

    r = list_helpers(category="Elettrodomestici")

    rows = _by_id(r["helpers"])
    assert rows["timer.lavatrice"]["state"] is None
    assert rows["timer.lavatrice"]["disabled_by"] == "user"
