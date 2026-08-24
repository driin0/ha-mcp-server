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
    assert result["summary"]["disabled"] == 0
    assert result["summary"]["unavailable"] == 0
    assert result["summary"]["unknown"] == 0
    assert result["summary"]["fail_open_wait_count"] == 1
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
        {"entity_id": "light.reconfigured", "area_id": None, "device_id": None,
         "labels": [], "disabled_by": None})
    _minimal_automation(fake_ha, "p2", "automation.probe2", "light.reconfigured")

    result = validate_automation("automation.probe2")

    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["outcome"] == "restored"
    assert issue["severity"] == "error"
    assert issue["disabled_by"] is None
    assert "integration" in issue["detail"]


def test_a_reference_to_a_deliberately_disabled_entity_is_disabled_not_restored(fake_ha):
    """_classify() has the registry row in hand and must read disabled_by
    from it, the same way list_orphan_entities() already does - a
    deliberately disabled entity is not an integration failure, and must
    not be reported with detail text claiming one."""
    from tools.validation import validate_automation

    fake_ha.registry.append(
        {"entity_id": "light.turned_off_on_purpose", "area_id": None, "device_id": None,
         "labels": [], "disabled_by": "user"})
    _minimal_automation(fake_ha, "p2b", "automation.probe2b", "light.turned_off_on_purpose")

    result = validate_automation("automation.probe2b")

    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["outcome"] == "disabled"
    assert issue["severity"] == "warning"
    assert issue["disabled_by"] == "user"
    assert "integration is not loaded" not in issue["detail"]
    assert result["summary"]["restored"] == 0
    assert result["summary"]["disabled"] == 1


def test_a_reference_with_state_unavailable_is_unavailable(fake_ha):
    from tools.validation import validate_automation

    fake_ha.states.append({"entity_id": "light.offline", "state": "unavailable", "attributes": {}})
    _minimal_automation(fake_ha, "p3", "automation.probe3", "light.offline")

    result = validate_automation("automation.probe3")

    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["outcome"] == "unavailable"
    assert issue["severity"] == "warning"


def test_a_reference_with_state_unknown_is_its_own_outcome_not_unavailable(fake_ha):
    """unknown is common as an entity's normal resting state (a button or
    event entity before it is first triggered, a scene, ...) - unlike
    unavailable, which never happens as a resting state. Conflating them
    under one "unavailable" outcome would make a diagnosis ("its own
    integration is reporting it as not answering right now") about an
    entity that has simply never had an event."""
    from tools.validation import validate_automation

    fake_ha.states.append({"entity_id": "sensor.confused", "state": "unknown", "attributes": {}})
    _minimal_automation(fake_ha, "p4", "automation.probe4", "sensor.confused")

    result = validate_automation("automation.probe4")

    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["outcome"] == "unknown"
    assert issue["severity"] == "info"
    # No false diagnosis: unlike the unavailable case, this must not claim
    # the integration is "not answering right now" - that is not
    # established from state "unknown" alone.
    assert "not answering right now" not in issue["detail"]
    assert result["summary"]["unavailable"] == 0
    assert result["summary"]["unknown"] == 1


def test_a_live_normal_reference_is_not_reported(fake_ha):
    from tools.validation import validate_automation

    # light.kitchen is one of fakeha's own DEFAULT_STATES, state "on".
    _minimal_automation(fake_ha, "p5", "automation.probe5", "light.kitchen")

    result = validate_automation("automation.probe5")

    assert result["issues"] == []
    assert result["fail_open_waits"] == []
    assert result["summary"] == {
        "refs_checked": 1, "dead_references": 0, "restored": 0,
        "disabled": 0, "unavailable": 0, "unknown": 0, "fail_open_wait_count": 0,
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


def test_validate_all_automations_returns_the_callers_own_offset(fake_ha):
    """envelope()'s own default offset (0) used to leak straight through
    regardless of what the caller actually passed - a caller paginating
    with offset=1 got back {"offset": 0} in every response, indistinguishable
    from a call that started at the very beginning, with no way to tell
    whether its own argument had been honoured at all."""
    from tools.validation import validate_all_automations

    # fakeha's own defaults seed exactly two automations (automation.morning,
    # automation.nas_shutdown - see DEFAULT_STATES, tests/fakeha.py), sorted
    # by name: "Morning" then "NAS shutdown".
    result = validate_all_automations(only_issues=False, limit=1, offset=1)

    assert result["offset"] == 1
    assert result["total"] == 1
    assert result["returned"] == 1
    assert result["results"][0]["name"] == "NAS shutdown"


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


# ---------------------------------------------------------------------------
# find_entity_usages()
# ---------------------------------------------------------------------------

def _minimal_script(fake_ha, script_id, entity_id, sequence, name="Probe script"):
    """The smallest possible script whose config is `sequence`, seeded the
    way create_script() would leave it: a state row (list_scripts() reads
    scripts from self.states, like list_automations() does for
    automations) plus a script_configs entry get_script() reads back."""
    fake_ha.states.append({
        "entity_id": entity_id, "state": "off",
        "attributes": {"friendly_name": name},
    })
    fake_ha.script_configs[script_id] = {
        "alias": name, "sequence": sequence, "mode": "single",
    }


def test_find_entity_usages_reports_a_field_reference_in_an_automation(fake_ha):
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}
    _minimal_automation(fake_ha, "u1", "automation.u1", "light.target")

    result = find_entity_usages("light.target")

    assert result["usages"] == [{
        "source_kind": "automation", "entity_id": "automation.u1",
        "name": "Probe", "where": "actions.0.target.entity_id",
        "source": "field",
    }]


def test_find_entity_usages_reports_a_template_reference_in_a_script(fake_ha):
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}
    _minimal_script(fake_ha, "guard", "script.guard", [
        {"condition": "template",
         "value_template": "{{ is_state('light.target', 'on') }}"},
    ])

    result = find_entity_usages("light.target")

    assert result["usages"] == [{
        "source_kind": "script", "entity_id": "script.guard",
        "name": "Probe script", "where": "sequence.0.value_template",
        "source": "template",
    }]


def test_find_entity_usages_reports_both_an_automation_and_a_script_together(fake_ha):
    """The scenario this tool exists for: the same entity referenced from
    more than one place, both of which must be found - not just the first
    match - since a rename has to update every one of them."""
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}
    _minimal_automation(fake_ha, "u2", "automation.u2", "light.shared")
    _minimal_script(fake_ha, "shared_user", "script.shared_user", [
        {"service": "light.turn_off", "target": {"entity_id": "light.shared"}},
    ])

    result = find_entity_usages("light.shared")

    kinds = {(u["source_kind"], u["entity_id"]) for u in result["usages"]}
    assert kinds == {("automation", "automation.u2"), ("script", "script.shared_user")}


def test_find_entity_usages_returns_empty_but_still_notes_the_scope(fake_ha):
    """No automation or script references the id at all - `usages` is
    empty, but `note` still explains what was and was not searched, since
    silence would read as a promise of full coverage."""
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}
    result = find_entity_usages("light.nobody_references_this")

    assert result["usages"] == []
    assert "dashboard" in result["note"].lower()
    assert "helper" in result["note"].lower()
    assert "template entit" in result["note"].lower()


def test_find_entity_usages_note_always_present_even_with_results(fake_ha):
    """The scope disclaimer is not conditioned on finding nothing - a
    caller who DID get a hit must still be told the sweep was partial."""
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}
    _minimal_automation(fake_ha, "u3", "automation.u3", "light.found")

    result = find_entity_usages("light.found")

    assert result["usages"]
    assert "note" in result
    assert "dashboard" in result["note"].lower()


def test_find_entity_usages_ignores_a_different_entity(fake_ha):
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}
    _minimal_automation(fake_ha, "u4", "automation.u4", "light.a")

    result = find_entity_usages("light.b")

    assert result["usages"] == []


def test_find_entity_usages_counts_and_reports_unreadable_configs(fake_ha):
    """automation.morning (a fakeha default) has a registered state but no
    entry in automation_configs at all, so get_automation() reports
    not_found for it - that must be skipped, not silently treated as "does
    not reference light.target", and the skip must be visible in `note`."""
    from tools.validation import find_entity_usages

    fake_ha.automation_configs = {}

    result = find_entity_usages("light.target")

    assert result["usages"] == []
    assert "skipped" in result["note"].lower() or "could not be read" in result["note"].lower()


# ---------------------------------------------------------------------------
# list_orphan_entities()
# ---------------------------------------------------------------------------

def test_list_orphan_entities_finds_a_registered_entity_with_no_state(fake_ha):
    from tools.validation import list_orphan_entities

    fake_ha.registry.append({
        "entity_id": "light.reconfigured", "area_id": "kitchen",
        "device_id": None, "labels": [], "platform": "hue",
        "disabled_by": None,
    })

    result = list_orphan_entities()

    orphan_ids = {o["entity_id"] for o in result["orphans"]}
    assert "light.reconfigured" in orphan_ids
    entry = next(o for o in result["orphans"] if o["entity_id"] == "light.reconfigured")
    assert entry["platform"] == "hue"
    assert entry["area_id"] == "kitchen"
    assert entry["disabled_by"] is None


def test_list_orphan_entities_excludes_entities_with_a_live_state(fake_ha):
    """light.kitchen is one of fakeha's own DEFAULT_STATES AND
    DEFAULT_REGISTRY entries - registered and live, so it must not be
    reported as an orphan."""
    from tools.validation import list_orphan_entities

    result = list_orphan_entities()

    orphan_ids = {o["entity_id"] for o in result["orphans"]}
    assert "light.kitchen" not in orphan_ids


def test_list_orphan_entities_reports_disabled_by_for_a_deliberately_disabled_entity(fake_ha):
    """A disabled entity legitimately has no state - a different situation
    from a silently orphaned one, distinguished by disabled_by rather than
    conflated with it.

    Asks for include_disabled=True because that is where this kind lives
    now: it is excluded from the default listing (it dominated the real
    population 3249 to 0), but when asked for it must still arrive
    labelled with WHAT disabled it, not merely present."""
    from tools.validation import list_orphan_entities

    fake_ha.registry.append({
        "entity_id": "sensor.disabled_by_user", "area_id": None,
        "device_id": None, "labels": [], "platform": "mqtt",
        "disabled_by": "user",
    })

    result = list_orphan_entities(include_disabled=True)

    entry = next(o for o in result["orphans"] if o["entity_id"] == "sensor.disabled_by_user")
    assert entry["disabled_by"] == "user"


def test_list_orphan_entities_reports_an_error_instead_of_raising_on_401(fake_ha):
    """The states read used to be a bare r.raise_for_status(), which raised
    httpx.HTTPStatusError straight through this function for an expired or
    revoked token instead of the error() envelope every other failure in
    this module already returns. A 401 is a normal (if unwelcome) HTTP
    response - rest_error() reports it as "home_assistant_error", the same
    code every other REST rejection in this codebase gets; "connection_failed"
    (below) is reserved for the request never getting a response at all."""
    from tools.validation import list_orphan_entities

    fake_ha.fail_rest("/api/states", status=401, message="Unauthorized")

    result = list_orphan_entities()

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 401


def test_list_orphan_entities_reports_an_error_instead_of_raising_when_unreachable(fake_ha):
    from tools.validation import list_orphan_entities

    fake_ha.raise_rest("/api/states")  # defaults to httpx.ConnectError

    result = list_orphan_entities()

    assert result["error"] == "connection_failed"


def test_live_snapshot_reports_an_error_instead_of_raising_on_401(fake_ha):
    """Same fix, isolated: _live_snapshot() is shared by validate_automation()
    and validate_all_automations(), both of which reach Home Assistant at
    least once before this call (get_automation(), list_automations()) over
    the identical /api/states endpoint - so exercising this through either
    public tool would test THEIR already-separate error handling, not this
    function's own. Testing _live_snapshot() directly isolates the fix this
    review actually asked for (tools/validation.py:140)."""
    from tools.validation import _live_snapshot

    fake_ha.fail_rest("/api/states", status=401, message="Unauthorized")

    states, entity_registry, device_registry, err = _live_snapshot()

    assert err["error"] == "home_assistant_error"
    assert err["status"] == 401
    assert states == {} and entity_registry == {} and device_registry == {}


def test_live_snapshot_reports_an_error_instead_of_raising_when_unreachable(fake_ha):
    from tools.validation import _live_snapshot

    fake_ha.raise_rest("/api/states")  # defaults to httpx.ConnectError

    states, entity_registry, device_registry, err = _live_snapshot()

    assert err["error"] == "connection_failed"
    assert states == {} and entity_registry == {} and device_registry == {}


# ---------------------------------------------------------------------------
# min_severity: the listing is filtered, the counts never are
# ---------------------------------------------------------------------------

def _reg(fake_ha, entity_id, *, platform="hue", disabled_by=None):
    fake_ha.registry.append({
        "entity_id": entity_id, "area_id": None, "device_id": None,
        "labels": [], "platform": platform, "disabled_by": disabled_by,
    })


def _seed_mixed_severities(fake_ha):
    """An instance holding one automation per severity tier.

    error   -> a dead reference (an entity in no registry at all)
    warning -> a reference whose entity is registered but "unavailable"
    info    -> a reference whose entity is registered but "unknown"
    """
    fake_ha.states.append({"entity_id": "light.offline", "state": "unavailable",
                           "attributes": {}})
    fake_ha.states.append({"entity_id": "button.never_pressed", "state": "unknown",
                           "attributes": {}})
    _reg(fake_ha, "light.offline")
    _reg(fake_ha, "button.never_pressed")
    for name, target in (
        ("dead", "light.does_not_exist_anywhere"),
        ("offline", "light.offline"),
        ("resting", "button.never_pressed"),
    ):
        fake_ha.states.append({
            "entity_id": f"automation.{name}", "state": "on",
            "attributes": {"id": name, "friendly_name": name},
        })
        fake_ha.automation_configs[name] = {
            "id": name, "alias": name,
            "triggers": [{"trigger": "state", "entity_id": "light.kitchen"}],
            "actions": [{"action": "light.turn_on",
                         "target": {"entity_id": target}}],
        }


def test_min_severity_defaults_to_error_and_hides_the_milder_tiers(fake_ha):
    """Run bare, the tool reports the class it exists for and nothing else.

    91 of 106 issues on a real instance were "this device is offline right
    now" - a real signal, but addressed to whoever maintains the
    integration, not to whoever wrote the automation. Reporting them by
    default buried zero dead references under 99 KB of them.
    """
    from tools.validation import validate_all_automations
    _seed_mixed_severities(fake_ha)

    result = validate_all_automations()

    reported = {r["entity_id"] for r in result["results"] if "summary" in r}
    assert "automation.dead" in reported
    assert "automation.offline" not in reported
    assert "automation.resting" not in reported


def test_the_counts_cover_everything_checked_even_when_the_listing_does_not(fake_ha):
    """The filter must reach the listing and stop there.

    A guard that reports "nothing found" because it stopped counting is
    the exact fault this project exists to remove. `summary` is what a
    caller reads to decide whether to look further, so it counts the whole
    checked population regardless of what `results` shows.
    """
    from tools.validation import validate_all_automations
    _seed_mixed_severities(fake_ha)

    filtered = validate_all_automations()
    unfiltered = validate_all_automations(min_severity="info")

    assert filtered["summary"] == unfiltered["summary"], (
        "the same instance, the same sweep: only the listing may differ"
    )
    assert filtered["total"] < unfiltered["total"], "the listing did shrink"
    assert filtered["summary"]["unavailable"] >= 1, "a filtered-out warning still counts"
    assert filtered["summary"]["unknown"] >= 1, "a filtered-out info still counts"


def test_a_filtered_listing_says_so(fake_ha):
    """Silence about what was left out reads as 'there was nothing else'."""
    from tools.validation import validate_all_automations
    _seed_mixed_severities(fake_ha)

    result = validate_all_automations()

    assert "min_severity" in result.get("note", "")


def test_min_severity_info_reports_every_tier(fake_ha):
    from tools.validation import validate_all_automations
    _seed_mixed_severities(fake_ha)

    result = validate_all_automations(min_severity="info")

    reported = {r["entity_id"] for r in result["results"] if "summary" in r}
    assert reported >= {"automation.dead", "automation.offline", "automation.resting"}


def test_an_unreadable_automation_is_never_filtered_out(fake_ha):
    """A read error is not a mild finding: it means this automation was not
    checked at all, so it cannot be graded by a severity it never got."""
    from tools.validation import validate_all_automations
    fake_ha.states.append({"entity_id": "automation.remote_only", "state": "on",
                           "attributes": {"id": "9999",
                                          "friendly_name": "remote only"}})

    result = validate_all_automations()

    unreadable = {r["entity_id"] for r in result["results"] if "read_error" in r}
    assert "automation.remote_only" in unreadable
    assert result["summary"]["read_errors"] == len(unreadable)


def test_an_unknown_min_severity_is_refused(fake_ha):
    from tools.validation import validate_all_automations
    result = validate_all_automations(min_severity="critical")
    assert result["error"] == "bad_min_severity"
    assert "error" in result["detail"] and "warning" in result["detail"]
    assert "results" not in result


# ---------------------------------------------------------------------------
# list_orphan_entities(): the unexpected kind by default
# ---------------------------------------------------------------------------

def test_orphans_exclude_deliberately_disabled_entities_by_default(fake_ha):
    """3249 of 3249 orphans on a real instance were disabled on purpose.

    A disabled entity legitimately has no state. Returning it alongside the
    silently-abandoned kind buried the count that mattered - zero - under
    678 KB of rows that were all working as configured.
    """
    from tools.validation import list_orphan_entities
    _reg(fake_ha, "light.abandoned")
    _reg(fake_ha, "light.turned_off", disabled_by="user")

    result = list_orphan_entities()

    ids = {o["entity_id"] for o in result["orphans"]}
    assert "light.abandoned" in ids
    assert "light.turned_off" not in ids


def test_the_excluded_disabled_entities_are_counted_not_hidden(fake_ha):
    from tools.validation import list_orphan_entities
    _reg(fake_ha, "light.abandoned")
    _reg(fake_ha, "light.turned_off", disabled_by="user")

    result = list_orphan_entities()

    assert result["excluded_disabled"] == 1


def test_include_disabled_returns_both_kinds(fake_ha):
    from tools.validation import list_orphan_entities
    _reg(fake_ha, "light.abandoned")
    _reg(fake_ha, "light.turned_off", disabled_by="user")

    result = list_orphan_entities(include_disabled=True)

    ids = {o["entity_id"] for o in result["orphans"]}
    assert ids >= {"light.abandoned", "light.turned_off"}
    assert result["excluded_disabled"] == 0


def test_orphans_paginate(fake_ha):
    """The population is thousands of rows on a real instance; every other
    collection tool in this codebase bounds itself and this one did not."""
    from tools.validation import list_orphan_entities
    fake_ha.registry[:] = [e for e in fake_ha.registry
                           if e["entity_id"] in
                           {s["entity_id"] for s in fake_ha.states}]
    for i in range(5):
        _reg(fake_ha, f"light.gone_{i}")

    page = list_orphan_entities(limit=2, offset=1)

    assert page["total"] == 5
    assert page["returned"] == 2
    assert page["offset"] == 1
    assert [o["entity_id"] for o in page["orphans"]] == ["light.gone_1", "light.gone_2"]
