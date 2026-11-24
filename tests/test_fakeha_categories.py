"""The fake's category registry behaves like Home Assistant's.

Every category guard in tools/categories.py is only as good as this model,
so its HA semantics are pinned here, each against the core source it
copies (read 2026-11-24, core `dev`):
- config/category_registry.py: commands, scope + category_id keys, errors.
- helpers/category_registry.py: names unique per scope, case-insensitive.
- config/entity_registry.py: `categories` merged per scope, None removes,
  the id is NOT validated.
- helpers/entity_registry.py: deleting a category clears it everywhere.
"""


def _ok(r):
    assert r["success"], r
    return r["result"]


def test_create_returns_an_entry_with_a_generated_id(fake_ha):
    entry = _ok(fake_ha.ws({"type": "config/category_registry/create",
                            "scope": "automation", "name": "Elettrodomestici",
                            "icon": "mdi:washing-machine"}))

    assert entry["category_id"]
    assert entry["name"] == "Elettrodomestici"
    assert entry["icon"] == "mdi:washing-machine"
    listed = _ok(fake_ha.ws({"type": "config/category_registry/list", "scope": "automation"}))
    assert [c["category_id"] for c in listed] == [entry["category_id"]]


def test_the_same_name_gets_a_different_id_in_another_scope(fake_ha):
    a = _ok(fake_ha.ws({"type": "config/category_registry/create",
                        "scope": "automation", "name": "X"}))
    h = _ok(fake_ha.ws({"type": "config/category_registry/create",
                        "scope": "helpers", "name": "X"}))

    assert a["category_id"] != h["category_id"]


def test_a_duplicate_name_in_one_scope_is_refused_case_insensitively(fake_ha):
    fake_ha.ws({"type": "config/category_registry/create", "scope": "script", "name": "Luci"})

    r = fake_ha.ws({"type": "config/category_registry/create", "scope": "script", "name": "luci"})

    assert not r["success"]
    assert r["error"]["code"] == "invalid_info"
    assert r["error"]["message"] == "The name 'luci' is already in use"


def test_update_and_delete_of_an_unknown_id_are_refused(fake_ha):
    for kind in ("update", "delete"):
        r = fake_ha.ws({"type": f"config/category_registry/{kind}",
                        "scope": "scene", "category_id": "nope", "name": "n"})
        assert r["error"] == {"code": "invalid_info", "message": "Category ID doesn't exist"}


def test_update_changes_name_and_icon(fake_ha):
    c = _ok(fake_ha.ws({"type": "config/category_registry/create", "scope": "scene", "name": "A"}))

    u = _ok(fake_ha.ws({"type": "config/category_registry/update", "scope": "scene",
                        "category_id": c["category_id"], "name": "B", "icon": None}))

    assert (u["name"], u["icon"]) == ("B", None)


def test_entity_categories_merge_per_scope_and_none_removes(fake_ha):
    fake_ha.ws({"type": "config/entity_registry/update", "entity_id": "automation.morning",
                "categories": {"automation": "c1", "helpers": "c2"}})

    r = _ok(fake_ha.ws({"type": "config/entity_registry/update",
                        "entity_id": "automation.morning",
                        "categories": {"automation": None}}))

    assert r["entity_entry"]["categories"] == {"helpers": "c2"}


def test_an_unknown_category_id_is_stored_as_sent(fake_ha):
    r = _ok(fake_ha.ws({"type": "config/entity_registry/update",
                        "entity_id": "automation.morning",
                        "categories": {"automation": "does-not-exist"}}))

    assert r["entity_entry"]["categories"] == {"automation": "does-not-exist"}


def test_labels_still_update_through_the_same_command(fake_ha):
    r = _ok(fake_ha.ws({"type": "config/entity_registry/update",
                        "entity_id": "light.kitchen", "labels": ["x"]}))

    assert r["entity_entry"]["labels"] == ["x"]


def test_deleting_a_category_clears_it_from_entities(fake_ha):
    c = _ok(fake_ha.ws({"type": "config/category_registry/create",
                        "scope": "automation", "name": "A"}))
    fake_ha.ws({"type": "config/entity_registry/update", "entity_id": "automation.morning",
                "categories": {"automation": c["category_id"]}})

    fake_ha.ws({"type": "config/category_registry/delete", "scope": "automation",
                "category_id": c["category_id"]})

    entry = _ok(fake_ha.ws({"type": "config/entity_registry/get",
                            "entity_id": "automation.morning"}))
    assert entry.get("categories", {}) == {}


def test_manifest_list_reports_integration_types(fake_ha):
    manifests = _ok(fake_ha.ws({"type": "manifest/list"}))

    types = {m["domain"]: m.get("integration_type") for m in manifests}
    assert types["template"] == "helper"
    assert types["hue"] == "hub"
