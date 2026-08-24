"""tools/_aliases.py: pure normalisation between Home Assistant's two
automation vocabularies, and the dotted-path accessors built on top of it.

No fake_ha fixture anywhere in this file - the module touches no network
and imports nothing from the project, which is the point of it.
"""
import copy

import pytest

from tools._aliases import PathError, get_path, set_path, stored_format, to_modern, to_stored


LEGACY_CONFIG = {
    "alias": "NAS shutdown guard",
    "trigger": [
        {"platform": "state", "entity_id": "input_boolean.nas_shutdown_request", "to": "on"},
    ],
    "condition": [
        {"condition": "template",
         "value_template": "{{ is_state('button.nas_shutdown', 'unavailable') }}"},
    ],
    "action": [
        {"service": "button.press", "target": {"entity_id": "button.nas_shutdown"}},
        {"wait_for_trigger": [
            {"platform": "state", "entity_id": "button.nas_shutdown", "to": "unavailable"},
        ], "timeout": "00:00:30"},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ],
    "mode": "single",
}

MODERN_CONFIG = {
    "alias": "Morning lights",
    "triggers": [{"trigger": "sun", "event": "sunset"}],
    "conditions": [],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": "light.living_room"}},
    ],
    "mode": "single",
}


# ---- to_modern() -------------------------------------------------------------

def test_to_modern_renames_a_fully_legacy_config():
    normalised, restore = to_modern(LEGACY_CONFIG)

    assert "triggers" in normalised and "trigger" not in normalised
    assert "conditions" in normalised and "condition" not in normalised
    assert "actions" in normalised and "action" not in normalised
    assert normalised["triggers"][0]["trigger"] == "state"
    assert "platform" not in normalised["triggers"][0]
    assert normalised["actions"][0]["action"] == "button.press"
    assert "service" not in normalised["actions"][0]
    assert normalised["actions"][2]["action"] == "switch.turn_off"
    # A step with neither "service" nor "action" (wait_for_trigger) is not
    # a service/action step at all and must pass through untouched - its
    # own nested "platform" (inside wait_for_trigger's own trigger list)
    # is not a root or action-step key and must not be touched either.
    assert normalised["actions"][1]["wait_for_trigger"][0]["platform"] == "state"


def test_to_modern_leaves_a_fully_modern_config_unchanged_in_content():
    normalised, restore = to_modern(MODERN_CONFIG)

    assert normalised == MODERN_CONFIG
    assert restore == {}


def test_to_modern_does_not_mutate_its_input():
    original = {
        "trigger": [{"platform": "sun", "event": "sunset"}],
        "action": [{"service": "light.turn_on"}],
    }
    snapshot = {
        "trigger": [{"platform": "sun", "event": "sunset"}],
        "action": [{"service": "light.turn_on"}],
    }

    to_modern(original)

    assert original == snapshot


def test_to_modern_handles_a_config_that_mixes_both_vocabularies_at_once():
    """Root already modern, but the trigger step still says `platform` -
    only the step-level rename should fire; there is nothing at the root
    to rename."""
    mixed = {
        "triggers": [{"platform": "sun", "event": "sunset"}],
        "actions": [{"action": "light.turn_on"}],
    }

    normalised, restore = to_modern(mixed)

    assert normalised["triggers"][0]["trigger"] == "sun"
    assert restore == {"triggers.0.trigger": "platform"}


# ---- to_stored() --------------------------------------------------------------

def test_round_trip_is_exact_for_a_fully_legacy_config():
    normalised, restore = to_modern(LEGACY_CONFIG)

    restored = to_stored(normalised, restore)

    assert restored == LEGACY_CONFIG


def test_round_trip_reinserts_a_renamed_key_at_the_end_not_its_original_position():
    """`==` on dicts ignores key order, so the test above cannot see this:
    to_stored() reverses a rename by deleting the modern key and adding
    the legacy one back (`parent[legacy_key] = value`), which - like any
    plain dict assignment for a key that is not already present - always
    inserts at the END, not back where the key originally was. Idempotent
    and semantically null for Home Assistant (a dict's key order carries
    no meaning to the automation engine), but visible to anyone keeping
    automations.yaml in git: a round-tripped legacy automation reorders
    every renamed key on its first edit, even one that changes nothing
    else. Pinned here explicitly so a future change to this reordering
    behaviour - in either direction - is a deliberate, visible decision,
    not a silent side effect only a key-order diff would catch."""
    normalised, restore = to_modern(LEGACY_CONFIG)

    restored = to_stored(normalised, restore)

    assert list(LEGACY_CONFIG.keys()) == ["alias", "trigger", "condition", "action", "mode"]
    assert list(restored.keys()) == ["alias", "mode", "trigger", "condition", "action"]
    assert list(LEGACY_CONFIG["trigger"][0].keys()) == ["platform", "entity_id", "to"]
    assert list(restored["trigger"][0].keys()) == ["entity_id", "to", "platform"]
    assert list(LEGACY_CONFIG["action"][0].keys()) == ["service", "target"]
    assert list(restored["action"][0].keys()) == ["target", "service"]


def test_round_trip_is_exact_for_a_fully_modern_config():
    normalised, restore = to_modern(MODERN_CONFIG)

    restored = to_stored(normalised, restore)

    assert restored == MODERN_CONFIG


def test_round_trip_is_exact_key_by_key_for_a_mixed_config():
    """Root legacy, but one step already written in the modern spelling -
    to_stored() must restore only what to_modern() actually renamed, and
    leave the step that was already modern alone."""
    mixed = {
        "trigger": [
            {"platform": "sun", "event": "sunset"},
            {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"},
        ],
        "action": [{"service": "light.turn_on"}],
    }

    normalised, restore = to_modern(mixed)
    restored = to_stored(normalised, restore)

    assert restored == mixed
    # Confirm the already-modern step's key was never touched in either
    # direction - it has no restore entry at all.
    assert "triggers.1.trigger" not in restore


def test_to_stored_does_not_mutate_its_input():
    normalised, restore = to_modern(LEGACY_CONFIG)
    snapshot = copy.deepcopy(normalised)

    to_stored(normalised, restore)

    assert normalised == snapshot


def test_to_stored_skips_a_renamed_path_that_no_longer_resolves():
    """An update between to_modern() and to_stored() replaced the whole
    actions list with an empty one - the recorded action-step rename
    paths no longer resolve at all (there is nothing at those indices any
    more). That must be skipped, not raised, and the still-valid
    root-level rename must still be reversed."""
    normalised, restore = to_modern(LEGACY_CONFIG)
    normalised["actions"] = []  # replaced wholesale, nothing left to rename

    restored = to_stored(normalised, restore)

    assert restored["action"] == []  # root-level rename still reversed
    assert restored["trigger"] == LEGACY_CONFIG["trigger"]  # untouched path still reversed


def test_to_stored_skips_a_path_whose_step_was_removed():
    """A recorded step-level rename whose specific list index no longer
    exists (the step it pointed at was deleted, not the whole list) is
    skipped, and the step that shifted into that index is left exactly as
    it was - a restore-map entry is not creatively reattached to whatever
    item now happens to sit at the recorded index."""
    normalised, restore = to_modern(LEGACY_CONFIG)
    del normalised["actions"][0]  # the button.press step this rename pointed at

    restored = to_stored(normalised, restore)

    assert len(restored["action"]) == 2
    # "actions.0.action" now points at the wait_for_trigger step, which has
    # no "action" key at all - skipped. "actions.2.action" is out of range
    # against the now 2-item list - also skipped. The switch step therefore
    # keeps its normalised ("action", not "service") spelling.
    assert "wait_for_trigger" in restored["action"][0]
    assert restored["action"][1]["action"] == "switch.turn_off"


# ---- stored_format() -----------------------------------------------------------

def test_stored_format_names_a_legacy_root():
    _, restore = to_modern(LEGACY_CONFIG)

    assert stored_format(restore) == "legacy"


def test_stored_format_names_a_modern_root():
    _, restore = to_modern(MODERN_CONFIG)

    assert stored_format(restore) == "modern"


def test_stored_format_is_modern_when_only_a_step_was_renamed():
    """The root was already modern; only a step-level key needed renaming.
    stored_format() names the root style, not every rename recorded."""
    mixed = {"triggers": [{"platform": "sun", "event": "sunset"}], "actions": []}
    _, restore = to_modern(mixed)

    assert stored_format(restore) == "modern"


# ---- get_path() / set_path(): walking dicts and lists --------------------------

def test_get_path_walks_dicts_and_lists_together():
    assert get_path(LEGACY_CONFIG, "action.0.target.entity_id") == "button.nas_shutdown"


def test_get_path_returns_a_whole_subtree_not_just_a_leaf():
    result = get_path(LEGACY_CONFIG, "condition.0")

    assert result == LEGACY_CONFIG["condition"][0]


def test_set_path_replaces_a_leaf_in_place():
    config = {
        "conditions": [
            {"condition": "template",
             "value_template": "{{ is_state('button.old', 'unavailable') }}"},
        ],
    }

    set_path(config, "conditions.0.value_template",
             "{{ is_state('button.new', 'unavailable') }}")

    assert config["conditions"][0]["value_template"] == (
        "{{ is_state('button.new', 'unavailable') }}"
    )


def test_set_path_replaces_a_whole_subtree():
    config = {"actions": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}]}

    set_path(config, "actions.0.target", {"entity_id": "light.b"})

    assert config["actions"][0]["target"] == {"entity_id": "light.b"}


# ---- get_path() / set_path(): PathError ----------------------------------------

def test_get_path_raises_path_error_naming_what_is_there():
    with pytest.raises(PathError) as exc_info:
        get_path(MODERN_CONFIG, "triggers.0.nope")

    message = str(exc_info.value)
    assert "nope" in message
    assert "trigger" in message  # the key that actually is there


def test_get_path_raises_path_error_for_an_index_past_the_end():
    with pytest.raises(PathError) as exc_info:
        get_path(MODERN_CONFIG, "triggers.5")

    assert "out of range" in str(exc_info.value)


def test_get_path_raises_path_error_for_a_non_numeric_list_index():
    with pytest.raises(PathError):
        get_path(MODERN_CONFIG, "triggers.first")


def test_set_path_never_creates_a_key():
    config = {"actions": [{"action": "light.turn_on"}]}

    with pytest.raises(PathError):
        set_path(config, "actions.0.nonexistent_field", "value")

    assert "nonexistent_field" not in config["actions"][0]


def test_set_path_raises_path_error_rather_than_extending_a_list():
    config = {"actions": [{"action": "light.turn_on"}]}

    with pytest.raises(PathError):
        set_path(config, "actions.1.action", "light.turn_off")

    assert len(config["actions"]) == 1


# ---- get_path() / set_path(): legacy spelling accepted --------------------------

def test_get_path_accepts_a_legacy_root_segment_against_a_modern_config():
    """MODERN_CONFIG has "triggers", not "trigger" - the legacy singular
    must still resolve to it."""
    assert get_path(MODERN_CONFIG, "trigger.0") == {"trigger": "sun", "event": "sunset"}


def test_get_path_accepts_a_legacy_step_segment_against_a_modern_config():
    """MODERN_CONFIG's trigger step has "trigger", not "platform" - the
    legacy step key must still resolve to it."""
    assert get_path(MODERN_CONFIG, "triggers.0.platform") == "sun"


def test_get_path_accepts_a_fully_legacy_spelled_path_against_a_legacy_config():
    assert get_path(LEGACY_CONFIG, "condition.0.condition") == "template"


def test_set_path_accepts_a_legacy_spelled_path():
    config = {"actions": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}]}

    set_path(config, "action.0.service", "light.turn_off")

    assert config["actions"][0]["action"] == "light.turn_off"


# ---- get_path() / set_path(): step aliases reach any nesting depth --------------
# patch_automation()'s own docstring documents the modern spelling and says a
# caller "does not need to know which one this particular automation is
# stored in" - but to_modern()/to_stored() only rewrite the top-level
# triggers/actions list items (see this module's own module docstring), so a
# step nested inside choose/if/repeat/parallel/wait_for_trigger keeps
# whatever vocabulary it was last saved in, regardless of the top level.
# Measured live: `actions.1.choose.0.sequence.0.action` (the documented
# spelling) failed to resolve against a step that still said `service`,
# while `...sequence.0.service` worked - get_path()/set_path() must resolve
# either spelling at any depth, not just the top level, for the documented
# promise to be true.

NESTED_LEGACY_CHOOSE_CONFIG = {
    "alias": "Nested choose",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.x"}],
    "conditions": [],
    "actions": [
        {"action": "light.turn_on"},
        {"choose": [
            {"conditions": [], "sequence": [
                {"service": "notify.mobile_app", "data": {"message": "hi"}},
            ]},
        ]},
    ],
    "mode": "single",
}


def test_get_path_resolves_a_deeply_nested_action_step_by_the_modern_spelling():
    assert get_path(NESTED_LEGACY_CHOOSE_CONFIG,
                    "actions.1.choose.0.sequence.0.action") == "notify.mobile_app"


def test_get_path_still_resolves_the_same_nested_step_by_its_actual_legacy_spelling():
    assert get_path(NESTED_LEGACY_CHOOSE_CONFIG,
                    "actions.1.choose.0.sequence.0.service") == "notify.mobile_app"


def test_set_path_resolves_a_deeply_nested_action_step_by_the_modern_spelling():
    config = copy.deepcopy(NESTED_LEGACY_CHOOSE_CONFIG)

    set_path(config, "actions.1.choose.0.sequence.0.action", "notify.persistent_notification")

    assert (config["actions"][1]["choose"][0]["sequence"][0]["service"]
           == "notify.persistent_notification")


def test_get_path_resolves_a_nested_step_already_modern_by_the_legacy_spelling():
    """The reverse direction: a nested step already saved in the modern
    spelling must still resolve when the caller asks for the legacy one -
    step aliases are bidirectional, not just legacy-to-modern."""
    config = copy.deepcopy(NESTED_LEGACY_CHOOSE_CONFIG)
    config["actions"][1]["choose"][0]["sequence"][0] = {
        "action": "notify.mobile_app", "data": {"message": "hi"},
    }

    assert get_path(config, "actions.1.choose.0.sequence.0.service") == "notify.mobile_app"


def test_get_path_does_not_alias_into_an_unrelated_data_payload():
    """A `data` payload reached by a dict key (not a list index) is never a
    step - `_STEP_ALIASES` must not fire there even when a coincidentally-
    named key ("trigger") exists inside it. Without this, set_path(cfg,
    "actions.0.data.platform", ...) could silently target an unrelated
    "trigger" key in someone's own service-call data."""
    config = {"actions": [{"action": "some.service",
                           "data": {"trigger": "not a platform field"}}]}

    with pytest.raises(PathError):
        get_path(config, "actions.0.data.platform")

    # The key that IS there must still resolve directly, unaffected.
    assert get_path(config, "actions.0.data.trigger") == "not a platform field"


def test_set_path_does_not_alias_into_an_unrelated_data_payload():
    config = {"actions": [{"action": "some.service",
                           "data": {"trigger": "untouched"}}]}

    with pytest.raises(PathError):
        set_path(config, "actions.0.data.platform", "clobbered")

    assert config["actions"][0]["data"]["trigger"] == "untouched"
