"""patch_dashboard(): one value or one card at a time, with the same safety
contract as patch_automation plus `expect` - the guard against "the thing
at this index is not what I think it is", which is how a clock, a
scheduler and a temperature stack were once deleted on a real dashboard.
"""
import copy

import pytest

from tests.dashboard_fixture import CASA, install
from tools.dashboards import patch_dashboard

SEC = "views.0.sections.0.cards"


@pytest.fixture
def ha(fake_ha):
    return install(fake_ha)


def _saves(ha):
    return [m for m in ha.ws_calls if m["type"] == "lovelace/config/save"]


def test_set_changes_one_value_and_nothing_else(ha):
    r = patch_dashboard("casa", "button_card_templates.wm_btn.show_icon", False)

    assert r == {"url_path": "casa", "op": "set",
                 "path": "button_card_templates.wm_btn.show_icon",
                 "old": True, "new": False, "saved": True}
    expected = copy.deepcopy(CASA)
    expected["button_card_templates"]["wm_btn"]["show_icon"] = False
    assert ha.lovelace["casa"] == expected


def test_set_on_a_missing_path_writes_nothing(ha):
    r = patch_dashboard("casa", f"{SEC}.1.titel", "x")

    assert r["error"] == "bad_path"
    assert "entity" in r["detail"] and "type" in r["detail"]
    assert not _saves(ha)


def test_a_wrong_index_is_caught_by_expect(ha):
    """The incident: index 2 is the clock, not the washing machine."""
    r = patch_dashboard("casa", f"{SEC}.2", {"type": "tile", "entity": "sensor.lavatrice",
                                             "name": "Lavatrice"},
                        expect={"entity": "sensor.lavatrice"})

    assert r["error"] == "unexpected_target"
    assert "clock" in r["detail"]
    assert not _saves(ha)
    assert ha.lovelace["casa"] == CASA


def test_expect_matching_lets_the_write_through(ha):
    r = patch_dashboard("casa", f"{SEC}.1.name", "Lavatrice",
                        op="insert", expect=None)
    assert r["saved"] is True

    r = patch_dashboard("casa", f"{SEC}.1", {"type": "tile", "entity": "sensor.lavatrice"},
                        expect={"type": "tile", "entity": "sensor.lavatrice"})
    assert r["old"] == {"type": "tile", "entity": "sensor.lavatrice", "name": "Lavatrice"}


def test_expect_on_a_non_dict_target_is_a_mismatch(ha):
    r = patch_dashboard("casa", "views.0.title", "X", expect={"type": "tile"})

    assert r["error"] == "unexpected_target"


def test_insert_into_a_list_and_at_its_end(ha):
    r = patch_dashboard("casa", f"{SEC}.0", {"type": "markdown"}, op="insert")
    assert r["old"] is None
    assert "shifted" in r

    patch_dashboard("casa", f"{SEC}.6", {"type": "end"}, op="insert")

    types = [c["type"] for c in ha.lovelace["casa"]["views"][0]["sections"][0]["cards"]]
    assert types[0] == "markdown" and types[-1] == "end" and len(types) == 7


def test_insert_with_expect_checks_the_card_currently_at_that_index(ha):
    r = patch_dashboard("casa", f"{SEC}.2", {"type": "markdown"}, op="insert",
                        expect={"type": "tile"})

    assert r["error"] == "unexpected_target"


def test_append_with_expect_is_refused(ha):
    r = patch_dashboard("casa", f"{SEC}.5", {"type": "markdown"}, op="insert",
                        expect={"type": "tile"})

    assert r["error"] == "unexpected_target"
    assert not _saves(ha)


def test_insert_a_new_dict_key(ha):
    r = patch_dashboard("casa", "button_card_templates.wm_btn.color", "red", op="insert")

    assert r["saved"] is True
    assert "shifted" not in r
    assert ha.lovelace["casa"]["button_card_templates"]["wm_btn"]["color"] == "red"


def test_insert_on_an_existing_key_is_refused(ha):
    r = patch_dashboard("casa", "button_card_templates.wm_btn.show_icon", False, op="insert")

    assert r["error"] == "path_exists"
    assert not _saves(ha)


def test_remove_returns_the_removed_card_and_warns_about_shifted_indices(ha):
    r = patch_dashboard("casa", f"{SEC}.2", op="remove", expect={"type": "clock"})

    assert r["old"] == {"type": "clock"}
    assert r["new"] is None
    assert f"{SEC}" in r["shifted"]
    cards = ha.lovelace["casa"]["views"][0]["sections"][0]["cards"]
    assert [c["type"] for c in cards][2] == "custom:scheduler-card"


@pytest.mark.parametrize("kwargs", [
    {"op": "remove", "value": {"type": "x"}},
    {"op": "replace", "value": 1},
])
def test_invalid_ops_are_refused(ha, kwargs):
    r = patch_dashboard("casa", f"{SEC}.0", **kwargs)

    assert r["error"] == "invalid_op"
    assert not _saves(ha)


def test_the_root_is_protected(ha):
    r = patch_dashboard("casa", "", {"views": []})

    assert r["error"] == "protected_path"
    assert "update_dashboard_config" in r["detail"]


def test_a_concurrent_change_is_refused(ha):
    def someone_else(config):
        config["views"][0]["title"] = "Changed elsewhere"

    ha.lovelace_mutate_on_read = {2: someone_else}

    r = patch_dashboard("casa", "button_card_templates.wm_btn.show_icon", False)

    assert r["error"] == "concurrent_modification"
    assert not _saves(ha)


def test_a_save_that_does_not_land_is_reported(ha):
    ha.lovelace_save_discarded = True

    r = patch_dashboard("casa", "button_card_templates.wm_btn.show_icon", False)

    assert r["error"] == "config_write_unverified"
    assert r["old"] is True and r["new"] is False


def test_a_yaml_dashboard_is_not_editable(ha):
    r = patch_dashboard("yamlboard", "views", [], op="set")

    assert r["error"] == "not_editable"
    assert not [m for m in ha.ws_calls if m["type"] == "lovelace/config"]


def test_a_yaml_refusal_at_save_time_is_also_not_editable(ha):
    ha.dashboards = [d for d in ha.dashboards if d["url_path"] != "yamlboard"]
    ha.dashboards.append({"id": "yamlboard", "url_path": "yamlboard", "mode": "storage"})
    ha.fail_ws("lovelace/config/save", code="error", message="Not supported")
    ha.lovelace["yamlboard"] = {"views": [{"title": "a"}]}

    r = patch_dashboard("yamlboard", "views.0.title", "b")

    assert r["error"] == "not_editable"


def test_a_strategy_dashboard_names_the_strategy(ha):
    r = patch_dashboard("strat", "views.0.title", "x")

    assert r["error"] == "bad_path"
    assert "strategy" in r["detail"]


def test_an_unknown_dashboard_is_not_found(ha):
    assert patch_dashboard("nope", "views.0.title", "x")["error"] == "not_found"


def test_a_never_saved_default_dashboard_says_so(ha):
    r = patch_dashboard("", "views.0.title", "x")

    assert r["error"] == "not_found"
    assert "never been saved" in r["detail"]


def test_a_card_nested_two_deep_can_be_patched(ha):
    r = patch_dashboard("casa", f"{SEC}.4.cards.0.name", "Temperatura",
                        expect=None)

    assert r["old"] == "Temp"
    stack = ha.lovelace["casa"]["views"][0]["sections"][0]["cards"][4]
    assert stack["cards"][0]["name"] == "Temperatura"


def test_home_assistant_rejecting_the_save_is_reported(ha):
    ha.fail_ws("lovelace/config/save", code="invalid_format", message="bad config")

    r = patch_dashboard("casa", "views.0.title", "x")

    assert r["error"] == "home_assistant_error"
    assert "bad config" in r["detail"]
