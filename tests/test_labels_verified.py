"""set_entity_labels / bulk_set_entity_labels refuse unknown label ids and
check what Home Assistant stored.

Home Assistant drops label ids that do not exist (core PR #173438) and still
answers success; both tools used to report that as success too - the label
silently never appeared.
"""
from tools.areas import bulk_set_entity_labels, set_entity_labels


def _updates(fake_ha):
    return [m for m in fake_ha.ws_calls if m["type"] == "config/entity_registry/update"]


def test_an_unknown_label_is_refused_before_anything_is_sent(fake_ha):
    r = set_entity_labels("light.kitchen", ["power", "nope"])

    assert r["error"] == "label_not_found"
    assert r["unknown"] == ["nope"]
    assert "power" in r["available"]
    assert not _updates(fake_ha)


def test_known_labels_are_set_and_confirmed(fake_ha):
    r = set_entity_labels("light.kitchen", ["power", "energia"])

    assert r == {"entity_id": "light.kitchen", "labels": ["power", "energia"], "success": True}


def test_clearing_labels_needs_no_lookup(fake_ha):
    assert set_entity_labels("automation.nas_shutdown", [])["labels"] == []


def test_a_write_that_drops_a_label_is_reported(fake_ha):
    fake_ha.ws_result("config/entity_registry/update",
                      {"entity_entry": {"entity_id": "light.kitchen", "labels": ["power"]}})

    r = set_entity_labels("light.kitchen", ["power", "energia"])

    assert r["error"] == "config_write_unverified"
    assert r["missing"] == ["energia"]


def test_bulk_refuses_unknown_labels_for_the_whole_batch(fake_ha):
    r = bulk_set_entity_labels(["light.kitchen", "light.study"], ["nope"])

    assert r["error"] == "label_not_found"
    assert not _updates(fake_ha)


def test_bulk_reports_an_entity_whose_labels_did_not_land(fake_ha, monkeypatch):
    import tools.areas as areas

    def ws_multi(msgs):
        return [{"id": 1, "type": "result", "success": True,
                 "result": {"entity_entry": {"labels": [] if m["entity_id"] == "light.study"
                                             else m["labels"]}}} for m in msgs]

    monkeypatch.setattr(areas, "_ws_multi", ws_multi)

    r = bulk_set_entity_labels(["light.kitchen", "light.study"], ["energia"])

    assert r["succeeded"] == 1
    assert r["failed"] == ["light.study"]
    assert "config_write_unverified" in r["reasons"]["light.study"]


def test_an_unreadable_label_registry_is_one_error(fake_ha):
    fake_ha.fail_ws("config/label_registry/list", code="unauthorized", message="no")

    assert set_entity_labels("light.kitchen", ["power"])["error"] == "unauthorized"
    assert bulk_set_entity_labels(["light.kitchen"], ["power"])["error"] == "unauthorized"
