"""get_template_helper() and update_template_helper(): reading and editing a
UI-created template helper in place, through the config entry's options flow.

The fake's options flow (tests/fakeha.py) mirrors what a real Home Assistant
2026.11.3 did when probed: the flow opens straight on the entry's own type,
current values ride in description.suggested_value, a submit REPLACES the
stored options (a field left out is wiped), and a 400 leaves the flow open.
"""
import httpx
import pytest

from tools.helpers import get_template_helper, update_template_helper

SENSOR = "01TEMPLATESENSOR"
MARKETPLACE = ("{{ integration_entities('marketplace') | select('match', 'update\\.') "
               "| select('is_state', 'on') | list | count }}")


def _options_flow_calls(fake_ha, method=None):
    return [c for c in fake_ha.rest_calls
            if "/api/config/config_entries/options/flow" in c.url.path
            and (method is None or c.method == method)]


def _submits(fake_ha):
    return [c for c in _options_flow_calls(fake_ha, "POST")
            if c.url.path != "/api/config/config_entries/options/flow"]


# ---- get_template_helper ---------------------------------------------------

def test_get_reads_type_and_current_fields_including_section_ones(fake_ha):
    result = get_template_helper(SENSOR)

    assert "error" not in result
    assert result["entry_id"] == SENSOR
    assert result["title"] == "HACS update disponibili"
    assert result["template_type"] == "sensor"
    assert result["fields"] == {
        "state": "{{ integration_entities('hacs') | count }}",
        "unit_of_measurement": "updates",
        "state_class": "measurement",
        "availability": "{{ true }}",
    }
    by_name = {f["name"]: f for f in result["available_fields"]}
    assert by_name["state"] == {"name": "state", "required": True, "section": None}
    assert by_name["availability"]["section"] == "additional_options"
    assert "device_class" in by_name and "name" not in by_name


def test_get_always_closes_the_flow_it_opened(fake_ha):
    get_template_helper(SENSOR)

    assert fake_ha.options_flows == {}
    assert len(_options_flow_calls(fake_ha, "DELETE")) == 1


def test_get_closes_the_flow_even_when_reading_the_form_fails(fake_ha, monkeypatch):
    import tools.helpers

    def boom(schema):
        raise RuntimeError("unexpected form shape")

    monkeypatch.setattr(tools.helpers, "_form_values", boom)

    with pytest.raises(RuntimeError):
        get_template_helper(SENSOR)

    assert fake_ha.options_flows == {}


def test_get_accepts_an_entity_id_and_resolves_its_config_entry(fake_ha):
    fake_ha.registry.append({"entity_id": "sensor.hacs_update_disponibili",
                             "config_entry_id": SENSOR, "labels": ["hacs"]})

    result = get_template_helper("sensor.hacs_update_disponibili")

    assert result["entry_id"] == SENSOR
    assert result["template_type"] == "sensor"
    assert result["entity_ids"] == ["sensor.hacs_update_disponibili"]


def test_get_refuses_an_entity_with_no_config_entry(fake_ha):
    """A YAML-defined template entity has no config entry, hence no
    options flow: refused by name, not 'not found'."""
    fake_ha.registry.append({"entity_id": "sensor.yaml_one", "config_entry_id": None})

    result = get_template_helper("sensor.yaml_one")

    assert result["error"] == "not_a_template_helper"
    assert _options_flow_calls(fake_ha) == []


def test_get_reports_an_unknown_entity_id(fake_ha):
    result = get_template_helper("sensor.nope")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "sensor.nope"
    assert _options_flow_calls(fake_ha) == []


def test_get_refuses_a_config_entry_of_another_domain(fake_ha):
    result = get_template_helper("01SUNENTRY")

    assert result["error"] == "not_a_template_helper"
    assert result["domain"] == "sun"
    assert _options_flow_calls(fake_ha) == []


def test_get_reports_an_unknown_entry_without_opening_a_flow(fake_ha):
    """Home Assistant answers an options flow for an unknown entry_id with
    a bare 500 - the tool must say 'not found' before ever getting there."""
    result = get_template_helper("01DOESNOTEXIST")

    assert result["error"] == "entry_not_found"
    assert _options_flow_calls(fake_ha) == []


def test_get_reports_a_refused_flow_start_as_an_error(fake_ha):
    fake_ha.fail_rest("/api/config/config_entries/options/flow", status=401,
                      message="Unauthorized")

    result = get_template_helper(SENSOR)

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 401


# ---- update_template_helper ------------------------------------------------

def test_update_changes_only_the_template_and_keeps_every_other_field(fake_ha):
    result = update_template_helper(SENSOR, state_template=MARKETPLACE)

    assert "error" not in result, result
    assert fake_ha.template_options[SENSOR]["options"] == {
        "state": MARKETPLACE,
        "unit_of_measurement": "updates",
        "state_class": "measurement",
        "additional_options": {"availability": "{{ true }}"},
    }
    assert result["changed"] == {"state": {
        "before": "{{ integration_entities('hacs') | count }}", "after": MARKETPLACE}}
    assert result["fields"]["state"] == MARKETPLACE
    assert result["fields"]["unit_of_measurement"] == "updates"
    assert result["entry_id"] == SENSOR
    assert fake_ha.options_flows == {}


def test_update_sends_only_keys_that_are_in_the_form(fake_ha):
    import json

    update_template_helper(SENSOR, state_template="{{ 1 }}")

    sent = json.loads(_submits(fake_ha)[0].content)
    assert set(sent) == {"state", "unit_of_measurement", "state_class", "additional_options"}
    assert "name" not in sent and "template_type" not in sent


def test_update_sets_a_section_field_by_its_flat_name(fake_ha):
    result = update_template_helper(SENSOR, availability_template="{{ is_state('sun.sun', 'above_horizon') }}")

    assert "error" not in result, result
    stored = fake_ha.template_options[SENSOR]["options"]
    assert stored["additional_options"] == {
        "availability": "{{ is_state('sun.sun', 'above_horizon') }}"}
    assert stored["state"] == "{{ integration_entities('hacs') | count }}"


def test_update_clears_a_field_given_as_empty_string(fake_ha):
    result = update_template_helper(SENSOR, unit_of_measurement="")

    assert "error" not in result, result
    stored = fake_ha.template_options[SENSOR]["options"]
    assert "unit_of_measurement" not in stored
    assert stored["state_class"] == "measurement"
    assert result["changed"] == {"unit_of_measurement": {"before": "updates", "after": None}}


def test_update_maps_state_template_to_value_template_on_a_switch(fake_ha):
    fake_ha.config_entries.append({"entry_id": "01SWITCH", "domain": "template",
                                   "title": "T switch", "supports_options": True})
    fake_ha.template_options["01SWITCH"] = {
        "template_type": "switch",
        "options": {"value_template": "{{ true }}", "turn_on": [{"action": "x.y"}]},
    }

    result = update_template_helper("01SWITCH", state_template="{{ false }}")

    assert "error" not in result, result
    assert fake_ha.template_options["01SWITCH"]["options"] == {
        "value_template": "{{ false }}", "turn_on": [{"action": "x.y"}]}


def test_update_refuses_state_template_on_a_type_without_one(fake_ha):
    fake_ha.config_entries.append({"entry_id": "01BUTTON", "domain": "template",
                                   "title": "T button", "supports_options": True})
    fake_ha.template_options["01BUTTON"] = {"template_type": "button",
                                            "options": {"press": []}}

    result = update_template_helper("01BUTTON", state_template="{{ 1 }}")

    assert result["error"] == "unknown_fields"
    assert _submits(fake_ha) == []
    assert fake_ha.options_flows == {}


def test_update_accepts_other_form_fields_through_fields(fake_ha):
    delay = {"hours": 0, "minutes": 0, "seconds": 5}
    result = update_template_helper("01TEMPLATEBINARY", fields={"delay_on": delay})

    assert "error" not in result, result
    stored = fake_ha.template_options["01TEMPLATEBINARY"]["options"]
    assert stored == {"state": "{{ false }}", "device_class": "update",
                      "additional_options": {"delay_on": delay}}


def test_update_reports_a_duration_given_as_a_string(fake_ha):
    """Measured: Home Assistant wants a duration as a dict, and refuses
    '00:00:05' - the refusal must reach the caller, with nothing saved."""
    result = update_template_helper("01TEMPLATEBINARY", fields={"delay_on": "00:00:05"})

    assert result["error"] == "validation_failed"
    assert "additional_options.delay_on" in result["errors"]["base"][0]
    assert "additional_options" not in fake_ha.template_options["01TEMPLATEBINARY"]["options"]


def test_update_refuses_a_field_not_in_the_form_and_closes_the_flow(fake_ha):
    result = update_template_helper(SENSOR, fields={"name": "Renamed"})

    assert result["error"] == "unknown_fields"
    assert result["unknown"] == ["name"]
    assert "state" in result["allowed"]
    assert _submits(fake_ha) == []
    assert fake_ha.options_flows == {}


def test_update_refuses_the_same_field_given_twice(fake_ha):
    result = update_template_helper(SENSOR, state_template="{{ 1 }}",
                                    fields={"state": "{{ 2 }}"})

    assert result["error"] == "conflicting_fields"
    assert _options_flow_calls(fake_ha) == []


def test_update_with_nothing_to_change_opens_no_flow(fake_ha):
    result = update_template_helper(SENSOR)

    assert result["error"] == "nothing_to_update"
    assert _options_flow_calls(fake_ha) == []


def test_update_reports_home_assistant_validation_error_with_schema_and_payload(fake_ha):
    before = dict(fake_ha.template_options[SENSOR]["options"])

    result = update_template_helper(SENSOR, state_template="{{ 7 ")

    assert result["error"] == "validation_failed"
    assert "TemplateSyntaxError" in result["errors"]["state"]
    assert result["payload_sent"]["state"] == "{{ 7 "
    assert result["payload_sent"]["unit_of_measurement"] == "updates"
    assert any(f["name"] == "state" for f in result["schema"])
    assert fake_ha.template_options[SENSOR]["options"] == before
    # A 400 leaves the flow open on Home Assistant's side: it must be closed.
    assert fake_ha.options_flows == {}


def test_update_reports_a_plain_text_400_too(fake_ha):
    """Measured: an inconsistent device_class/unit pair answers 400 with a
    plain-text body '400: Bad Request', not JSON."""
    fake_ha.rest_responses.clear()
    real = fake_ha._options_flow

    def plain_400(request, path):
        if request.method == "POST" and path != "/api/config/config_entries/options/flow":
            return httpx.Response(400, text="400: Bad Request")
        return real(request, path)

    fake_ha._options_flow = plain_400

    result = update_template_helper(SENSOR, fields={"device_class": "temperature"})

    assert result["error"] == "validation_failed"
    assert result["errors"] == "400: Bad Request"
    assert fake_ha.options_flows == {}


def test_update_closes_the_flow_when_the_submit_raises(fake_ha):
    fake_ha.options_submit_raises = httpx.ReadTimeout("timed out")

    with pytest.raises(httpx.ReadTimeout):
        update_template_helper(SENSOR, state_template="{{ 1 }}")

    assert fake_ha.options_flows == {}


def test_update_reports_a_write_that_did_not_land(fake_ha):
    fake_ha.options_submit_discarded = True

    result = update_template_helper(SENSOR, state_template=MARKETPLACE)

    assert result["error"] == "config_write_unverified"
    assert result["intended"]["state"] == MARKETPLACE
    assert result["observed"]["state"] == "{{ integration_entities('hacs') | count }}"
    assert fake_ha.options_flows == {}


def test_update_refuses_a_non_template_entry(fake_ha):
    result = update_template_helper("01SUNENTRY", state_template="{{ 1 }}")

    assert result["error"] == "not_a_template_helper"
    assert _options_flow_calls(fake_ha) == []


def test_update_resolves_an_entity_id(fake_ha):
    fake_ha.registry.append({"entity_id": "sensor.hacs_update_disponibili",
                             "config_entry_id": SENSOR, "labels": ["hacs"]})

    result = update_template_helper("sensor.hacs_update_disponibili",
                                    state_template=MARKETPLACE)

    assert "error" not in result, result
    assert result["entry_id"] == SENSOR
    assert result["entity_ids"] == ["sensor.hacs_update_disponibili"]
    assert fake_ha.template_options[SENSOR]["options"]["state"] == MARKETPLACE
