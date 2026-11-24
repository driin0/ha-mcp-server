"""Category tools (tools/categories.py) against the fake's category registry.

Home Assistant keys categories by scope - automation, script, scene,
helpers - and the same name has a different id in each scope. It also
stores any category id it is sent on an entity without checking it exists
(see tests/test_fakeha_categories.py), so resolution and validation are
this module's job.
"""
from tools.categories import (
    _resolve_category, create_category, delete_category, list_categories,
    update_category,
)


def _mk(fake_ha, scope, name, icon=None):
    r = fake_ha.ws({"type": "config/category_registry/create", "scope": scope,
                    "name": name, "icon": icon})
    return r["result"]["category_id"]


def _assign(fake_ha, entity_id, scope, category_id):
    fake_ha.ws({"type": "config/entity_registry/update", "entity_id": entity_id,
                "categories": {scope: category_id}})


def _writes(fake_ha, kind):
    return [m for m in fake_ha.ws_calls if m["type"] == kind]


# ---- list -------------------------------------------------------------------

def test_an_unknown_scope_is_refused(fake_ha):
    r = list_categories("helper")

    assert r["error"] == "invalid_scope"
    assert "helpers" in r["detail"]


def test_list_is_sorted_by_name(fake_ha):
    _mk(fake_ha, "automation", "Zeta")
    _mk(fake_ha, "automation", "alfa", "mdi:a")

    r = list_categories("automation")

    assert [c["name"] for c in r["categories"]] == ["alfa", "Zeta"]
    assert r["categories"][0] == {"category_id": r["categories"][0]["category_id"],
                                  "name": "alfa", "icon": "mdi:a"}


def test_list_with_counts_counts_assigned_entities(fake_ha):
    cid = _mk(fake_ha, "automation", "A")
    _assign(fake_ha, "automation.morning", "automation", cid)

    r = list_categories("automation", with_counts=True)

    assert r["categories"][0]["entity_count"] == 1


def test_a_failed_registry_read_is_an_error_not_an_empty_list(fake_ha):
    fake_ha.fail_ws("config/category_registry/list", code="unknown_error", message="boom")

    assert list_categories("scene")["error"] == "unknown_error"


# ---- create / update / delete ---------------------------------------------

def test_create_returns_the_entry_with_its_scope(fake_ha):
    r = create_category("helpers", "Elettrodomestici", "mdi:washing-machine")

    assert r["scope"] == "helpers"
    assert r["name"] == "Elettrodomestici"
    assert r["icon"] == "mdi:washing-machine"
    assert r["category_id"]


def test_create_without_icon_sends_no_icon(fake_ha):
    create_category("scene", "A")

    assert "icon" not in _writes(fake_ha, "config/category_registry/create")[-1]


def test_a_duplicate_name_reports_home_assistants_message(fake_ha):
    _mk(fake_ha, "script", "Luci")

    r = create_category("script", "luci")

    assert r["error"] == "invalid_info"
    assert "already in use" in r["detail"]


def test_update_by_name_returns_old_and_new(fake_ha):
    _mk(fake_ha, "scene", "Vecchio", "mdi:a")

    r = update_category("scene", "vecchio", name="Nuovo")

    assert r["old"] == {"name": "Vecchio", "icon": "mdi:a"}
    assert r["new"] == {"name": "Nuovo", "icon": "mdi:a"}


def test_update_by_id(fake_ha):
    cid = _mk(fake_ha, "scene", "A")

    assert update_category("scene", cid, icon="mdi:b")["new"]["icon"] == "mdi:b"


def test_an_empty_icon_clears_it(fake_ha):
    _mk(fake_ha, "scene", "A", "mdi:a")

    update_category("scene", "A", icon="")

    assert _writes(fake_ha, "config/category_registry/update")[-1]["icon"] is None


def test_update_with_nothing_to_change_is_refused(fake_ha):
    _mk(fake_ha, "scene", "A")

    assert update_category("scene", "A")["error"] == "nothing_to_update"
    assert not _writes(fake_ha, "config/category_registry/update")


def test_delete_reports_which_entities_lost_the_category(fake_ha):
    cid = _mk(fake_ha, "automation", "A")
    _assign(fake_ha, "automation.morning", "automation", cid)

    r = delete_category("automation", "A")

    assert r["deleted"] is True
    assert r["cleared_from"] == ["automation.morning"]
    row = next(e for e in fake_ha.registry if e["entity_id"] == "automation.morning")
    assert row.get("categories", {}) == {}


# ---- resolution -------------------------------------------------------------

def test_a_name_that_exists_only_in_another_scope_says_where(fake_ha):
    _mk(fake_ha, "automation", "Elettrodomestici")
    _mk(fake_ha, "helpers", "Altro")

    entry, err = _resolve_category("helpers", "Elettrodomestici")

    assert entry is None
    assert err["error"] == "category_not_found"
    assert err["exists_in_scopes"] == ["automation"]
    assert err["available"] == ["Altro"]


def test_an_id_is_matched_before_a_name(fake_ha):
    cid = _mk(fake_ha, "scene", "A")
    _mk(fake_ha, "scene", cid)  # a category whose NAME is the other's id

    entry, err = _resolve_category("scene", cid)

    assert err is None
    assert entry["name"] == "A"


def test_resolution_never_matches_partially(fake_ha):
    _mk(fake_ha, "scene", "Elettrodomestici")

    entry, err = _resolve_category("scene", "Elettro")

    assert entry is None and err["error"] == "category_not_found"


# ---- assignment -------------------------------------------------------------

import pytest  # noqa: E402

from tools.categories import bulk_set_entity_category, set_entity_category  # noqa: E402


@pytest.fixture
def reg(fake_ha):
    """Registry rows for each scope, a helper-integration entity and a
    plain integration entity."""
    fake_ha.registry += [
        {"entity_id": "script.wash", "platform": "script", "labels": []},
        {"entity_id": "scene.night", "platform": "homeassistant", "labels": []},
        {"entity_id": "timer.lavatrice", "platform": "timer", "labels": []},
        {"entity_id": "schedule.wm", "platform": "schedule", "labels": []},
        {"entity_id": "sensor.wm_energy_today", "platform": "utility_meter", "labels": []},
        {"entity_id": "sensor.lavatrice", "platform": "smartthinq_sensors", "labels": []},
    ]
    return fake_ha


def _row(fake_ha, entity_id):
    return next(e for e in fake_ha.registry if e["entity_id"] == entity_id)


@pytest.mark.parametrize("entity_id,scope", [
    ("automation.morning", "automation"),
    ("script.wash", "script"),
    ("scene.night", "scene"),
    ("timer.lavatrice", "helpers"),
    ("schedule.wm", "helpers"),
    ("sensor.wm_energy_today", "helpers"),
])
def test_the_scope_is_derived_from_the_entity(reg, entity_id, scope):
    cid = _mk(reg, scope, "Elettrodomestici")

    r = set_entity_category(entity_id, "Elettrodomestici")

    assert r["scope"] == scope
    assert _row(reg, entity_id)["categories"] == {scope: cid}


def test_an_entity_that_no_category_page_lists_is_refused(reg):
    _mk(reg, "helpers", "Elettrodomestici")

    r = set_entity_category("sensor.lavatrice", "Elettrodomestici")

    assert r["error"] == "not_categorizable"
    assert all(w["entity_id"] != "sensor.lavatrice"
               for w in _writes(reg, "config/entity_registry/update"))


def test_an_entity_missing_from_the_registry_is_refused(reg):
    r = set_entity_category("automation.yaml_only", "x")

    assert r["error"] == "not_in_registry"


def test_set_by_id_reports_old_and_new_by_name(reg):
    a = _mk(reg, "automation", "A")
    b = _mk(reg, "automation", "B")
    _assign(reg, "automation.morning", "automation", a)

    r = set_entity_category("automation.morning", b)

    assert r["old"] == {"category_id": a, "name": "A"}
    assert r["new"] == {"category_id": b, "name": "B"}


def test_an_empty_category_clears_only_that_scope(reg):
    a = _mk(reg, "automation", "A")
    _assign(reg, "automation.morning", "automation", a)
    _assign(reg, "automation.morning", "helpers", "h1")

    r = set_entity_category("automation.morning", "")

    assert r["new"] is None
    assert _writes(reg, "config/entity_registry/update")[-1]["categories"] == {"automation": None}
    assert _row(reg, "automation.morning")["categories"] == {"helpers": "h1"}


def test_an_id_from_another_scope_is_refused_before_anything_is_sent(reg):
    """Home Assistant would store it (see test_fakeha_categories.py) and the
    UI would never show it - the silent failure this module exists to stop."""
    helpers_id = _mk(reg, "helpers", "Elettrodomestici")
    _mk(reg, "automation", "Altro")
    before = len(_writes(reg, "config/entity_registry/update"))

    r = set_entity_category("automation.morning", helpers_id)

    assert r["error"] == "category_not_found"
    assert len(_writes(reg, "config/entity_registry/update")) == before


def test_a_write_that_does_not_land_is_reported(reg):
    _mk(reg, "automation", "A")
    reg.ws_result("config/entity_registry/update",
                  {"entity_entry": {"entity_id": "automation.morning", "categories": {}}})

    r = set_entity_category("automation.morning", "A")

    assert r["error"] == "config_write_unverified"


# ---- bulk -------------------------------------------------------------------

def test_bulk_refuses_more_than_200_without_sending(reg):
    r = bulk_set_entity_category([f"automation.a{i}" for i in range(201)], "x")

    assert r["error"] == "too_many_entities"
    assert not _writes(reg, "config/entity_registry/update")


def test_bulk_by_name_uses_each_scopes_own_id(reg):
    a = _mk(reg, "automation", "Elettrodomestici")
    h = _mk(reg, "helpers", "Elettrodomestici")

    r = bulk_set_entity_category(["automation.morning", "timer.lavatrice"], "Elettrodomestici")

    assert r == {"total": 2, "succeeded": 2, "failed": []}
    assert _row(reg, "automation.morning")["categories"] == {"automation": a}
    assert _row(reg, "timer.lavatrice")["categories"] == {"helpers": h}


def test_bulk_by_id_fails_the_entities_of_other_scopes(reg):
    a = _mk(reg, "automation", "Elettrodomestici")

    r = bulk_set_entity_category(
        ["automation.morning", "timer.lavatrice", "sensor.lavatrice"], a)

    assert r["succeeded"] == 1
    failed = {f["entity_id"]: f["reason"] for f in r["failed"]}
    assert set(failed) == {"timer.lavatrice", "sensor.lavatrice"}
    assert "exists in automation" in failed["timer.lavatrice"]
    assert "not_categorizable" in failed["sensor.lavatrice"]
    sent = {w["entity_id"] for w in _writes(reg, "config/entity_registry/update")}
    assert "timer.lavatrice" not in sent


def test_bulk_clear_with_empty_category(reg):
    a = _mk(reg, "automation", "A")
    _assign(reg, "automation.morning", "automation", a)

    r = bulk_set_entity_category(["automation.morning"], "")

    assert r["succeeded"] == 1
    assert _row(reg, "automation.morning")["categories"] == {}


def test_bulk_transport_failure_is_one_error(reg):
    _mk(reg, "automation", "A")
    reg.fail_ws_transport("config/entity_registry/update")

    r = bulk_set_entity_category(["automation.morning"], "A")

    assert r["error"] == "websocket_error"


# ---- review gate fixes ------------------------------------------------------

def test_bulk_turns_an_unreadable_category_registry_into_one_error(reg):
    """A systemic read failure must not read as N entity-level rejections."""
    reg.fail_ws("config/category_registry/list", code="unknown_command", message="Unknown command.")

    r = bulk_set_entity_category(["automation.morning", "script.wash"], "A")

    assert r["error"] == "unknown_command"


def test_bulk_reports_a_write_that_did_not_land(reg):
    _mk(reg, "automation", "A")
    reg.ws_result("config/entity_registry/update",
                  {"entity_entry": {"entity_id": "automation.morning", "categories": {}}})

    r = bulk_set_entity_category(["automation.morning"], "A")

    assert r["succeeded"] == 0
    assert r["failed"][0]["reason"].startswith("config_write_unverified")


def test_an_id_from_another_scope_names_the_scope_it_belongs_to(reg):
    helpers_id = _mk(reg, "helpers", "Elettrodomestici")

    r = set_entity_category("automation.morning", helpers_id)

    assert r["error"] == "category_not_found"
    assert r["exists_in_scopes"] == ["helpers"]


def test_bulk_rejects_non_string_entity_ids_at_the_schema():
    from tools._base import mcp

    tool = mcp._tool_manager.get_tool("bulk_set_entity_category")
    schema = tool.parameters["properties"]["entity_ids"]

    assert schema.get("items", {}).get("type") == "string"
