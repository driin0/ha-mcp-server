"""tools/validation.py: resolving what tools/_refs.py extracts against a
live snapshot (states + both registries) and classifying it into the three
outcomes the module docstring lays out - dead_reference, restored,
unavailable - plus passing find_fail_open_waits() straight through.

Every test here builds its own automation config rather than relying on
fakeha.py's DEFAULT_AUTOMATION_CONFIGS: that fixture exists to exercise
get_automation()'s own normalisation (tests/test_automations.py) and does
not model the incident's actual rename mismatch (its guard and its actions
all name the SAME button id). The incident shape - a stale id trapped only
in a template, guarding actions that were updated to the current id - is
rebuilt here directly, matching tests/test_refs.py's own INCIDENT_CONFIG.
"""
import copy


# Legacy vocabulary throughout, matching tests/test_refs.py's own
# INCIDENT_CONFIG: the guard's template still names the button's OLD id
# (button.nas_shutdown); the actions - updated when the button was renamed -
# reference the NEW one (button.nas_shut_down). That mismatch between a
# stale template reference and a live field reference is the entire
# incident this module exists to catch before it does damage again.
INCIDENT_CONFIG = {
    "alias": "NAS shutdown guard",
    "trigger": [
        {"platform": "state", "entity_id": "input_boolean.nas_shutdown_request", "to": "on"},
    ],
    "condition": [
        {"condition": "template",
         "value_template": '{{ not is_state("button.nas_shutdown", "unavailable") }}'},
    ],
    "action": [
        {"service": "button.press", "target": {"entity_id": "button.nas_shut_down"}},
        {"wait_for_trigger": [
            {"platform": "state", "entity_id": "button.nas_shut_down", "to": "unavailable"},
        ], "timeout": "00:00:30"},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ],
    "mode": "single",
}


def _seed_incident(fake_ha, automation_id="9001", entity_id="automation.nas_shutdown_guard"):
    """Seed fake_ha with the incident automation and every entity its
    config references EXCEPT the stale button.nas_shutdown - which must
    stay absent from both self.states and self.registry for it to be a
    dead_reference at all."""
    fake_ha.states.append({
        "entity_id": entity_id, "state": "on",
        "attributes": {"id": automation_id, "friendly_name": "NAS shutdown guard"},
    })
    fake_ha.automation_configs[automation_id] = copy.deepcopy(INCIDENT_CONFIG)
    fake_ha.states += [
        {"entity_id": "input_boolean.nas_shutdown_request", "state": "off", "attributes": {}},
        {"entity_id": "button.nas_shut_down", "state": "2026-08-20T00:00:00+00:00", "attributes": {}},
        {"entity_id": "switch.nas_power", "state": "on", "attributes": {}},
    ]
    fake_ha.registry += [
        {"entity_id": "input_boolean.nas_shutdown_request", "area_id": None, "device_id": None, "labels": []},
        {"entity_id": "button.nas_shut_down", "area_id": None, "device_id": None, "labels": []},
        {"entity_id": "switch.nas_power", "area_id": None, "device_id": None, "labels": []},
    ]
    return entity_id


# ---------------------------------------------------------------------------
# validate_automation() - the incident's own shape
# ---------------------------------------------------------------------------

def test_the_incidents_stale_template_reference_is_reported_as_dead(fake_ha):
    from tools.validation import validate_automation

    entity_id = _seed_incident(fake_ha)

    result = validate_automation(entity_id)

    dead = [i for i in result["issues"] if i["outcome"] == "dead_reference"]
    assert len(dead) == 1
    issue = dead[0]
    assert issue["id"] == "button.nas_shutdown"
    assert issue["where"] == "conditions.0.value_template"
    assert issue["source"] == "template"
    assert issue["severity"] == "error"


def test_the_dead_templates_detail_explains_the_fail_open_mechanism(fake_ha):
    """The whole point of this tool, per its own module docstring: the
    detail text for a dead reference found inside a template must explain
    WHY it is dangerous, not just that the id is missing."""
    from tools.validation import validate_automation

    entity_id = _seed_incident(fake_ha)

    result = validate_automation(entity_id)
    detail = next(i for i in result["issues"] if i["outcome"] == "dead_reference")["detail"]

    assert "is_state" in detail
    assert "None" in detail
    assert "unavailable" in detail
    assert "PASSES" in detail or "passes" in detail
    assert "fail" in detail.lower() and "open" in detail.lower()


def test_the_incidents_live_references_are_not_reported(fake_ha):
    """button.nas_shut_down (the current id), switch.nas_power and
    input_boolean.nas_shutdown_request are all seeded with live states -
    a validator that flags them anyway is crying wolf on a correct part
    of the automation."""
    from tools.validation import validate_automation

    entity_id = _seed_incident(fake_ha)

    result = validate_automation(entity_id)

    flagged_ids = {i["id"] for i in result["issues"]}
    assert flagged_ids == {"button.nas_shutdown"}


def test_the_incidents_fail_open_wait_is_reported(fake_ha):
    from tools.validation import validate_automation

    entity_id = _seed_incident(fake_ha)

    result = validate_automation(entity_id)

    assert len(result["fail_open_waits"]) == 1
    wait = result["fail_open_waits"][0]
    assert wait["wait_where"] == "actions.1"
    assert wait["action_where"] == "actions.2"
    assert wait["service"] == "switch.turn_off"
    assert wait["timeout"] == "00:00:30"


def test_the_incidents_summary_counts_match(fake_ha):
    from tools.validation import validate_automation

    entity_id = _seed_incident(fake_ha)

    result = validate_automation(entity_id)

    assert result["summary"]["dead_references"] == 1
    assert result["summary"]["restored"] == 0
    assert result["summary"]["unavailable"] == 0
    assert result["summary"]["fail_open_waits"] == 1
    assert result["automation_id"] == "9001"
    assert result["name"] == "NAS shutdown guard"


# ---------------------------------------------------------------------------
# validate_automation() - the three outcomes, isolated
# ---------------------------------------------------------------------------

def _minimal_automation(fake_ha, automation_id, entity_id, ref_entity_id):
    """The smallest possible automation whose only reference is
    ref_entity_id, as a plain field (target.entity_id) - not a template -
    so each outcome test below isolates exactly the case it names."""
    fake_ha.states.append({
        "entity_id": entity_id, "state": "on",
        "attributes": {"id": automation_id, "friendly_name": "Probe"},
    })
    fake_ha.automation_configs[automation_id] = {
        "alias": "Probe",
        "trigger": [{"platform": "sun", "event": "sunset"}],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ref_entity_id}}],
    }


def test_a_reference_absent_from_both_registry_and_states_is_dead(fake_ha):
    from tools.validation import validate_automation

    _minimal_automation(fake_ha, "p1", "automation.probe1", "light.ghost")

    result = validate_automation("automation.probe1")

    assert result["issues"] == [{
        "id": "light.ghost", "kind": "entity", "where": "actions.0.target.entity_id",
        "source": "field", "outcome": "dead_reference", "severity": "error",
        "detail": result["issues"][0]["detail"],
    }]
    assert "is_state" not in result["issues"][0]["detail"]  # field, not template


def test_a_reference_in_the_registry_with_no_state_is_restored(fake_ha):
    from tools.validation import validate_automation

    fake_ha.registry.append(
        {"entity_id": "light.reconfigured", "area_id": None, "device_id": None, "labels": []})
    _minimal_automation(fake_ha, "p2", "automation.probe2", "light.reconfigured")

    result = validate_automation("automation.probe2")

    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["outcome"] == "restored"
    assert issue["severity"] == "error"
    assert "integration" in issue["detail"]


def test_a_reference_with_state_unavailable_is_unavailable(fake_ha):
    from tools.validation import validate_automation

    fake_ha.states.append({"entity_id": "light.offline", "state": "unavailable", "attributes": {}})
    _minimal_automation(fake_ha, "p3", "automation.probe3", "light.offline")

    result = validate_automation("automation.probe3")

    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["outcome"] == "unavailable"
    assert issue["severity"] == "warning"


def test_a_reference_with_state_unknown_is_also_unavailable(fake_ha):
    from tools.validation import validate_automation

    fake_ha.states.append({"entity_id": "sensor.confused", "state": "unknown", "attributes": {}})
    _minimal_automation(fake_ha, "p4", "automation.probe4", "sensor.confused")

    result = validate_automation("automation.probe4")

    assert result["issues"][0]["outcome"] == "unavailable"


def test_a_live_normal_reference_is_not_reported(fake_ha):
    from tools.validation import validate_automation

    # light.kitchen is one of fakeha's own DEFAULT_STATES, state "on".
    _minimal_automation(fake_ha, "p5", "automation.probe5", "light.kitchen")

    result = validate_automation("automation.probe5")

    assert result["issues"] == []
    assert result["fail_open_waits"] == []
    assert result["summary"] == {
        "refs_checked": 1, "dead_references": 0, "restored": 0,
        "unavailable": 0, "fail_open_waits": 0,
    }


# ---------------------------------------------------------------------------
# validate_automation() - device references
# ---------------------------------------------------------------------------

def test_a_device_id_absent_from_the_device_registry_is_dead(fake_ha):
    from tools.validation import validate_automation

    fake_ha.states.append({
        "entity_id": "automation.probe6", "state": "on",
        "attributes": {"id": "p6", "friendly_name": "Probe"},
    })
    fake_ha.automation_configs["p6"] = {
        "alias": "Probe",
        "trigger": [{"platform": "sun", "event": "sunset"}],
        "action": [{"device_id": "ghost_device", "domain": "light", "type": "turn_on"}],
    }

    result = validate_automation("automation.probe6")

    assert result["issues"] == [{
        "id": "ghost_device", "kind": "device", "where": "actions.0.device_id",
        "source": "field", "outcome": "dead_reference", "severity": "error",
        "detail": result["issues"][0]["detail"],
    }]


def test_a_device_id_present_in_the_device_registry_is_not_reported(fake_ha):
    from tools.validation import validate_automation

    fake_ha.states.append({
        "entity_id": "automation.probe7", "state": "on",
        "attributes": {"id": "p7", "friendly_name": "Probe"},
    })
    # "device_bed" is one of fakeha's own DEFAULT_DEVICES.
    fake_ha.automation_configs["p7"] = {
        "alias": "Probe",
        "trigger": [{"platform": "sun", "event": "sunset"}],
        "action": [{"device_id": "device_bed", "domain": "light", "type": "turn_on"}],
    }

    result = validate_automation("automation.probe7")

    assert result["issues"] == []


# ---------------------------------------------------------------------------
# validate_automation() - error passthrough
# ---------------------------------------------------------------------------

def test_an_automation_that_does_not_exist_reports_get_automations_own_error(fake_ha):
    from tools.validation import validate_automation

    result = validate_automation("automation.does_not_exist_at_all")

    assert result["error"] == "not_found"
    assert "issues" not in result


# ---------------------------------------------------------------------------
# validate_all_automations()
# ---------------------------------------------------------------------------

def test_validate_all_automations_makes_one_http_request_per_automation(fake_ha):
    """fakeha seeds two default automations (automation.nas_shutdown,
    automation.morning) plus the two added here - four total. This test
    also seeds a config for automation.morning's own id
    ("1684270733501", otherwise absent from DEFAULT_AUTOMATION_CONFIGS),
    so every one of the four resolves on the FIRST GET - the common case
    _fetch_config() documents, as opposed to the two-GET fallback path
    (id 404s, slug is then tried) exercised separately in
    test_validate_all_automations_reports_a_read_error_regardless_of_only_issues."""
    from tools.validation import validate_all_automations

    fake_ha.registry = []
    fake_ha.automation_configs["1684270733501"] = {
        "alias": "Morning", "trigger": [], "action": [],
    }
    _minimal_automation(fake_ha, "a1", "automation.a1", "light.kitchen")
    _minimal_automation(fake_ha, "a2", "automation.a2", "light.ghost")

    fake_ha.rest_calls.clear()
    validate_all_automations(only_issues=False)

    config_reads = [
        c for c in fake_ha.rest_calls
        if c.url.path.startswith("/api/config/automation/config/")
    ]
    assert len(config_reads) == 4
    assert all(c.method == "GET" for c in config_reads)


def test_validate_all_automations_only_issues_filters_clean_automations(fake_ha):
    from tools.validation import validate_all_automations

    fake_ha.registry = []
    _minimal_automation(fake_ha, "clean1", "automation.clean1", "light.kitchen")
    _minimal_automation(fake_ha, "broken1", "automation.broken1", "light.ghost")

    result = validate_all_automations(only_issues=True)

    entity_ids = {r["entity_id"] for r in result["results"]}
    assert "automation.broken1" in entity_ids
    assert "automation.clean1" not in entity_ids


def test_validate_all_automations_checked_differs_from_total_when_only_issues(fake_ha):
    """The distinction the module docstring insists on: summary.checked
    counts every automation actually validated, `total` (from envelope())
    counts only what only_issues=True kept in `results`."""
    from tools.validation import validate_all_automations

    fake_ha.registry = []
    _minimal_automation(fake_ha, "clean2", "automation.clean2", "light.kitchen")
    _minimal_automation(fake_ha, "broken2", "automation.broken2", "light.ghost")

    result = validate_all_automations(only_issues=True)

    # fakeha's own default automation (automation.nas_shutdown) is clean
    # against this test's registry/state seeding too - it has no
    # references at all in its own DEFAULT_AUTOMATION_CONFIGS' actions
    # once considered... in fact it does reference button.nas_shutdown/
    # switch.nas_power, neither seeded here, so it also reports issues.
    # The assertion below only relies on the inequality, not exact counts.
    assert result["summary"]["checked"] >= 3
    assert result["total"] < result["summary"]["checked"]
    assert result["total"] == len(result["results"])


def test_validate_all_automations_without_only_issues_keeps_every_automation(fake_ha):
    from tools.validation import validate_all_automations

    fake_ha.registry = []
    _minimal_automation(fake_ha, "clean3", "automation.clean3", "light.kitchen")

    result = validate_all_automations(only_issues=False)

    assert result["total"] == result["summary"]["checked"]
    entity_ids = {r["entity_id"] for r in result["results"]}
    assert "automation.clean3" in entity_ids


def test_validate_all_automations_reports_a_read_error_regardless_of_only_issues(fake_ha):
    """An automation entity registered in the state machine but with no
    stored config at all (e.g. YAML-defined, or a race) must still show
    up - not be silently dropped from the sweep - even with
    only_issues=True."""
    from tools.validation import validate_all_automations

    fake_ha.registry = []
    fake_ha.states.append({
        "entity_id": "automation.yaml_defined", "state": "on",
        "attributes": {"friendly_name": "YAML defined"},
    })

    result = validate_all_automations(only_issues=True)

    entry = next(r for r in result["results"] if r["entity_id"] == "automation.yaml_defined")
    assert entry["read_error"]["error"] == "not_found"
    assert result["summary"]["read_errors"] >= 1


def test_validate_all_automations_shares_one_snapshot_not_one_per_automation(fake_ha):
    """The live snapshot (registries + states) must be fetched once for
    the whole call, not once per automation - otherwise the "one HTTP
    request per automation" promise in the docstring would be false for
    the WebSocket side of the cost."""
    from tools.validation import validate_all_automations

    fake_ha.registry = []
    _minimal_automation(fake_ha, "s1", "automation.s1", "light.kitchen")
    _minimal_automation(fake_ha, "s2", "automation.s2", "light.study")
    _minimal_automation(fake_ha, "s3", "automation.s3", "light.bed_light")

    fake_ha.ws_calls.clear()
    validate_all_automations(only_issues=False)

    registry_list_calls = [c for c in fake_ha.ws_calls if c["type"] == "config/entity_registry/list"]
    device_list_calls = [c for c in fake_ha.ws_calls if c["type"] == "config/device_registry/list"]
    # list_automations() itself also reads config/entity_registry/list
    # once for its own labels lookup - two total is the honest fixed
    # cost, not one per automation (which would be four, for four
    # automations including fakeha's own default).
    assert len(registry_list_calls) <= 2
    assert len(device_list_calls) == 1
