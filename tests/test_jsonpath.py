"""tools/_jsonpath.py: plain dotted paths into a JSON document.

No aliasing (unlike tools/_aliases.py, which is automation-specific). A
segment is a dict key, or a non-negative integer index when the current
node is a list. Errors name the failing segment and what is there.
"""
import pytest

from tools import _jsonpath as jp

DOC = {
    "views": [{"title": "Casa", "cards": [{"type": "tile"}, {"type": "clock"}]}],
    "button_card_templates": {"wm_btn": {"show_icon": True}, "1": {"x": 1}},
}


def doc():
    import copy
    return copy.deepcopy(DOC)


def test_get_walks_keys_and_indices():
    assert jp.get(doc(), "views.0.cards.1.type") == "clock"


def test_a_digit_segment_is_a_key_when_the_node_is_a_dict():
    assert jp.get(doc(), "button_card_templates.1.x") == 1


def test_a_missing_key_names_what_is_there():
    with pytest.raises(jp.PathError) as e:
        jp.get(doc(), "views.0.sections")
    assert "sections" in str(e.value)
    assert "cards" in str(e.value) and "title" in str(e.value)


def test_an_index_out_of_range_names_the_length():
    with pytest.raises(jp.PathError) as e:
        jp.get(doc(), "views.0.cards.5")
    assert "2" in str(e.value)


def test_a_non_integer_segment_into_a_list_is_refused():
    with pytest.raises(jp.PathError):
        jp.get(doc(), "views.first")


def test_a_negative_index_is_refused():
    with pytest.raises(jp.PathError):
        jp.get(doc(), "views.-1")


def test_walking_into_a_scalar_is_refused():
    with pytest.raises(jp.PathError):
        jp.get(doc(), "views.0.title.x")


def test_an_empty_path_is_refused():
    with pytest.raises(jp.PathError):
        jp.get(doc(), "")


def test_set_replaces_and_returns_old():
    d = doc()
    assert jp.set_(d, "button_card_templates.wm_btn.show_icon", False) is True
    assert d["button_card_templates"]["wm_btn"]["show_icon"] is False


def test_set_never_creates():
    d = doc()
    with pytest.raises(jp.PathError):
        jp.set_(d, "button_card_templates.wm_btn.color", "red")
    assert "color" not in d["button_card_templates"]["wm_btn"]


def test_insert_into_a_list_at_an_index_and_at_its_end():
    d = doc()
    jp.insert(d, "views.0.cards.0", {"type": "heading"})
    jp.insert(d, "views.0.cards.3", {"type": "end"})
    assert [c["type"] for c in d["views"][0]["cards"]] == ["heading", "tile", "clock", "end"]


def test_insert_past_the_end_is_refused():
    with pytest.raises(jp.PathError):
        jp.insert(doc(), "views.0.cards.3", {})


def test_insert_adds_a_new_dict_key():
    d = doc()
    jp.insert(d, "button_card_templates.wm_btn.color", "red")
    assert d["button_card_templates"]["wm_btn"]["color"] == "red"


def test_insert_on_an_existing_key_is_refused():
    with pytest.raises(jp.PathError) as e:
        jp.insert(doc(), "button_card_templates.wm_btn.show_icon", False)
    assert "exists" in str(e.value)


def test_remove_returns_what_it_removed():
    d = doc()
    assert jp.remove(d, "views.0.cards.0") == {"type": "tile"}
    assert d["views"][0]["cards"] == [{"type": "clock"}]
    assert jp.remove(d, "button_card_templates.1") == {"x": 1}


def test_remove_of_a_missing_path_is_refused():
    with pytest.raises(jp.PathError):
        jp.remove(doc(), "views.0.cards.9")
