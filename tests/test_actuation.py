"""Package C: tools that actuate Home Assistant and must report what
actually happened, not just that the HTTP call returned 2xx.

34 tools used to return a hardcoded {"ok": True} regardless of whether the
target entity existed or the command took effect. Each test below drives
one of the converted tools through the three cases a caller must now be
able to tell apart: a real target that succeeds, a nonexistent target, and
a target that accepted the call but did not reach the requested state.
FakeHA never simulates side effects of a POST — every /api/services/* call
answers 200 [] regardless of the body, exactly like Home Assistant does for
both an idempotent no-op and a call aimed at nothing — so "success" here
always comes from what fake_ha.states says the entity's own state already
is, the same source observe_actuation() reads in production.
"""
import pytest


# ---- tools/locks.py ---------------------------------------------------------

def test_lock_control_reports_a_verified_success(fake_ha):
    from tools.locks import lock_control

    fake_ha.states = [
        {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
    ]

    result = lock_control("lock.front_door", "lock")

    assert result == {"entity_id": "lock.front_door", "command": "lock",
                      "verified": True, "state": "locked"}


def test_lock_control_reports_a_nonexistent_target(fake_ha):
    from tools.locks import lock_control

    result = lock_control("lock.ghost_does_not_exist", "unlock")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "lock.ghost_does_not_exist"
    assert "verified" not in result


def test_lock_control_reports_accepted_but_unverified_for_a_jammed_lock(fake_ha):
    """Home Assistant answers the lock service call with 200 regardless of
    whether the lock physically jams - only the read-back tells them apart."""
    from tools.locks import lock_control

    fake_ha.states = [
        {"entity_id": "lock.poorly_installed_door", "state": "jammed", "attributes": {}},
    ]

    result = lock_control("lock.poorly_installed_door", "lock")

    assert result["verified"] is False
    assert result["state"] == "jammed"


def test_lock_control_reports_null_for_a_lock_still_locking(fake_ha):
    """A lock still in the transient "locking" state has neither confirmed
    nor denied the command - not the same as "jammed", which is a real,
    settled mismatch."""
    from tools.locks import lock_control

    fake_ha.states = [
        {"entity_id": "lock.slow_deadbolt", "state": "locking", "attributes": {}},
    ]

    result = lock_control("lock.slow_deadbolt", "lock")

    assert result["verified"] is None
    assert result["state"] == "locking"


def test_lock_control_rejects_an_unknown_command(fake_ha):
    from tools.locks import lock_control

    with pytest.raises(ValueError):
        lock_control("lock.front_door", "smash")


# ---- tools/alarm.py ----------------------------------------------------------

def test_alarm_control_reports_a_verified_success(fake_ha):
    from tools.alarm import alarm_control

    fake_ha.states = [
        {"entity_id": "alarm_control_panel.security", "state": "armed_home", "attributes": {}},
    ]

    result = alarm_control("alarm_control_panel.security", "arm_home", code="1234")

    assert result["verified"] is True
    assert result["state"] == "armed_home"


def test_alarm_control_reports_a_nonexistent_target(fake_ha):
    from tools.alarm import alarm_control

    result = alarm_control("alarm_control_panel.ghost", "disarm")

    assert result["error"] == "entity_not_found"
    assert "verified" not in result


def test_alarm_control_reports_accepted_but_unverified(fake_ha):
    """The panel never reaches armed_home - e.g. an exit-delay abort, a
    sensor blocking the arm - which the old code could not have reported
    even if it noticed, since it never read the state back at all."""
    from tools.alarm import alarm_control

    fake_ha.states = [
        {"entity_id": "alarm_control_panel.security", "state": "armed_away", "attributes": {}},
    ]

    result = alarm_control("alarm_control_panel.security", "arm_home", code="1234")

    assert result["verified"] is False
    assert result["state"] == "armed_away"


def test_alarm_control_reports_null_while_still_arming(fake_ha):
    """Measured live: a panel with a short exit delay took about five
    seconds to reach armed_home, well past the read-back budget - "arming"
    at that point means genuinely in progress, not a denial."""
    from tools.alarm import alarm_control

    fake_ha.states = [
        {"entity_id": "alarm_control_panel.security", "state": "arming", "attributes": {}},
    ]

    result = alarm_control("alarm_control_panel.security", "arm_home", code="1234")

    assert result["verified"] is None
    assert result["state"] == "arming"


def test_alarm_control_disarm_has_no_null_case(fake_ha):
    """Measured live, disarm was instantaneous every time - it keeps a
    plain True/False verified with no transitional state to soften."""
    from tools.alarm import alarm_control

    fake_ha.states = [
        {"entity_id": "alarm_control_panel.security", "state": "pending", "attributes": {}},
    ]

    result = alarm_control("alarm_control_panel.security", "disarm", code="1234")

    assert result["verified"] is False
    assert result["state"] == "pending"


def test_alarm_control_rejects_an_unknown_command(fake_ha):
    from tools.alarm import alarm_control

    result = alarm_control("alarm_control_panel.security", "levitate")

    assert result["error"] == "invalid_command"


# ---- tools/covers.py ---------------------------------------------------------

def test_cover_control_open_reports_a_verified_success(fake_ha):
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.garage_door", "state": "open", "attributes": {}},
    ]

    result = cover_control("cover.garage_door", "open")

    assert result["verified"] is True
    assert result["state"] == "open"


def test_cover_control_reports_a_nonexistent_target(fake_ha):
    from tools.covers import cover_control

    result = cover_control("cover.ghost", "close")

    assert result["error"] == "entity_not_found"


def test_cover_control_set_position_verifies_against_the_attribute(fake_ha):
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.hall_window", "state": "open",
         "attributes": {"current_position": 50}},
    ]

    result = cover_control("cover.hall_window", "set_position", position=50)

    assert result["verified"] is True
    assert result["position"] == 50


def test_cover_control_set_position_unverified_when_position_does_not_match(fake_ha):
    """A cover that ignores the requested position (unsupported value,
    obstruction) still answers the service call with 200 []."""
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.hall_window", "state": "open",
         "attributes": {"current_position": 10}},
    ]

    result = cover_control("cover.hall_window", "set_position", position=50)

    assert result["verified"] is False
    assert result["position"] == 10


def test_cover_control_close_reports_null_while_still_closing(fake_ha):
    """Measured live, a window cover can take ten seconds for a full
    close - well past the read-back budget. "closing" at that point is
    genuinely in progress, not a denial that the cover ever moved."""
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.hall_window", "state": "closing", "attributes": {}},
    ]

    result = cover_control("cover.hall_window", "close")

    assert result["verified"] is None
    assert result["state"] == "closing"


def test_cover_control_open_reports_false_when_it_moves_the_wrong_way(fake_ha):
    """"opening" softens to null for an open command, but "closing" does
    not - that is not "still working on it", it is heading the wrong way."""
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.hall_window", "state": "closing", "attributes": {}},
    ]

    result = cover_control("cover.hall_window", "open")

    assert result["verified"] is False
    assert result["state"] == "closing"


def test_cover_control_set_position_reports_null_while_still_in_transit(fake_ha):
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.hall_window", "state": "opening",
         "attributes": {"current_position": 30}},
    ]

    result = cover_control("cover.hall_window", "set_position", position=80)

    assert result["verified"] is None
    assert result["state"] == "opening"
    assert result["position"] == 30


def test_cover_control_toggle_verifies_against_the_prior_state(fake_ha):
    """toggle has no single target state - it must read the state before
    the call to know what "changed" means."""
    from tools.covers import cover_control

    fake_ha.sequence_states("cover.hall_window", [
        {"entity_id": "cover.hall_window", "state": "closed", "attributes": {}},
        {"entity_id": "cover.hall_window", "state": "open", "attributes": {}},
    ])

    result = cover_control("cover.hall_window", "toggle")

    assert result["verified"] is True
    assert result["state"] == "open"


def test_cover_control_stop_is_verified_once_no_longer_mid_travel(fake_ha):
    from tools.covers import cover_control

    fake_ha.states = [
        {"entity_id": "cover.hall_window", "state": "open", "attributes": {}},
    ]

    result = cover_control("cover.hall_window", "stop")

    assert result["verified"] is True


# ---- tools/vacuum.py ---------------------------------------------------------

def test_vacuum_control_start_reports_a_verified_success(fake_ha):
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "cleaning", "attributes": {}},
    ]

    result = vacuum_control("start", entity_id="vacuum.roomba")

    assert result["verified"] is True
    assert result["state"] == "cleaning"


def test_vacuum_control_reports_a_nonexistent_target(fake_ha):
    from tools.vacuum import vacuum_control

    result = vacuum_control("start", entity_id="vacuum.ghost")

    assert result["error"] == "entity_not_found"


def test_vacuum_control_requires_an_entity_id(fake_ha):
    """Measured live: vacuum_control used to actuate whichever vacuum.*
    entity happened to be first in /api/states when entity_id was omitted -
    silent guesswork over which robot moves on a multi-vacuum instance."""
    from tools.vacuum import vacuum_control

    with pytest.raises(ValueError):
        vacuum_control("start", entity_id="")


def test_vacuum_control_fan_speed_unverified_when_the_value_is_silently_ignored(fake_ha):
    """Measured live: vacuum.set_fan_speed with a value outside the
    entity's own fan_speed_list returns 200 [] and leaves the attribute
    untouched - no error, no effect."""
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "cleaning",
         "attributes": {"fan_speed": "medium"}},
    ]

    result = vacuum_control("fan_speed", entity_id="vacuum.roomba", fan_speed="turbo")

    assert result["verified"] is False
    assert result["fan_speed"] == "medium"


def test_vacuum_control_fan_speed_verified_when_the_value_is_accepted(fake_ha):
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "idle",
         "attributes": {"fan_speed": "max"}},
    ]

    result = vacuum_control("fan_speed", entity_id="vacuum.roomba", fan_speed="max")

    assert result["verified"] is True
    assert result["fan_speed"] == "max"


def test_vacuum_control_return_verified_only_once_docked(fake_ha):
    """'return' used to count "returning" itself as verified: true - the
    state the vacuum is in for the entire drive back, not once it
    arrives. Only "docked" verifies now."""
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "docked", "attributes": {}},
    ]

    result = vacuum_control("return", entity_id="vacuum.roomba")

    assert result["verified"] is True
    assert result["state"] == "docked"


def test_vacuum_control_return_reports_null_while_still_returning(fake_ha):
    """Measured live, a vacuum can sit in "returning" well past the
    read-back budget - genuinely in progress, not a denial."""
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "returning", "attributes": {}},
    ]

    result = vacuum_control("return", entity_id="vacuum.roomba")

    assert result["verified"] is None
    assert result["state"] == "returning"


def test_vacuum_control_return_reports_false_for_a_real_mismatch(fake_ha):
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "error", "attributes": {}},
    ]

    result = vacuum_control("return", entity_id="vacuum.roomba")

    assert result["verified"] is False
    assert result["state"] == "error"


def test_vacuum_control_locate_has_no_state_to_verify(fake_ha):
    """locate only plays a sound - honestly reported as accepted but
    unverifiable, not folded into either verified: true or false."""
    from tools.vacuum import vacuum_control

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "docked", "attributes": {}},
    ]

    result = vacuum_control("locate", entity_id="vacuum.roomba")

    assert result["verified"] is None
    assert "detail" in result


def test_vacuum_control_locate_reports_a_nonexistent_target(fake_ha):
    from tools.vacuum import vacuum_control

    result = vacuum_control("locate", entity_id="vacuum.ghost")

    assert result["error"] == "entity_not_found"


def test_vacuum_control_fan_speed_without_a_value_is_refused(fake_ha):
    from tools.vacuum import vacuum_control

    result = vacuum_control("fan_speed", entity_id="vacuum.roomba")

    assert result["error"] == "invalid_command"


def test_vacuum_room_reports_a_verified_success(fake_ha):
    from tools.vacuum import vacuum_room

    fake_ha.states = [
        {"entity_id": "vacuum.roomba", "state": "cleaning", "attributes": {}},
    ]

    result = vacuum_room(rooms=[1, 3], entity_id="vacuum.roomba")

    assert result["verified"] is True
    assert result["rooms"] == [1, 3]


def test_vacuum_room_reports_a_nonexistent_target(fake_ha):
    from tools.vacuum import vacuum_room

    result = vacuum_room(rooms=[1], entity_id="vacuum.ghost")

    assert result["error"] == "entity_not_found"


def test_vacuum_room_requires_an_entity_id(fake_ha):
    from tools.vacuum import vacuum_room

    with pytest.raises(ValueError):
        vacuum_room(rooms=[1], entity_id="")


# ---- tools/system.py: apply_update -------------------------------------------

def test_apply_update_reports_a_verified_success(fake_ha):
    from tools.system import apply_update

    fake_ha.states = [
        {"entity_id": "update.core", "state": "off",
         "attributes": {"installed_version": "2026.8.1", "latest_version": "2026.8.1"}},
    ]

    result = apply_update("update.core")

    assert result["verified"] is True
    assert result["installed_version"] == "2026.8.1"
    assert result["latest_version"] == "2026.8.1"


def test_apply_update_reports_a_nonexistent_target(fake_ha):
    from tools.system import apply_update

    result = apply_update("update.ghost")

    assert result["error"] == "entity_not_found"


def test_apply_update_reports_accepted_but_unverified(fake_ha):
    """Still "on" (installed_version != latest_version) after the call -
    e.g. a long-running firmware flash this tool's one short read-back
    cannot wait out."""
    from tools.system import apply_update

    fake_ha.states = [
        {"entity_id": "update.core", "state": "on",
         "attributes": {"installed_version": "2026.8.0", "latest_version": "2026.8.1"}},
    ]

    result = apply_update("update.core")

    assert result["verified"] is False
    assert result["installed_version"] == "2026.8.0"


# ---- tools/lights.py: set_light -----------------------------------------------

def test_set_light_reports_a_verified_success(fake_ha):
    """204/2.55 rounds to 80 - the light's own attributes, read back after
    the call, actually reflect the requested brightness_pct."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {"brightness": 204}},
    ]

    result = set_light("light.kitchen", brightness_pct=80)

    assert result["verified"] is True
    assert result["observed_state"] == "on"
    assert result["brightness_pct"] == 80


def test_set_light_does_not_verify_an_attribute_that_never_changed(fake_ha):
    """set_light() used to check only state == "on" for any turn_on/attribute
    call, so a light that ignored every requested attribute (an unsupported
    effect, an rgbw-only light asked for a color temperature) still reported
    verified: true - see tools/lights.py's module-level fix. FakeHA never
    simulates side effects of a POST (see this file's module docstring), so
    "attributes": {} here stands in for exactly that: the call was accepted,
    but nothing about brightness actually moved."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    ]

    result = set_light("light.kitchen", brightness_pct=80)

    assert result["verified"] is False
    assert result["observed_state"] == "on"
    assert result["brightness_pct"] is None  # honest: nothing was read back at that key


def test_set_light_verifies_color_temp_k(fake_ha):
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on",
         "attributes": {"color_temp_kelvin": 2700}},
    ]

    result = set_light("light.kitchen", color_temp_k=2700)

    assert result["verified"] is True
    assert result["color_temp_k"] == 2700


def test_set_light_does_not_verify_color_temp_k_on_a_light_that_ignored_it(fake_ha):
    """Measured live: an rgbw-only light (no 'color_temp' color mode) 200s a
    color_temp_k request and leaves color_temp_kelvin unset - the exact
    shape this reproduces."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on",
         "attributes": {"rgbw_color": [0, 15, 30, 15]}},
    ]

    result = set_light("light.kitchen", color_temp_k=2700)

    assert result["verified"] is False
    assert result["color_temp_k"] is None


def test_set_light_verifies_rgb_color_by_hue_and_saturation(fake_ha):
    """Home Assistant does not preserve an rgb_color's value/brightness for
    a light whose native color mode is 'hs': it stores hue/saturation and
    derives rgb_color back at full value. Measured live: requesting
    rgb_color=[10, 20, 30] on a color_temp/hs light reads back rgb_color
    [85, 170, 255] - same hue (210) and saturation (66.667), different
    value. hs_color is what set_light() actually compares against."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on",
         "attributes": {"hs_color": [210.0, 66.667], "rgb_color": [85, 170, 255]}},
    ]

    result = set_light("light.kitchen", rgb_color=[10, 20, 30])

    assert result["verified"] is True
    assert result["rgb_color"] == [85, 170, 255]


def test_set_light_does_not_verify_rgb_color_with_the_wrong_hue(fake_ha):
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on",
         "attributes": {"hs_color": [0.0, 100.0], "rgb_color": [255, 0, 0]}},
    ]

    result = set_light("light.kitchen", rgb_color=[10, 20, 30])  # blue-ish hue

    assert result["verified"] is False
    assert result["rgb_color"] == [255, 0, 0]


def test_set_light_verifies_effect(fake_ha):
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {"effect": "rainbow"}},
    ]

    result = set_light("light.kitchen", effect="rainbow")

    assert result["verified"] is True
    assert result["effect"] == "rainbow"


def test_set_light_does_not_verify_an_unsupported_effect(fake_ha):
    """Measured live: a light with no effect_list at all (no EFFECT feature)
    200s an effect request and leaves "effect" unset."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    ]

    result = set_light("light.kitchen", effect="Disco")

    assert result["verified"] is False
    assert result["effect"] is None


def test_set_light_off_reports_a_verified_success(fake_ha):
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
    ]

    result = set_light("light.kitchen", state="off")

    assert result["verified"] is True


def test_set_light_reports_a_nonexistent_target(fake_ha):
    from tools.lights import set_light

    result = set_light("light.ghost")

    assert result["error"] == "entity_not_found"


def test_set_light_reports_accepted_but_unverified(fake_ha):
    """A light that never turns on (unreachable, unavailable) still
    answers the service call with 200 []."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "unavailable", "attributes": {}},
    ]

    result = set_light("light.kitchen")

    assert result["verified"] is False
    assert result["observed_state"] == "unavailable"


def test_set_light_rejects_an_unrecognised_state(fake_ha):
    """set_light used to turn the light on for any state string that was
    not exactly 'off' or 'toggle', echoing back the caller's (bogus)
    requested value rather than what the light actually did."""
    from tools.lights import set_light

    fake_ha.states = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    ]

    result = set_light("light.kitchen", state="banana")

    assert result["error"] == "invalid_state"
    assert fake_ha.rest_calls == []  # never even called Home Assistant


def test_set_light_toggle_verifies_against_the_prior_state(fake_ha):
    from tools.lights import set_light

    fake_ha.sequence_states("light.kitchen", [
        {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    ])

    result = set_light("light.kitchen", state="toggle")

    assert result["verified"] is True
    assert result["observed_state"] == "on"


# ---- tools/climate.py: set_climate ---------------------------------------------

def test_set_climate_reports_a_verified_success(fake_ha):
    from tools.climate import set_climate

    fake_ha.states = [
        {"entity_id": "climate.hvac", "state": "cool",
         "attributes": {"temperature": 21}},
    ]

    result = set_climate("climate.hvac", hvac_mode="cool", temperature=21)

    assert result["verified"] is True
    assert result["applied"] == {"hvac_mode": "cool", "temperature": 21}
    assert result["attributes"]["temperature"] == 21


def test_set_climate_reports_a_nonexistent_target(fake_ha):
    from tools.climate import set_climate

    result = set_climate("climate.ghost", hvac_mode="cool")

    assert result["error"] == "entity_not_found"


def test_set_climate_reports_accepted_but_unverified_when_only_part_took_effect(fake_ha):
    """hvac_mode applied but the requested temperature never landed - a
    partial success the old code could not have reported even if it
    noticed, since it never read anything back."""
    from tools.climate import set_climate

    fake_ha.states = [
        {"entity_id": "climate.hvac", "state": "cool",
         "attributes": {"temperature": 18}},
    ]

    result = set_climate("climate.hvac", hvac_mode="cool", temperature=21)

    assert result["verified"] is False
    assert result["attributes"]["temperature"] == 18


def test_set_climate_refuses_an_empty_call(fake_ha):
    from tools.climate import set_climate

    result = set_climate("climate.hvac")

    assert result["error"] == "no_changes_requested"


# ---- tools/switches.py: toggle_entity ------------------------------------------

def test_toggle_entity_on_reports_a_verified_success(fake_ha):
    from tools.switches import toggle_entity

    fake_ha.states = [
        {"entity_id": "switch.decorative_lights", "state": "on", "attributes": {}},
    ]

    result = toggle_entity("switch.decorative_lights", state="on")

    assert result["verified"] is True


def test_toggle_entity_reports_a_nonexistent_target(fake_ha):
    from tools.switches import toggle_entity

    result = toggle_entity("switch.ghost", state="on")

    assert result["error"] == "entity_not_found"


def test_toggle_entity_toggle_verifies_against_the_prior_state(fake_ha):
    from tools.switches import toggle_entity

    fake_ha.sequence_states("switch.decorative_lights", [
        {"entity_id": "switch.decorative_lights", "state": "off", "attributes": {}},
        {"entity_id": "switch.decorative_lights", "state": "on", "attributes": {}},
    ])

    result = toggle_entity("switch.decorative_lights")

    assert result["verified"] is True
    assert result["observed_state"] == "on"


def test_toggle_entity_reports_accepted_but_unverified(fake_ha):
    from tools.switches import toggle_entity

    fake_ha.states = [
        {"entity_id": "switch.decorative_lights", "state": "off", "attributes": {}},
    ]

    result = toggle_entity("switch.decorative_lights", state="on")

    assert result["verified"] is False
    assert result["observed_state"] == "off"


# ---- tools/fans.py: fan_control -------------------------------------------------

def test_fan_control_turn_on_reports_a_verified_success(fake_ha):
    from tools.fans import fan_control

    fake_ha.states = [
        {"entity_id": "fan.living_room_fan", "state": "on", "attributes": {}},
    ]

    result = fan_control("fan.living_room_fan", "turn_on")

    assert result["verified"] is True


def test_fan_control_reports_a_nonexistent_target(fake_ha):
    from tools.fans import fan_control

    result = fan_control("fan.ghost", "turn_on")

    assert result["error"] == "entity_not_found"


def test_fan_control_set_percentage_verifies_against_the_attribute(fake_ha):
    from tools.fans import fan_control

    fake_ha.states = [
        {"entity_id": "fan.living_room_fan", "state": "on",
         "attributes": {"percentage": 66}},
    ]

    result = fan_control("fan.living_room_fan", "set_percentage", percentage=66)

    assert result["verified"] is True
    assert result["percentage"] == 66


def test_fan_control_set_percentage_reports_accepted_but_unverified(fake_ha):
    from tools.fans import fan_control

    fake_ha.states = [
        {"entity_id": "fan.living_room_fan", "state": "on",
         "attributes": {"percentage": 33}},
    ]

    result = fan_control("fan.living_room_fan", "set_percentage", percentage=66)

    assert result["verified"] is False
    assert result["percentage"] == 33


# ---- tools/helpers.py: set_helper ----------------------------------------------

def test_set_helper_input_boolean_verified_success(fake_ha):
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "input_boolean.guest_mode", "state": "on", "attributes": {}},
    ]

    result = set_helper("input_boolean.guest_mode", "on")

    assert result["verified"] is True


def test_set_helper_reports_a_nonexistent_target(fake_ha):
    from tools.helpers import set_helper

    result = set_helper("input_boolean.ghost", "on")

    assert result["error"] == "entity_not_found"


def test_set_helper_rejects_an_unsupported_domain(fake_ha):
    from tools.helpers import set_helper

    result = set_helper("sensor.not_a_helper", "42")

    assert result["error"] == "unsupported_domain"


def test_set_helper_input_number_numeric_match(fake_ha):
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "input_number.probe", "state": "42.0", "attributes": {}},
    ]

    result = set_helper("input_number.probe", "42")

    assert result["verified"] is True


def test_set_helper_input_select_reports_accepted_but_unverified(fake_ha):
    """An option not among the entity's own choices is rejected by Home
    Assistant with a non-2xx response in reality; here the fake still 200s
    the POST (it does not validate options), so this exercises the
    read-back mismatch path specifically."""
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "input_select.scene", "state": "Day", "attributes": {}},
    ]

    result = set_helper("input_select.scene", "Night")

    assert result["verified"] is False
    assert result["state"] == "Day"


def test_set_helper_counter_increment_uses_the_counters_own_step(fake_ha):
    """verified must check prior + step, not merely "the value changed" -
    a counter is at counter.step 1 by default here but this pins a
    non-default step so a naive "!= prior" predicate would also pass."""
    from tools.helpers import set_helper

    fake_ha.sequence_states("counter.probe", [
        {"entity_id": "counter.probe", "state": "10", "attributes": {"step": 5, "initial": 0}},
        {"entity_id": "counter.probe", "state": "15", "attributes": {"step": 5, "initial": 0}},
    ])

    result = set_helper("counter.probe", "increment")

    assert result["verified"] is True
    assert result["state"] == "15"


def test_set_helper_counter_increment_unverified_when_the_value_does_not_move(fake_ha):
    """A counter already at its configured maximum accepts an increment
    without moving - this must show verified: false, not true just because
    the call was accepted."""
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "counter.probe", "state": "10", "attributes": {"step": 5, "initial": 0}},
    ]

    result = set_helper("counter.probe", "increment")

    assert result["verified"] is False
    assert result["state"] == "10"


def test_set_helper_counter_reset_targets_the_initial_attribute(fake_ha):
    from tools.helpers import set_helper

    fake_ha.sequence_states("counter.probe", [
        {"entity_id": "counter.probe", "state": "10", "attributes": {"step": 1, "initial": 3}},
        {"entity_id": "counter.probe", "state": "3", "attributes": {"step": 1, "initial": 3}},
    ])

    result = set_helper("counter.probe", "reset")

    assert result["verified"] is True


def test_set_helper_timer_start_expects_active(fake_ha):
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "timer.probe", "state": "active", "attributes": {}},
    ]

    result = set_helper("timer.probe", "start")

    assert result["verified"] is True


def test_set_helper_timer_pause_on_an_idle_timer_is_unverified(fake_ha):
    """Home Assistant accepts timer.pause on a timer that is not running
    without effect - the state stays "idle" rather than becoming "paused"."""
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "timer.probe", "state": "idle", "attributes": {}},
    ]

    result = set_helper("timer.probe", "pause")

    assert result["verified"] is False
    assert result["state"] == "idle"


# ---- tools/helpers.py: set_number, set_select, set_text -----------------------

def test_set_number_verified_success(fake_ha):
    from tools.helpers import set_number

    fake_ha.states = [
        {"entity_id": "number.volume", "state": "50.0", "attributes": {}},
    ]

    result = set_number("number.volume", 50)

    assert result["verified"] is True


def test_set_number_reports_a_nonexistent_target(fake_ha):
    from tools.helpers import set_number

    result = set_number("number.ghost", 50)

    assert result["error"] == "entity_not_found"


def test_set_number_reports_accepted_but_clamped(fake_ha):
    """A value outside the entity's min/max is accepted and silently
    clamped rather than rejected."""
    from tools.helpers import set_number

    fake_ha.states = [
        {"entity_id": "number.volume", "state": "100.0", "attributes": {}},
    ]

    result = set_number("number.volume", 150)

    assert result["verified"] is False
    assert result["state"] == "100.0"


def test_set_select_verified_success(fake_ha):
    from tools.helpers import set_select

    fake_ha.states = [
        {"entity_id": "select.speed", "state": "high", "attributes": {}},
    ]

    result = set_select("select.speed", "high")

    assert result["verified"] is True


def test_set_select_reports_a_nonexistent_target(fake_ha):
    from tools.helpers import set_select

    result = set_select("select.ghost", "high")

    assert result["error"] == "entity_not_found"


def test_set_text_verified_success(fake_ha):
    from tools.helpers import set_text

    fake_ha.states = [
        {"entity_id": "input_text.message", "state": "hello", "attributes": {}},
    ]

    result = set_text("input_text.message", "hello")

    assert result["verified"] is True


def test_set_text_reports_a_nonexistent_target(fake_ha):
    from tools.helpers import set_text

    result = set_text("input_text.ghost", "hello")

    assert result["error"] == "entity_not_found"


def test_set_text_reports_accepted_but_truncated(fake_ha):
    from tools.helpers import set_text

    fake_ha.states = [
        {"entity_id": "input_text.message", "state": "hel", "attributes": {}},
    ]

    result = set_text("input_text.message", "hello")

    assert result["verified"] is False
    assert result["state"] == "hel"


# ---- tools/helpers.py: timer_control, counter_control --------------------------

def test_timer_control_start_reports_a_verified_success(fake_ha):
    from tools.helpers import timer_control

    fake_ha.states = [
        {"entity_id": "timer.probe", "state": "active", "attributes": {}},
    ]

    result = timer_control("timer.probe", "start")

    assert result["verified"] is True


def test_timer_control_reports_a_nonexistent_target(fake_ha):
    from tools.helpers import timer_control

    result = timer_control("timer.ghost", "start")

    assert result["error"] == "entity_not_found"


def test_timer_control_finish_on_a_never_started_timer_is_unverified(fake_ha):
    from tools.helpers import timer_control

    fake_ha.states = [
        {"entity_id": "timer.probe", "state": "idle", "attributes": {}},
    ]

    result = timer_control("timer.probe", "finish")

    # finish's expected state ("idle") happens to equal the timer's
    # already-idle state here, so this documents that observe_actuation()
    # cannot distinguish "already there" from "got there" - the tradeoff
    # of comparing terminal state rather than requiring a transition.
    assert result["verified"] is True


def test_counter_control_increment_uses_the_counters_own_step(fake_ha):
    from tools.helpers import counter_control

    fake_ha.sequence_states("counter.probe", [
        {"entity_id": "counter.probe", "state": "10", "attributes": {"step": 5, "initial": 0}},
        {"entity_id": "counter.probe", "state": "15", "attributes": {"step": 5, "initial": 0}},
    ])

    result = counter_control("counter.probe", "increment")

    assert result["verified"] is True
    assert result["state"] == "15"


def test_counter_control_reports_a_nonexistent_target(fake_ha):
    from tools.helpers import counter_control

    result = counter_control("counter.ghost", "increment")

    assert result["error"] == "entity_not_found"


def test_counter_control_decrement_unverified_when_the_value_does_not_move(fake_ha):
    """A counter already at its configured minimum accepts a decrement
    without moving."""
    from tools.helpers import counter_control

    fake_ha.states = [
        {"entity_id": "counter.probe", "state": "0", "attributes": {"step": 1, "initial": 0, "minimum": 0}},
    ]

    result = counter_control("counter.probe", "decrement")

    assert result["verified"] is False
    assert result["state"] == "0"


# ---- tools/buttons.py: press_button --------------------------------------------

def test_press_button_accepts_a_real_target(fake_ha):
    from tools.buttons import press_button

    fake_ha.states = [
        {"entity_id": "button.push", "state": "2026-08-23T12:00:00+00:00", "attributes": {}},
    ]

    result = press_button("button.push")

    assert result == {"entity_id": "button.push", "accepted": True, "verified": None,
                      "detail": result["detail"]}


def test_press_button_reports_a_nonexistent_target(fake_ha):
    from tools.buttons import press_button

    result = press_button("button.ghost")

    assert result["error"] == "entity_not_found"


# ---- tools/system.py: restart_homeassistant ------------------------------------

def test_restart_homeassistant_is_honest_about_being_unverified(fake_ha):
    from tools.system import restart_homeassistant

    result = restart_homeassistant()

    assert result["accepted"] is True
    assert result["verified"] is None


# ---- tools/notifications.py -----------------------------------------------------

def test_send_notification_accepts_a_real_target(fake_ha):
    from tools.notifications import send_notification

    fake_ha.states = [
        {"entity_id": "notify.mobile_app_phone", "state": "unknown", "attributes": {}},
    ]

    result = send_notification("hello", target="notify.mobile_app_phone")

    assert result["accepted"] is True
    assert result["verified"] is None
    assert result["target"] == "notify.mobile_app_phone"


def test_send_notification_reports_a_nonexistent_target(fake_ha):
    from tools.notifications import send_notification

    result = send_notification("hello", target="notify.ghost")

    assert result["error"] == "entity_not_found"


def test_send_notification_with_buttons_reports_a_nonexistent_target(fake_ha):
    from tools.notifications import send_notification_with_buttons

    result = send_notification_with_buttons(
        target="notify.ghost", message="hi", buttons=[[{"text": "Yes", "callback_data": "/yes"}]])

    assert result["error"] == "entity_not_found"


def test_send_photo_reports_a_nonexistent_target(fake_ha):
    from tools.notifications import send_photo

    result = send_photo(target="notify.ghost", photo_url="https://example.com/x.jpg")

    assert result["error"] == "entity_not_found"


def test_send_photo_accepts_a_real_target(fake_ha):
    from tools.notifications import send_photo

    fake_ha.states = [
        {"entity_id": "notify.telegram_home", "state": "unknown",
         "attributes": {"friendly_name": "Telegram Home (123456)"}},
    ]

    result = send_photo(target="notify.telegram_home", photo_url="https://example.com/x.jpg")

    assert result["accepted"] is True
    assert result["verified"] is None
    assert result["chat_id"] == 123456


def test_send_photo_reports_a_non_telegram_target_as_an_error_not_a_crash(fake_ha):
    """notify.notifier is a completely ordinary, non-Telegram notify
    target - picking the wrong notify service here is a plausible caller
    mistake, not an exceptional condition. The old code let
    _resolve_telegram_chat_id() raise a bare, uncaught ValueError for
    this; it must come back as an error() instead, naming what this tool
    supports."""
    from tools.notifications import send_photo

    fake_ha.states = [
        {"entity_id": "notify.notifier", "state": "unknown", "attributes": {}},
        {"entity_id": "notify.telegram_home", "state": "unknown",
         "attributes": {"friendly_name": "Telegram Home (123456)"}},
    ]

    result = send_photo(target="notify.notifier", photo_url="https://example.com/x.jpg")

    assert result["error"] == "not_a_telegram_target"
    assert result["entity_id"] == "notify.notifier"
    assert result["telegram_targets"] == ["notify.telegram_home"]


def test_send_camera_snapshot_reports_a_nonexistent_notify_target(fake_ha):
    from tools.notifications import send_camera_snapshot

    result = send_camera_snapshot("camera.gate", target="notify.ghost")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "notify.ghost"


def test_send_camera_snapshot_reports_a_nonexistent_camera(fake_ha):
    from tools.notifications import send_camera_snapshot

    fake_ha.states = [
        {"entity_id": "notify.telegram_home", "state": "unknown",
         "attributes": {"friendly_name": "Telegram Home (123456)"}},
    ]

    result = send_camera_snapshot("camera.ghost", target="notify.telegram_home")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "camera.ghost"


def test_send_camera_snapshot_reports_a_nonexistent_camera_even_with_a_bad_notify_target(fake_ha):
    """The camera is checked before the notify target's chat_id is
    resolved, so a bad camera_entity_id is reported as itself rather than
    masked by the *other* argument's chat_id error - the old ordering let
    a nonexistent camera get hidden behind whichever failure the notify
    target hit first."""
    from tools.notifications import send_camera_snapshot

    fake_ha.states = [
        {"entity_id": "notify.notifier", "state": "unknown", "attributes": {}},
    ]

    result = send_camera_snapshot("camera.ghost", target="notify.notifier")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "camera.ghost"


def test_send_camera_snapshot_reports_a_non_telegram_target_as_an_error_not_a_crash(fake_ha):
    from tools.notifications import send_camera_snapshot

    fake_ha.states = [
        {"entity_id": "notify.notifier", "state": "unknown", "attributes": {}},
        {"entity_id": "camera.gate", "state": "idle",
         "attributes": {"access_token": "tok123"}},
    ]

    result = send_camera_snapshot("camera.gate", target="notify.notifier")

    assert result["error"] == "not_a_telegram_target"
    assert result["entity_id"] == "notify.notifier"


def test_create_persistent_notification_verifies_a_given_id(fake_ha):
    from tools.notifications import create_persistent_notification

    fake_ha.ws_result("persistent_notification/get", [
        {"notification_id": "backup_done", "title": "Backup", "message": "ok",
         "created_at": "2026-08-23T00:00:00+00:00"},
    ])

    result = create_persistent_notification("ok", title="Backup", notification_id="backup_done")

    assert result["verified"] is True
    assert result["notification_id"] == "backup_done"


def test_create_persistent_notification_reports_an_id_that_never_landed(fake_ha):
    from tools.notifications import create_persistent_notification

    fake_ha.ws_result("persistent_notification/get", [])

    result = create_persistent_notification("ok", notification_id="backup_done")

    assert result["verified"] is False


def test_create_persistent_notification_without_an_id_is_honestly_unverifiable(fake_ha):
    """Home Assistant generates the id internally and never returns it from
    this call - there is nothing stable to look up afterward."""
    from tools.notifications import create_persistent_notification

    result = create_persistent_notification("ok")

    assert result["notification_id"] is None
    assert result["accepted"] is True
    assert result["verified"] is None


def test_dismiss_persistent_notification_verifies_removal(fake_ha, monkeypatch):
    """persistent_notification/get is read once before the dismiss call (to
    confirm the id is real) and once after (to confirm it is gone) - a
    static fake_ha.ws_result() cannot tell those two reads apart, so this
    swaps in a sequence that pops one answer per read."""
    from tools.notifications import dismiss_persistent_notification

    calls = [
        [{"notification_id": "backup_done", "title": "", "message": "", "created_at": ""}],
        [],
    ]

    def fake_ws(msg):
        if msg["type"] == "persistent_notification/get":
            return {"id": 1, "type": "result", "success": True, "result": calls.pop(0)}
        return fake_ha.ws(msg)

    import tools.notifications as notifications_module
    monkeypatch.setattr(notifications_module, "_ws", fake_ws)

    result = dismiss_persistent_notification("backup_done")

    assert result["verified"] is True


def test_dismiss_persistent_notification_reports_an_absent_id(fake_ha):
    from tools.notifications import dismiss_persistent_notification

    fake_ha.ws_result("persistent_notification/get", [])

    result = dismiss_persistent_notification("ghost")

    assert result["error"] == "entity_not_found"


# ---- tools/alerts.py --------------------------------------------------------------

def test_acknowledge_alert_accepts_a_real_target(fake_ha):
    from tools.alerts import acknowledge_alert

    fake_ha.states = [
        {"entity_id": "alert.gas_leak", "state": "on", "attributes": {}},
    ]

    result = acknowledge_alert("alert.gas_leak")

    assert result["accepted"] is True
    assert result["verified"] is None


def test_acknowledge_alert_calls_turn_off_not_the_nonexistent_acknowledge_service(fake_ha):
    """Home Assistant's alert domain has no alert.acknowledge service - only
    turn_on, turn_off and toggle exist (confirmed live against
    /api/services). The old code posted to alert/acknowledge, which 400s
    on every real Home Assistant instance; alert.turn_off is what actually
    acknowledges an alert."""
    from tools.alerts import acknowledge_alert

    fake_ha.states = [
        {"entity_id": "alert.gas_leak", "state": "on", "attributes": {}},
    ]

    acknowledge_alert("alert.gas_leak")

    assert any(c.url.path == "/api/services/alert/turn_off" for c in fake_ha.rest_calls)
    assert not any(c.url.path == "/api/services/alert/acknowledge" for c in fake_ha.rest_calls)


def test_acknowledge_alert_reports_a_nonexistent_target(fake_ha):
    from tools.alerts import acknowledge_alert

    result = acknowledge_alert("alert.ghost")

    assert result["error"] == "entity_not_found"


def test_toggle_alert_reports_a_nonexistent_target(fake_ha):
    from tools.alerts import toggle_alert

    result = toggle_alert("alert.ghost")

    assert result["error"] == "entity_not_found"


# ---- tools/todo.py -----------------------------------------------------------------

def test_add_todo_item_verifies_the_item_landed(fake_ha):
    from tools.todo import add_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "0", "attributes": {}}]
    fake_ha.ws_result("call_service", {
        "response": {"todo.shopping_list": {"items": [{"uid": "1", "summary": "Milk"}]}},
    })

    result = add_todo_item("todo.shopping_list", "Milk")

    assert result["verified"] is True


def test_add_todo_item_reports_a_nonexistent_list(fake_ha):
    from tools.todo import add_todo_item

    result = add_todo_item("todo.ghost_list", "Milk")

    assert result["error"] == "entity_not_found"


def test_add_todo_item_reports_accepted_but_unverified(fake_ha):
    """The item never shows up in get_todo_items() after the call - the
    old code could not have noticed, since it never read anything back."""
    from tools.todo import add_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "0", "attributes": {}}]
    fake_ha.ws_result("call_service", {"response": {"todo.shopping_list": {"items": []}}})

    result = add_todo_item("todo.shopping_list", "Milk")

    assert result["verified"] is False


def test_add_todo_item_reports_a_rejected_call_as_an_error_not_a_crash(fake_ha):
    """Security review item 7 (leftover from a previous wave): a due_date
    Home Assistant does not accept makes todo/add_item reject the call
    itself with a non-2xx REST response. The old code had no try/except
    around r.raise_for_status(), so this propagated as an uncaught
    HTTPStatusError instead of the error() envelope every sibling in this
    file returns - same fix as update_todo_item, its already-fixed
    sibling."""
    from tools.todo import add_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "0", "attributes": {}}]
    fake_ha.fail_rest("/api/services/todo/add_item", status=400,
                      message="Bad Request")

    result = add_todo_item("todo.shopping_list", "Milk", due_date="not-a-date")

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 400
    assert "verified" not in result


def test_update_todo_item_matches_by_uid_or_summary(fake_ha):
    from tools.todo import update_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "1", "attributes": {}}]
    fake_ha.ws_result("call_service", {
        "response": {"todo.shopping_list": {"items": [{"uid": "abc", "summary": "Milk", "status": "completed"}]}},
    })

    result = update_todo_item("todo.shopping_list", "abc", status="completed")

    assert result["verified"] is True


def test_update_todo_item_reports_a_rejected_call_as_an_error_not_a_crash(fake_ha):
    """An item that does not exist, or a status value outside
    needs_action/completed, both make Home Assistant reject
    todo/update_item with a non-2xx REST response - measured live, a 500
    for the first and a 400 for the second, neither JSON. The old code
    had no try/except around r.raise_for_status(), so this propagated as
    an uncaught HTTPStatusError instead of the error() envelope every
    other failure path in this file returns."""
    from tools.todo import update_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "1", "attributes": {}}]
    fake_ha.fail_rest("/api/services/todo/update_item", status=500,
                      message="Server got itself in trouble")

    result = update_todo_item("todo.shopping_list", "NoSuchItemXYZ", status="completed")

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 500
    assert "verified" not in result


def test_remove_todo_item_verifies_absence(fake_ha):
    from tools.todo import remove_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "0", "attributes": {}}]
    fake_ha.ws_result("call_service", {"response": {"todo.shopping_list": {"items": []}}})

    result = remove_todo_item("todo.shopping_list", "Milk")

    assert result["verified"] is True


def test_remove_todo_item_reports_a_rejected_call_as_an_error_not_a_crash(fake_ha):
    """Security review item 7 (leftover from a previous wave): an item that
    does not exist under `item` makes todo/remove_item reject the call
    itself with a non-2xx REST response. The old code had no try/except
    around r.raise_for_status(), so this propagated as an uncaught
    HTTPStatusError instead of the error() envelope every sibling in this
    file returns - same fix as update_todo_item, its already-fixed
    sibling."""
    from tools.todo import remove_todo_item

    fake_ha.states = [{"entity_id": "todo.shopping_list", "state": "1", "attributes": {}}]
    fake_ha.fail_rest("/api/services/todo/remove_item", status=500,
                      message="Server got itself in trouble")

    result = remove_todo_item("todo.shopping_list", "NoSuchItemXYZ")

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 500
    assert "verified" not in result


# ---- tools/calendar.py: add_calendar_event ---------------------------------------

def test_add_calendar_event_verifies_the_event_landed(fake_ha):
    from tools.calendar import add_calendar_event

    fake_ha.states = [{"entity_id": "calendar.home", "state": "off", "attributes": {}}]
    fake_ha.calendar_events["calendar.home"] = [
        {"summary": "Dentist", "start": {"dateTime": "2026-08-25T09:00:00+02:00"}},
    ]

    result = add_calendar_event(
        "calendar.home", "Dentist", "2026-08-25T09:00:00+02:00", "2026-08-25T10:00:00+02:00")

    assert result["verified"] is True


def test_add_calendar_event_reports_a_nonexistent_calendar(fake_ha):
    from tools.calendar import add_calendar_event

    result = add_calendar_event(
        "calendar.ghost", "Dentist", "2026-08-25T09:00:00+02:00", "2026-08-25T10:00:00+02:00")

    assert result["error"] == "entity_not_found"


def test_add_calendar_event_reports_accepted_but_unverified(fake_ha):
    from tools.calendar import add_calendar_event

    fake_ha.states = [{"entity_id": "calendar.home", "state": "off", "attributes": {}}]
    fake_ha.calendar_events["calendar.home"] = []

    result = add_calendar_event(
        "calendar.home", "Dentist", "2026-08-25T09:00:00+02:00", "2026-08-25T10:00:00+02:00")

    assert result["verified"] is False


def test_add_calendar_event_reports_a_rejected_call_as_an_error_not_a_crash(fake_ha):
    """A calendar entity that does not support calendar.create_event (no
    CREATE_EVENT feature) makes Home Assistant reject the create call
    itself - measured live, a 500, not JSON. The old code had no
    try/except around r.raise_for_status(), so this propagated as an
    uncaught HTTPStatusError instead of the error() envelope every other
    failure path in this file returns."""
    from tools.calendar import add_calendar_event

    fake_ha.states = [{"entity_id": "calendar.home", "state": "off", "attributes": {}}]
    fake_ha.fail_rest("/api/services/calendar/create_event", status=500,
                      message="Server got itself in trouble")

    result = add_calendar_event(
        "calendar.home", "Dentist", "2026-08-25T09:00:00+02:00", "2026-08-25T10:00:00+02:00")

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 500
    assert "verified" not in result


# ---- tools/groups.py ---------------------------------------------------------------

def test_create_group_verifies_membership(fake_ha):
    from tools.groups import create_group

    fake_ha.states = [
        {"entity_id": "group.living_room_lights", "state": "on",
         "attributes": {"entity_id": ["light.kitchen", "light.study"]}},
    ]

    result = create_group("Living Room Lights", ["light.kitchen", "light.study"])

    assert result["verified"] is True
    assert result["entity_id"] == "group.living_room_lights"


def test_create_group_reports_a_membership_mismatch(fake_ha):
    """A member entity_id that does not itself exist is silently dropped
    from the group rather than rejected."""
    from tools.groups import create_group

    fake_ha.states = [
        {"entity_id": "group.living_room_lights", "state": "on",
         "attributes": {"entity_id": ["light.kitchen"]}},
    ]

    result = create_group("Living Room Lights", ["light.kitchen", "light.ghost"])

    assert result["verified"] is False
    assert result["entities"] == ["light.kitchen"]


def test_update_group_verifies_the_new_members(fake_ha):
    from tools.groups import update_group

    fake_ha.states = [
        {"entity_id": "group.living_room_lights", "state": "on",
         "attributes": {"entity_id": ["light.kitchen"], "friendly_name": "Living Room"}},
    ]

    result = update_group("group.living_room_lights", entities=["light.kitchen"])

    assert result["verified"] is True


def test_update_group_reports_a_nonexistent_target(fake_ha):
    from tools.groups import update_group

    result = update_group("group.ghost", entities=["light.kitchen"])

    assert result["error"] == "entity_not_found"


def test_delete_group_verifies_removal(fake_ha, monkeypatch):
    """delete_group reads the entity before the call (to confirm there is
    something to delete) and again after (to confirm it is gone) - this
    swaps the entity out of fake_ha.states between those two reads, the
    way a real deletion would make the second one 404."""
    from tools.groups import delete_group

    fake_ha.states = [
        {"entity_id": "group.living_room_lights", "state": "on", "attributes": {}},
    ]
    real_handle = fake_ha.handle

    def handle(request):
        if request.url.path == "/api/services/group/remove":
            fake_ha.states = []
        return real_handle(request)

    monkeypatch.setattr(fake_ha, "handle", handle)

    result = delete_group("group.living_room_lights")

    assert result["verified"] is True


def test_delete_group_reports_a_nonexistent_target(fake_ha):
    from tools.groups import delete_group

    result = delete_group("group.ghost")

    assert result["error"] == "entity_not_found"


def test_delete_group_reports_a_yaml_group_that_survives(fake_ha):
    """group/remove only affects storage-backed groups - a YAML-defined
    one is accepted without effect."""
    from tools.groups import delete_group

    fake_ha.states = [
        {"entity_id": "group.living_room_lights", "state": "on", "attributes": {}},
    ]

    result = delete_group("group.living_room_lights")

    assert result["verified"] is False


# ---- tools/media_players.py --------------------------------------------------------

def test_send_tts_reports_a_nonexistent_target(fake_ha):
    from tools.media_players import send_tts

    result = send_tts("media_player.ghost", "hello")

    assert result["error"] == "entity_not_found"


def test_send_tts_accepts_a_real_target(fake_ha):
    from tools.media_players import send_tts

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {}},
        {"entity_id": "tts.google_translate", "state": "idle", "attributes": {}},
    ]

    result = send_tts("media_player.kitchen", "hello")

    assert result["accepted"] is True
    assert result["verified"] is None


def test_send_tts_reports_a_missing_engine_instead_of_accepted_true(fake_ha):
    """Same gap package E fixed in broadcast_tts (see the tests below): HA
    answers tts/speak 200 [] for a nonexistent engine exactly like a real
    announcement queued, so a 2xx response alone cannot tell them apart."""
    from tools.media_players import send_tts

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {}},
    ]

    result = send_tts("media_player.kitchen", "hello", engine="tts.does_not_exist")

    assert result["error"] == "entity_not_found"
    assert result["entity_id"] == "tts.does_not_exist"
    # tts/speak must never have been called once the engine was known missing.
    assert not any(c.url.path == "/api/services/tts/speak" for c in fake_ha.rest_calls)


def test_send_tts_skips_engine_check_for_alexa_players(fake_ha):
    """Alexa players go through notify.alexa_media_*, not the TTS engine -
    a missing tts.* entity must not block them (mirrors broadcast_tts)."""
    from tools.media_players import send_tts

    fake_ha.states = [
        {"entity_id": "media_player.echo_kitchen", "state": "idle", "attributes": {}},
    ]

    result = send_tts("media_player.echo_kitchen", "hello", engine="tts.does_not_exist")

    assert result["method"] == "alexa_announce"
    assert result["accepted"] is True


def test_send_tts_reports_a_rejected_call_as_an_error_not_a_crash(fake_ha):
    """Security review item 7 (leftover from a previous wave): send_tts()
    had no try/except around r.raise_for_status(), so a call Home Assistant
    rejects itself (a malformed notify service call, a TTS engine that
    refuses the payload) propagated as an uncaught HTTPStatusError instead
    of the error() envelope every sibling in this file already returns.
    Same fix as update_todo_item/add_calendar_event: rest_error()."""
    from tools.media_players import send_tts

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {}},
        {"entity_id": "tts.google_translate", "state": "idle", "attributes": {}},
    ]
    fake_ha.fail_rest("/api/services/tts/speak", status=500,
                      message="Server got itself in trouble")

    result = send_tts("media_player.kitchen", "hello")

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 500
    assert "accepted" not in result


def test_media_player_control_reports_a_nonexistent_target(fake_ha):
    from tools.media_players import media_player_control

    result = media_player_control("media_player.ghost", "play")

    assert result["error"] == "entity_not_found"


def test_media_player_control_accepts_a_real_target(fake_ha):
    from tools.media_players import media_player_control

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "playing", "attributes": {}},
    ]

    result = media_player_control("media_player.kitchen", "play")

    assert result["accepted"] is True
    assert result["verified"] is None


def test_media_player_control_rejects_an_unknown_command(fake_ha):
    from tools.media_players import media_player_control

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "playing", "attributes": {}},
    ]

    result = media_player_control("media_player.kitchen", "levitate")

    assert result["error"] == "invalid_command"


def test_media_player_control_reports_a_rejected_call_as_an_error_not_a_crash(fake_ha):
    """Security review item 7 (leftover from a previous wave): a volume
    outside 0.0-1.0, or any other payload Home Assistant rejects itself,
    makes media_player/volume_set answer with a non-2xx REST response -
    the old code had no try/except around r.raise_for_status(), so this
    propagated as an uncaught HTTPStatusError instead of the error()
    envelope every other failure path in this file returns."""
    from tools.media_players import media_player_control

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "playing", "attributes": {}},
    ]
    fake_ha.fail_rest("/api/services/media_player/volume_set", status=400,
                      message="Bad Request")

    result = media_player_control("media_player.kitchen", "volume", volume=1.5)

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 400
    assert "accepted" not in result


def test_search_and_play_media_reports_a_nonexistent_target(fake_ha):
    from tools.media_players import search_and_play_media

    result = search_and_play_media("media_player.ghost", "Daft Punk")

    assert result["error"] == "entity_not_found"


# ---- tools/automations.py: create_automation(enabled=False) ------------------------
# Package D. Measured live: automation.turn_off sent immediately after the
# config POST, with no wait for the entity to register, left 9 of 10 freshly
# created automations "on" despite enabled=False.

def test_create_automation_disabled_waits_for_registration_then_verifies_off(fake_ha):
    """The entity does not exist for the first two reads (the registration
    race) - create_automation() must wait it out, then confirm 'off' rather
    than trusting the request.

    Uses sequence_states rather than fake_ha.states: the config POST itself
    (fakeha's CRUD route) registers the entity as "on", the same way a real
    create does - sequence_states is checked ahead of that in fakeha's
    routing, so it is what lets this test control what the *post-turn_off*
    read-back reports without that POST-time default clobbering it."""
    from tools.automations import create_automation

    fake_ha.delay_registration("automation.morning_lights", reads=2)
    fake_ha.sequence_states("automation.morning_lights", [
        {"entity_id": "automation.morning_lights", "state": "off", "attributes": {}},
    ])

    result = create_automation("Morning lights", trigger=[], action=[], enabled=False)

    assert result["enabled"] is False
    assert result["verified"] is True
    assert result["state"] == "off"
    # The turn_off service call must have been sent, not skipped.
    assert any(c.url.path == "/api/services/automation/turn_off" for c in fake_ha.rest_calls)


def test_create_automation_disabled_reports_an_error_when_it_stays_armed(fake_ha):
    """The old bug: enabled=False requested, but the automation is still
    'on' after creation. This must not be a bare success. No fake_ha.states
    setup needed: fakeha's config-POST route registers a fresh automation
    as "on" by default (matching Home Assistant), and turn_off - like every
    /api/services/* call in fakeha - never mutates it, exactly reproducing
    the race this guards against."""
    from tools.automations import create_automation

    result = create_automation("Morning lights", trigger=[], action=[], enabled=False)

    assert result["error"] == "automation_not_disabled"
    assert result["state"] == "on"
    assert result["enabled"] is True


def test_create_automation_disabled_reports_an_error_when_never_registered(fake_ha):
    from tools.automations import create_automation

    fake_ha.delay_registration("automation.never_shows_up", reads=99)

    result = create_automation("Never shows up", trigger=[], action=[], enabled=False)

    assert result["error"] == "automation_not_registered"
    # turn_off must never be sent to an entity that was never confirmed to exist.
    assert not any(c.url.path == "/api/services/automation/turn_off" for c in fake_ha.rest_calls)


def test_create_automation_enabled_reports_the_observed_state(fake_ha):
    from tools.automations import create_automation

    result = create_automation("Morning lights", trigger=[], action=[])

    assert result["enabled"] is True
    assert result["verified"] is True
    assert result["state"] == "on"


def test_create_automation_enabled_true_never_actively_arms(fake_ha):
    """enabled=True is create_automation()'s default, not necessarily an
    explicit request - so it must never send an active turn_on, only
    observe. This is what re-running create_automation() (overwrite=True,
    same name) over an automation a person had deliberately disabled must
    NOT silently re-arm: with no active turn_on, the state fakeha's
    config-POST route preserves (see its own comment) is the state
    actually observed, and Home Assistant's real config-write endpoint
    behaves the same way - measured live."""
    from tools.automations import create_automation

    create_automation("Morning lights", trigger=[], action=[])
    # A person disables it - not through this tool, so automation_configs
    # is untouched, only the entity's own registered state changes.
    for s in fake_ha.states:
        if s["entity_id"] == "automation.morning_lights":
            s["state"] = "off"

    result = create_automation("Morning lights", trigger=["updated"], action=[],
                               overwrite=True)

    assert result["error"] == "automation_state_unverified"
    assert result["state"] == "off"
    assert result["enabled"] is False
    assert not any(c.url.path == "/api/services/automation/turn_on"
                  for c in fake_ha.rest_calls)


def test_create_automation_reports_home_assistants_write_time_rejection(fake_ha):
    """Same class of bug already fixed on update_automation()'s and
    patch_automation()'s own config-write POSTs: a bare r.raise_for_status()
    discarded Home Assistant's own validation message (e.g. "Invalid
    trigger 'nope_not_a_platform' specified") as an uncaught
    httpx.HTTPStatusError instead of the named error() every other
    refusal in create_automation() already returns. Nothing is created,
    and _set_and_verify_enabled() must never be reached - the enabled
    path this fix sits right above must stay untouched."""
    import httpx

    from tools.automations import create_automation

    real_handle = fake_ha.handle

    def handle_post_rejected(request):
        if (request.method == "POST"
                and request.url.path == "/api/config/automation/config/bad_trigger_probe"):
            return httpx.Response(400, text="Invalid trigger 'nope_not_a_platform' specified")
        return real_handle(request)

    fake_ha.handle = handle_post_rejected

    result = create_automation(
        "Bad trigger probe",
        trigger=[{"platform": "nope_not_a_platform"}],
        action=[],
    )

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 400
    assert "nope_not_a_platform" in result["detail"]
    assert result["automation_id"] == "bad_trigger_probe"
    assert result["entity_id"] == "automation.bad_trigger_probe"
    assert "bad_trigger_probe" not in fake_ha.automation_configs
    # The enabled-verification path must never run against a rejected config.
    assert not any(c.url.path.startswith("/api/services/automation/")
                  for c in fake_ha.rest_calls)


# ---- tools/helpers.py: create_template_sensor -------------------------------------

def test_create_template_sensor_reports_a_rejected_flow_start_as_an_error_not_a_crash(fake_ha):
    """A transport/auth failure (a revoked token, e.g.) makes the very
    first REST call in this tool's three-step config-flow sequence fail -
    measured live, a 401. The old code had no try/except around
    r1.raise_for_status(), so this propagated as an uncaught
    HTTPStatusError instead of the error() envelope every other failure
    path in this codebase returns."""
    from tools.helpers import create_template_sensor

    fake_ha.fail_rest("/api/config/config_entries/flow", status=401, message="Unauthorized")

    result = create_template_sensor("Test Sensor", "{{ 1 + 1 }}")

    assert result["error"] == "home_assistant_error"
    assert result["status"] == 401


# ---- tools/helpers.py: set_helper - unrecognised command rejection (E1) ------------

def test_set_helper_timer_rejects_an_unrecognised_command(fake_ha):
    """A typo'd/foreign command (e.g. 'stop') used to silently fall through
    to 'start' - the opposite of what a caller who wrote 'stop' wanted."""
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "timer.probe", "state": "active", "attributes": {}},
    ]

    result = set_helper("timer.probe", "stop")

    assert result["error"] == "invalid_value"
    assert result["allowed"] == ["cancel", "finish", "pause", "start"]
    assert fake_ha.rest_calls == []  # never even called Home Assistant


def test_set_helper_counter_rejects_a_typo(fake_ha):
    """'decremnt' used to silently fall through to 'increment'."""
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "counter.probe", "state": "3", "attributes": {"step": 1}},
    ]

    result = set_helper("counter.probe", "decremnt")

    assert result["error"] == "invalid_value"
    assert result["allowed"] == ["decrement", "increment", "reset"]
    assert fake_ha.rest_calls == []


def test_set_helper_input_boolean_rejects_an_unrecognised_value(fake_ha):
    from tools.helpers import set_helper

    fake_ha.states = [
        {"entity_id": "input_boolean.guest_mode", "state": "off", "attributes": {}},
    ]

    result = set_helper("input_boolean.guest_mode", "true")

    assert result["error"] == "invalid_value"
    assert result["allowed"] == ["off", "on"]


# ---- tools/climate.py: set_climate - partial failure (E3) -------------------------

def test_set_climate_reports_what_applied_before_a_later_field_was_refused(fake_ha):
    """hvac_mode is accepted; fan_mode is then refused by Home Assistant.
    The old code let that exception propagate and discarded the fact that
    hvac_mode had already landed."""
    from tools.climate import set_climate

    fake_ha.fail_rest("/api/services/climate/set_fan_mode", status=400,
                      message="fan_mode not valid")

    result = set_climate("climate.hvac", hvac_mode="cool", fan_mode="turbo")

    assert result["error"] == "service_call_failed"
    assert result["applied"] == {"hvac_mode": "cool"}
    assert result["failed_field"] == "fan_mode"
    assert result["not_attempted"] == []


def test_set_climate_reports_fields_not_yet_attempted(fake_ha):
    """hvac_mode is refused first, so temperature (requested after it) must
    never even be sent, and the return must say so."""
    from tools.climate import set_climate

    fake_ha.fail_rest("/api/services/climate/set_hvac_mode", status=400,
                      message="invalid hvac_mode")

    result = set_climate("climate.hvac", hvac_mode="banana", temperature=21,
                         fan_mode="high")

    assert result["error"] == "service_call_failed"
    assert result["applied"] == {}
    assert result["failed_field"] == "hvac_mode"
    assert result["not_attempted"] == ["temperature", "fan_mode"]
    # temperature/fan_mode calls must never have been sent.
    assert not any(c.url.path == "/api/services/climate/set_temperature"
                  for c in fake_ha.rest_calls)


# ---- tools/media_players.py: broadcast_tts - engine existence (E5) ----------------

def test_broadcast_tts_reports_a_missing_engine_instead_of_ok_true(fake_ha):
    """Measured live: with no tts.* entity registered, tts/speak still
    answers 200 [] for every player - the old code reported ok: true for
    all of them with nothing actually announced."""
    from tools.media_players import broadcast_tts

    fake_ha.states = [
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {}},
        {"entity_id": "media_player.living_room", "state": "idle", "attributes": {}},
    ]

    result = broadcast_tts("Dinner is ready", engine="tts.does_not_exist")

    assert result["engine_exists"] is False
    assert result["ok_count"] == 0
    assert all(p["ok"] is False for p in result["players"])
    assert "note" in result
    # tts/speak must never have been called once the engine was known missing.
    assert not any(c.url.path == "/api/services/tts/speak" for c in fake_ha.rest_calls)


def test_broadcast_tts_still_attempts_alexa_players_when_the_engine_is_missing(fake_ha):
    """Alexa players go through notify.alexa_media_*, not the TTS engine -
    a missing tts.* entity must not block them."""
    from tools.media_players import broadcast_tts

    fake_ha.states = [
        {"entity_id": "media_player.echo_kitchen", "state": "idle", "attributes": {}},
    ]

    result = broadcast_tts("Dinner is ready", engine="tts.does_not_exist")

    assert result["players"][0]["method"] == "alexa_announce"
    assert result["players"][0]["ok"] is True


def test_broadcast_tts_reports_ok_when_the_engine_exists(fake_ha):
    from tools.media_players import broadcast_tts

    fake_ha.states = [
        {"entity_id": "tts.google_translate", "state": "idle", "attributes": {}},
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {}},
    ]

    result = broadcast_tts("Dinner is ready")

    assert result["engine_exists"] is True
    assert result["ok_count"] == 1
    assert "note" not in result
