"""tools/_refs.py: what a Home Assistant automation config references, and
one particular failure shape — a wait_for_trigger whose timeout does not
fail closed ahead of a destructive action.

No fake_ha fixture anywhere in this file: extract_refs() and
find_fail_open_waits() take a plain dict and return plain data — no
network, no live Home Assistant, nothing evaluated. That purity is the
point: it is what lets a future CLI (scripts/lint_automations.py) point
this exact code at a YAML file on a machine with no Home Assistant
running on it at all.
"""
from tools._refs import extract_refs, find_fail_open_waits


# ---------------------------------------------------------------------------
# extract_refs() — field references
# ---------------------------------------------------------------------------

def test_a_plain_entity_id_field_is_an_entity_ref():
    config = {"action": [{"service": "light.turn_on",
                          "target": {"entity_id": "light.kitchen"}}]}

    assert extract_refs(config) == [
        {"id": "light.kitchen", "kind": "entity",
         "where": "action.0.target.entity_id", "source": "field"},
    ]


def test_a_plain_device_id_field_is_a_device_ref():
    config = {"action": [{"device_id": "abc123deviceid", "domain": "light",
                          "type": "turn_on"}]}

    assert extract_refs(config) == [
        {"id": "abc123deviceid", "kind": "device",
         "where": "action.0.device_id", "source": "field"},
    ]


def test_a_list_of_entity_ids_yields_one_ref_per_element_with_an_indexed_path():
    config = {"action": [{"service": "light.turn_on",
                          "target": {"entity_id": ["light.a", "light.b"]}}]}

    assert extract_refs(config) == [
        {"id": "light.a", "kind": "entity",
         "where": "action.0.target.entity_id.0", "source": "field"},
        {"id": "light.b", "kind": "entity",
         "where": "action.0.target.entity_id.1", "source": "field"},
    ]


def test_a_list_of_device_ids_yields_one_ref_per_element_with_an_indexed_path():
    config = {"action": [{"service": "light.turn_on",
                          "target": {"device_id": ["dev1", "dev2"]}}]}

    assert extract_refs(config) == [
        {"id": "dev1", "kind": "device",
         "where": "action.0.target.device_id.0", "source": "field"},
        {"id": "dev2", "kind": "device",
         "where": "action.0.target.device_id.1", "source": "field"},
    ]


def test_entity_id_all_is_not_a_reference():
    """'entity_id: all' targets every entity of a domain — it is valid
    Home Assistant, and it references no one specific entity. Reporting
    it as a dead/live reference would make the validator cry wolf on a
    perfectly correct automation."""
    config = {"action": [{"service": "light.turn_off",
                          "target": {"entity_id": "all"}}]}

    assert extract_refs(config) == []


def test_entity_id_none_is_not_a_reference():
    config = {"action": [{"service": "light.turn_off",
                          "target": {"entity_id": "none"}}]}

    assert extract_refs(config) == []


def test_a_templated_entity_id_field_is_not_a_field_reference():
    """entity_id can itself hold a template — Home Assistant renders it
    before dispatch. What it renders to is not knowable statically, so
    the literal template text must not be reported as a field reference."""
    config = {"action": [{"service": "light.turn_on",
                          "target": {"entity_id": "{{ trigger.entity_id }}"}}]}

    assert extract_refs(config) == []


def test_walks_the_whole_object_regardless_of_nesting():
    """No enumeration of 'shapes that can hold entity_id' — any dict
    anywhere in the tree, however deeply nested, is walked."""
    config = {
        "action": [
            {"choose": [
                {"conditions": [], "sequence": [
                    {"service": "light.turn_on",
                     "target": {"entity_id": "light.deep"}},
                ]},
            ]},
        ],
    }

    assert extract_refs(config) == [
        {"id": "light.deep", "kind": "entity",
         "where": "action.0.choose.0.sequence.0.target.entity_id",
         "source": "field"},
    ]


def test_root_level_entity_id_outside_any_step_is_still_found():
    """The walk does not assume entity_id only ever appears inside a
    trigger/condition/action step."""
    config = {"entity_id": "light.top_level"}

    assert extract_refs(config) == [
        {"id": "light.top_level", "kind": "entity",
         "where": "entity_id", "source": "field"},
    ]


# ---------------------------------------------------------------------------
# extract_refs() — vocabulary independence
# ---------------------------------------------------------------------------

def test_finds_the_same_refs_whichever_root_vocabulary_is_used():
    legacy = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.door"}]}
    modern = {"triggers": [{"trigger": "state", "entity_id": "binary_sensor.door"}]}

    assert [r["id"] for r in extract_refs(legacy)] == ["binary_sensor.door"]
    assert [r["id"] for r in extract_refs(modern)] == ["binary_sensor.door"]


def test_finds_refs_whichever_step_vocabulary_is_used():
    legacy = {"action": [{"service": "light.turn_on",
                          "target": {"entity_id": "light.a"}}]}
    modern = {"actions": [{"action": "light.turn_on",
                           "target": {"entity_id": "light.a"}}]}

    assert [r["id"] for r in extract_refs(legacy)] == ["light.a"]
    assert [r["id"] for r in extract_refs(modern)] == ["light.a"]


# ---------------------------------------------------------------------------
# extract_refs() — template references
# ---------------------------------------------------------------------------

def test_is_state_in_a_template_is_a_template_reference():
    config = {"condition": [{"condition": "template",
                             "value_template": "{{ is_state('button.x', 'on') }}"}]}

    assert extract_refs(config) == [
        {"id": "button.x", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_is_state_attr_in_a_template_is_a_template_reference():
    config = {"condition": [{"condition": "template",
                             "value_template":
                                 '{{ is_state_attr("climate.hall", "hvac_action", "heating") }}'}]}

    assert extract_refs(config) == [
        {"id": "climate.hall", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_states_function_call_in_a_template_is_a_template_reference():
    config = {"condition": [{"value_template": "{{ states('sensor.temp') | float > 20 }}"}]}

    assert extract_refs(config) == [
        {"id": "sensor.temp", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_states_attribute_access_in_a_template_is_a_template_reference():
    """The states.<domain>.<object_id> Jinja attribute form — no function
    call, no quotes at all."""
    config = {"condition": [{"value_template": "{{ states.sensor.temp.state }}"}]}

    assert extract_refs(config) == [
        {"id": "sensor.temp", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_state_attr_in_a_template_is_a_template_reference():
    config = {"condition": [{"value_template":
                             "{{ state_attr('cover.garage', 'current_position') > 0 }}"}]}

    assert extract_refs(config) == [
        {"id": "cover.garage", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_expand_in_a_template_is_a_template_reference():
    config = {"condition": [{"value_template":
                             "{{ expand('group.all_lights') | selectattr('state','eq','on') "
                             "| list | count > 0 }}"}]}

    assert extract_refs(config) == [
        {"id": "group.all_lights", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_has_value_in_a_template_is_a_template_reference():
    config = {"condition": [{"value_template": "{{ has_value('sensor.outdoor_temp') }}"}]}

    assert extract_refs(config) == [
        {"id": "sensor.outdoor_temp", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]


def test_single_and_double_quotes_are_both_recognised():
    config = {"condition": [
        {"value_template": "{{ is_state('button.single', 'on') }}"},
        {"value_template": '{{ is_state("button.double", "on") }}'},
    ]}

    refs = extract_refs(config)

    assert {r["id"] for r in refs} == {"button.single", "button.double"}
    assert all(r["source"] == "template" for r in refs)


def test_a_string_with_no_template_marker_is_never_scanned_for_templates():
    """'is_state(' in plain text with no {{ or {% is not a template at
    all — nothing here should be treated as Jinja."""
    config = {"action": [{"service": "notify.mobile_app",
                          "data": {"message": "is_state('a.b','c') looks like code but isn't"}}]}

    assert extract_refs(config) == []


def test_multiple_template_references_in_one_string_are_all_reported():
    config = {"condition": [{"value_template":
                             "{{ is_state('a.b', 'on') and is_state('c.d', 'off') }}"}]}

    refs = extract_refs(config)

    assert [r["id"] for r in refs] == ["a.b", "c.d"]
    assert all(r["where"] == "condition.0.value_template" for r in refs)


def test_a_percent_style_template_delimiter_is_also_scanned():
    config = {"action": [{"service": "light.turn_on",
                          "data_template": {"brightness":
                              "{% if is_state('sun.sun', 'above_horizon') %}255"
                              "{% else %}50{% endif %}"}}]}

    assert extract_refs(config) == [
        {"id": "sun.sun", "kind": "entity",
         "where": "action.0.data_template.brightness", "source": "template"},
    ]


def test_no_maintained_field_allowlist_any_string_key_can_hold_a_template():
    """The extractor must not special-case 'value_template' or
    'data_template' by name — a template inside an arbitrary, made-up
    field name must be found too, since HA does not restrict where a
    template may appear either."""
    config = {"action": [{"service": "notify.custom",
                          "data": {"some_future_field":
                              "{{ is_state('binary_sensor.unexpected', 'on') }}"}}]}

    assert extract_refs(config) == [
        {"id": "binary_sensor.unexpected", "kind": "entity",
         "where": "action.0.data.some_future_field", "source": "template"},
    ]


# ---------------------------------------------------------------------------
# extract_refs() — the incident's own shape
# ---------------------------------------------------------------------------

# Legacy vocabulary throughout (trigger/condition/action, platform/service).
# The guard's template still names the button's OLD id (button.nas_shutdown);
# the actions — updated when the button was renamed — reference the NEW one
# (button.nas_shut_down). That mismatch between a stale template reference
# and a current field reference is the entire incident.
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


def test_the_incidents_own_shape_surfaces_the_stale_template_reference():
    refs = extract_refs(INCIDENT_CONFIG)

    by_id = {}
    for r in refs:
        by_id.setdefault(r["id"], []).append(r)

    # The stale name lives only in the template.
    assert by_id["button.nas_shutdown"] == [
        {"id": "button.nas_shutdown", "kind": "entity",
         "where": "condition.0.value_template", "source": "template"},
    ]
    # The current name is referenced twice, both as field refs.
    assert {r["where"] for r in by_id["button.nas_shut_down"]} == {
        "action.0.target.entity_id", "action.1.wait_for_trigger.0.entity_id",
    }
    assert all(r["source"] == "field" for r in by_id["button.nas_shut_down"])
    assert {r["id"] for r in by_id["switch.nas_power"]} == {"switch.nas_power"}
    assert {r["id"] for r in by_id["input_boolean.nas_shutdown_request"]} == {
        "input_boolean.nas_shutdown_request",
    }


# ---------------------------------------------------------------------------
# find_fail_open_waits()
# ---------------------------------------------------------------------------

def test_a_timeout_with_no_continue_on_timeout_followed_by_turn_off_is_reported():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state", "entity_id": "button.x"}],
         "timeout": "00:00:30"},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0"
    assert waits[0]["action_where"] == "action.1"
    assert waits[0]["service"] == "switch.turn_off"
    assert waits[0]["timeout"] == "00:00:30"


def test_continue_on_timeout_false_fails_closed_and_is_not_reported():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30",
         "continue_on_timeout": False},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ]}

    assert find_fail_open_waits(config) == []


def test_continue_on_timeout_true_is_still_fail_open():
    """The explicit True and the implicit default behave identically —
    both let execution carry on past the wait once the timeout elapses."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30",
         "continue_on_timeout": True},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_no_timeout_blocks_forever_and_is_not_reported():
    """A wait with no timeout at all fails closed by construction — it
    never lets execution continue on its own."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}]},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ]}

    assert find_fail_open_waits(config) == []


def test_a_non_destructive_action_after_the_wait_is_not_reported():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"service": "notify.mobile_app", "data": {"message": "done"}},
    ]}

    assert find_fail_open_waits(config) == []


def test_turn_off_on_any_domain_is_destructive():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"service": "light.turn_off", "target": {"entity_id": "light.a"}},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_any_switch_service_is_destructive_not_only_turn_off():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"service": "switch.turn_on", "target": {"entity_id": "switch.a"}},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_homeassistant_stop_is_destructive():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:05"},
        {"service": "homeassistant.stop"},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_homeassistant_restart_is_destructive():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:05"},
        {"service": "homeassistant.restart"},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_homeassistant_reload_core_config_is_not_destructive():
    """Only stop/restart under the homeassistant domain — not every
    homeassistant.* service."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:05"},
        {"service": "homeassistant.reload_core_config"},
    ]}

    assert find_fail_open_waits(config) == []


def test_hassio_host_actions_are_destructive():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:05"},
        {"service": "hassio.host_reboot"},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_hassio_non_host_actions_are_not_destructive():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:05"},
        {"service": "hassio.addon_restart"},
    ]}

    assert find_fail_open_waits(config) == []


def test_service_via_action_key_is_recognised_modern_vocabulary():
    """The one place both vocabularies matter: the service name lives
    under service: or action: depending on how the automation was saved."""
    config = {"actions": [
        {"wait_for_trigger": [{"trigger": "state"}], "timeout": "00:00:30"},
        {"action": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_service_via_service_key_is_recognised_legacy_vocabulary():
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
    ]}

    assert len(find_fail_open_waits(config)) == 1


def test_no_action_root_key_returns_no_waits():
    assert find_fail_open_waits({"alias": "does nothing"}) == []


def test_recurses_into_choose_branches():
    config = {"action": [
        {"choose": [
            {"conditions": [], "sequence": [
                {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
                {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
            ]},
        ]},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0.choose.0.sequence.0"
    assert waits[0]["action_where"] == "action.0.choose.0.sequence.1"


def test_recurses_into_choose_default():
    config = {"action": [
        {"choose": [], "default": [
            {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
            {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
        ]},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0.default.0"


def test_recurses_into_if_then_and_else():
    config = {"action": [
        {"if": [], "then": [
            {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
            {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
        ], "else": [
            {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
            {"service": "switch.turn_on", "target": {"entity_id": "switch.b"}},
        ]},
    ]}

    waits = find_fail_open_waits(config)

    assert {w["wait_where"] for w in waits} == {"action.0.then.0", "action.0.else.0"}


def test_recurses_into_repeat_sequence():
    config = {"action": [
        {"repeat": {"count": 3, "sequence": [
            {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
            {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
        ]}},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0.repeat.sequence.0"


def test_recurses_into_parallel_branches():
    config = {"action": [
        {"parallel": [
            [
                {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
                {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
            ],
        ]},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0.parallel.0.0"


def test_recurses_into_a_parallel_branch_wrapped_in_its_own_sequence():
    config = {"action": [
        {"parallel": [
            {"sequence": [
                {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
                {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
            ]},
        ]},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0.parallel.0.sequence.0"


def test_a_destructive_action_in_a_different_branch_is_not_reported():
    """'Follows it in the same sequence' means the same flat list — a
    fail-open wait in one choose branch is not blamed for a destructive
    action living in a sibling branch it can never reach."""
    config = {"action": [
        {"choose": [
            {"conditions": [], "sequence": [
                {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
            ]},
            {"conditions": [], "sequence": [
                {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
            ]},
        ]},
    ]}

    assert find_fail_open_waits(config) == []


def test_a_destructive_action_downstream_of_the_wait_inside_an_if_then_is_reported():
    """The incident shape as Home Assistant's own UI editor writes it: the
    wait sits at the top level, and the destructive action is not a
    sibling step but one level of nesting BELOW it, inside an if/then
    that comes after the wait. This is reachable from the wait's own
    timeout (whichever way the `if` resolves, the branch taken is still on
    the path the timeout opened up) and must be reported - unlike the
    sibling-branch case in
    test_a_destructive_action_in_a_different_branch_is_not_reported above,
    which is a genuinely different shape."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"if": [{"condition": "state", "entity_id": "input_boolean.x", "state": "on"}],
         "then": [
             {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
         ]},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0"
    assert waits[0]["action_where"] == "action.1.then.0"
    assert waits[0]["service"] == "switch.turn_off"


def test_a_destructive_action_downstream_of_the_wait_inside_choose_sequence_is_reported():
    """Same shape, via choose/sequence instead of if/then."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"choose": [
            {"conditions": [{"condition": "state", "entity_id": "input_boolean.x", "state": "on"}],
             "sequence": [
                 {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
             ]},
        ]},
    ]}

    waits = find_fail_open_waits(config)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.0"
    assert waits[0]["action_where"] == "action.1.choose.0.sequence.0"
    assert waits[0]["service"] == "switch.turn_off"


def test_a_later_wait_for_trigger_that_fails_closed_re_gates_what_follows():
    """A second, safely-blocking wait between the fail-open one and the
    destructive action means the destructive action is no longer exposed
    by the first wait — reaching it now requires the second wait's own
    trigger to fire, not just a timeout."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"wait_for_trigger": [{"platform": "state"}]},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
    ]}

    assert find_fail_open_waits(config) == []


def test_every_destructive_action_after_a_fail_open_wait_is_reported():
    """Not just the first — every destructive action reachable after the
    wait in the same sequence is exposed by it."""
    config = {"action": [
        {"wait_for_trigger": [{"platform": "state"}], "timeout": "00:00:30"},
        {"service": "switch.turn_off", "target": {"entity_id": "switch.a"}},
        {"service": "light.turn_off", "target": {"entity_id": "light.b"}},
    ]}

    waits = find_fail_open_waits(config)

    assert [w["action_where"] for w in waits] == ["action.1", "action.2"]


def test_the_incidents_own_shape_is_detected():
    waits = find_fail_open_waits(INCIDENT_CONFIG)

    assert len(waits) == 1
    assert waits[0]["wait_where"] == "action.1"
    assert waits[0]["action_where"] == "action.2"
    assert waits[0]["service"] == "switch.turn_off"
    assert waits[0]["timeout"] == "00:00:30"
