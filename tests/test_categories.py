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
