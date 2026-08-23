"""tools/automations.py: the id resolution shared by get_automation(),
create_automation() and delete_automation() (_resolve_automation_id(),
_fetch_config()), get_automation()'s own shape, and the two read-modify-write
edit tools built on top of it - update_automation() and patch_automation().

create_automation()'s and delete_automation()'s own behavioural tests stay
where they already were (tests/test_tools_shape.py, tests/test_actuation.py)
- this file covers only what changed here: get_automation() itself, the id
resolution now shared by all three instead of written out three times, and
update_automation()/patch_automation() in full.

enabled=True/False on update_automation() is verified through
_set_and_verify_enabled(), the same helper create_automation() now shares -
its own tests live in tests/test_actuation.py alongside create_automation()'s
enabled=False tests, since both exercise the identical function; this file
adds update_automation()-specific cases (re-enabling a disabled automation,
the config-write id-resolution edge case) that create_automation() has no
equivalent of.
"""


def test_get_automation_resolves_the_numeric_id_and_normalises_the_config(fake_ha):
    """automation.nas_shutdown's config id ("1684270733500") is unrelated
    to its entity_id's own slug ("nas_shutdown") in DEFAULT_STATES, and its
    seeded config (DEFAULT_AUTOMATION_CONFIGS in tests/fakeha.py) is
    legacy-vocabulary - both must be resolved and normalised."""
    from tools.automations import get_automation

    result = get_automation("automation.nas_shutdown")

    assert result["automation_id"] == "1684270733500"
    assert result["entity_id"] == "automation.nas_shutdown"
    assert result["name"] == "NAS shutdown"
    assert result["mode"] == "single"


def test_get_automation_config_comes_back_in_the_modern_vocabulary(fake_ha):
    from tools.automations import get_automation

    result = get_automation("automation.nas_shutdown")
    config = result["config"]

    assert "triggers" in config and "trigger" not in config
    assert "conditions" in config and "condition" not in config
    assert "actions" in config and "action" not in config
    assert config["triggers"][0]["trigger"] == "state"
    assert config["actions"][0]["action"] == "button.press"
    assert config["actions"][2]["action"] == "switch.turn_off"
    # wait_for_trigger is neither a trigger nor an action step in HA's own
    # sense - it has no "service"/"action" key of its own - and must pass
    # through untouched, including its own nested trigger list.
    assert config["actions"][1]["wait_for_trigger"][0]["platform"] == "state"


def test_get_automation_reports_the_stored_format_as_legacy(fake_ha):
    from tools.automations import get_automation

    result = get_automation("automation.nas_shutdown")

    assert result["stored_format"] == "legacy"


def test_get_automation_reports_the_stored_format_as_modern(fake_ha):
    from tools.automations import create_automation, get_automation

    create_automation(
        "Morning lights",
        trigger=[{"platform": "sun", "event": "sunset"}],
        action=[{"service": "light.turn_on"}],
    )
    # create_automation() posts the legacy vocabulary itself (see its own
    # payload) - overwrite it directly with a modern-vocabulary config to
    # exercise the "already modern" path independently of that.
    fake_ha.automation_configs["morning_lights"] = {
        "alias": "Morning lights",
        "triggers": [{"trigger": "sun", "event": "sunset"}],
        "conditions": [],
        "actions": [{"action": "light.turn_on"}],
        "mode": "single",
    }

    result = get_automation("automation.morning_lights")

    assert result["stored_format"] == "modern"


def test_get_automation_reports_a_missing_automation(fake_ha):
    from tools.automations import get_automation

    result = get_automation("automation.ghost")

    assert result["error"] == "not_found"
    assert result["entity_id"] == "automation.ghost"
    assert "config" not in result


def test_get_automation_reports_an_entity_with_no_stored_config(fake_ha):
    """automation.morning (DEFAULT_STATES) exists as an entity but has no
    matching row in automation_configs - the shape a YAML-defined
    automation's entity would have (a state, but no UI-editable config)."""
    from tools.automations import get_automation

    result = get_automation("automation.morning")

    assert result["error"] == "not_found"
    assert result["entity_id"] == "automation.morning"


def test_get_automation_falls_back_to_the_slug_when_the_numeric_id_config_is_gone(fake_ha):
    """The entity's `id` attribute resolves to a numeric id, but no config
    is stored under it (deleted out from under the entity, e.g.) while a
    config does exist under the entity_id's own slug - the same fallback
    _fetch_config() gives delete_automation()."""
    from tools.automations import get_automation

    fake_ha.states = [
        {"entity_id": "automation.morning", "state": "on",
         "attributes": {"id": "999999999", "friendly_name": "Morning"}},
    ]
    fake_ha.automation_configs["morning"] = {
        "alias": "Morning", "trigger": [], "condition": [], "action": [],
    }

    result = get_automation("automation.morning")

    assert result["automation_id"] == "morning"
    assert result["name"] == "Morning"


# ---- update_automation() ------------------------------------------------

def test_update_automation_preserves_the_id_and_only_changes_the_requested_field(fake_ha):
    from tools.automations import create_automation, update_automation

    create_automation("Morning lights",
                      trigger=[{"platform": "sun", "event": "sunset"}],
                      action=[{"service": "light.turn_on"}])
    before = dict(fake_ha.automation_configs["morning_lights"])

    result = update_automation("automation.morning_lights", name="Evening lights")

    assert result["automation_id"] == "morning_lights"
    assert result["entity_id"] == "automation.morning_lights"
    assert result["updated"] == ["name"]
    assert result["stored_format"] == "legacy"  # create_automation()'s own payload
    stored = fake_ha.automation_configs["morning_lights"]
    assert stored["alias"] == "Evening lights"
    assert stored["trigger"] == before["trigger"]
    assert stored["action"] == before["action"]


def test_update_automation_writes_back_legacy_for_a_legacy_automation(fake_ha):
    """automation.nas_shutdown's seeded config is legacy-vocabulary
    (DEFAULT_AUTOMATION_CONFIGS) - an edit through this tool must not come
    out modern."""
    from tools.automations import update_automation

    result = update_automation("automation.nas_shutdown", name="NAS shutdown v2")

    assert result["stored_format"] == "legacy"
    stored = fake_ha.automation_configs["1684270733500"]
    assert stored["alias"] == "NAS shutdown v2"
    assert "trigger" in stored and "triggers" not in stored
    assert "condition" in stored and "conditions" not in stored
    assert "action" in stored and "actions" not in stored
    assert stored["action"][0]["service"] == "button.press"


def test_update_automation_mode_can_be_set(fake_ha):
    from tools.automations import update_automation

    result = update_automation("automation.nas_shutdown", mode="restart")

    assert result["updated"] == ["mode"]
    assert fake_ha.automation_configs["1684270733500"]["mode"] == "restart"


def test_update_automation_passes_through_unknown_structures_untouched(fake_ha):
    """A device trigger and a nested if/then branch marked enabled: false
    survive an edit because they are never parsed - only the top-level
    fields update_automation() knows about are ever replaced."""
    from tools.automations import update_automation

    fake_ha.states.append({"entity_id": "automation.complex", "state": "on",
                           "attributes": {"id": "complex", "friendly_name": "Complex"}})
    fake_ha.automation_configs["complex"] = {
        "alias": "Complex",
        "triggers": [{"device_id": "dev1", "domain": "sensor", "type": "motion",
                      "entity_id": "binary_sensor.motion"}],
        "conditions": [],
        "actions": [
            {"if": [{"condition": "state", "entity_id": "light.kitchen", "state": "on"}],
             "then": [{"action": "light.turn_off", "target": {"entity_id": "light.kitchen"}}],
             "enabled": False},
        ],
        "mode": "single",
    }

    result = update_automation("automation.complex", name="Complex renamed")

    assert result["updated"] == ["name"]
    stored = fake_ha.automation_configs["complex"]
    assert stored["alias"] == "Complex renamed"
    assert stored["triggers"][0]["device_id"] == "dev1"
    assert stored["actions"][0]["enabled"] is False
    assert stored["actions"][0]["if"][0]["entity_id"] == "light.kitchen"
    assert stored["actions"][0]["then"][0]["action"] == "light.turn_off"


def test_update_automation_yaml_defined_returns_not_found(fake_ha):
    """automation.morning (DEFAULT_STATES) has a state but no config id -
    the shape a YAML-defined automation has."""
    from tools.automations import update_automation

    result = update_automation("automation.morning", name="renamed")

    assert result["error"] == "not_found"
    assert "YAML" in result["detail"]
    assert result["entity_id"] == "automation.morning"


def test_update_automation_no_fields_passed_writes_nothing(fake_ha):
    """A fully no-op call must not resubmit the config - see the module
    docstring's caveat that Home Assistant's own write endpoint renames
    the root vocabulary on every save, so a pointless write would migrate
    a legacy file for no reason at all."""
    from tools.automations import update_automation

    result = update_automation("automation.nas_shutdown")

    assert result["updated"] == []
    assert not any(
        c.method == "POST"
        and c.url.path == "/api/config/automation/config/1684270733500"
        for c in fake_ha.rest_calls
    )


def test_update_automation_disabling_verifies_off(fake_ha):
    from tools.automations import update_automation

    fake_ha.sequence_states("automation.nas_shutdown", [
        {"entity_id": "automation.nas_shutdown", "state": "off",
         "attributes": {"id": "1684270733500"}},
    ])

    result = update_automation("automation.nas_shutdown", enabled=False)

    assert result["enabled"] is False
    assert result["verified"] is True
    assert result["state"] == "off"
    assert result["updated"] == []
    assert any(c.url.path == "/api/services/automation/turn_off"
              for c in fake_ha.rest_calls)


def test_update_automation_disable_reports_error_when_it_stays_armed(fake_ha):
    """The founding bug, for update_automation() this time: enabled=False
    requested, but the automation is still 'on' afterward. Must not be a
    bare success. No overrides needed - nas_shutdown's DEFAULT_STATES
    entry is 'on', and /api/services/* never mutates fakeha's state."""
    from tools.automations import update_automation

    result = update_automation("automation.nas_shutdown", enabled=False)

    assert result["error"] == "automation_not_disabled"
    assert result["state"] == "on"
    assert result["enabled"] is True
    assert result["automation_id"] == "1684270733500"
    assert result["updated"] == []


def test_update_automation_enabling_a_disabled_automation_verifies_on(fake_ha):
    """update_automation() can re-enable, not just disable - a capability
    create_automation() never needed, since a fresh automation is already
    armed by default."""
    from tools.automations import update_automation

    fake_ha.sequence_states("automation.nas_shutdown", [
        {"entity_id": "automation.nas_shutdown", "state": "off",
         "attributes": {"id": "1684270733500"}},
        {"entity_id": "automation.nas_shutdown", "state": "on",
         "attributes": {"id": "1684270733500"}},
    ])

    result = update_automation("automation.nas_shutdown", enabled=True)

    assert result["enabled"] is True
    assert result["verified"] is True
    assert result["state"] == "on"
    assert any(c.url.path == "/api/services/automation/turn_on"
              for c in fake_ha.rest_calls)


def test_update_automation_enable_toggle_reports_not_registered_when_entity_vanishes(fake_ha):
    """The config still resolves (by slug, matching the id
    create_automation() uses by construction), but the entity itself never
    answers - the toggle must be refused, not sent blind, and must be
    reported as unregistered rather than a bare success."""
    from tools.automations import create_automation, update_automation

    create_automation("Morning lights", trigger=[], action=[])
    fake_ha.delay_registration("automation.morning_lights", reads=99)

    result = update_automation("automation.morning_lights", enabled=False)

    assert result["error"] == "automation_not_registered"
    assert not any(c.url.path == "/api/services/automation/turn_off"
                  for c in fake_ha.rest_calls)


# ---- patch_automation() --------------------------------------------------

def test_patch_automation_changes_one_value_and_reports_old(fake_ha):
    from tools.automations import patch_automation

    result = patch_automation(
        "automation.nas_shutdown", "conditions.0.value_template",
        "{{ is_state('button.nas_shutdown_v2', 'unavailable') }}",
    )

    assert result["old"] == "{{ is_state('button.nas_shutdown', 'unavailable') }}"
    assert result["new"] == "{{ is_state('button.nas_shutdown_v2', 'unavailable') }}"
    assert result["stored_format"] == "legacy"
    stored = fake_ha.automation_configs["1684270733500"]
    assert (stored["condition"][0]["value_template"]
           == "{{ is_state('button.nas_shutdown_v2', 'unavailable') }}")


def test_patch_automation_legacy_spelled_path_is_accepted(fake_ha):
    """conditions.0... and condition.0... must resolve identically -
    exactly one notation, either root spelling."""
    from tools.automations import patch_automation

    result = patch_automation("automation.nas_shutdown",
                              "condition.0.value_template", "changed")

    assert result["old"] == "{{ is_state('button.nas_shutdown', 'unavailable') }}"
    assert result["new"] == "changed"
    assert fake_ha.automation_configs["1684270733500"]["condition"][0]["value_template"] == "changed"


def test_patch_automation_a_template_deep_inside_actions_is_reached_and_rewritten(fake_ha):
    """The incident this whole plan exists for: one entity_id changed
    inside one action step, everything around it untouched."""
    from tools.automations import get_automation, patch_automation

    before = get_automation("automation.nas_shutdown")["config"]

    result = patch_automation("automation.nas_shutdown",
                              "actions.2.target.entity_id", "switch.nas_power_v2")

    assert result["old"] == "switch.nas_power"
    assert result["new"] == "switch.nas_power_v2"

    after = get_automation("automation.nas_shutdown")["config"]
    assert after["actions"][2]["target"]["entity_id"] == "switch.nas_power_v2"
    # Nothing else moved.
    assert after["triggers"] == before["triggers"]
    assert after["conditions"] == before["conditions"]
    assert after["actions"][0] == before["actions"][0]
    assert after["actions"][1] == before["actions"][1]
    assert after["mode"] == before["mode"]


def test_patch_automation_mistyped_path_returns_bad_path_and_writes_nothing(fake_ha):
    from tools.automations import patch_automation

    before = dict(fake_ha.automation_configs["1684270733500"])

    result = patch_automation("automation.nas_shutdown",
                              "conditions.0.valeu_template", "x")

    assert result["error"] == "bad_path"
    assert "valeu_template" in result["detail"]
    assert fake_ha.automation_configs["1684270733500"] == before
    assert not any(
        c.method == "POST"
        and c.url.path == "/api/config/automation/config/1684270733500"
        for c in fake_ha.rest_calls
    )


def test_patch_automation_index_out_of_range_returns_bad_path(fake_ha):
    from tools.automations import patch_automation

    result = patch_automation("automation.nas_shutdown", "actions.99.service", "x")

    assert result["error"] == "bad_path"
    assert fake_ha.automation_configs["1684270733500"]["action"][2]["service"] == "switch.turn_off"


def test_patch_automation_yaml_defined_returns_not_found(fake_ha):
    from tools.automations import patch_automation

    result = patch_automation("automation.morning", "trigger.0.platform", "x")

    assert result["error"] == "not_found"
    assert "YAML" in result["detail"]
