"""tools/automations.py: the id resolution shared by get_automation(),
create_automation() and delete_automation() (_resolve_automation_id(),
_fetch_config()), and get_automation()'s own shape.

create_automation()'s and delete_automation()'s own behavioural tests stay
where they already were (tests/test_tools_shape.py, tests/test_actuation.py)
- this file covers only what changed here: get_automation() itself, and the
id resolution now shared by all three instead of written out three times.
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
