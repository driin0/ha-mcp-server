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
    assert "advance offset" in result["note"]


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


def test_create_automation_from_blueprint_reports_home_assistants_rejection(fake_ha):
    """Posts to the same config-write endpoint create_automation() does,
    and is rejected by Home Assistant the same way on a malformed config -
    a bare r.raise_for_status() used to discard HA's own explanation as an
    uncaught httpx.HTTPStatusError; rest_error() reports it instead, the
    same fix already applied to create_automation()'s identical POST."""
    from tools.automations import create_automation_from_blueprint

    fake_ha.fail_rest("/api/config/automation/config/", status=400,
                      message="Message malformed: not a file")

    result = create_automation_from_blueprint(
        blueprint_path="homeassistant/motion_trigger.yaml",
        alias="Hallway motion",
        input_values={},
    )

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 400
    assert "not a file" in result["detail"]
    assert result["entity_id"] == "automation.hallway_motion"
    assert "hallway_motion" not in fake_ha.automation_configs


# ---- tools/automations.py & tools/scripts.py: _slug() id collisions (D2) ----------
# "Morning lights" and "Morning, lights!" both slug to "morning_lights" -
# create_automation()/create_script() must refuse the second by default
# rather than silently replace the first's definition.

def test_create_automation_refuses_a_colliding_name_by_default(fake_ha):
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[])
    result = create_automation("Morning, lights!", trigger=[], action=[])

    assert result["error"] == "id_collision"
    assert result["existing_alias"] == "Morning lights"
    assert result["requested_name"] == "Morning, lights!"


def test_create_automation_overwrite_replaces_the_collision(fake_ha):
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[])
    result = create_automation("Morning, lights!", trigger=[], action=[], overwrite=True)

    assert "error" not in result
    assert fake_ha.automation_configs["morning_lights"]["alias"] == "Morning, lights!"


def test_create_automation_calling_again_with_the_same_name_is_not_a_collision(fake_ha):
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[])
    result = create_automation("Morning lights", trigger=["updated"], action=[])

    assert "error" not in result
    assert fake_ha.automation_configs["morning_lights"]["trigger"] == ["updated"]


def test_create_automation_collision_check_sends_exactly_two_requests_when_absent(fake_ha):
    """_fetch_config()'s slug-fallback is a no-op on each individual check:
    create_automation() passes automation_id as both its own id and its own
    slug, so a 404 on either GET must never trigger a second, identical one
    of its own - see _fetch_config()'s own `if automation_id != slug` guard.

    Exactly two GETs overall, not one: create_automation() (overwrite=False)
    runs _check_collision() twice by design - once up front, and again
    immediately before the write - to shrink the window in which something
    else could create an automation under this same id between the two (see
    _check_collision()'s own docstring, and this module's lost-update fix).
    Neither of those two calls triggers its own extra slug-fallback request,
    which is what this test actually guards - two, not four."""
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[])

    config_gets = [
        c for c in fake_ha.rest_calls
        if c.method == "GET"
        and c.url.path == "/api/config/automation/config/morning_lights"
    ]
    assert len(config_gets) == 2


def test_create_automation_refuses_a_collision_created_between_check_and_write(fake_ha):
    """The race the second _check_collision() call exists for: nothing
    occupies this id at the time of the up-front check, but something else
    (another create_automation() call for the exact same name, issued in
    parallel; another MCP client; a person in the HA UI) creates one under
    the same slug before this call's own write lands. The pre-write
    recheck must catch that and refuse - the id_collision it reports is
    exactly as real as one caught by the up-front check, just discovered
    one round trip later. Without this second check, this call's write
    would have silently replaced the racer's automation instead."""
    import httpx

    from tools.automations import create_automation

    real_handle = fake_ha.handle
    config_gets = {"count": 0}

    def handle_with_race(request):
        if (request.method == "GET"
                and request.url.path == "/api/config/automation/config/morning_lights"):
            config_gets["count"] += 1
            if config_gets["count"] == 2:
                # Something else creates a DIFFERENTLY-named automation
                # under this exact slug between the two checks.
                fake_ha.automation_configs["morning_lights"] = {
                    "alias": "Raced by someone else", "trigger": [], "action": []}
                return httpx.Response(200, json={
                    "id": "morning_lights",
                    **fake_ha.automation_configs["morning_lights"]})
        return real_handle(request)

    fake_ha.handle = handle_with_race

    result = create_automation("Morning lights", trigger=[], action=[])

    assert result["error"] == "id_collision"
    assert result["existing_alias"] == "Raced by someone else"
    assert result["requested_name"] == "Morning lights"
    assert config_gets["count"] == 2
    # Nothing was written by this call - the racer's automation stands.
    assert not any(c.method == "POST" for c in fake_ha.rest_calls)
    assert fake_ha.automation_configs["morning_lights"]["alias"] == "Raced by someone else"


def test_create_automation_reports_a_failed_collision_check_instead_of_raising(fake_ha):
    """A transient failure reading the existing config (a 500, an
    unauthorized 401) used to fall through as "no collision", silently
    re-enabling the exact lossy-slug replacement this check exists to
    prevent. It must now refuse - named, not a bare HTTPStatusError - and
    create nothing."""
    from tools.automations import create_automation

    fake_ha.fail_rest("/api/config/automation/config/", status=500,
                      message="Internal Server Error")

    result = create_automation("Morning lights", trigger=[], action=[])

    assert result["error"] == "collision_check_failed"
    assert result["status"] == 500
    assert "morning_lights" not in fake_ha.automation_configs


def test_create_automation_overwrite_proceeds_despite_a_failed_collision_check(fake_ha):
    """overwrite=True skips the collision check entirely - a broken GET on
    that same config endpoint must not stop a create that never attempts
    to read it. Only the collision-check GET is broken here (not the
    create POST that follows, to the same path), which fail_rest() cannot
    express on its own since it is not method-aware."""
    import httpx

    from tools.automations import create_automation

    real_handle = fake_ha.handle

    def handle_get_fails(request):
        if (request.method == "GET"
                and request.url.path == "/api/config/automation/config/morning_lights"):
            return httpx.Response(500, json={"message": "Internal Server Error"})
        return real_handle(request)

    fake_ha.handle = handle_get_fails

    result = create_automation("Morning lights", trigger=[], action=[], overwrite=True)

    assert "error" not in result
    assert fake_ha.automation_configs["morning_lights"]["alias"] == "Morning lights"


def test_create_automation_mode_defaults_to_single(fake_ha):
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[])

    assert fake_ha.automation_configs["morning_lights"]["mode"] == "single"


def test_create_automation_mode_is_passed_through(fake_ha):
    """mode used to be hardcoded to "single" - create_automation() now
    exposes it, matching update_automation()."""
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[], mode="restart")

    assert fake_ha.automation_configs["morning_lights"]["mode"] == "restart"


def test_create_script_refuses_a_colliding_name_by_default(fake_ha):
    from tools.scripts import create_script

    create_script("Turn everything off", sequence=[])
    result = create_script("Turn, everything off!", sequence=[])

    assert result["error"] == "id_collision"
    assert result["existing_alias"] == "Turn everything off"


def test_create_script_overwrite_replaces_the_collision(fake_ha):
    from tools.scripts import create_script

    create_script("Turn everything off", sequence=[])
    result = create_script("Turn, everything off!", sequence=[], overwrite=True)

    assert "error" not in result
    assert fake_ha.script_configs["turn_everything_off"]["alias"] == "Turn, everything off!"


# ---- tools/automations.py: delete_automation() (D3) -------------------------------
# Home Assistant's delete endpoint is keyed by the automation's own config
# id, which for a UI-created automation differs from its entity_id's slug.
# Measured live: a nonexistent config id 400s ("Resource not found"), not
# 404 - the old code only ever checked for 404.

def test_delete_automation_resolves_the_numeric_id_from_the_entity_attribute(fake_ha):
    """A UI-created automation: entity_id's own slug ('automation.morning')
    does not match its actual config id (a numeric timestamp, carried in
    the `id` attribute) - deleting must use the attribute, not the slug."""
    from tools.automations import delete_automation

    fake_ha.states = [
        {"entity_id": "automation.morning", "state": "on",
         "attributes": {"id": "1690221234567", "friendly_name": "Morning"}},
    ]
    fake_ha.automation_configs["1690221234567"] = {"alias": "Morning"}

    result = delete_automation("automation.morning")

    assert result == {"deleted": "automation.morning", "status": 200}
    assert "1690221234567" not in fake_ha.automation_configs


def test_delete_automation_reports_a_400_as_not_deletable_not_a_404(fake_ha):
    """The old code only recognised 404 as "not found" - Home Assistant
    actually answers 400, so that branch was dead code."""
    from tools.automations import delete_automation

    fake_ha.states = [
        {"entity_id": "automation.yaml_only", "state": "on",
         "attributes": {"friendly_name": "YAML only"}},
    ]
    # No matching automation_configs entry - the fake answers DELETE with
    # 400 "Resource not found", exactly like real Home Assistant.

    result = delete_automation("automation.yaml_only")

    assert result["error"] == "not_deletable"


def test_delete_automation_reports_a_nonexistent_entity(fake_ha):
    from tools.automations import delete_automation

    result = delete_automation("automation.ghost")

    assert result["error"] == "entity_not_found"


def test_delete_automation_reports_a_failed_read_instead_of_raising(fake_ha):
    """Resolving the config id reads the entity's own state first - a
    transient failure doing that (a 500, a revoked token) is not the same
    as the entity not existing and must not raise an uncaught
    httpx.HTTPStatusError - the same guarantee get_automation()/
    update_automation()/patch_automation() get from _resolve_and_fetch()."""
    from tools.automations import delete_automation

    fake_ha.fail_rest("/api/states/automation.nas_shutdown", status=500,
                      message="Internal Server Error")

    result = delete_automation("automation.nas_shutdown")

    assert result["error"] == "config_read_failed"
    assert result["status"] == 500
    assert not any(c.method == "DELETE" for c in fake_ha.rest_calls)


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


def test_reload_integration_uses_the_rest_endpoint_not_a_nonexistent_ws_command(fake_ha):
    """Home Assistant has no "config_entries/reload" WebSocket command at
    all - confirmed against its own source and live, where an earlier
    version of this tool got back {"error": "unknown_command"} on every
    call. Reloading a config entry is a REST call:
    POST /api/config/config_entries/entry/{entry_id}/reload."""
    from tools.system import reload_integration

    fake_ha.rest_responses["/api/config/config_entries/entry/abc123/reload"] = (
        200, {"require_restart": False})

    result = reload_integration("abc123")

    assert result == {"entry_id": "abc123", "reloaded": True, "require_restart": False}
    assert fake_ha.ws_calls == []  # never sent as a WS command
    assert any(
        c.url.path == "/api/config/config_entries/entry/abc123/reload"
        for c in fake_ha.rest_calls
    )


def test_reload_integration_reports_a_missing_entry(fake_ha):
    from tools.system import reload_integration

    fake_ha.rest_responses["/api/config/config_entries/entry/ghost/reload"] = (
        404, {"message": "Invalid entry specified"})

    result = reload_integration("ghost")

    assert result["error"] == "not_found"


def test_reload_integration_reports_a_refused_reload(fake_ha):
    """Home Assistant answers 403 when the entry cannot be reloaded
    (OperationNotAllowed) - distinct from a missing entry."""
    from tools.system import reload_integration

    fake_ha.rest_responses["/api/config/config_entries/entry/abc123/reload"] = (
        403, {"message": "Entry cannot be reloaded"})

    result = reload_integration("abc123")

    assert result["error"] == "not_allowed"


def test_reload_integration_reports_a_require_restart_signal(fake_ha):
    from tools.system import reload_integration

    fake_ha.rest_responses["/api/config/config_entries/entry/abc123/reload"] = (
        200, {"require_restart": True})

    result = reload_integration("abc123")

    assert result["reloaded"] is True
    assert result["require_restart"] is True


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


def test_create_backup_defaults_to_every_available_agent(fake_ha):
    """backup/generate now requires agent_ids - the tool used to send none
    at all, so every call failed with invalid_format. When the caller
    passes none, default to every agent backup/agents/info reports."""
    from tools.system import create_backup

    fake_ha.ws_result("backup/agents/info", {"agents": [
        {"agent_id": "backup.local", "name": "local"},
    ]})
    fake_ha.ws_result("backup/generate", {"backup_job_id": "abc123"})

    result = create_backup(name="Nightly")

    assert result["backup_job_id"] == "abc123"
    assert result["agent_ids"] == ["backup.local"]
    generate_call = [c for c in fake_ha.ws_calls if c["type"] == "backup/generate"][0]
    assert generate_call["agent_ids"] == ["backup.local"]
    assert generate_call["name"] == "Nightly"


def test_create_backup_uses_caller_supplied_agent_ids(fake_ha):
    from tools.system import create_backup

    fake_ha.ws_result("backup/generate", {"backup_job_id": "xyz789"})

    result = create_backup(agent_ids=["backup.local", "backup.other"])

    assert result["backup_job_id"] == "xyz789"
    # No agents/info lookup needed - the caller already said which agents.
    assert not any(c["type"] == "backup/agents/info" for c in fake_ha.ws_calls)
    generate_call = [c for c in fake_ha.ws_calls if c["type"] == "backup/generate"][0]
    assert generate_call["agent_ids"] == ["backup.local", "backup.other"]


def test_create_backup_reports_no_agents_instead_of_failing_silently(fake_ha):
    from tools.system import create_backup

    fake_ha.ws_result("backup/agents/info", {"agents": []})

    result = create_backup()

    assert result["error"] == "no_backup_agents"


def test_create_backup_surfaces_a_ws_failure_as_an_error_not_a_raw_frame(fake_ha):
    """create_backup used to return `result.get("result") or result` -
    on failure `result` has no "result" key, so the raw
    {"success": False, "error": {...}} frame was returned as-is, two levels
    removed from an actual error() envelope, and nobody read it."""
    from tools.system import create_backup

    fake_ha.fail_ws("backup/generate", code="unknown_error", message="agent unavailable")

    result = create_backup(agent_ids=["backup.local"])

    assert result["error"] == "unknown_error"
    assert "success" not in result
    assert "backup_job_id" not in result


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
    called = [c["type"] for c in fake_ha.ws_calls]
    assert "config/entity_registry/list" in called
    assert "config/device_registry/list" in called


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


def test_list_entities_by_integration_reports_area_inherited_from_device(fake_ha):
    """An entity's area is its own area_id when set, and otherwise its
    device's - the area_id field reported here must follow that, not just
    the entity's own registry row."""
    from tools.diagnostics import list_entities_by_integration

    fake_ha.ws_result("config/entity_registry/list", [
        {"entity_id": "light.hue_1", "platform": "hue", "name": "Hue 1",
         "area_id": None, "device_id": "dev1", "labels": [], "disabled_by": None},
    ])
    fake_ha.devices = [{"id": "dev1", "area_id": "living_room"}]

    result = list_entities_by_integration("hue")

    assert result["entities"][0]["area_id"] == "living_room"


def test_list_entities_by_integration_reports_no_area_when_none_is_set(fake_ha):
    from tools.diagnostics import list_entities_by_integration

    fake_ha.ws_result("config/entity_registry/list", [
        {"entity_id": "light.hue_1", "platform": "hue", "name": "Hue 1",
         "area_id": None, "device_id": None, "labels": [], "disabled_by": None},
    ])

    result = list_entities_by_integration("hue")

    assert result["entities"][0]["area_id"] is None


def test_list_entities_by_integration_degrades_on_a_device_registry_failure(fake_ha):
    """area_id here is enrichment, not a filter - unlike search_entities, a
    failed device-registry read must degrade to the entity-registry-only
    view and say so in `note`, not abort the whole call."""
    from tools.diagnostics import list_entities_by_integration

    fake_ha.ws_result("config/entity_registry/list", [
        {"entity_id": "light.hue_1", "platform": "hue", "name": "Hue 1",
         "area_id": "kitchen", "device_id": None, "labels": [], "disabled_by": None},
    ])
    fake_ha.fail_ws("config/device_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_entities_by_integration("hue")

    assert result["entities"][0]["entity_id"] == "light.hue_1"
    assert result["entities"][0]["area_id"] == "kitchen"
    assert "note" in result
    assert "device registry" in result["note"].lower()


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

    called = [c["type"] for c in fake_ha.ws_calls]
    assert "config/entity_registry/list" in called
    assert "config/device_registry/list" in called


def test_search_entities_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.diagnostics import search_entities

    fake_ha.fail_ws("config/entity_registry/list", code="unauthorized",
                    message="Admin required")

    result = search_entities(query="kitchen")

    assert result["error"] == "unauthorized"
    assert "entities" not in result


def test_search_entities_filters_by_area_inherited_from_device(fake_ha):
    """An entity's area is its own area_id when set, and otherwise its
    device's - the area_id filter and the area_id field it reports must
    both follow that, not just the entity's own registry row. Measured on
    a live instance: a light whose area comes from its device was invisible
    to this filter before this fix."""
    from tools.diagnostics import search_entities

    fake_ha.states = [
        {"entity_id": "light.garage", "state": "on",
         "attributes": {"friendly_name": "Garage light"}},
    ]
    fake_ha.registry = [
        {"entity_id": "light.garage", "area_id": None, "device_id": "dev1", "labels": []},
    ]
    fake_ha.devices = [{"id": "dev1", "area_id": "garage"}]

    result = search_entities(query="", area_id="garage")

    assert result["total"] == 1
    assert result["entities"][0]["entity_id"] == "light.garage"
    assert result["entities"][0]["area_id"] == "garage"


def test_search_entities_reports_no_area_when_none_is_set(fake_ha):
    from tools.diagnostics import search_entities

    fake_ha.states = [
        {"entity_id": "light.attic", "state": "off",
         "attributes": {"friendly_name": "Attic light"}},
    ]
    fake_ha.registry = [
        {"entity_id": "light.attic", "area_id": None, "device_id": None, "labels": []},
    ]

    result = search_entities(query="attic")

    assert result["entities"][0]["area_id"] is None


def test_search_entities_device_registry_failure_is_also_fatal(fake_ha):
    """search_entities has always treated a registry failure as fatal
    regardless of which filter was passed, because `labels` is reported on
    every row too - the device registry backing the same area_id is held
    to the same standard rather than silently degrading."""
    from tools.diagnostics import search_entities

    fake_ha.fail_ws("config/device_registry/list", code="unauthorized",
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
    assert "200" in result["note"]
    assert "250" in result["note"]


def test_get_entity_dependencies_notes_a_cap_even_with_no_failures(fake_ha):
    """The note used to appear only when failed_checks was non-zero. With
    more automations than the probe limit but every probe succeeding (and
    finding nothing), the old code returned a clean empty result with no
    hint that the search stopped early - exactly the situation this tool
    exists to prevent a bad rename/delete on."""
    from tools.diagnostics import get_entity_dependencies

    fake_ha.states = [
        {"entity_id": f"automation.a{n}", "state": "on",
         "attributes": {"friendly_name": f"A{n}", "id": f"a{n}"}}
        for n in range(205)
    ]
    fake_ha.rest_responses["/api/config/automation/config/"] = (
        200, {"trigger": [], "condition": [], "action": []})

    result = get_entity_dependencies("light.kitchen")

    assert result["failed_checks"] == 0
    assert result["automations"] == []
    assert "note" in result
    assert "200" in result["note"]
    assert "205" in result["note"]


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
    assert "advance offset" in result["note"]


def test_get_device_returns_the_matching_device(fake_ha):
    from tools.areas import get_device

    fake_ha.ws_result("config/device_registry/list", [
        {"id": "d1", "name": "Router"},
        {"id": "d2", "name": "Switch"},
    ])

    result = get_device("d2")

    assert result["id"] == "d2"
    assert result["name"] == "Switch"


def test_get_device_says_not_found_for_a_genuinely_absent_id(fake_ha):
    from tools.areas import get_device

    fake_ha.ws_result("config/device_registry/list", [
        {"id": "d1", "name": "Router"},
    ])

    result = get_device("does-not-exist")

    assert result["error"] == "device_not_found"
    assert "detail" in result


def test_get_device_reports_a_registry_read_failure_not_not_found(fake_ha):
    """get_device used to iterate `r.get("result", [])`, so a failed device
    registry read (a dead connection, a revoked token) fell through the
    empty default straight to "Device not found: <id>" - the same fault
    already fixed in dashboards.py's _dashboard_id() - and bypassed
    error() entirely, so the response carried no `detail` at all."""
    from tools.areas import get_device

    fake_ha.fail_ws("config/device_registry/list", code="unauthorized",
                    message="Admin required")

    result = get_device("d1")

    assert result["error"] == "unauthorized"
    assert result["error"] != "device_not_found"


def test_get_device_reports_a_transport_failure_not_not_found(fake_ha):
    """A revoked token fails before Home Assistant even answers with a
    "success" key - _ws() itself returns {"error": "Auth failed: ..."}.
    That must not read as "device not found" either."""
    from tools.areas import get_device

    fake_ha.fail_ws_transport("config/device_registry/list")

    result = get_device("d1")

    assert result["error"] != "device_not_found"
    assert "Auth failed" in result["detail"]


def test_list_labels_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import list_labels

    fake_ha.fail_ws("config/label_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_labels()

    assert result["error"] == "unauthorized"
    assert "labels" not in result


def test_list_labels_wraps_a_success_through_the_shared_envelope(fake_ha):
    """list_labels used to hand-roll {total, labels} itself - no returned,
    no offset, no empty-collection note - which could regress to a bare
    list and still pass a shape check that only looks for a "labels" key."""
    from tools.areas import list_labels

    fake_ha.ws_result("config/label_registry/list", [
        {"label_id": "energy", "name": "Energy"},
        {"label_id": "outdoor", "name": "Outdoor"},
    ])

    result = list_labels()

    assert result["total"] == 2
    assert result["returned"] == 2
    assert result["offset"] == 0
    assert [l["label_id"] for l in result["labels"]] == ["energy", "outdoor"]


def test_list_labels_says_when_nothing_matches(fake_ha):
    from tools.areas import list_labels

    fake_ha.ws_result("config/label_registry/list", [])

    result = list_labels()

    assert result["total"] == 0
    assert result["note"] == "no labels found"


def test_list_floors_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import list_floors

    fake_ha.fail_ws("config/floor_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_floors()

    assert result["error"] == "unauthorized"
    assert "floors" not in result


def test_list_floors_wraps_a_success_through_the_shared_envelope(fake_ha):
    """Same conversion as list_labels: a hand-rolled {total, floors} gains
    returned/offset/note by routing through envelope()."""
    from tools.areas import list_floors

    fake_ha.ws_result("config/floor_registry/list", [
        {"floor_id": "first", "name": "First floor", "level": 1},
        {"floor_id": "ground", "name": "Ground floor", "level": 0},
    ])

    result = list_floors()

    assert result["total"] == 2
    assert result["returned"] == 2
    assert result["offset"] == 0
    assert [f["floor_id"] for f in result["floors"]] == ["ground", "first"]


def test_list_floors_says_when_nothing_matches(fake_ha):
    from tools.areas import list_floors

    fake_ha.ws_result("config/floor_registry/list", [])

    result = list_floors()

    assert result["total"] == 0
    assert result["note"] == "no floors found"


def test_get_entity_registry_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.areas import get_entity_registry

    fake_ha.fail_ws("config/entity_registry/get", code="not_found",
                    message="Entity not registered")

    result = get_entity_registry("light.ghost")

    assert result["error"] == "not_found"
    assert "entity_id" not in result


def test_get_entity_registry_returns_entity_own_area(fake_ha):
    from tools.areas import get_entity_registry

    # Entity with its own area should report it (test_bed_light has area_id="bedroom")
    result = get_entity_registry("light.bed_light")

    assert result["entity_id"] == "light.bed_light"
    assert result["area_id"] == "bedroom"
    assert result["device_id"] == "device_bed"


def test_get_entity_registry_falls_back_to_device_area(fake_ha):
    from tools.areas import get_entity_registry

    # Entity inheriting area from device (no own area_id, device has area_id)
    result = get_entity_registry("light.kitchen_lights")

    assert result["entity_id"] == "light.kitchen_lights"
    assert result["device_id"] == "device_kitchen"
    # Should resolve to the device's area
    assert result["area_id"] == "stanza_del_dispositivo"


def test_get_entity_registry_no_area_at_all(fake_ha):
    from tools.areas import get_entity_registry

    # Entity with no area (no own area_id, no device_id at all)
    result = get_entity_registry("switch.garage_door")

    assert result["entity_id"] == "switch.garage_door"
    assert result["area_id"] is None
    assert result["device_id"] is None


def test_get_entity_registry_device_registry_failure_surfaces_error(fake_ha):
    from tools.areas import get_entity_registry

    # Set up an entity with a device_id but make device registry fail
    fake_ha.fail_ws("config/device_registry/list", code="internal_error",
                    message="Device registry error")

    result = get_entity_registry("light.kitchen_lights")

    # Should return the error, not silently return null for area_id
    assert result["error"] == "internal_error"
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


# ---- tools/areas.py: bulk_set_entity_labels -------------------------------

def test_bulk_set_entity_labels_reports_a_normal_success(fake_ha):
    from tools.areas import bulk_set_entity_labels

    fake_ha.ws_result("config/entity_registry/update",
                      {"entity_entry": {"labels": ["energia"]}})

    result = bulk_set_entity_labels(["light.kitchen", "light.study"], ["energia"])

    assert result == {"total": 2, "succeeded": 2, "failed": []}
    assert "error" not in result


def test_bulk_set_entity_labels_reports_per_entity_failures_normally(fake_ha, monkeypatch):
    """An ordinary per-entity registry rejection (a bogus entity_id among
    otherwise valid ones) is not a transport failure - it must stay a
    normal bulk result, one failed id, not collapse into a single
    error()."""
    from tools.areas import bulk_set_entity_labels

    def fake_ws_multi(msgs):
        results = []
        for msg in msgs:
            if msg["entity_id"] == "light.totally_bogus_entity_zzz":
                results.append({"id": 1, "type": "result", "success": False,
                                "error": {"code": "not_found", "message": "Entity not found"}})
            else:
                results.append({"id": 1, "type": "result", "success": True,
                                "result": {"entity_entry": {"labels": msg["labels"]}}})
        return results

    import tools.areas as areas_module
    monkeypatch.setattr(areas_module, "_ws_multi", fake_ws_multi)

    result = bulk_set_entity_labels(
        ["light.bed_light", "light.totally_bogus_entity_zzz"], ["energia"])

    assert result == {"total": 2, "succeeded": 1, "failed": ["light.totally_bogus_entity_zzz"]}
    assert "error" not in result


def test_bulk_set_entity_labels_reports_a_transport_failure_as_one_error(fake_ha):
    """Under an invalid token (or any connection/auth failure), _ws_commands
    answers every message in the batch with the same {"error": "..."}
    shape, carrying no "success" key at all. The old code read that with
    `r.get("success")`, which is falsy for every one of them, so it folded
    a systemic transport failure into an ordinary-looking bulk result -
    {"succeeded": 0, "failed": [...]} with no "error" key anywhere -
    indistinguishable from every entity individually being rejected.
    Measured live against a throwaway Home Assistant instance under an
    invalid HA_TOKEN. This must now surface as one error(), not a bulk
    result at all."""
    from tools.areas import bulk_set_entity_labels

    fake_ha.fail_ws_transport("config/entity_registry/update", "Auth failed: {'type': 'auth_invalid'}")

    result = bulk_set_entity_labels(["light.bed_light", "light.study"], ["energia"])

    assert result["error"] == "websocket_error"
    assert "Auth failed" in result["detail"]
    assert "succeeded" not in result
    assert "failed" not in result


def test_bulk_set_entity_labels_rejects_a_batch_over_the_limit(fake_ha):
    from tools.areas import bulk_set_entity_labels, _BULK_LABEL_MAX

    result = bulk_set_entity_labels(
        [f"light.l{n}" for n in range(_BULK_LABEL_MAX + 1)], ["energia"])

    assert result["error"] == "too_many_entities"
    assert fake_ha.ws_calls == []  # nothing sent


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


def test_remove_lovelace_resource_sends_the_key_home_assistant_expects(fake_ha):
    """Home Assistant's lovelace/resources/delete command is keyed by
    `resource_id`, not `id` - the tool used to send `id`, which Home
    Assistant silently ignores (no error frame, no reply at all), so every
    call hung for the full WS read timeout and then raised TimeoutError.
    Pinning the exact key sent is what a `fail_ws`/`ws_result` test on the
    old code could not have caught: the fake answers by command *type*
    alone, so a wrong key inside an otherwise well-formed message would
    still get a normal success reply from the fake while hanging forever
    against the real thing - only asserting the sent message's own
    contents catches it."""
    from tools.dashboards import remove_lovelace_resource

    fake_ha.ws_result("lovelace/resources/delete", None)

    result = remove_lovelace_resource("9ed6e7503f1549e6bf3b73f079b7542d")

    assert result == {"deleted": "9ed6e7503f1549e6bf3b73f079b7542d", "success": True}
    sent = fake_ha.ws_calls[-1]
    assert sent["type"] == "lovelace/resources/delete"
    assert sent["resource_id"] == "9ed6e7503f1549e6bf3b73f079b7542d"
    assert "id" not in sent  # the old, wrong key - HA never replies to this one


def test_remove_lovelace_resource_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.dashboards import remove_lovelace_resource

    fake_ha.fail_ws("lovelace/resources/delete", code="not_found",
                    message="Resource not found")

    result = remove_lovelace_resource("does-not-exist")

    assert result["error"] == "not_found"
    assert "deleted" not in result


def test_update_dashboard_resolves_url_path_and_succeeds(fake_ha):
    from tools.dashboards import update_dashboard

    fake_ha.ws_result("lovelace/dashboards/list", [
        {"url_path": "energia", "id": "energia_id", "title": "Energia"},
    ])
    fake_ha.ws_result("lovelace/dashboards/update", {"id": "energia_id", "title": "Energy"})

    result = update_dashboard("energia", title="Energy")

    assert "error" not in result
    update_call = [c for c in fake_ha.ws_calls if c["type"] == "lovelace/dashboards/update"][0]
    assert update_call["dashboard_id"] == "energia_id"


def test_update_dashboard_says_not_found_for_a_genuinely_absent_url_path(fake_ha):
    from tools.dashboards import update_dashboard

    fake_ha.ws_result("lovelace/dashboards/list", [
        {"url_path": "energia", "id": "energia_id", "title": "Energia"},
    ])

    result = update_dashboard("does-not-exist", title="X")

    assert result["error"] == "not_found"


def test_update_dashboard_reports_a_registry_read_failure_not_not_found(fake_ha):
    """_dashboard_id used to fold a failed lovelace/dashboards/list read into
    an empty list via `result.get("result") or []`, so update_dashboard told
    the caller the dashboard did not exist when the registry read itself had
    failed - wrong error, wrong recovery advice."""
    from tools.dashboards import update_dashboard

    fake_ha.fail_ws("lovelace/dashboards/list", code="unauthorized", message="Admin required")

    result = update_dashboard("energia", title="X")

    assert result["error"] == "unauthorized"
    assert result["error"] != "not_found"


def test_delete_dashboard_resolves_url_path_and_succeeds(fake_ha):
    from tools.dashboards import delete_dashboard

    fake_ha.ws_result("lovelace/dashboards/list", [
        {"url_path": "energia", "id": "energia_id", "title": "Energia"},
    ])
    fake_ha.ws_result("lovelace/dashboards/delete", {})

    result = delete_dashboard("energia")

    assert result["deleted"] == "energia"
    delete_call = [c for c in fake_ha.ws_calls if c["type"] == "lovelace/dashboards/delete"][0]
    assert delete_call["dashboard_id"] == "energia_id"


def test_delete_dashboard_says_not_found_for_a_genuinely_absent_url_path(fake_ha):
    from tools.dashboards import delete_dashboard

    fake_ha.ws_result("lovelace/dashboards/list", [
        {"url_path": "energia", "id": "energia_id", "title": "Energia"},
    ])

    result = delete_dashboard("does-not-exist")

    assert result["error"] == "not_found"


def test_delete_dashboard_reports_a_registry_read_failure_not_not_found(fake_ha):
    from tools.dashboards import delete_dashboard

    fake_ha.fail_ws("lovelace/dashboards/list", code="unauthorized", message="Admin required")

    result = delete_dashboard("energia")

    assert result["error"] == "unauthorized"
    assert result["error"] != "not_found"


# ---- tools/hacs.py ---------------------------------------------------------

def test_hacs_check_reports_a_transport_failure_instead_of_a_false_success(fake_ha):
    """_ws returns {"error": "Auth failed: ..."} - no "success" key at all -
    when the connection or the authentication fails. The old
    `not result.get("success", True)` treated a missing key as a success
    (the default), so this shape passed _hacs_check as if HACS had
    answered normally. Routing through ws_error() first must catch it and
    report it as itself, not misdiagnose it as hacs_not_available - a
    transport failure says nothing about whether HACS is installed."""
    from tools.hacs import _hacs_check

    frame = {"error": "Auth failed: {'type': 'auth_invalid'}"}

    result = _hacs_check(frame)

    assert result["error"] == "websocket_error"
    assert "auth_invalid" in result["detail"]


def test_install_hacs_repo_reports_a_transport_failure_instead_of_a_false_success(fake_ha):
    """End-to-end version of the _hacs_check fix: a write whose request
    never reached Home Assistant must not come back looking like
    {"installed": True}."""
    from tools.hacs import install_hacs_repo

    fake_ha.ws_responses["hacs/repository/download"] = {
        "error": "Auth failed: {'type': 'auth_invalid'}"
    }

    result = install_hacs_repo("1234")

    assert result["error"] == "websocket_error"
    assert "installed" not in result


def test_hacs_info_wraps_a_success(fake_ha):
    from tools.hacs import hacs_info

    fake_ha.ws_result("hacs/info", {"version": "2.0.0", "stage": "running"})

    result = hacs_info()

    assert result["version"] == "2.0.0"


def test_hacs_info_reports_hacs_not_installed(fake_ha):
    from tools.hacs import hacs_info

    fake_ha.fail_ws("hacs/info", code="unknown_command", message="unknown command")

    result = hacs_info()

    assert result["error"] == "hacs_not_available"


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


def test_get_energy_summary_groups_by_area_including_device_inherited(fake_ha):
    """An entity's area is its own area_id when set, and otherwise its
    device's - the grouping here must resolve both, not just the first,
    and an entity with neither must fall into 'other' rather than being
    mis-grouped."""
    from tools.sensors import get_energy_summary

    fake_ha.states = [
        {"entity_id": "sensor.plug_kitchen", "state": "100",
         "attributes": {"friendly_name": "Kitchen plug", "device_class": "power"}},
        {"entity_id": "sensor.plug_office", "state": "50",
         "attributes": {"friendly_name": "Office plug", "device_class": "power"}},
        {"entity_id": "sensor.plug_orphan", "state": "10",
         "attributes": {"friendly_name": "Orphan plug", "device_class": "power"}},
    ]
    fake_ha.registry = [
        {"entity_id": "sensor.plug_kitchen", "area_id": "kitchen", "device_id": None, "labels": []},
        {"entity_id": "sensor.plug_office", "area_id": None, "device_id": "dev1", "labels": []},
        {"entity_id": "sensor.plug_orphan", "area_id": None, "device_id": None, "labels": []},
    ]
    fake_ha.devices = [{"id": "dev1", "area_id": "office"}]
    fake_ha.ws_result("config/area_registry/list", [
        {"area_id": "kitchen", "name": "Kitchen"},
        {"area_id": "office", "name": "Office"},
    ])

    result = get_energy_summary()

    groups = {g["group"]: g for g in result["groups"]}
    assert groups["Kitchen"]["total_w"] == 100
    assert groups["Office"]["total_w"] == 50
    assert groups["other"]["total_w"] == 10
    assert "note" not in result


def test_get_energy_summary_degrades_on_a_device_registry_failure_with_a_note(fake_ha):
    """The area map here is only used to group sensors, not to filter -
    this tool has no area_id parameter - so a failed read must degrade to
    'other' for every sensor rather than aborting, but it must say so:
    silently dumping everything into 'other' is indistinguishable from an
    instance where nothing has an area."""
    from tools.sensors import get_energy_summary

    fake_ha.states = [
        {"entity_id": "sensor.plug_kitchen", "state": "100",
         "attributes": {"friendly_name": "Kitchen plug", "device_class": "power"}},
    ]
    fake_ha.registry = [
        {"entity_id": "sensor.plug_kitchen", "area_id": "kitchen", "device_id": None, "labels": []},
    ]
    fake_ha.fail_ws("config/device_registry/list", code="unauthorized",
                    message="Admin required")

    result = get_energy_summary()

    assert result["groups"][0]["group"] == "other"
    assert "note" in result
    assert "area" in result["note"].lower()


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
    # list_sensors has no offset parameter - the note must not tell the
    # caller to advance one that does not exist.
    assert "advance offset" not in result["note"]


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


def test_list_lights_filters_by_area(fake_ha):
    """area_id lives in the entity registry, not in state attributes - Home
    Assistant never sets it there. The old filter read attrs.get("area_id"),
    a key that is always absent, so it could never match a single light on
    any real instance. fake_ha's default registry already has light.kitchen
    in "kitchen" and light.study in "study"."""
    from tools.lights import list_lights

    result = list_lights(area_id="kitchen")

    assert result["total"] == 1
    assert result["lights"][0]["entity_id"] == "light.kitchen"
    called = [c["type"] for c in fake_ha.ws_calls]
    assert "config/entity_registry/list" in called
    assert "config/device_registry/list" in called


def test_list_lights_area_filter_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.lights import list_lights

    fake_ha.fail_ws("config/entity_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_lights(area_id="kitchen")

    assert result["error"] == "unauthorized"
    assert "lights" not in result


def test_list_lights_without_area_filter_skips_the_registry_call(fake_ha):
    """The registry lookup is only needed to filter by area - a plain
    listing must not pay for a WS round trip it does not use."""
    from tools.lights import list_lights

    list_lights()

    assert fake_ha.ws_calls == []


def test_list_lights_filters_by_area_inherited_from_device(fake_ha):
    """An entity's area is its own area_id when set, and otherwise its
    device's - reading only the entity registry misses every light whose
    area comes from its device, which on a real installation is most of
    them. Measured live: list_lights(area_id=...) returned no results for
    such a light while list_areas() correctly listed it under that area."""
    from tools.lights import list_lights

    fake_ha.states = [
        {"entity_id": "light.garage", "state": "on",
         "attributes": {"friendly_name": "Garage light"}},
    ]
    fake_ha.registry = [
        {"entity_id": "light.garage", "area_id": None, "device_id": "dev1", "labels": []},
    ]
    fake_ha.devices = [{"id": "dev1", "area_id": "garage"}]

    result = list_lights(area_id="garage")

    assert result["total"] == 1
    assert result["lights"][0]["entity_id"] == "light.garage"


def test_list_lights_area_filter_excludes_a_light_with_no_area(fake_ha):
    from tools.lights import list_lights

    fake_ha.states = [
        {"entity_id": "light.hallway", "state": "on",
         "attributes": {"friendly_name": "Hallway light"}},
    ]
    fake_ha.registry = [
        {"entity_id": "light.hallway", "area_id": None, "device_id": None, "labels": []},
    ]

    result = list_lights(area_id="kitchen")

    assert result["total"] == 0


def test_list_lights_area_filter_device_registry_failure_is_also_fatal(fake_ha):
    from tools.lights import list_lights

    fake_ha.fail_ws("config/device_registry/list", code="unauthorized",
                    message="Admin required")

    result = list_lights(area_id="kitchen")

    assert result["error"] == "unauthorized"
    assert "lights" not in result


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


def test_create_helper_normal_creation_succeeds_for_each_domain(fake_ha):
    from tools.helpers import create_helper

    cases = [
        ("input_boolean", "Guest Mode", {"initial": True}, "input_boolean.guest_mode"),
        ("input_number", "Timer Minutes", {"min": 0, "max": 60, "step": 1}, "input_number.timer_minutes"),
        ("input_text", "Message", {"max": 100}, "input_text.message"),
        ("input_select", "Scene", {"options": ["Day", "Night"]}, "input_select.scene"),
        ("input_datetime", "Wake Up", {"has_time": True}, "input_datetime.wake_up"),
        ("counter", "Visitors", {"initial": 0}, "counter.visitors"),
        ("timer", "Pizza", {"duration": "00:20:00"}, "timer.pizza"),
        ("input_button", "Doorbell", {}, "input_button.doorbell"),
    ]
    for domain, name, config, entity_id in cases:
        helper_id = entity_id.split(".", 1)[1]
        fake_ha.ws_result(f"{domain}/create", {"id": helper_id, "name": name})
        fake_ha.states.append({"entity_id": entity_id, "state": "off", "attributes": {}})

        result = create_helper(domain=domain, name=name, config=config)

        assert "error" not in result, (domain, result)
        assert result["helper_id"] == helper_id, (domain, result)
        assert result["entity_id"] == entity_id, (domain, result)


def test_create_helper_refuses_a_config_that_tries_to_overwrite_type(fake_ha):
    """The exact hijack this fix closes: a config carrying "type" used to be
    spread after the reserved keys in the WS message, so it silently
    replaced input_boolean/create with an arbitrary command - here,
    config/auth/create, which would have created a system-admin user while
    the caller believed it was creating an inert boolean helper."""
    from tools.helpers import create_helper

    result = create_helper(
        domain="input_boolean",
        name="PortaSulRetro",
        config={"type": "config/auth/create", "name": "PortaSulRetro",
                "group_ids": ["system-admin"]},
    )

    assert result["error"] == "invalid_config_keys"
    assert "type" in result["offending_keys"]
    assert not fake_ha.ws_calls  # refused before ever reaching the WS layer


def test_create_helper_refuses_an_unexpected_config_key(fake_ha):
    from tools.helpers import create_helper

    result = create_helper(domain="input_boolean", name="Guest Mode",
                            config={"not_a_real_field": 1})

    assert result["error"] == "invalid_config_keys"
    assert result["offending_keys"] == ["not_a_real_field"]
    assert not fake_ha.ws_calls


def test_create_helper_rejects_an_unsupported_domain(fake_ha):
    from tools.helpers import create_helper

    result = create_helper(domain="climate", name="Nope")

    assert result["error"] == "unsupported_domain"
    assert not fake_ha.ws_calls


def test_create_helper_reports_a_ws_failure_as_an_error(fake_ha):
    from tools.helpers import create_helper

    fake_ha.fail_ws("input_boolean/create", code="invalid_format", message="bad name")

    result = create_helper(domain="input_boolean", name="")

    assert result["error"] == "invalid_format"


def test_create_helper_verifies_the_entity_actually_exists(fake_ha):
    """{domain}/create's response only ever carries a storage item id, never
    entity_id - the tool used to construct f"{domain}.{helper_id}" (or,
    lacking even an id, f"{domain}.{_slug(name)}") and report it unverified.
    Here the storage item id is real but no matching entity was ever
    created, which must surface as an error rather than a claimed
    entity_id."""
    from tools.helpers import create_helper

    fake_ha.ws_result("input_boolean/create", {"id": "guest_mode", "name": "Guest Mode"})
    # Deliberately do NOT add input_boolean.guest_mode to fake_ha.states.

    result = create_helper(domain="input_boolean", name="Guest Mode")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "input_boolean.guest_mode"


def test_create_helper_reports_no_id_in_response_instead_of_inventing_one(fake_ha):
    from tools.helpers import create_helper

    fake_ha.ws_result("input_boolean/create", {"name": "Guest Mode"})  # no "id"

    result = create_helper(domain="input_boolean", name="Guest Mode")

    assert result["error"] == "no_id_in_response"


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


def test_get_addon_logs_reports_supervisor_unavailable_as_prose_not_a_dict_repr(fake_ha):
    """Security review M9: get_addon_logs() returns str (its output IS the
    log), so its error path used to `return str(err)` - a Python dict repr
    handed to the model as prose ("{'error': 'supervisor_not_available',
    ...}"), unlike every other error path in this file which returns the
    error() dict directly. Fixed to the same "code: message" prose
    get_error_log() already uses for its own str-typed error case."""
    from tools.addons import get_addon_logs

    result = get_addon_logs("core_mosquitto")

    assert isinstance(result, str)
    assert result == (
        "supervisor_not_available: Add-on management requires Home "
        "Assistant OS or Supervised installation. This feature is not "
        "available in standalone mode."
    )
    assert "{'error'" not in result


# ---- tools/addons.py: call_addon_api path-traversal guard -------------------------
#
# Security review finding C1 (before publication): `path.lstrip("/")` strips
# leading slashes and nothing else, and httpx resolves '../' segments and
# sends the collapsed path on the wire - `call_addon_api(slug="x",
# path="../../../host/shutdown", method="POST")` reached `POST
# /host/shutdown` on a real Supervisor proxy with the add-on's own
# manager-role token attached. These tests exercise the fix: `slug` and
# `path` are rejected outright on a '..' segment (raw or percent-encoded),
# and the built URL is independently re-checked to still resolve inside
# `/addons/{slug}/api/` before anything is sent.

def test_call_addon_api_allows_a_legitimate_call(fake_ha, monkeypatch):
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")
    # path='/api/devices' -> lstripped to 'api/devices' -> appended after
    # the tool's own '.../api/' -> the doubled 'api/api/' the docstring's
    # own examples show; not something this fix changes.
    fake_ha.rest_responses["/addons/a0d7b954_zigbee2mqtt/api/api/devices"] = (
        200, {"devices": []})

    result = addons.call_addon_api(slug="a0d7b954_zigbee2mqtt", path="/api/devices")

    assert result == {"devices": []}


def test_call_addon_api_rejects_literal_path_traversal(fake_ha, monkeypatch):
    """The review's own reproduction: a raw '../' sequence that httpx
    collapses into a request outside the add-on's own API entirely."""
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")

    result = addons.call_addon_api(slug="x", path="../../../host/shutdown", method="POST")

    assert result["error"] == "invalid_path"
    assert fake_ha.rest_calls == []


def test_call_addon_api_rejects_percent_encoded_path_traversal(fake_ha, monkeypatch):
    """'%2e%2e' decodes to '..' - a plain `'..' in path` check alone would
    miss this; the decoded form is checked too."""
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")

    result = addons.call_addon_api(slug="x", path="%2e%2e/%2e%2e/%2e%2e/host/shutdown")

    assert result["error"] == "invalid_path"
    assert fake_ha.rest_calls == []


def test_call_addon_api_rejects_traversal_with_an_encoded_slash(fake_ha, monkeypatch):
    """The '..' segments are literal here; only the separating slashes are
    percent-encoded ('%2f') - still rejected."""
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")

    result = addons.call_addon_api(slug="x", path="..%2f..%2f..%2fhost%2fshutdown")

    assert result["error"] == "invalid_path"
    assert fake_ha.rest_calls == []


def test_call_addon_api_rejects_traversal_in_an_absolute_path(fake_ha, monkeypatch):
    """A leading slash goes through path.lstrip("/") before the traversal
    check runs - confirms that stripping the leading slash does not also
    strip away the '..' segments behind it."""
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")

    result = addons.call_addon_api(slug="x", path="/../../../host/shutdown")

    assert result["error"] == "invalid_path"
    assert fake_ha.rest_calls == []


def test_call_addon_api_rejects_slug_traversal(fake_ha, monkeypatch):
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")

    result = addons.call_addon_api(slug="../../host", path="devices")

    assert result["error"] == "invalid_slug"
    assert fake_ha.rest_calls == []


def test_call_addon_api_resolved_url_check_blocks_traversal_even_if_the_string_check_is_bypassed(
    fake_ha, monkeypatch,
):
    """Belt and braces, proven independently: with the '..' string check
    disabled (standing in for a future encoding trick it fails to catch),
    the second check - resolving the built URL and confirming it still
    starts with /addons/{slug}/api/ - still blocks the review's own
    reproduction on its own."""
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")
    monkeypatch.setattr(addons, "_contains_dotdot", lambda value: False)

    result = addons.call_addon_api(slug="x", path="../../../host/shutdown", method="POST")

    assert result["error"] == "invalid_path"
    assert fake_ha.rest_calls == []


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


# ─── Transport-failure frame: {"error": "..."} with no "success" key ────────
#
# _ws() returns this shape - not Home Assistant's {"success": False, "error":
# {...}} - when the connection or the authentication itself fails. A check
# written as `result.get("success", True)` reads the missing key as a
# default success, which is how 24 call sites across these eight files used
# to report success for a write that never reached Home Assistant. One
# representative tool per file, reproducing that exact frame via
# fake_ha.fail_ws_transport().

def test_delete_assist_pipeline_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.assist import delete_assist_pipeline

    fake_ha.fail_ws_transport("assist_pipeline/pipeline/delete")

    result = delete_assist_pipeline("preferred")

    assert "error" in result
    assert "deleted" not in result


def test_update_dashboard_config_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.dashboards import update_dashboard_config

    fake_ha.fail_ws_transport("lovelace/config/save")

    result = update_dashboard_config("does-not-exist", {"views": []})

    assert "error" in result
    assert "saved" not in result


def test_delete_user_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.users import delete_user

    fake_ha.fail_ws_transport("config/auth/delete")

    result = delete_user("some-uid")

    assert "error" in result
    assert "deleted" not in result


def test_delete_person_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.persons import delete_person

    fake_ha.fail_ws_transport("person/delete")

    result = delete_person("jane_doe")

    assert "error" in result
    assert "deleted" not in result


def test_delete_tag_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.tags import delete_tag

    fake_ha.fail_ws_transport("tag/delete")

    result = delete_tag("abc123")

    assert "error" in result
    assert "deleted" not in result


def test_browse_media_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.media_players import browse_media

    fake_ha.fail_ws_transport("media_player/browse_media")

    result = browse_media("media_player.living_room")

    assert "error" in result
    assert "children" not in result


def test_import_blueprint_reports_a_transport_failure_as_an_error(fake_ha):
    from tools.automations import import_blueprint

    fake_ha.fail_ws_transport("blueprint/import")

    result = import_blueprint("https://example.com/blueprint.yaml")

    assert "error" in result
    assert "imported" not in result


# ---- tools/automations.py: import_blueprint() actually saves (D4) -----------------
# blueprint/import is only the preview step the blueprint editor uses - it
# validates and returns the parsed YAML but writes nothing to disk. Verified
# live: blueprint/list was unchanged right after blueprint/import alone, and
# only showed the new path once blueprint/save was also sent.

def test_import_blueprint_also_calls_save_and_reports_it(fake_ha):
    from tools.automations import import_blueprint

    fake_ha.ws_result("blueprint/import", {
        "suggested_filename": "someone/my_blueprint",
        "raw_data": "blueprint:\n  name: My Blueprint\n  domain: automation\n",
        "blueprint": {"metadata": {"name": "My Blueprint", "domain": "automation"}},
        "validation_errors": None,
        "exists": False,
    })
    fake_ha.ws_result("blueprint/save", {"overrides_existing": False})

    result = import_blueprint("https://example.com/my_blueprint.yaml")

    assert result["imported"] is True
    assert result["saved"] is True
    assert result["path"] == "someone/my_blueprint"
    assert result["domain"] == "automation"
    assert result["name"] == "My Blueprint"
    assert result["overrides_existing"] is False
    save_call = next(m for m in fake_ha.ws_calls if m["type"] == "blueprint/save")
    assert save_call["domain"] == "automation"
    assert save_call["path"] == "someone/my_blueprint"
    assert "raw_data" not in save_call
    assert save_call["yaml"] == "blueprint:\n  name: My Blueprint\n  domain: automation\n"


def test_import_blueprint_refuses_to_save_an_invalid_one(fake_ha):
    from tools.automations import import_blueprint

    fake_ha.ws_result("blueprint/import", {
        "suggested_filename": "someone/broken",
        "raw_data": "not: a valid blueprint",
        "blueprint": {},
        "validation_errors": ["missing 'name'"],
        "exists": False,
    })

    result = import_blueprint("https://example.com/broken.yaml")

    assert result["error"] == "invalid_blueprint"
    assert not any(m["type"] == "blueprint/save" for m in fake_ha.ws_calls)


def test_import_blueprint_reports_a_save_failure(fake_ha):
    from tools.automations import import_blueprint

    fake_ha.ws_result("blueprint/import", {
        "suggested_filename": "someone/my_blueprint",
        "raw_data": "blueprint:\n  name: My Blueprint\n  domain: script\n",
        "blueprint": {"metadata": {"name": "My Blueprint", "domain": "script"}},
        "validation_errors": None,
        "exists": False,
    })
    fake_ha.fail_ws("blueprint/save", code="unknown_error", message="disk full")

    result = import_blueprint("https://example.com/my_blueprint.yaml")

    assert result["error"] == "unknown_error"
    assert "saved" not in result


def test_get_system_health_reports_a_transport_failure_as_an_error(fake_ha):
    """The site that needed judgment rather than a mechanical swap: a plain
    transport/auth failure must surface as itself, not be folded into the
    "not available via Supervisor proxy" fallback note - that note asserts a
    cause (a proxy limitation) this failure has not established."""
    from tools.diagnostics import get_system_health

    fake_ha.fail_ws_transport("system_health/info")

    result = get_system_health()

    assert "error" in result
    assert "_note" not in result
    assert "homeassistant" not in result


def test_get_system_health_unknown_command_falls_back_to_rest_config(fake_ha):
    """The one failure shape that *should* fall back: an unknown_command (or
    not_found) response means this connection does not support the WS
    command at all - e.g. a Supervisor-proxied add-on token - the same
    discriminator tools/hacs.py's _hacs_check and list_schedules() use."""
    from tools.diagnostics import get_system_health

    fake_ha.fail_ws("system_health/info", code="unknown_command",
                    message="Unknown command.")

    result = get_system_health()

    assert "error" not in result
    assert "_note" in result
    assert result["homeassistant"]["version"] == fake_ha.config["version"]


def test_get_system_health_other_failure_does_not_fall_back(fake_ha):
    """A real failure - e.g. missing permissions - must not be reported as
    a Supervisor proxy limitation: that is a cause the tool has not
    established."""
    from tools.diagnostics import get_system_health

    fake_ha.fail_ws("system_health/info", code="unauthorized",
                    message="Admin required")

    result = get_system_health()

    assert result["error"] == "unauthorized"
    assert "_note" not in result
