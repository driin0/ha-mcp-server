import httpx

from tools._base import (
    mcp, HA_URL, HEADERS, HELPER_DOMAINS, _slug, _ws, envelope, error, observe_actuation, ws_error,
)

# timer/{command} settles into a fixed state, the same way lock/{command}
# does (tools/locks.py) - measured live against a throwaway Home Assistant
# instance: start -> active, pause -> paused, cancel -> idle, finish -> idle.
_TIMER_EXPECTED_STATE = {"start": "active", "pause": "paused", "cancel": "idle", "finish": "idle"}


def _counter_expected_value(prior_state: dict, command: str) -> float | None:
    """The value counter/{command} should produce, per Home Assistant's own
    documented arithmetic - prior value ± the counter's own `step`
    attribute for increment/decrement, or its `initial` attribute for
    reset. None when the prior state was not itself numeric (should not
    happen for a real counter, but a predicate must not crash on it).

    Shared by set_helper()'s counter branch and counter_control() - same
    entity, same three commands, same arithmetic.
    """
    attrs = prior_state.get("attributes", {})
    if command == "reset":
        return attrs.get("initial", 0)
    try:
        prior_value = float(prior_state["state"])
    except (TypeError, ValueError):
        return None
    step = attrs.get("step", 1)
    return prior_value + step if command == "increment" else prior_value - step


def _numeric_match(state: dict, expected) -> bool:
    """satisfied() for observe_actuation() when success means "the state,
    read as a float, equals `expected`" - used for counter and
    input_number, where the state itself is that number as a string."""
    if expected is None:
        return False
    try:
        return abs(float(state["state"]) - float(expected)) < 1e-9
    except (TypeError, ValueError):
        return False

# Per-domain whitelist of the config keys {domain}/create legitimately
# accepts, mirroring each helper integration's own STORAGE_FIELDS schema
# (homeassistant/components/<domain>/__init__.py, checked against a running
# 2026.8 instance). "name" and "type" are deliberately absent from every
# set: "type" selects which WebSocket command actually runs and must never
# be a caller-controlled value - it decided which command ran even when
# reserved keys were spread before config, since a whitelist rejects an
# unknown key outright rather than merely losing a key-collision fight -
# and "name" is already this tool's own required parameter, so a same-named
# key inside config could only shadow or duplicate it.
_HELPER_CREATE_FIELDS = {
    "input_boolean": {"initial", "icon"},
    "input_number": {"min", "max", "initial", "step", "unit_of_measurement", "icon", "mode"},
    "input_text": {"min", "max", "initial", "icon", "unit_of_measurement", "pattern", "mode"},
    "input_select": {"options", "initial", "icon"},
    "input_datetime": {"has_date", "has_time", "icon", "initial"},
    "counter": {"initial", "step", "minimum", "maximum", "icon", "restore"},
    "timer": {"duration", "restore", "icon"},
    "input_button": {"icon"},
}


@mcp.tool()
def list_helpers(domain: str = "") -> dict:
    """
    List helpers, optionally filtered by domain.

    domain: leave empty for all, or one of:
      input_boolean, input_number, input_text, input_select,
      input_datetime, counter, timer, input_button

    Returns: {total, returned, offset, note?, helpers: [...]}
    """
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        r.raise_for_status()
        helpers = []
        for s in r.json():
            d = s["entity_id"].split(".")[0]
            if d not in HELPER_DOMAINS:
                continue
            if domain and d != domain:
                continue
            helpers.append({
                "entity_id": s["entity_id"],
                "domain": d,
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                "state": s["state"],
                "attributes": {
                    k: v for k, v in s.get("attributes", {}).items()
                    if k not in ("friendly_name", "icon", "editable")
                },
            })
        return envelope(sorted(helpers, key=lambda x: (x["domain"], x["name"])), key="helpers")


@mcp.tool()
def set_helper(entity_id: str, value: str) -> dict:
    """
    Set the value of a helper entity.

    - input_boolean: value = 'on' or 'off'
    - input_number:  value = numeric string (e.g. '42')
    - input_text:    value = any string
    - input_select:  value = one of the allowed options
    - input_datetime: value = 'YYYY-MM-DD HH:MM:SS' or 'HH:MM:SS' or 'YYYY-MM-DD'
    - counter:       value = 'increment', 'decrement', or 'reset'
    - timer:         value = 'start', 'pause', 'cancel', or 'finish'

    Returns: {entity_id, value, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found"/"unsupported_domain", ...}
    otherwise.

    `verified` is true only when the helper's own state, read back after
    the call, matches: exact string/numeric equality for input_boolean,
    input_number, input_text and input_select; a loose substring match for
    input_datetime (Home Assistant reformats a partial date or time
    on write, so exact equality would false-negative on a value that did
    take effect); the arithmetic result (prior ± step, or `initial`) for
    counter; and the fixed state each timer command settles into (see
    tools/timer_control docs) for timer.
    """
    domain = entity_id.split(".")[0]
    if domain not in HELPER_DOMAINS:
        return error("unsupported_domain", f"Unsupported helper domain: {domain}",
                     entity_id=entity_id, allowed=sorted(HELPER_DOMAINS))

    prior_state = None
    if domain == "counter":
        with httpx.Client() as client:
            r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return error("entity_not_found",
                         f"{entity_id} does not exist on this Home Assistant instance.",
                         entity_id=entity_id, value=value)
        r.raise_for_status()
        prior_state = r.json()

    with httpx.Client() as client:
        if domain == "input_boolean":
            svc = "turn_on" if value == "on" else "turn_off"
            r = client.post(f"{HA_URL}/api/services/input_boolean/{svc}",
                            headers=HEADERS, json={"entity_id": entity_id}, timeout=10)
        elif domain == "input_number":
            r = client.post(f"{HA_URL}/api/services/input_number/set_value",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "value": float(value)}, timeout=10)
        elif domain == "input_text":
            r = client.post(f"{HA_URL}/api/services/input_text/set_value",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "value": value}, timeout=10)
        elif domain == "input_select":
            r = client.post(f"{HA_URL}/api/services/input_select/select_option",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "option": value}, timeout=10)
        elif domain == "input_datetime":
            r = client.post(f"{HA_URL}/api/services/input_datetime/set_datetime",
                            headers=HEADERS,
                            json={"entity_id": entity_id, "datetime": value}, timeout=10)
        elif domain == "counter":
            svc = value if value in ("increment", "decrement", "reset") else "increment"
            r = client.post(f"{HA_URL}/api/services/counter/{svc}",
                            headers=HEADERS, json={"entity_id": entity_id}, timeout=10)
        else:  # timer
            svc = value if value in ("start", "pause", "cancel", "finish") else "start"
            r = client.post(f"{HA_URL}/api/services/timer/{svc}",
                            headers=HEADERS, json={"entity_id": entity_id}, timeout=10)
        r.raise_for_status()

    if domain == "input_boolean":
        satisfied = lambda s: s["state"] == ("on" if value == "on" else "off")
    elif domain == "input_number":
        satisfied = lambda s: _numeric_match(s, value)
    elif domain in ("input_text", "input_select"):
        satisfied = lambda s: s["state"] == value
    elif domain == "input_datetime":
        # HA reformats a partial date/time on write (e.g. a bare "HH:MM:SS"
        # against an entity that also carries a date), so exact equality
        # would false-negative on a value that did take effect - a loose
        # substring match in either direction is the practical middle
        # ground between "verify nothing" and "reimplement HA's formatter".
        satisfied = lambda s: bool(s["state"]) and s["state"] not in ("unknown", "unavailable") and (
            value.strip() in s["state"] or s["state"] in value.strip())
    elif domain == "counter":
        # svc above already folds an unrecognised value to "increment" -
        # the same fold applies here so the expected value matches the
        # command that was actually sent.
        counter_command = value if value in ("increment", "decrement", "reset") else "increment"
        expected = _counter_expected_value(prior_state, counter_command)
        satisfied = lambda s: _numeric_match(s, expected)
    else:  # timer
        expected = _TIMER_EXPECTED_STATE.get(value, _TIMER_EXPECTED_STATE["start"])
        satisfied = lambda s: s["state"] == expected

    obs = observe_actuation(entity_id, satisfied)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, value=value)
    return {
        "entity_id": entity_id,
        "value": value,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }


@mcp.tool()
def create_helper(
    domain: str,
    name: str,
    config: dict = None,
) -> dict:
    """
    Create a new helper entity.

    domain: one of input_boolean, input_number, input_text,
            input_select, input_datetime, counter, timer, input_button

    config: optional domain-specific fields. Examples:

    input_boolean:
      {}  (no extra config needed)

    input_number:
      {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%"}

    input_text:
      {"min": 0, "max": 100}

    input_select:
      {"options": ["Option A", "Option B", "Option C"]}

    input_datetime:
      {"has_date": true, "has_time": true}

    counter:
      {"initial": 0, "step": 1, "minimum": 0, "maximum": 100, "restore": true}

    timer:
      {"duration": "00:05:00", "restore": false}

    input_button:
      {}  (no extra config needed)

    config may only contain the keys legitimate for `domain` (see above) -
    anything else, including "type" or "name", is refused rather than sent.
    """
    if domain not in _HELPER_CREATE_FIELDS:
        return error("unsupported_domain", f"Unsupported domain: {domain}",
                     allowed=sorted(_HELPER_CREATE_FIELDS))

    config = config or {}
    allowed = _HELPER_CREATE_FIELDS[domain]
    offending = sorted(set(config) - allowed)
    if offending:
        return error(
            "invalid_config_keys",
            f"config for domain '{domain}' accepts only {sorted(allowed)}; "
            f"reject unexpected key(s): {offending}",
            domain=domain, offending_keys=offending, allowed_keys=sorted(allowed),
        )

    # Helper "storage" collections are created over the WebSocket API
    # ({domain}/create) — exactly like the GUI helper editor. There is no REST
    # config endpoint for these, so an httpx POST returns 404.
    res = _ws({"type": f"{domain}/create", "name": name, **config})
    if err := ws_error(res):
        return err

    item = res["result"]
    helper_id = item.get("id")
    if not helper_id:
        return error("no_id_in_response",
                     "Home Assistant did not return an id for the created helper",
                     domain=domain, name=name, result=item)
    entity_id = f"{domain}.{helper_id}"

    # The create response never carries entity_id, only the storage item's
    # own id - verify the entity actually exists rather than trusting one
    # constructed locally (the old fallback invented an id from the name
    # via _slug() whenever the response carried none, and reported an
    # entity_id that could point at nothing).
    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
    if r.status_code != 200:
        return error(
            "entity_not_found",
            f"Helper storage item was created (id={helper_id!r}) but entity "
            f"{entity_id} does not exist",
            domain=domain, name=name, helper_id=helper_id, entity_id=entity_id,
        )
    return {"helper_id": helper_id, "entity_id": entity_id, "result": item}


@mcp.tool()
def delete_helper(entity_id: str) -> dict:
    """
    Delete a helper entity by entity_id.
    Supported: input_boolean, input_number, input_text, input_select,
               input_datetime, counter, timer, input_button.
    """
    domain = entity_id.split(".")[0]
    supported = {
        "input_boolean", "input_number", "input_text", "input_select",
        "input_datetime", "counter", "timer", "input_button",
    }
    if domain not in supported:
        return {"error": f"Cannot delete domain: {domain}"}
    helper_id = entity_id.split(".", 1)[1]
    # Storage-collection delete also goes over the WebSocket API.
    res = _ws({"type": f"{domain}/delete", f"{domain}_id": helper_id})
    if not res.get("success"):
        return {"error": "WebSocket delete failed", "entity_id": entity_id,
                "detail": res.get("error")}
    return {"deleted": entity_id, "ok": True}


@mcp.tool()
def create_template_sensor(
    name: str,
    state_template: str,
    unit_of_measurement: str = "",
    icon: str = "",
    device_class: str = "",
    state_class: str = "",
) -> dict:
    """
    Create a template sensor helper in Home Assistant via the config flow.

    state_template: Jinja2 template for the sensor value.

    Examples:
      Count local light groups only, excluding entities mirrored from other
      instances (adjust the pattern to your own remote prefixes):
        state_template: >
          {{ states.light
             | selectattr('attributes.entity_id', 'defined')
             | rejectattr('entity_id', 'search', 'annex|workshop')
             | list | count }}

      Current temperature from a sensor:
        state_template: "{{ states('sensor.living_room_temperature') }}"
    """
    with httpx.Client() as client:
        # Step 1: start the template config flow
        r1 = client.post(
            f"{HA_URL}/api/config/config_entries/flow",
            headers=HEADERS,
            json={"handler": "template"},
            timeout=15,
        )
        r1.raise_for_status()
        flow = r1.json()
        flow_id = flow["flow_id"]

        # Step 2: select sensor as template type (template flow starts with a menu)
        r2 = client.post(
            f"{HA_URL}/api/config/config_entries/flow/{flow_id}",
            headers=HEADERS,
            json={"next_step_id": "sensor"},
            timeout=15,
        )
        r2.raise_for_status()
        form_schema = r2.json()

        # Step 3: submit sensor config — only include fields present in the schema
        schema_keys = {f["name"] for f in form_schema.get("data_schema", [])}
        candidate: dict = {
            "name": name,
            "state": state_template,
            "unit_of_measurement": unit_of_measurement,
            "icon": icon,
            "device_class": device_class,
            "state_class": state_class,
        }
        payload = {k: v for k, v in candidate.items() if v and (not schema_keys or k in schema_keys)}

        r3 = client.post(
            f"{HA_URL}/api/config/config_entries/flow/{flow_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        if r3.status_code == 400:
            return {"error": "400 on form submit", "schema": form_schema, "payload_sent": payload}
        r3.raise_for_status()
        result = r3.json()
        entry_id = result.get("result", {}).get("entry_id", "")

        response: dict = {"entry_id": entry_id, "name": name, "result": result}
        if icon and entry_id:
            entity_id = f"sensor.{_slug(name)}"
            try:
                ws_result = _ws({
                    "type": "config/entity_registry/update",
                    "entity_id": entity_id,
                    "icon": icon,
                })
                response["icon_set"] = ws_result.get("success", False)
                if not ws_result.get("success"):
                    response["icon_error"] = ws_result
            except Exception as exc:
                response["icon_error"] = str(exc)
        return response


@mcp.tool()
def delete_template_sensor(entry_id: str) -> dict:
    """
    Delete a template sensor config entry by entry_id.
    Use the entry_id returned by create_template_sensor.
    """
    with httpx.Client() as client:
        r = client.delete(
            f"{HA_URL}/api/config/config_entries/entry/{entry_id}",
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        return {"deleted": entry_id, "status": r.status_code}


@mcp.tool()
def set_number(entity_id: str, value: float) -> dict:
    """
    Set the value of a number or input_number entity.

    entity_id: e.g. 'number.volume' or 'input_number.timer_minutes'
    value: numeric value within the entity's min/max range

    Returns: {entity_id, value, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all. `verified` is true only when the entity's own state,
    read back after the call, numerically equals `value` — a value outside
    the entity's min/max range is accepted and silently clamped rather than
    rejected, so `verified: false` (with `state` showing the clamped value)
    is how that is told apart from a value genuinely applied.
    """
    domain = entity_id.split(".")[0]
    if domain not in ("number", "input_number"):
        raise ValueError("entity_id must be a number.* or input_number.* entity")
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/{domain}/set_value",
            headers=HEADERS,
            json={"entity_id": entity_id, "value": value},
            timeout=10,
        )
        r.raise_for_status()

    obs = observe_actuation(entity_id, lambda s: _numeric_match(s, value))
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, value=value)
    return {
        "entity_id": entity_id,
        "value": value,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }


@mcp.tool()
def set_select(entity_id: str, option: str) -> dict:
    """
    Set the selected option of a select or input_select entity.

    entity_id: e.g. 'select.fan_mode' or 'input_select.scene'
    option: one of the available options for this entity

    Returns: {entity_id, option, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all. `verified` is true only when the entity's own state,
    read back after the call, equals `option` — an option not in the
    entity's own option list is rejected by Home Assistant (a non-2xx
    response, raised like any other refused call), so `verified: false`
    here means something else kept the change from landing.
    """
    domain = entity_id.split(".")[0]
    if domain not in ("select", "input_select"):
        raise ValueError("entity_id must be a select.* or input_select.* entity")
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/{domain}/select_option",
            headers=HEADERS,
            json={"entity_id": entity_id, "option": option},
            timeout=10,
        )
        r.raise_for_status()

    obs = observe_actuation(entity_id, lambda s: s["state"] == option)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, option=option)
    return {
        "entity_id": entity_id,
        "option": option,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }


@mcp.tool()
def set_text(entity_id: str, value: str) -> dict:
    """
    Set the value of a text or input_text entity.

    entity_id: e.g. 'input_text.message'
    value: string value

    Returns: {entity_id, value, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all. `verified` is true only when the entity's own state,
    read back after the call, equals `value` — a value outside the
    entity's min/max length is accepted and silently truncated rather than
    rejected, so `verified: false` (with `state` showing what was actually
    stored) is how that is told apart from a value genuinely applied.
    """
    domain = entity_id.split(".")[0]
    if domain not in ("text", "input_text"):
        raise ValueError("entity_id must be a text.* or input_text.* entity")
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/{domain}/set_value",
            headers=HEADERS,
            json={"entity_id": entity_id, "value": value},
            timeout=10,
        )
        r.raise_for_status()

    obs = observe_actuation(entity_id, lambda s: s["state"] == value)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, value=value)
    return {
        "entity_id": entity_id,
        "value": value,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }


@mcp.tool()
def timer_control(entity_id: str, command: str, duration: str = "") -> dict:
    """
    Control a timer entity.

    command: 'start' | 'pause' | 'cancel' | 'finish'
    duration: optional override duration in HH:MM:SS format (only for 'start')

    Returns: {entity_id, command, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all.

    `verified` is true only when the timer's own state, read back after the
    call, matches the state that command settles into — measured live:
    start -> "active", pause -> "paused", cancel -> "idle", finish -> "idle".
    A `pause` on a timer that is not running, or a `finish` on one that was
    never started, is accepted by Home Assistant without effect, which is
    exactly the case `verified: false` exists to report.
    """
    if command not in _TIMER_EXPECTED_STATE:
        raise ValueError("command must be: start, pause, cancel, or finish")
    data: dict = {"entity_id": entity_id}
    if command == "start" and duration:
        data["duration"] = duration
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/timer/{command}",
            headers=HEADERS,
            json=data,
            timeout=10,
        )
        r.raise_for_status()

    expected = _TIMER_EXPECTED_STATE[command]
    obs = observe_actuation(entity_id, lambda s: s["state"] == expected)
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    return {
        "entity_id": entity_id,
        "command": command,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }


@mcp.tool()
def counter_control(entity_id: str, command: str) -> dict:
    """
    Control a counter entity.

    command: 'increment' | 'decrement' | 'reset'

    Returns: {entity_id, command, verified, state} on a call Home Assistant
    accepted, or {error: "entity_not_found", ...} when entity_id has no
    state at all. `verified` is true only when the counter's own value,
    read back after the call, equals the arithmetic result Home Assistant
    documents for that command — prior value ± the counter's own `step`
    attribute for increment/decrement, or its `initial` attribute for
    reset — not merely that the value changed. A counter already at its
    configured `maximum`/`minimum` accepts an increment/decrement without
    moving, which `verified: false` reports rather than hides.
    """
    if command not in ("increment", "decrement", "reset"):
        raise ValueError("command must be: increment, decrement, or reset")

    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    r.raise_for_status()
    prior_state = r.json()
    expected = _counter_expected_value(prior_state, command)

    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/counter/{command}",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()

    obs = observe_actuation(entity_id, lambda s: _numeric_match(s, expected))
    if not obs["exists"]:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id, command=command)
    return {
        "entity_id": entity_id,
        "command": command,
        "verified": obs["verified"],
        "state": obs["state"]["state"],
    }
