"""find_dashboard_cards() and get_dashboard_outline().

A card is a dict reached through a `cards` list, a `card` key or a
`badges` list. Rows of an entities card, tile features, header/footer are
content of their card, never matches or siblings of their own. Each match
carries its neighbours, so a caller sees what else lives in that section
before touching it.
"""
import pytest

from tests.dashboard_fixture import install
from tools.dashboards import find_dashboard_cards, get_dashboard_outline, patch_dashboard

SEC0 = "views.0.sections.0.cards"


@pytest.fixture
def ha(fake_ha):
    return install(fake_ha)


def _paths(r):
    return [m["path"] for m in r["matches"]]


def test_a_filter_is_required(ha):
    assert find_dashboard_cards("casa")["error"] == "missing_filter"


def test_by_entity_returns_the_tile_with_its_neighbours(ha):
    r = find_dashboard_cards("casa", entity="sensor.lavatrice")

    assert _paths(r) == [f"{SEC0}.1"]
    m = r["matches"][0]
    assert m["type"] == "tile"
    assert m["section"] == {"index": 0, "heading": "Lavatrice"}
    assert m["view"] == {"index": 0, "title": "Casa", "path": "casa"}
    assert [s["path"] for s in m["siblings"]] == [
        f"{SEC0}.0", f"{SEC0}.2", f"{SEC0}.3", f"{SEC0}.4"]
    assert [s["type"] for s in m["siblings"]] == [
        "heading", "clock", "custom:scheduler-card", "vertical-stack"]


def test_entity_matching_is_exact_not_substring(ha):
    r = find_dashboard_cards("casa", entity="sensor.lavatrice")

    assert f"{SEC0}.4.cards.1" not in _paths(r)


def test_an_entities_card_matches_once_at_the_card_not_at_its_rows(ha):
    r = find_dashboard_cards("casa", entity="sensor.lavatrice_run_state")

    assert _paths(r) == [f"{SEC0}.4.cards.1"]
    m = r["matches"][0]
    assert [s["path"] for s in m["siblings"]] == [f"{SEC0}.4.cards.0"]


def test_plain_string_entities_rows_count(ha):
    assert _paths(find_dashboard_cards("casa", entity="sensor.lavatrice_remaining")) == [
        f"{SEC0}.4.cards.1"]


def test_by_heading_finds_the_section_and_the_heading_card(ha):
    r = find_dashboard_cards("casa", heading="lavatrice")

    by_path = {m["path"]: m for m in r["matches"]}
    assert set(by_path) == {"views.0.sections.0", f"{SEC0}.0"}
    assert by_path["views.0.sections.0"]["kind"] == "section"
    assert len(by_path["views.0.sections.0"]["children"]) == 5


def test_by_type(ha):
    assert _paths(find_dashboard_cards("casa", type="custom:button-card")) == [
        "views.0.sections.1.cards.1"]


def test_text_finds_a_template_mentioning_an_entity(ha):
    r = find_dashboard_cards("casa", text="states['sensor.lavatrice']")

    assert _paths(r) == ["views.0.sections.1.cards.1"]


def test_text_does_not_match_a_stack_through_its_children(ha):
    r = find_dashboard_cards("casa", text="sensor.temperatura")

    assert _paths(r) == [f"{SEC0}.4.cards.0"]


def test_filters_combine(ha):
    assert _paths(find_dashboard_cards("casa", entity="sensor.lavatrice", type="clock")) == []


def test_badges_are_cards(ha):
    assert _paths(find_dashboard_cards("casa", entity="person.riccardo")) == ["views.0.badges.0"]


def test_unknown_dashboard(ha):
    assert find_dashboard_cards("nope", type="tile")["error"] == "not_found"


def test_a_match_round_trips_through_patch_dashboard(ha):
    m = find_dashboard_cards("casa", entity="sensor.temperatura")["matches"][0]

    r = patch_dashboard("casa", f"{m['path']}.name", "Temperatura")

    assert r["old"] == "Temp"
    expect = {"type": m["type"], "entity": "sensor.temperatura"}
    assert patch_dashboard("casa", m["path"], None, op="remove", expect=expect)["saved"] is True


def test_outline_lists_every_card_with_depth(ha):
    o = get_dashboard_outline("casa")

    view = o["views"][0]
    assert view["path"] == "views.0" and view["title"] == "Casa"
    sec = view["sections"][0]
    assert sec["heading"] == "Lavatrice"
    assert [(c["path"], c["depth"]) for c in sec["cards"]] == [
        (f"{SEC0}.0", 0), (f"{SEC0}.1", 0), (f"{SEC0}.2", 0), (f"{SEC0}.3", 0),
        (f"{SEC0}.4", 0), (f"{SEC0}.4.cards.0", 1), (f"{SEC0}.4.cards.1", 1)]
    assert sec["cards"][1]["summary"] == "tile sensor.lavatrice"
    assert view["badges"][0]["path"] == "views.0.badges.0"
    assert o["root_keys"] == {"button_card_templates": ["wm_btn"]}


def test_outline_of_a_strategy_dashboard_says_so(ha):
    o = get_dashboard_outline("strat")

    assert o["views"] == []
    assert o["strategy"] == {"type": "original-states"}


def test_outline_of_a_never_saved_default(ha):
    assert get_dashboard_outline("")["error"] == "not_found"


# ---- review gate 2 ------------------------------------------------------------

@pytest.fixture
def odd(fake_ha):
    """Real-world shapes the first walker missed or crashed on."""
    fake_ha.lovelace["odd"] = {
        "views": [
            "not-a-view",
            {"title": "V", "path": "v",
             "header": {"card": {"type": "markdown", "content": "sensor.header_x"}},
             "badges": ["sensor.badge_x"],
             "sections": [{"type": "grid", "cards": [
                 {"type": "heading", "heading": 42},
                 {"type": "custom:button-card", "custom_fields": {
                     "f1": {"card": {"type": "tile", "entity": "sensor.q"}}}},
                 {"type": "picture-elements", "elements": [
                     {"type": "state-icon", "entity": "sensor.pic"}]},
                 {"type": "conditional", "conditions": [
                     {"condition": "state", "entity": "sensor.cond", "state": "on"}],
                  "card": {"type": "markdown", "content": "x"}},
             ]}]},
        ],
        "button_card_templates": {"wm_btn": {"label": "[[[ return states['sensor.wm_power'].state ]]]"},
                                  "a.b": {"x": 1}},
    }
    fake_ha.dashboards = [{"id": "odd", "url_path": "odd", "mode": "storage"}]
    return fake_ha


def test_a_non_string_heading_does_not_crash(odd):
    assert "error" not in find_dashboard_cards("odd", heading="x")


def test_a_non_dict_view_does_not_crash(odd):
    assert "error" not in find_dashboard_cards("odd", heading="V")
    assert "error" not in get_dashboard_outline("odd")


def test_button_card_templates_are_searched(odd):
    r = find_dashboard_cards("odd", text="sensor.wm_power")

    assert [m["path"] for m in r["matches"]] == ["button_card_templates.wm_btn"]
    assert r["matches"][0]["kind"] == "template"


def test_a_template_name_with_a_dot_has_no_path(odd):
    r = find_dashboard_cards("odd", text='"x": 1')

    assert r["matches"][0]["path"] is None
    assert "." in r["matches"][0]["reason"]


@pytest.mark.parametrize("entity,path", [
    ("sensor.badge_x", "views.1.badges.0"),
    ("sensor.q", "views.1.sections.0.cards.1.custom_fields.f1.card"),
    ("sensor.pic", "views.1.sections.0.cards.2"),
    ("sensor.cond", "views.1.sections.0.cards.3"),
])
def test_entities_in_real_world_shapes_are_found(odd, entity, path):
    assert [m["path"] for m in find_dashboard_cards("odd", entity=entity)["matches"]] == [path]


def test_the_sections_view_header_card_is_searched(odd):
    r = find_dashboard_cards("odd", text="sensor.header_x")

    assert [m["path"] for m in r["matches"]] == ["views.1.header.card"]
