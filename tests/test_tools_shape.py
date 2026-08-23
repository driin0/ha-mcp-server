def test_list_automations_returns_an_envelope(fake_ha):
    from tools.automations import list_automations

    result = list_automations()

    assert result["total"] == 2
    assert result["returned"] == 2
    assert [row["name"] for row in result["automations"]] == ["Morning", "NAS shutdown"]


def test_list_automations_filters_by_label(fake_ha):
    from tools.automations import list_automations

    result = list_automations(label="power")

    assert result["total"] == 1
    assert result["automations"][0]["entity_id"] == "automation.nas_shutdown"
    assert result["automations"][0]["labels"] == ["power"]


def test_list_automations_announces_truncation(fake_ha):
    from tools.automations import list_automations

    fake_ha.states = [
        {"entity_id": f"automation.a{n}", "state": "on",
         "attributes": {"friendly_name": f"A{n:03d}"}}
        for n in range(140)
    ]
    fake_ha.registry = []

    result = list_automations(limit=3)

    assert result["total"] == 140
    assert result["returned"] == 3
    assert "3 of 140" in result["note"]


def test_list_automations_says_when_nothing_matches(fake_ha):
    from tools.automations import list_automations

    result = list_automations(search="no such thing")

    assert result["total"] == 0
    assert result["automations"] == []
    assert result["note"] == "no automations found"


def test_list_automations_refuses_to_filter_by_a_label_it_could_not_read(fake_ha):
    """An empty list here would be indistinguishable from "no automation
    carries that label"."""
    from tools.automations import list_automations

    fake_ha.fail_ws("config/entity_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_automations(label="power")

    assert result["error"] == "unauthorized"
    assert "automations" not in result


def test_list_automations_still_lists_when_the_registry_is_unreadable(fake_ha):
    """Without a label filter the labels are a nicety, not a precondition."""
    from tools.automations import list_automations

    fake_ha.fail_ws("config/entity_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_automations()

    assert result["total"] == 2
    assert all(row["labels"] == [] for row in result["automations"])


def test_device_triggers_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.automations import list_device_triggers

    fake_ha.fail_ws("device_automation/trigger/list",
                    code="not_found", message="Unknown device")

    result = list_device_triggers("abc123")

    assert result["error"] == "not_found"
    assert "triggers" not in result


def test_device_triggers_wraps_a_success(fake_ha):
    from tools.automations import list_device_triggers

    fake_ha.ws_result("device_automation/trigger/list",
                      [{"platform": "device", "type": "turned_on"}])

    result = list_device_triggers("abc123")

    assert result["total"] == 1
    assert result["triggers"][0]["type"] == "turned_on"


def test_trace_list_says_traces_are_volatile(fake_ha):
    from tools.automations import get_automation_trace

    fake_ha.ws_result("trace/list", [])

    result = get_automation_trace("automation.nas_shutdown")

    assert result["total"] == 0
    assert "restart" in result["note"].lower()


def test_device_conditions_uses_its_own_command_and_key(fake_ha):
    from tools.automations import list_device_conditions

    fake_ha.ws_result("device_automation/condition/list",
                      [{"condition": "device", "type": "is_on"}])

    result = list_device_conditions("abc123")

    assert result["total"] == 1
    assert result["conditions"][0]["type"] == "is_on"
    assert fake_ha.ws_calls[-1]["type"] == "device_automation/condition/list"


def test_device_actions_uses_its_own_command_and_key(fake_ha):
    from tools.automations import list_device_actions

    fake_ha.ws_result("device_automation/action/list",
                      [{"domain": "light", "type": "turn_on"}])

    result = list_device_actions("abc123")

    assert result["total"] == 1
    assert result["actions"][0]["type"] == "turn_on"
    assert fake_ha.ws_calls[-1]["type"] == "device_automation/action/list"


def test_blueprints_wraps_a_success(fake_ha):
    from tools.automations import list_blueprints

    fake_ha.ws_result("blueprint/list", {
        "homeassistant/motion_trigger.yaml": {
            "metadata": {
                "name": "Motion trigger",
                "description": "Turn on a light on motion",
                "domain": "automation",
                "input": {"motion_entity": {}, "light_entity": {}},
            },
        },
    })

    result = list_blueprints()

    assert result["total"] == 1
    assert result["blueprints"][0]["path"] == "homeassistant/motion_trigger.yaml"
    assert result["blueprints"][0]["name"] == "Motion trigger"
    assert set(result["blueprints"][0]["input"]) == {"motion_entity", "light_entity"}
    assert fake_ha.ws_calls[-1]["type"] == "blueprint/list"


def test_blueprints_reports_a_ws_failure_as_an_error(fake_ha):
    """list_blueprints gained a ws_error() check in this conversion; nothing
    had exercised the failure branch before this test."""
    from tools.automations import list_blueprints

    fake_ha.fail_ws("blueprint/list", code="unauthorized", message="Admin required")

    result = list_blueprints()

    assert result["error"] == "unauthorized"
    assert "blueprints" not in result


def test_create_automation_from_blueprint_posts_to_the_rest_config_endpoint(fake_ha):
    """config/automation/config/save does not exist as a WebSocket command -
    Home Assistant only exposes automation config writes over REST, at the
    same endpoint create_automation() already posts to. A WS-shaped
    assertion (fake_ha.ws_calls) would pass this vacuously even on the old,
    broken implementation, so this checks the REST call fakeha.py records
    instead."""
    import json as _json

    from tools.automations import create_automation_from_blueprint

    fake_ha.rest_responses["/api/config/automation/config/"] = (
        200, {"result": "ok"})

    result = create_automation_from_blueprint(
        blueprint_path="homeassistant/motion_trigger.yaml",
        alias="Hallway motion",
        input_values={"motion_entity": "binary_sensor.hallway",
                     "light_entity": "light.hallway"},
    )

    sent = fake_ha.rest_calls[-1]
    assert sent.url.path == "/api/config/automation/config/hallway_motion"
    assert sent.method == "POST"
    body = _json.loads(sent.content)
    assert body["alias"] == "Hallway motion"
    assert body["use_blueprint"] == {
        "path": "homeassistant/motion_trigger.yaml",
        "input": {"motion_entity": "binary_sensor.hallway",
                  "light_entity": "light.hallway"},
    }
    assert result["automation_id"] == "hallway_motion"
    assert result["entity_id"] == "automation.hallway_motion"


def test_create_automation_from_blueprint_raises_on_a_failed_call(fake_ha):
    import httpx
    import pytest

    from tools.automations import create_automation_from_blueprint

    fake_ha.fail_rest("/api/config/automation/config/", status=400,
                      message="Message malformed: not a file")

    with pytest.raises(httpx.HTTPStatusError):
        create_automation_from_blueprint(
            blueprint_path="homeassistant/motion_trigger.yaml",
            alias="Hallway motion",
            input_values={},
        )


def test_schedules_wraps_a_success(fake_ha):
    from tools.automations import list_schedules

    fake_ha.ws_result("scheduler/items", [
        {"schedule_id": "sch1", "entity_id": "switch.pump", "name": "Watering",
         "enabled": True, "next_trigger": "2026-08-24T07:00:00+00:00",
         "timeslots": [], "actions": []},
    ])

    result = list_schedules()

    assert result["total"] == 1
    assert result["schedules"][0]["schedule_id"] == "sch1"
    assert result["schedules"][0]["name"] == "Watering"


def test_schedules_unknown_command_reports_not_available(fake_ha):
    """HA answers an unregistered command type when the custom component is
    not loaded — that specific failure is translated to the diagnosis."""
    from tools.automations import list_schedules

    fake_ha.fail_ws("scheduler/items", code="unknown_command",
                    message="Unknown command.")

    result = list_schedules()

    assert result["error"] == "scheduler_not_available"
    assert "schedules" not in result


def test_schedules_other_failure_surfaces_as_itself(fake_ha):
    """A real failure — e.g. missing permissions — must not be reported as
    "the Scheduler component is not installed": that is a cause the tool has
    not established."""
    from tools.automations import list_schedules

    fake_ha.fail_ws("scheduler/items", code="unauthorized", message="Admin required")

    result = list_schedules()

    assert result["error"] == "unauthorized"
    assert result["error"] != "scheduler_not_available"


def test_automation_trace_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.automations import get_automation_trace

    fake_ha.fail_ws("trace/list", code="not_found",
                    message="Unknown automation")

    result = get_automation_trace("automation.nas_shutdown")

    assert result["error"] == "not_found"
    assert "traces" not in result


def test_automation_trace_limit_truncates(fake_ha):
    from tools.automations import get_automation_trace

    fake_ha.ws_result("trace/list", [
        {"run_id": f"run{n}", "state": "stopped", "timestamp": {},
         "last_step": "action/0", "error": None, "script_execution": "finished"}
        for n in range(5)
    ])

    result = get_automation_trace("automation.nas_shutdown", limit=2)

    assert result["total"] == 5
    assert result["returned"] == 2
    assert len(result["traces"]) == 2
    assert result["traces"][0]["run_id"] == "run0"


def test_automation_trace_uses_trace_list_with_domain_and_item_id(fake_ha):
    """automation/trace/list does not exist on Home Assistant - the real
    command is trace/list, scoped to the automation domain via `domain`,
    with the automation's object_id (no 'automation.' prefix) as `item_id`.
    A shape-only assertion cannot catch a wrong command or a wrong argument
    name, so this pins the actual outgoing request."""
    from tools.automations import get_automation_trace

    fake_ha.ws_result("trace/list", [])

    get_automation_trace("automation.nas_shutdown")

    sent = fake_ha.ws_calls[-1]
    assert sent["type"] == "trace/list"
    assert sent["domain"] == "automation"
    assert sent["item_id"] == "nas_shutdown"


# ---- tools/system.py --------------------------------------------------

def test_config_entries_reports_the_failure_it_used_to_hide(fake_ha):
    from tools.system import list_config_entries

    fake_ha.fail_ws("config_entries/get", code="unauthorized",
                    message="Admin required")

    result = list_config_entries()

    assert result["error"] == "unauthorized"
    assert "entries" not in result


def test_config_entries_distinguishes_no_match_from_failure(fake_ha):
    from tools.system import list_config_entries

    fake_ha.ws_result("config_entries/get", [
        {"entry_id": "1", "domain": "hue", "title": "Hue", "state": "loaded"},
    ])

    result = list_config_entries(domain="synology_dsm")

    assert result["total"] == 0
    assert result["entries"] == []
    assert result["note"] == "no entries found"


def test_config_entries_survives_a_null_title(fake_ha):
    """Home Assistant permits title: null and, on a malformed/legacy entry,
    domain: null too; sorting used to raise TypeError on either. Both null
    fields are exercised here so a fix that only guards one half (e.g. a
    merge resolution that drops the other) still fails this test."""
    from tools.system import list_config_entries

    fake_ha.ws_result("config_entries/get", [
        {"entry_id": "1", "domain": "hue", "title": None, "state": "loaded"},
        {"entry_id": "2", "domain": "hue", "title": "Hue", "state": "loaded"},
        {"entry_id": "3", "domain": None, "title": "Orphaned", "state": "loaded"},
    ])

    result = list_config_entries()

    assert result["total"] == 3


def test_config_entries_uses_its_own_command(fake_ha):
    """Four near-identical WS-backed tools live in this file; a copy-paste
    slip on the command name is invisible to a shape-only assertion."""
    from tools.system import list_config_entries

    fake_ha.ws_result("config_entries/get", [])

    list_config_entries()

    assert fake_ha.ws_calls[-1]["type"] == "config_entries/get"


def test_repairs_wraps_a_success(fake_ha):
    from tools.system import list_repairs

    fake_ha.ws_result("repairs/list_issues", {"issues": [
        {"issue_id": "low_battery", "domain": "zwave_js", "severity": "warning",
         "translation_key": "low_battery", "ignored": False,
         "created": "2026-08-01T00:00:00+00:00"},
        {"issue_id": "deprecated_yaml", "domain": "mqtt", "severity": "warning",
         "translation_key": "deprecated_yaml", "ignored": True,
         "created": "2026-08-01T00:00:00+00:00"},
    ]})

    result = list_repairs()

    assert result["total"] == 1
    assert result["repairs"][0]["issue_id"] == "low_battery"
    assert fake_ha.ws_calls[-1]["type"] == "repairs/list_issues"


def test_repairs_reports_a_ws_failure_as_an_error(fake_ha):
    """The §8.2 bug: a failed repairs/list_issues call used to become an
    empty list, indistinguishable from "no active repairs"."""
    from tools.system import list_repairs

    fake_ha.fail_ws("repairs/list_issues", code="unauthorized",
                    message="Admin required")

    result = list_repairs()

    assert result["error"] == "unauthorized"
    assert "repairs" not in result


def test_backups_wraps_a_success(fake_ha):
    from tools.system import list_backups

    fake_ha.ws_result("backup/info", {"backups": [
        {"backup_id": "abc123", "name": "Nightly",
         "date": "2026-08-22T03:00:00+00:00", "size": 2097152, "type": "full",
         "protected": False, "homeassistant_version": "2026.8.1"},
    ]})

    result = list_backups()

    assert result["total"] == 1
    assert result["backups"][0]["backup_id"] == "abc123"
    assert result["backups"][0]["size_mb"] == 2.0
    assert fake_ha.ws_calls[-1]["type"] == "backup/info"


def test_backups_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.system import list_backups

    fake_ha.fail_ws("backup/info", code="unauthorized", message="Admin required")

    result = list_backups()

    assert result["error"] == "unauthorized"
    assert "backups" not in result


def test_updates_wraps_a_success(fake_ha):
    from tools.system import list_updates

    fake_ha.states = [
        {"entity_id": "update.core", "state": "on",
         "attributes": {"friendly_name": "Home Assistant Core",
                        "installed_version": "2026.8.0",
                        "latest_version": "2026.8.1"}},
        {"entity_id": "update.addon_x", "state": "off",
         "attributes": {"friendly_name": "Addon X"}},
    ]

    result = list_updates()

    assert result["total"] == 1
    assert result["updates"][0]["entity_id"] == "update.core"
    assert result["updates"][0]["latest_version"] == "2026.8.1"


def test_updates_says_when_nothing_is_pending(fake_ha):
    from tools.system import list_updates

    fake_ha.states = []

    result = list_updates()

    assert result["total"] == 0
    assert result["updates"] == []
    assert result["note"] == "no updates found"


def test_config_flows_wraps_a_success(fake_ha):
    from tools.system import list_config_flows

    fake_ha.ws_result("config_entries/flow/progress", [
        {"flow_id": "f1", "handler": "hue", "step_id": "init", "context": {}},
    ])

    result = list_config_flows()

    assert result["total"] == 1
    assert result["flows"][0]["flow_id"] == "f1"
    assert fake_ha.ws_calls[-1]["type"] == "config_entries/flow/progress"


def test_config_flows_ws_failure_and_rest_404_reports_the_failure(fake_ha):
    """Both the WS attempt and its REST fallback used to collapse into a
    bare `return []` on total failure — the same failure-hiding pattern
    this task removes, found a second time in the same file. This exercises
    the REST fallback's status_code != 200 branch: FakeHA has no route for
    /api/config/config_entries/flow, so it 404s (a Response, not a raise)."""
    from tools.system import list_config_flows

    fake_ha.fail_ws("config_entries/flow/progress", code="unauthorized",
                    message="Admin required")

    result = list_config_flows()

    assert result["error"] == "unauthorized"
    assert "flows" not in result


def test_config_flows_ws_failure_and_rest_connection_error_reports_both_layers(fake_ha):
    """A bare `except Exception: pass` around the REST fallback used to
    discard its failure entirely, so a caller with both a broken WebSocket
    connection and a broken REST fallback could not tell the second layer
    had even been tried. Both must now be visible in one response.

    This must exercise the except-Exception branch specifically, not the
    status_code != 200 branch above: a 404 is a Response and never reaches
    the except at all, so the REST call is made to raise instead - the way
    a connection-level failure (refused connection, DNS failure) looks to
    httpx, as opposed to a request that reached Home Assistant and got a
    404 back."""
    from tools.system import list_config_flows

    fake_ha.fail_ws("config_entries/flow/progress", code="unauthorized",
                    message="Admin required")
    fake_ha.raise_rest("/api/config/config_entries/flow")

    result = list_config_flows()

    assert result["error"] == "unauthorized"
    assert result["rest_detail"]
    assert "raised" in result["rest_detail"]
    assert "flows" not in result


# ---- tools/diagnostics.py ----

def test_get_states_by_domain_filters_and_wraps(fake_ha):
    from tools.diagnostics import get_states_by_domain

    result = get_states_by_domain("light")

    assert result["total"] == 2
    assert {e["entity_id"] for e in result["entities"]} == {"light.kitchen", "light.study"}


def test_get_states_by_domain_says_when_nothing_matches(fake_ha):
    from tools.diagnostics import get_states_by_domain

    result = get_states_by_domain("climate")

    assert result["total"] == 0
    assert result["entities"] == []
    assert result["note"] == "no entities found"


def test_history_is_a_series_and_has_no_offset(fake_ha):
    from tools.diagnostics import get_history

    fake_ha.history["light.kitchen"] = [
        {"entity_id": "light.kitchen", "state": "off",
         "last_changed": "2026-08-22T20:00:00+00:00"},
        {"entity_id": "light.kitchen", "state": "on",
         "last_changed": "2026-08-23T07:00:00+00:00"},
    ]

    result = get_history("light.kitchen")

    assert "offset" not in result or result["offset"] == 0
    assert result["total"] == 2
    assert [p["state"] for p in result["history"]] == ["off", "on"]


def test_history_note_points_at_hours_not_pagination(fake_ha):
    """No history recorded for this entity - the note must send the caller
    to `hours`, not to an offset/limit that does not exist on this tool."""
    from tools.diagnostics import get_history

    result = get_history("light.kitchen", hours=1)

    assert result["total"] == 0
    assert "widen" in result["note"]
    assert "hours" in result["note"]
    assert "offset" not in result["note"]


def test_history_raises_on_a_failed_call(fake_ha):
    """A non-2xx response - a missing/disabled history endpoint, a bad
    token - must surface as a failure, not silently become "no data"."""
    import httpx
    import pytest

    from tools.diagnostics import get_history

    fake_ha.fail_rest("/api/history/period/", status=404, message="Not Found")

    with pytest.raises(httpx.HTTPStatusError):
        get_history("light.kitchen")


def test_logbook_wraps_a_real_result_under_its_own_key(fake_ha):
    from tools.diagnostics import get_logbook

    fake_ha.logbook = [
        {"when": "2026-08-23T07:00:00.000000+00:00", "domain": "light",
         "name": "Kitchen", "message": "turned on", "entity_id": "light.kitchen"},
        {"when": "2026-08-23T07:01:00.000000+00:00", "domain": "",
         "message": "ignored - no domain"},
    ]

    result = get_logbook()

    assert result["total"] == 1
    assert result["events"][0]["entity_id"] == "light.kitchen"
    assert result["events"][0]["message"] == "turned on"


def test_logbook_says_when_nothing_matches(fake_ha):
    from tools.diagnostics import get_logbook

    result = get_logbook()

    assert result["total"] == 0
    assert result["events"] == []


def test_logbook_raises_on_a_failed_call(fake_ha):
    import httpx
    import pytest

    from tools.diagnostics import get_logbook

    fake_ha.fail_rest("/api/logbook/", status=401, message="Unauthorized")

    with pytest.raises(httpx.HTTPStatusError):
        get_logbook()


def test_list_entities_by_integration_wraps_a_success(fake_ha):
    from tools.diagnostics import list_entities_by_integration

    fake_ha.ws_result("config/entity_registry/list", [
        {"entity_id": "light.hue_1", "platform": "hue", "name": "Hue 1",
         "area_id": "living_room", "labels": [], "disabled_by": None},
        {"entity_id": "light.kitchen", "platform": "other", "name": "Kitchen",
         "area_id": "kitchen", "labels": [], "disabled_by": None},
    ])

    result = list_entities_by_integration("hue")

    assert result["total"] == 1
    assert result["entities"][0]["entity_id"] == "light.hue_1"
    assert fake_ha.ws_calls[-1]["type"] == "config/entity_registry/list"


def test_list_entities_by_integration_reports_its_own_truncation(fake_ha):
    """Mirrors the search_entities fix: the loop used to break at `limit`
    and never learned the true total."""
    from tools.diagnostics import list_entities_by_integration

    fake_ha.ws_result("config/entity_registry/list", [
        {"entity_id": f"sensor.s{n}", "platform": "shelly", "name": f"S{n}",
         "area_id": None, "labels": [], "disabled_by": None}
        for n in range(30)
    ])

    result = list_entities_by_integration("shelly", limit=5)

    assert result["returned"] == 5
    assert result["total"] == 30


def test_list_entities_by_integration_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.diagnostics import list_entities_by_integration

    fake_ha.fail_ws("config/entity_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_entities_by_integration("hue")

    assert result["error"] == "unauthorized"
    assert "entities" not in result


def test_get_entity_exposure_wraps_a_success(fake_ha):
    """homeassistant/expose_entity/list answers with exposed_entities as a
    dict keyed by entity_id (assistant_id -> bool), not a list of records -
    a different shape from the nonexistent command this used to call."""
    from tools.diagnostics import get_entity_exposure

    fake_ha.ws_result("homeassistant/expose_entity/list", {"exposed_entities": {
        "light.kitchen": {"conversation": True, "cloud.alexa": False},
        "sensor.temp": {},
    }})

    result = get_entity_exposure()

    assert result["total"] == 1
    assert result["entities"][0]["entity_id"] == "light.kitchen"
    assert result["entities"][0]["assistants"] == {"conversation": True, "cloud.alexa": False}


def test_get_entity_exposure_uses_its_own_command_with_no_arguments(fake_ha):
    """conversation/expose_entity/list does not exist on Home Assistant. The
    real command is homeassistant/expose_entity/list, and - unlike the
    command this used to send - it takes no arguments at all: Home
    Assistant always reports every exposed entity, it cannot be scoped to
    a set of entity_ids."""
    from tools.diagnostics import get_entity_exposure

    fake_ha.ws_result("homeassistant/expose_entity/list", {"exposed_entities": {}})

    get_entity_exposure()

    sent = fake_ha.ws_calls[-1]
    assert sent["type"] == "homeassistant/expose_entity/list"
    assert set(sent.keys()) == {"type"}


def test_get_entity_exposure_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.diagnostics import get_entity_exposure

    fake_ha.fail_ws("homeassistant/expose_entity/list", code="unauthorized",
                    message="Admin required")

    result = get_entity_exposure()

    assert result["error"] == "unauthorized"
    assert "entities" not in result


def test_search_entities_reports_its_own_truncation(fake_ha):
    from tools.diagnostics import search_entities

    fake_ha.states = [
        {"entity_id": f"sensor.s{n}", "state": "1",
         "attributes": {"friendly_name": f"S{n}"}}
        for n in range(80)
    ]
    fake_ha.registry = []

    result = search_entities(query="", domain="sensor", limit=10)

    assert result["returned"] == 10
    assert result["total"] == 80
    assert "10 of 80" in result["note"]


def test_search_entities_uses_its_own_command(fake_ha):
    from tools.diagnostics import search_entities

    search_entities(query="kitchen")

    assert fake_ha.ws_calls[-1]["type"] == "config/entity_registry/list"


def test_search_entities_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.diagnostics import search_entities

    fake_ha.fail_ws("config/entity_registry/list", code="unauthorized",
                    message="Admin required")

    result = search_entities(query="kitchen")

    assert result["error"] == "unauthorized"
    assert "entities" not in result


def test_call_service_passes_the_response_through_untouched(fake_ha):
    from tools.diagnostics import call_service

    result = call_service("light", "turn_on", entity_id="light.kitchen")

    assert "result" in result


def test_call_service_posts_to_the_right_endpoint_with_merged_data(fake_ha):
    import json as _json

    from tools.diagnostics import call_service

    call_service("light", "turn_on", entity_id="light.kitchen",
                 service_data={"brightness_pct": 80})

    sent = fake_ha.rest_calls[-1]
    assert sent.url.path == "/api/services/light/turn_on"
    assert _json.loads(sent.content) == {
        "brightness_pct": 80,
        "entity_id": "light.kitchen",
    }


def test_call_service_raises_on_a_failed_call(fake_ha):
    """A non-2xx response means the call never reached the service, or was
    refused - it must not come back looking like {"result": <success>}."""
    import httpx
    import pytest

    from tools.diagnostics import call_service

    fake_ha.fail_rest("/api/services/light/turn_on", status=401,
                      message="Unauthorized")

    with pytest.raises(httpx.HTTPStatusError):
        call_service("light", "turn_on", entity_id="light.kitchen")


def test_get_entity_dependencies_caps_the_searched_count_at_the_probe_limit(fake_ha):
    """total_automations_searched used to report len(automations) - the
    unsliced list - while the probe loop only ever walks automations[:200].
    On an instance with more than 200 automations that overstated how much
    was actually searched."""
    from tools.diagnostics import get_entity_dependencies

    fake_ha.states = [
        {"entity_id": f"automation.a{n}", "state": "on",
         "attributes": {"friendly_name": f"A{n}", "id": f"a{n}"}}
        for n in range(250)
    ]

    result = get_entity_dependencies("light.kitchen")

    assert result["total_automations_searched"] == 200
    assert result["total_scripts_searched"] == 0


def test_get_entity_dependencies_counts_failed_checks(fake_ha):
    """FakeHA has no route for /api/config/automation/config/<id>, so every
    probe 404s by default - a failed check, not evidence the automation
    does not reference the entity. failed_checks must say so, and a note
    must warn the answer may be incomplete."""
    from tools.diagnostics import get_entity_dependencies

    fake_ha.states = [
        {"entity_id": "automation.a1", "state": "on",
         "attributes": {"friendly_name": "A1", "id": "a1"}},
        {"entity_id": "automation.a2", "state": "on",
         "attributes": {"friendly_name": "A2", "id": "a2"}},
    ]

    result = get_entity_dependencies("light.kitchen")

    assert result["failed_checks"] == 2
    assert "may be incomplete" in result["note"]


def test_get_entity_dependencies_failed_checks_rises_with_more_failed_probes(fake_ha):
    """One probe succeeding and one failing must report exactly one failed
    check, not zero (fails open) and not two (over-counts a good probe)."""
    from tools.diagnostics import get_entity_dependencies

    fake_ha.states = [
        {"entity_id": "automation.a1", "state": "on",
         "attributes": {"friendly_name": "A1", "id": "a1"}},
        {"entity_id": "automation.a2", "state": "on",
         "attributes": {"friendly_name": "A2", "id": "a2"}},
    ]
    # a1's probe succeeds (200, no reference inside); a2's has no fake
    # route and 404s, the way an authorisation failure or a deleted
    # automation would.
    fake_ha.rest_responses["/api/config/automation/config/a1"] = (
        200, {"trigger": [], "condition": [], "action": []})

    result = get_entity_dependencies("light.kitchen")

    assert result["failed_checks"] == 1
    assert result["automations"] == []


# ---- tools/areas.py -----------------------------------------------------

def test_list_areas_wraps_a_success(fake_ha):
    from tools.areas import list_areas

    fake_ha.ws_result("config/area_registry/list", [
        {"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"},
    ])
    fake_ha.ws_result("config/floor_registry/list", [
        {"floor_id": "ground", "name": "Ground floor", "level": 0},
    ])
    fake_ha.template_response = [{"area_id": "kitchen", "entities": ["light.kitchen"]}]

    result = list_areas()

    assert result["total"] == 1
    assert result["areas"][0]["area_id"] == "kitchen"
    assert result["areas"][0]["floor_name"] == "Ground floor"
    assert result["areas"][0]["entities"] == ["light.kitchen"]
    assert fake_ha.ws_calls[-2]["type"] == "config/area_registry/list"
    assert fake_ha.ws_calls[-1]["type"] == "config/floor_registry/list"


def test_list_areas_reports_a_ws_failure_as_an_error(fake_ha):
    """The area registry call failing must not become an empty `areas: []`
    - that reads as "no areas configured", not "the call failed"."""
    from tools.areas import list_areas

    fake_ha.fail_ws("config/area_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_areas()

    assert result["error"] == "unauthorized"
    assert "areas" not in result


def test_list_areas_reports_a_floor_registry_failure_too(fake_ha):
    """The area call can succeed while the floor call - the second command
    in the same batch - fails; that must surface too, not be swallowed."""
    from tools.areas import list_areas

    fake_ha.ws_result("config/area_registry/list", [
        {"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"},
    ])
    fake_ha.fail_ws("config/floor_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_areas()

    assert result["error"] == "unauthorized"
    assert "areas" not in result


def test_list_devices_reports_a_ws_failure_as_an_error(fake_ha):
    """A failed device registry read must not become an empty `devices: []`
    - that reads as "no devices exist", not "the call failed"."""
    from tools.areas import list_devices

    fake_ha.fail_ws("config/device_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_devices()

    assert result["error"] == "unauthorized"
    assert "devices" not in result


def test_list_devices_wraps_a_success_through_the_shared_envelope(fake_ha):
    """list_devices used to hand-roll its own {total, returned, offset,
    devices} dict, which never emitted a truncation note. envelope() does."""
    from tools.areas import list_devices

    fake_ha.ws_result("config/device_registry/list", [
        {"id": f"d{n}", "name": f"Device {n:02d}"} for n in range(5)
    ])

    result = list_devices(limit=2)

    assert result["total"] == 5
    assert result["returned"] == 2
    assert "2 of 5" in result["note"]


def test_list_labels_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import list_labels

    fake_ha.fail_ws("config/label_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_labels()

    assert result["error"] == "unauthorized"
    assert "labels" not in result


def test_list_floors_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import list_floors

    fake_ha.fail_ws("config/floor_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_floors()

    assert result["error"] == "unauthorized"
    assert "floors" not in result


def test_get_entity_registry_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import get_entity_registry

    fake_ha.fail_ws("config/entity_registry/get", code="not_found",
                    message="Entity not registered")

    result = get_entity_registry("light.ghost")

    assert result["error"] == "not_found"
    assert "entity_id" not in result


def test_entity_labels_names_its_entity(fake_ha):
    from tools.areas import get_entity_labels

    fake_ha.ws_responses["config/entity_registry/get"] = {
        "id": 1, "type": "result", "success": True,
        "result": {"entity_id": "automation.nas_shutdown", "labels": ["power"]},
    }

    result = get_entity_labels("automation.nas_shutdown")

    assert result == {"entity_id": "automation.nas_shutdown", "labels": ["power"]}
    assert fake_ha.ws_calls[-1]["type"] == "config/entity_registry/get"


def test_entity_labels_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import get_entity_labels

    fake_ha.fail_ws("config/entity_registry/get", code="not_found",
                    message="Entity not registered")

    result = get_entity_labels("light.ghost")

    assert result["error"] == "not_found"
    assert "labels" not in result


def test_list_zones_wraps_a_success(fake_ha):
    from tools.areas import list_zones

    fake_ha.states = [
        {"entity_id": "zone.home", "state": "zoning",
         "attributes": {"friendly_name": "Home", "latitude": 45.0,
                        "longitude": 9.0, "radius": 100}},
    ]

    result = list_zones()

    assert result["total"] == 1
    assert result["zones"][0]["entity_id"] == "zone.home"
    assert result["zones"][0]["radius"] == 100


def test_list_zones_says_when_nothing_matches(fake_ha):
    from tools.areas import list_zones

    fake_ha.states = []

    result = list_zones()

    assert result["total"] == 0
    assert result["zones"] == []
    assert result["note"] == "no zones found"


# ---- tools/calendar.py ---------------------------------------------------

def test_list_calendars_wraps_a_success(fake_ha):
    from tools.calendar import list_calendars

    fake_ha.calendars = [{"entity_id": "calendar.home", "name": "Home"}]

    result = list_calendars()

    assert result["total"] == 1
    assert result["calendars"][0]["entity_id"] == "calendar.home"


def test_list_calendars_treats_a_missing_endpoint_as_none_not_a_failure(fake_ha):
    """Home Assistant only registers /api/calendars once the calendar
    integration has loaded - its 404 means "none", not a failure, and that
    distinction must survive the move to the envelope."""
    from tools.calendar import list_calendars

    fake_ha.fail_rest("/api/calendars", status=404)

    result = list_calendars()

    assert result["total"] == 0
    assert result["calendars"] == []
    assert "error" not in result


def test_get_calendar_events_wraps_a_success(fake_ha):
    from tools.calendar import get_calendar_events

    fake_ha.calendar_events["calendar.home"] = [
        {"summary": "Dentist", "start": {"dateTime": "2026-08-25T09:00:00+02:00"}},
    ]

    result = get_calendar_events("calendar.home")

    assert result["total"] == 1
    assert result["events"][0]["summary"] == "Dentist"


# ---- tools/dashboards.py --------------------------------------------------

def test_list_dashboards_wraps_a_success(fake_ha):
    from tools.dashboards import list_dashboards

    fake_ha.ws_result("lovelace/dashboards/list", [
        {"url_path": "energia", "title": "Energia", "mode": "storage",
         "show_in_sidebar": True, "require_admin": False},
    ])

    result = list_dashboards()

    assert result["total"] == 1
    assert result["dashboards"][0]["url_path"] == "energia"
    assert fake_ha.ws_calls[-1]["type"] == "lovelace/dashboards/list"


def test_list_dashboards_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.dashboards import list_dashboards

    fake_ha.fail_ws("lovelace/dashboards/list", code="unauthorized",
                    message="Admin required")

    result = list_dashboards()

    assert result["error"] == "unauthorized"
    assert "dashboards" not in result


def test_list_lovelace_resources_wraps_a_success(fake_ha):
    from tools.dashboards import list_lovelace_resources

    fake_ha.ws_result("lovelace/resources/list", [
        {"id": "1", "url": "/hacsfiles/button-card/button-card.js", "res_type": "module"},
    ])

    result = list_lovelace_resources()

    assert result["total"] == 1
    assert result["resources"][0]["type"] == "module"
    assert fake_ha.ws_calls[-1]["type"] == "lovelace/resources/list"


def test_list_lovelace_resources_failure_is_an_error_not_a_record(fake_ha):
    """Used to return [{"error": ..., "detail": ...}] - a failure reachable
    by iterating the result, indistinguishable from a resource record."""
    from tools.dashboards import list_lovelace_resources

    fake_ha.fail_ws("lovelace/resources/list", code="not_found",
                    message="Unknown command")

    result = list_lovelace_resources()

    assert result["error"] == "not_found"
    assert result["detail"] == "Unknown command"
    assert "resources" not in result


# ---- tools/hacs.py ---------------------------------------------------------

def test_list_hacs_repos_reports_hacs_not_installed(fake_ha):
    """list_hacs_repos and search_hacs route their ws_error() failure
    through hacs.py's _hacs_check, same as hacs_info and every other tool
    in this file - an unknown_command (the custom component not loaded)
    is reported as the friendly hacs_not_available, not the raw WS code."""
    from tools.hacs import list_hacs_repos

    fake_ha.fail_ws("hacs/repositories/list", code="unknown_command",
                    message="HACS is not installed")

    result = list_hacs_repos()

    assert result["error"] == "hacs_not_available"
    assert "repositories" not in result


def test_list_hacs_repos_reports_other_failures_unmodified(fake_ha):
    """Only "the component isn't loaded" becomes hacs_not_available - any
    other failure (e.g. a permissions error) must not be misdiagnosed as
    HACS being absent."""
    from tools.hacs import list_hacs_repos

    fake_ha.fail_ws("hacs/repositories/list", code="unauthorized",
                    message="Admin required")

    result = list_hacs_repos()

    assert result["error"] == "unauthorized"
    assert "repositories" not in result


def test_list_hacs_repos_wraps_a_success(fake_ha):
    from tools.hacs import list_hacs_repos

    fake_ha.ws_result("hacs/repositories/list", [
        {"id": "1", "full_name": "custom-cards/button-card", "name": "button-card",
         "category": "plugin", "installed": True, "stars": 500},
    ])

    result = list_hacs_repos()

    assert result["total"] == 1
    assert result["repositories"][0]["full_name"] == "custom-cards/button-card"
    assert fake_ha.ws_calls[-1]["type"] == "hacs/repositories/list"


def test_search_hacs_wraps_a_success_sorted_and_capped_at_20(fake_ha):
    """Two near-identical WS-backed tools (list_hacs_repos, search_hacs)
    share one command type - a copy-paste slip on the command name is
    invisible to a shape-only assertion."""
    from tools.hacs import search_hacs

    fake_ha.ws_result("hacs/repositories/list", [
        {"id": str(n), "full_name": f"user/repo{n}", "name": f"repo{n}",
         "category": "plugin", "stars": n}
        for n in range(25)
    ])

    result = search_hacs(query="repo")

    assert result["returned"] == 20
    assert result["repositories"][0]["stars"] == 24
    assert fake_ha.ws_calls[-1]["type"] == "hacs/repositories/list"


def test_search_hacs_reports_a_truthful_total_past_the_cap(fake_ha):
    """The slice to the top 20 used to happen before envelope() ever saw the
    data, so `total` could never exceed `returned` no matter how many
    repositories actually matched - the same lie removed from
    search_entities in an earlier task. With 25 matches, `total` must say
    25, not 20, and the truncation must be visible in `note`."""
    from tools.hacs import search_hacs

    fake_ha.ws_result("hacs/repositories/list", [
        {"id": str(n), "full_name": f"user/repo{n}", "name": f"repo{n}",
         "category": "plugin", "stars": n}
        for n in range(25)
    ])

    result = search_hacs(query="repo")

    assert result["total"] == 25
    assert result["returned"] == 20
    assert result["total"] > result["returned"]
    assert "note" in result
    assert "20 of 25" in result["note"]


def test_search_hacs_reports_hacs_not_installed(fake_ha):
    """Same translation as list_hacs_repos: an instance without HACS gets
    the friendly hacs_not_available, not the raw WS unknown_command."""
    from tools.hacs import search_hacs

    fake_ha.fail_ws("hacs/repositories/list", code="unknown_command",
                    message="HACS is not installed")

    result = search_hacs(query="anything")

    assert result["error"] == "hacs_not_available"
    assert "repositories" not in result


# ---- tools/notifications.py -------------------------------------------------

def test_list_notify_services_wraps_a_success(fake_ha):
    from tools.notifications import list_notify_services

    fake_ha.states = [
        {"entity_id": "notify.telegram_home", "state": "unknown",
         "attributes": {"friendly_name": "Telegram Home (123456)"}},
    ]

    result = list_notify_services()

    assert result["total"] == 1
    assert result["services"][0]["entity_id"] == "notify.telegram_home"
    assert result["services"][0]["type"] == "telegram_private"


def test_list_persistent_notifications_wraps_a_success(fake_ha):
    from tools.notifications import list_persistent_notifications

    fake_ha.ws_result("persistent_notification/get", [
        {"notification_id": "n1", "title": "Update ready", "message": "See settings",
         "created_at": "2026-08-23T00:00:00+00:00"},
    ])

    result = list_persistent_notifications()

    assert result["total"] == 1
    assert result["notifications"][0]["notification_id"] == "n1"
    assert fake_ha.ws_calls[-1]["type"] == "persistent_notification/get"


def test_list_persistent_notifications_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.notifications import list_persistent_notifications

    fake_ha.fail_ws("persistent_notification/get", code="unauthorized",
                    message="Admin required")

    result = list_persistent_notifications()

    assert result["error"] == "unauthorized"
    assert "notifications" not in result


# ---- tools/sensors.py -------------------------------------------------------

def test_get_energy_wraps_a_success(fake_ha):
    from tools.sensors import get_energy

    fake_ha.states = [
        {"entity_id": "sensor.plug_kitchen", "state": "150.5",
         "attributes": {"friendly_name": "Kitchen plug", "device_class": "power",
                        "unit_of_measurement": "W"}},
        {"entity_id": "sensor.plug_idle", "state": "0",
         "attributes": {"friendly_name": "Idle plug", "device_class": "power",
                        "unit_of_measurement": "W"}},
    ]

    result = get_energy()

    assert result["total"] == 1
    assert result["consumers"][0]["entity_id"] == "sensor.plug_kitchen"


def test_list_sensors_wraps_a_success(fake_ha):
    from tools.sensors import list_sensors

    fake_ha.states = [
        {"entity_id": "sensor.temp_kitchen", "state": "21.5",
         "attributes": {"friendly_name": "Kitchen temp", "unit_of_measurement": "°C",
                        "device_class": "temperature"}},
    ]

    result = list_sensors()

    assert result["total"] == 1
    assert result["sensors"][0]["entity_id"] == "sensor.temp_kitchen"


def test_list_sensors_reports_its_own_truncation(fake_ha):
    """The loop used to `break` once `limit` results were collected, so
    `total` could never exceed `limit` either - this mirrors the
    list_entities_by_integration / search_entities fix."""
    from tools.sensors import list_sensors

    fake_ha.states = [
        {"entity_id": f"sensor.s{n}", "state": "1",
         "attributes": {"friendly_name": f"S{n:03d}"}}
        for n in range(150)
    ]

    result = list_sensors(limit=10)

    assert result["total"] == 150
    assert result["returned"] == 10


# ---- tools/statistics.py -----------------------------------------------------

def test_statistics_is_a_series_without_offset(fake_ha):
    from tools.statistics import get_statistics

    fake_ha.ws_result("recorder/statistics_during_period",
                      {"sensor.power": [{"start": 1, "mean": 5.0}]})

    result = get_statistics("sensor.power")

    assert result["total"] == 1
    assert result["statistics"][0]["mean"] == 5.0
    assert "offset" not in result or result["offset"] == 0
    assert fake_ha.ws_calls[-1]["type"] == "recorder/statistics_during_period"


def test_statistics_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.statistics import get_statistics

    fake_ha.fail_ws("recorder/statistics_during_period", code="unauthorized",
                    message="Admin required")

    result = get_statistics("sensor.power")

    assert result["error"] == "unauthorized"
    assert "statistics" not in result


def test_statistics_summary_wraps_a_success(fake_ha):
    from tools.statistics import get_statistics_summary

    fake_ha.ws_result("recorder/statistics_during_period", {
        "sensor.power": [
            {"start": 1, "mean": 10.0, "min": 5.0, "max": 15.0, "sum": 100.0},
            {"start": 2, "mean": 20.0, "min": 10.0, "max": 30.0, "sum": 110.0},
        ],
    })

    result = get_statistics_summary(["sensor.power"])

    assert result["total"] == 1
    row = result["statistics"][0]
    assert row["entity_id"] == "sensor.power"
    assert row["samples"] == 2
    assert row["mean"] == 15.0
    assert row["sum_delta"] == 10.0
    assert fake_ha.ws_calls[-1]["type"] == "recorder/statistics_during_period"


def test_statistics_summary_reports_no_data_per_entity(fake_ha):
    from tools.statistics import get_statistics_summary

    fake_ha.ws_result("recorder/statistics_during_period", {})

    result = get_statistics_summary(["sensor.ghost"])

    assert result["total"] == 1
    assert result["statistics"][0] == {"entity_id": "sensor.ghost", "error": "no_data"}


def test_statistics_summary_distinguishes_a_ws_failure_from_no_data(fake_ha):
    """A failed per-entity call used to collapse into the same {"error":
    "no_data"} as an entity with nothing recorded - a real failure must be
    reported as itself instead."""
    from tools.statistics import get_statistics_summary

    fake_ha.fail_ws("recorder/statistics_during_period", code="unauthorized",
                    message="Admin required")

    result = get_statistics_summary(["sensor.power"])

    row = result["statistics"][0]
    assert row["error"] == "unauthorized"
    assert row["error"] != "no_data"
    assert row["detail"] == "Admin required"


# ---- tools/todo.py -----------------------------------------------------------

def test_list_todo_lists_wraps_a_success(fake_ha):
    from tools.todo import list_todo_lists

    fake_ha.states = [
        {"entity_id": "todo.shopping_list", "state": "3",
         "attributes": {"friendly_name": "Shopping list", "todo_items": 3}},
    ]

    result = list_todo_lists()

    assert result["total"] == 1
    assert result["lists"][0]["entity_id"] == "todo.shopping_list"
    assert result["lists"][0]["item_count"] == 3


def test_get_todo_items_wraps_a_success(fake_ha):
    from tools.todo import get_todo_items

    fake_ha.ws_result("call_service", {
        "response": {"todo.shopping_list": {"items": [{"uid": "1", "summary": "Milk"}]}},
    })

    result = get_todo_items("todo.shopping_list")

    assert result["total"] == 1
    assert result["items"][0]["summary"] == "Milk"
    sent = fake_ha.ws_calls[-1]
    assert sent["type"] == "call_service"
    assert sent["domain"] == "todo"
    assert sent["service"] == "get_items"


def test_get_todo_items_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.todo import get_todo_items

    fake_ha.fail_ws("call_service", code="not_found", message="Unknown entity")

    result = get_todo_items("todo.ghost_list")

    assert result["error"] == "not_found"
    assert "items" not in result


# ---- Task 11: the eighteen single-tool files -------------------------------
# The runtime sweep in test_conformance.py proves every one of these returns
# a dict; it says nothing about the content. Each test below checks the
# content, and the two WebSocket-backed ones (list_tags, list_users) also
# pin the command name sent.

def test_list_lights_filters_by_state(fake_ha):
    """Beyond test_harness.py's shape check: a filter param actually filters."""
    from tools.lights import list_lights

    result = list_lights(state="on")

    assert result["total"] == 1
    assert result["lights"][0]["entity_id"] == "light.kitchen"


def test_get_alarm_state_wraps_a_success(fake_ha):
    from tools.alarm import get_alarm_state

    fake_ha.states = [
        {"entity_id": "alarm_control_panel.home", "state": "armed_away",
         "attributes": {"friendly_name": "Home Alarm", "code_format": "number",
                        "changed_by": "user1", "open_sensors": {},
                        "bypassed_sensors": []}},
    ]

    result = get_alarm_state()

    assert result["total"] == 1
    assert result["alarms"][0]["entity_id"] == "alarm_control_panel.home"
    assert result["alarms"][0]["state"] == "armed_away"


def test_list_alerts_wraps_a_success(fake_ha):
    from tools.alerts import list_alerts

    fake_ha.states = [
        {"entity_id": "alert.gas_leak", "state": "on",
         "attributes": {"friendly_name": "Gas Leak", "notification_frequency": 5,
                        "data": {}}},
    ]

    result = list_alerts()

    assert result["total"] == 1
    assert result["alerts"][0]["entity_id"] == "alert.gas_leak"
    assert result["alerts"][0]["state"] == "on"


def test_list_cameras_wraps_a_success(fake_ha):
    from tools.cameras import list_cameras

    fake_ha.states = [
        {"entity_id": "camera.front_door", "state": "idle",
         "attributes": {"friendly_name": "Front Door", "model_name": "Reolink RLC-810A"}},
    ]

    result = list_cameras()

    assert result["total"] == 1
    assert result["cameras"][0]["entity_id"] == "camera.front_door"
    assert result["cameras"][0]["model"] == "Reolink RLC-810A"


def test_list_climate_wraps_a_success(fake_ha):
    from tools.climate import list_climate

    fake_ha.states = [
        {"entity_id": "climate.living_room", "state": "heat",
         "attributes": {"friendly_name": "Living Room", "current_temperature": 19.5,
                        "temperature": 21.0, "hvac_modes": ["off", "heat"],
                        "fan_mode": "auto", "fan_modes": ["auto", "low"]}},
    ]

    result = list_climate()

    assert result["total"] == 1
    assert result["climate"][0]["entity_id"] == "climate.living_room"
    assert result["climate"][0]["temperature"] == 21.0


def test_list_covers_wraps_a_success(fake_ha):
    from tools.covers import list_covers

    fake_ha.states = [
        {"entity_id": "cover.garage", "state": "open",
         "attributes": {"friendly_name": "Garage Door", "current_position": 100,
                        "device_class": "garage"}},
    ]

    result = list_covers()

    assert result["total"] == 1
    assert result["covers"][0]["entity_id"] == "cover.garage"
    assert result["covers"][0]["position"] == 100


def test_list_fans_wraps_a_success(fake_ha):
    from tools.fans import list_fans

    fake_ha.states = [
        {"entity_id": "fan.bedroom", "state": "on",
         "attributes": {"friendly_name": "Bedroom Fan", "percentage": 66,
                        "oscillating": True}},
    ]

    result = list_fans()

    assert result["total"] == 1
    assert result["fans"][0]["entity_id"] == "fan.bedroom"
    assert result["fans"][0]["percentage"] == 66


def test_list_groups_filters_by_search(fake_ha):
    from tools.groups import list_groups

    fake_ha.states = [
        {"entity_id": "group.living_room_lights", "state": "on",
         "attributes": {"friendly_name": "Living Room Lights",
                        "entity_id": ["light.kitchen"], "all": False}},
        {"entity_id": "group.upstairs", "state": "off",
         "attributes": {"friendly_name": "Upstairs", "entity_id": [], "all": True}},
    ]

    result = list_groups(search="living")

    assert result["total"] == 1
    assert result["groups"][0]["entity_id"] == "group.living_room_lights"
    assert result["groups"][0]["entities"] == ["light.kitchen"]


def test_list_helpers_filters_by_domain(fake_ha):
    from tools.helpers import list_helpers

    fake_ha.states = [
        {"entity_id": "input_boolean.guest_mode", "state": "off",
         "attributes": {"friendly_name": "Guest Mode"}},
        {"entity_id": "input_number.timer_minutes", "state": "5",
         "attributes": {"friendly_name": "Timer Minutes", "min": 0, "max": 60}},
    ]

    result = list_helpers(domain="input_boolean")

    assert result["total"] == 1
    assert result["helpers"][0]["entity_id"] == "input_boolean.guest_mode"
    assert result["helpers"][0]["domain"] == "input_boolean"


def test_list_locks_wraps_a_success(fake_ha):
    from tools.locks import list_locks

    fake_ha.states = [
        {"entity_id": "lock.front_door", "state": "locked",
         "attributes": {"friendly_name": "Front Door", "changed_by": "keypad"}},
    ]

    result = list_locks()

    assert result["total"] == 1
    assert result["locks"][0]["entity_id"] == "lock.front_door"
    assert result["locks"][0]["state"] == "locked"


def test_list_media_players_wraps_a_success(fake_ha):
    from tools.media_players import list_media_players

    fake_ha.states = [
        {"entity_id": "media_player.living_room", "state": "playing",
         "attributes": {"friendly_name": "Living Room", "media_title": "Song",
                        "media_artist": "Artist", "volume_level": 0.5}},
    ]

    result = list_media_players()

    assert result["total"] == 1
    assert result["media_players"][0]["entity_id"] == "media_player.living_room"
    assert result["media_players"][0]["media_title"] == "Song"


def test_list_persons_wraps_a_success(fake_ha):
    from tools.persons import list_persons

    fake_ha.states = [
        {"entity_id": "person.jane", "state": "home",
         "attributes": {"friendly_name": "Jane", "latitude": 45.0, "longitude": 9.0,
                        "source": "device_tracker.jane_phone"}},
    ]

    result = list_persons()

    assert result["total"] == 1
    assert result["persons"][0]["entity_id"] == "person.jane"
    assert result["persons"][0]["state"] == "home"


def test_list_scenes_reports_member_entity_states(fake_ha):
    """Uses the default fake states, where light.kitchen is already 'on', to
    check the entities dict is enriched with real current states."""
    from tools.scenes import list_scenes

    fake_ha.states = fake_ha.states + [
        {"entity_id": "scene.movie_night", "state": "scening",
         "attributes": {"friendly_name": "Movie Night",
                        "entity_id": ["light.kitchen", "light.ghost"]}},
    ]

    result = list_scenes()

    assert result["total"] == 1
    assert result["scenes"][0]["entities"] == {
        "light.kitchen": "on", "light.ghost": "unknown"}


def test_list_scripts_wraps_a_success(fake_ha):
    from tools.scripts import list_scripts

    fake_ha.states = [
        {"entity_id": "script.restart_broker", "state": "off",
         "attributes": {"friendly_name": "Restart Broker"}},
    ]

    result = list_scripts()

    assert result["total"] == 1
    assert result["scripts"][0]["entity_id"] == "script.restart_broker"


def test_list_switches_wraps_a_success(fake_ha):
    from tools.switches import list_switches

    fake_ha.states = [
        {"entity_id": "switch.lamp", "state": "on",
         "attributes": {"friendly_name": "Lamp"}},
    ]

    result = list_switches()

    assert result["total"] == 1
    assert result["switches"][0]["entity_id"] == "switch.lamp"


def test_list_addons_reports_supervisor_unavailable_as_a_top_level_error(fake_ha):
    """Used to `return [err]` - a failure reachable by iterating the result.
    In this test environment HA_URL never contains "supervisor", so this is
    the only branch of list_addons() reachable without also monkeypatching
    the module's Supervisor base URL (exercised in the next test)."""
    from tools.addons import list_addons

    result = list_addons()

    assert result["error"] == "supervisor_not_available"
    assert "addons" not in result
    assert not isinstance(result, list)


def test_list_addons_wraps_a_success(fake_ha, monkeypatch):
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")
    fake_ha.rest_responses["/addons"] = (200, {"data": {"addons": [
        {"slug": "core_mosquitto", "name": "Mosquitto broker", "version": "6.4.0",
         "version_latest": "6.4.0", "state": "started", "update_available": False,
         "repository": "core"},
    ]}})

    result = addons.list_addons()

    assert result["total"] == 1
    assert result["addons"][0]["slug"] == "core_mosquitto"


def test_list_tags_wraps_a_success(fake_ha):
    from tools.tags import list_tags

    fake_ha.ws_result("tag/list", [
        {"id": "abc123", "name": "Front Door",
         "last_scanned": "2026-08-20T10:00:00+00:00",
         "last_scanned_by_device_id": "dev1"},
    ])

    result = list_tags()

    assert result["total"] == 1
    assert result["tags"][0]["id"] == "abc123"
    assert fake_ha.ws_calls[-1]["type"] == "tag/list"


def test_list_tags_failure_is_an_error_not_a_record(fake_ha):
    """Used to `return [{"error": ...}]` - a failure reachable by iterating
    the result, indistinguishable from a tag record."""
    from tools.tags import list_tags

    fake_ha.fail_ws("tag/list", code="unauthorized", message="Admin required")

    result = list_tags()

    assert result["error"] == "unauthorized"
    assert "tags" not in result


def test_list_users_wraps_a_success(fake_ha):
    from tools.users import list_users

    fake_ha.ws_result("config/auth/list", [
        {"id": "u1", "name": "Riccardo", "is_admin": True, "is_active": True,
         "local_only": False, "system_generated": False},
        {"id": "u2", "name": "Supervisor", "is_admin": True, "is_active": True,
         "local_only": False, "system_generated": True},
    ])

    result = list_users()

    assert result["total"] == 1
    assert result["users"][0]["id"] == "u1"
    assert fake_ha.ws_calls[-1]["type"] == "config/auth/list"


def test_list_users_failure_is_an_error_not_a_record(fake_ha):
    """Used to `return [{"error": ...}]` - a failure reachable by iterating
    the result, indistinguishable from a user record."""
    from tools.users import list_users

    fake_ha.fail_ws("config/auth/list", code="unauthorized", message="Admin required")

    result = list_users()

    assert result["error"] == "unauthorized"
    assert "users" not in result
