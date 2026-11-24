"""A fake Home Assistant, in process.

Tools reach Home Assistant two ways: httpx for the REST API and `_ws` for the
WebSocket API. This module answers both from the same in-memory dataset, so a
test can say "the registry contains X" once and have every tool see it.
"""
import copy
import urllib.parse
from datetime import datetime, timedelta
import json

import httpx

# /api/config/automation/config/<id>, seeded for automation.nas_shutdown
# (id "1684270733500" in DEFAULT_STATES below) - the incident automation
# the automation-editing plan exists because of: a legacy-vocabulary config
# (trigger/condition/action, platform/service) whose condition template
# references an entity that had been renamed, button.nas_shutdown, and
# whose actions press that button, wait for it to confirm, then cut power
# at the switch. Real enough to exercise get_automation()'s normalisation
# against, not a synthetic minimal shape.
DEFAULT_AUTOMATION_CONFIGS = {
    "1684270733500": {
        "alias": "NAS shutdown",
        "description": "",
        "trigger": [
            {"platform": "state", "entity_id": "input_boolean.nas_shutdown_request",
             "to": "on"},
        ],
        "condition": [
            {"condition": "template",
             "value_template": "{{ is_state('button.nas_shutdown', 'unavailable') }}"},
        ],
        "action": [
            {"service": "button.press", "target": {"entity_id": "button.nas_shutdown"}},
            {"wait_for_trigger": [
                {"platform": "state", "entity_id": "button.nas_shutdown",
                 "to": "unavailable"},
            ], "timeout": "00:00:30", "continue_on_timeout": True},
            {"service": "switch.turn_off", "target": {"entity_id": "switch.nas_power"}},
        ],
        "mode": "single",
    },
}

DEFAULT_STATES = [
    {"entity_id": "light.kitchen", "state": "on",
     "attributes": {"friendly_name": "Kitchen", "brightness": 200}},
    {"entity_id": "light.study", "state": "off",
     "attributes": {"friendly_name": "Study"}},
    {"entity_id": "automation.nas_shutdown", "state": "on",
     "attributes": {"friendly_name": "NAS shutdown", "id": "1684270733500",
                    "last_triggered": "2026-08-22T02:00:00+00:00"}},
    {"entity_id": "automation.morning", "state": "off",
     "attributes": {"friendly_name": "Morning", "id": "1684270733501",
                    "last_triggered": None}},
]

DEFAULT_REGISTRY = [
    {"entity_id": "light.kitchen", "area_id": "kitchen", "device_id": None, "labels": []},
    {"entity_id": "light.study", "area_id": "study", "device_id": None, "labels": []},
    {"entity_id": "automation.nas_shutdown", "area_id": None, "device_id": None, "labels": ["power"]},
    {"entity_id": "automation.morning", "area_id": None, "device_id": None, "labels": []},
    # Entity with own area (takes precedence over device area)
    {"entity_id": "light.bed_light", "area_id": "bedroom", "device_id": "device_bed", "labels": []},
    # Entity inheriting area from device
    {"entity_id": "light.kitchen_lights", "area_id": None, "device_id": "device_kitchen", "labels": []},
    # Entity with no area at all
    {"entity_id": "switch.garage_door", "area_id": None, "device_id": None, "labels": []},
]

# config/device_registry/list rows. Empty by default: none of the default
# entities above are attached to a device, so the default fixture exercises
# only the "area set directly on the entity" path. A test that wants the
# "area inherited from the device" path adds a device here and points an
# entity's device_id at it - see DEFAULT_REGISTRY for the entity side.
DEFAULT_DEVICES = [
    {"id": "device_bed", "name": "Bed", "area_id": None, "manufacturer": None, "model": None, "labels": []},
    {"id": "device_kitchen", "name": "Kitchen", "area_id": "stanza_del_dispositivo", "manufacturer": None, "model": None, "labels": []},
]

# /api/config/config_entries/entry rows. Two UI-created template helpers and
# one entry of another domain, so a tool can be shown refusing a config
# entry that is not a template helper.
DEFAULT_CONFIG_ENTRIES = [
    {"entry_id": "01TEMPLATESENSOR", "domain": "template",
     "title": "HACS update disponibili", "state": "loaded", "supports_options": True},
    {"entry_id": "01TEMPLATEBINARY", "domain": "template",
     "title": "HACS has update", "state": "loaded", "supports_options": True},
    {"entry_id": "01SUNENTRY", "domain": "sun", "title": "Sun",
     "state": "loaded", "supports_options": False},
]

# The stored options of each template entry, in the shape the options flow
# takes on submit: top-level fields, plus the `additional_options` section
# as a nested dict. `template_type` is not a form field - it selects the
# form, and the options flow's step_id is that type.
DEFAULT_TEMPLATE_OPTIONS = {
    "01TEMPLATESENSOR": {
        "template_type": "sensor",
        "options": {
            "state": "{{ integration_entities('hacs') | count }}",
            "unit_of_measurement": "updates",
            "state_class": "measurement",
            "additional_options": {"availability": "{{ true }}"},
        },
    },
    "01TEMPLATEBINARY": {
        "template_type": "binary_sensor",
        "options": {"state": "{{ false }}", "device_class": "update"},
    },
}

# The options-flow form of each template type, as Home Assistant 2026.11.3
# returns it (measured against a throwaway instance): (name, required)
# pairs, and a section as ("additional_options", [(name, required), ...]).
# `name` is never in the options form - it is set at creation only.
TEMPLATE_OPTION_FIELDS = {
    "sensor": [("state", True), ("device_class", False), ("state_class", False),
               ("unit_of_measurement", False), ("device_id", False),
               ("additional_options", [("availability", False)])],
    "binary_sensor": [("state", True), ("device_class", False), ("device_id", False),
                      ("additional_options", [("availability", False),
                                              ("delay_on", False), ("delay_off", False)])],
    "switch": [("value_template", False), ("turn_on", False), ("turn_off", False),
               ("device_id", False), ("additional_options", [("availability", False)])],
    "button": [("press", False), ("device_class", False), ("device_id", False),
               ("additional_options", [("availability", False)])],
}

_STATE_CLASSES = ["measurement", "measurement_angle", "total", "total_increasing"]


class FakeHA:
    """Answers REST and WebSocket calls from one dataset."""

    def __init__(self):
        self.states = [dict(s) for s in DEFAULT_STATES]
        self.registry = [dict(e) for e in DEFAULT_REGISTRY]
        self.devices = [dict(d) for d in DEFAULT_DEVICES]
        self.config = {"version": "2026.8.1", "location_name": "Test",
                       "language": "en"}
        # Per-command WebSocket overrides: {"tag/list": {"success": True, ...}}
        self.ws_responses = {}
        # REST failure overrides: {"/api/services/light/turn_on": (401, {...})}.
        # Keyed by a fragment matched with `in path`, not exact equality -
        # some real paths carry a timestamp (e.g. /api/history/period/<start>)
        # that a test cannot know in advance, so it fails on a stable prefix
        # like "/api/history/period/" instead. Checked before any route
        # below, so a test can make any REST path - not just an unrouted one
        # - fail the way Home Assistant does.
        self.rest_responses = {}
        # REST failures that never produce a Response at all - a connection
        # refused, a DNS failure, a timeout. {"/api/config/...": ConnectError(...)}.
        # Same fragment-matching rule as rest_responses, and checked first:
        # a request the transport itself could not complete never reaches
        # Home Assistant, so there is no status code to distinguish it by.
        self.rest_raises = {}
        # /api/history/period/<start>: {entity_id: [state, state, ...]}.
        # Home Assistant's real shape is a list of lists, one inner list per
        # requested entity; an entity absent here means "no data", which is
        # reported as a top-level [] rather than an empty inner list, the
        # same way Home Assistant omits entities with nothing to show.
        self.history = {}
        # The window is applied the way Home Assistant applies it
        # (homeassistant/components/history/__init__.py): rows with
        # start < last_changed <= end, where end is the `end_time` query
        # parameter or - when it is absent - start + 1 day, NOT now. That
        # default is the reason get_history() once returned only the first
        # day of any window longer than 24 h. history_oldest models
        # recorder retention: rows older than it are gone. Rows without a
        # last_changed are always returned (fixtures that predate windows).
        self.history_oldest = None
        # /api/logbook/<start>: a flat list of event dicts, optionally
        # filtered server-side by the `entity` query param.
        self.logbook = []
        # /api/template: list_areas() posts a Jinja template that resolves
        # to [{"area_id": ..., "entities": [...]}] per area; empty by
        # default (no area has any entity), overridable per test.
        self.template_response = []
        # /api/calendars and /api/calendars/<entity_id>: calendar entities
        # and, per entity_id, the events on it. Both empty by default - a
        # test simulates "calendar integration not loaded" with
        # fail_rest("/api/calendars", 404), the way Home Assistant itself
        # 404s that path rather than answering with an empty list.
        self.calendars = []
        self.calendar_events = {}
        # Per-entity queue of state overrides for GET /api/states/<entity_id>:
        # each read consumes the next item, and the last one repeats once the
        # queue is down to one - models an entity whose read-back only comes
        # to reflect a service call's effect after a retry (observe_actuation
        # in tools/_base.py), as opposed to self.states, which answers every
        # read for that entity_id identically within one test.
        self.state_sequences = {}
        # GET /api/states/<entity_id>: entity_ids here answer 404 for the
        # next N reads, then fall through to the normal answer - models the
        # gap between a config write (e.g. creating an automation) returning
        # and Home Assistant's entity platform actually registering the
        # entity. {entity_id: remaining_404_reads}. See delay_registration().
        self.registration_delay = {}
        # /api/config/automation/config/<id> and /api/config/script/config/<id>:
        # id -> the payload last POSTed there, keyed exactly like Home
        # Assistant's own config-storage API. A POST here also creates or
        # updates a matching row in self.states, the way a real create
        # immediately registers an entity (verified live) - automations
        # default to "on" (armed) and scripts to "off" (idle) ONLY for an
        # id not already in the store; re-POSTing an existing id (an
        # update) preserves whatever state it already had instead (also
        # verified live - see the POST handler below for the reasoning).
        # Seeded with DEFAULT_AUTOMATION_CONFIGS above, matching the id
        # already on automation.nas_shutdown in DEFAULT_STATES;
        # script_configs has no such default seed.
        self.automation_configs = copy.deepcopy(DEFAULT_AUTOMATION_CONFIGS)
        self.script_configs = {}
        # Config entries and the template options flow. options_flows maps
        # an open flow_id to its entry_id: a flow stays here until it is
        # either submitted successfully or aborted with DELETE, exactly as
        # Home Assistant keeps it in memory (measured: a 400 on submit
        # leaves the flow open, a DELETE afterwards answers 200).
        self.config_entries = [dict(e) for e in DEFAULT_CONFIG_ENTRIES]
        self.template_options = copy.deepcopy(DEFAULT_TEMPLATE_OPTIONS)
        self.options_flows = {}
        self._next_flow = 0
        # Test hooks for the options-flow submit: an exception to raise
        # instead of answering (a dropped connection mid-write), and a
        # switch that accepts a submit without storing it (a write that
        # does not land, for read-back verification).
        self.options_submit_raises = None
        self.options_submit_discarded = False
        # Category registry, per scope: {scope: [entry, ...]} where entry is
        # {category_id, name, icon, created_at, modified_at} - Home
        # Assistant's own _entry_dict shape, scope not included. Empty by
        # default. Entity categories live on the registry rows themselves
        # ("categories": {scope: category_id}).
        self.categories = {}
        self._next_category = 0
        # Label ids the label registry knows: config/entity_registry/update
        # drops any other id it is sent.
        self.known_labels = {"power"}
        # manifest/list: what tools/categories.py reads to tell a helper
        # integration (integration_type "helper") from any other.
        self.manifests = [
            {"domain": "template", "integration_type": "helper"},
            {"domain": "utility_meter", "integration_type": "helper"},
            {"domain": "hue", "integration_type": "hub"},
        ]
        # Lovelace. lovelace: {url_path: config | None}, "" = the default
        # dashboard; None = never saved from the UI (Home Assistant answers
        # config_not_found "No config found."), absent = no such dashboard.
        # dashboards: lovelace/dashboards/list rows ({url_path, mode, ...});
        # a "yaml"-mode dashboard refuses saves with "Not supported", as
        # LovelaceYAML does. Saving is last-write-wins, no version check -
        # lovelace/dashboard.py LovelaceStorage.async_save.
        # lovelace_mutate_on_read: {n: callable(config)} run on the stored
        # config just before answering the n-th lovelace/config read (1-based)
        # - a concurrent writer landing between a tool's reads.
        # lovelace_save_discarded: accept saves without storing them.
        self.lovelace = {"": None}
        self.dashboards = []
        self.lovelace_reads = 0
        self.lovelace_mutate_on_read = {}
        self.lovelace_save_discarded = False
        # Supervisor GET /core/logs: the journal as text/plain. `lines=N`
        # returns the last N lines (supervisor/api/host.py); `no_colors`
        # strips ANSI codes, which are otherwise present - so the fake puts
        # an ANSI code in every line when it is NOT sent.
        self.core_log = ""
        # system_log/list: newest first, Home Assistant's to_list() shape.
        self.system_log = []
        # Everything the tools sent, for assertions.
        self.rest_calls = []
        self.ws_calls = []

    # ---- REST ----------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.rest_calls.append(request)
        path = request.url.path
        for fragment, exc in self.rest_raises.items():
            if fragment in path:
                raise exc
        for fragment, (status, body) in self.rest_responses.items():
            if fragment in path:
                return httpx.Response(status, json=body)
        if path == "/api/states":
            return httpx.Response(200, json=self.states)
        if path.startswith("/api/states/"):
            wanted = path.removeprefix("/api/states/")
            if self.registration_delay.get(wanted, 0) > 0:
                self.registration_delay[wanted] -= 1
                return httpx.Response(404, json={"message": "Entity not found."})
            if wanted in self.state_sequences:
                seq = self.state_sequences[wanted]
                state = seq.pop(0) if len(seq) > 1 else seq[0]
                return httpx.Response(200, json=state)
            for s in self.states:
                if s["entity_id"] == wanted:
                    return httpx.Response(200, json=s)
            return httpx.Response(404, json={"message": "Entity not found."})
        if path == "/api/config":
            return httpx.Response(200, json=self.config)
        for domain, store, default_state in (
            ("automation", self.automation_configs, "on"),
            ("script", self.script_configs, "off"),
        ):
            prefix = f"/api/config/{domain}/config/"
            if path.startswith(prefix):
                item_id = path.removeprefix(prefix)
                entity_id = f"{domain}.{item_id}"
                if request.method == "POST":
                    body = json.loads(request.content or b"{}")
                    is_new = item_id not in store
                    existing = next(
                        (s for s in self.states if s["entity_id"] == entity_id), None)
                    # Stored verbatim, whatever vocabulary `body` is
                    # actually in - deliberately NOT a model of the real
                    # config-write endpoint's own rename-on-save (root
                    # keys and an action step's service: get renamed to
                    # the modern spelling on every save, regardless of
                    # what was posted; only a trigger step's platform:
                    # survives as sent - see tools/_aliases.py's module
                    # docstring for the live measurement this describes).
                    # No test in this suite exercises that renaming; it
                    # was verified separately against a live instance, and
                    # get_automation()/update_automation()/
                    # patch_automation()'s own docstrings say so - a fake
                    # that silently re-normalised on every write would
                    # make `stored_format` untestable as "what was
                    # actually read", since every POST would immediately
                    # make the next GET look modern again.
                    store[item_id] = body
                    # A genuinely new automation registers armed
                    # (default_state) - measured live for creation. But
                    # re-POSTing an EXISTING one (an update) does not reset
                    # its armed/disarmed state: measured live, an
                    # automation turned off, then updated with an
                    # unrelated field change (alias only, enabled
                    # untouched), read back "off" afterward, not reset to
                    # "on". update_automation() relies on this: an edit
                    # must not silently re-arm what was deliberately
                    # disabled.
                    state = (existing["state"] if existing and not is_new
                            else default_state)
                    row = {"entity_id": entity_id, "state": state,
                           "attributes": {"friendly_name": body.get("alias", item_id),
                                         "id": item_id}}
                    for i, s in enumerate(self.states):
                        if s["entity_id"] == entity_id:
                            self.states[i] = row
                            break
                    else:
                        self.states.append(row)
                    return httpx.Response(200, json={"result": "ok"})
                if request.method == "GET":
                    if item_id in store:
                        return httpx.Response(200, json={"id": item_id, **store[item_id]})
                    return httpx.Response(404, json={"message": "Resource not found"})
                if request.method == "DELETE":
                    # Home Assistant answers 400, not 404, for a config id
                    # it does not have - measured live, see delete_automation().
                    if item_id in store:
                        del store[item_id]
                        # Matches by entity_id (an automation this fake
                        # itself created via POST, where slug == config id)
                        # or by the `id` attribute (a UI-style automation a
                        # test seeded directly into self.states, where the
                        # config id differs from the entity_id's own slug -
                        # see delete_automation()'s numeric-id resolution).
                        self.states = [
                            s for s in self.states
                            if s["entity_id"] != entity_id
                            and s.get("attributes", {}).get("id") != item_id
                        ]
                        return httpx.Response(200, json={"result": "ok"})
                    return httpx.Response(400, json={"message": "Resource not found"})
        if path == "/api/config/config_entries/entry" and request.method == "GET":
            domain = request.url.params.get("domain")
            return httpx.Response(200, json=[
                e for e in self.config_entries if not domain or e["domain"] == domain])
        if path.startswith("/api/config/config_entries/options/flow"):
            return self._options_flow(request, path)
        if path.startswith("/api/services/"):
            return httpx.Response(200, json=[])
        if path == "/core/logs":
            lines = self.core_log.splitlines()
            n = int(request.url.params.get("lines", "100"))
            lines = lines[-n:]
            if "no_colors" not in request.url.params:
                lines = [f"\x1b[32m{line}\x1b[0m" for line in lines]
            return httpx.Response(200, text="\n".join(lines),
                                  headers={"content-type": "text/plain"})
        if path.startswith("/api/history/period/"):
            return self._history(request, path)
        if path.startswith("/api/logbook/"):
            entity = request.url.params.get("entity", "")
            entries = self.logbook
            if entity:
                entries = [e for e in entries if e.get("entity_id") == entity]
            return httpx.Response(200, json=entries)
        if path == "/api/template":
            return httpx.Response(200, json=self.template_response)
        if path == "/api/calendars":
            return httpx.Response(200, json=self.calendars)
        if path.startswith("/api/calendars/"):
            entity_id = path.removeprefix("/api/calendars/")
            return httpx.Response(200, json=self.calendar_events.get(entity_id, []))
        return httpx.Response(404, json={"message": f"No fake route for {path}"})

    # ---- template options flow ---------------------------------------------
    def _options_form(self, flow_id: str, entry_id: str) -> dict:
        """The form the template options flow opens with: straight on the
        entry's own type (no menu, unlike the config flow), each field that
        has a stored value carrying it as description.suggested_value."""
        stored = self.template_options[entry_id]
        options = stored["options"]

        def field(name, required, values):
            f = {"selector": {"template": {}}, "name": name, "required": required}
            if not required:
                f["optional"] = True
            if name in values:
                f["description"] = {"suggested_value": values[name]}
            return f

        schema = []
        for name, spec in TEMPLATE_OPTION_FIELDS[stored["template_type"]]:
            if isinstance(spec, list):
                section = options.get(name, {})
                schema.append({"type": "expandable", "name": name, "expanded": False,
                               "required": False, "optional": True,
                               "schema": [field(n, r, section) for n, r in spec]})
            else:
                schema.append(field(name, spec, options))
        return {"type": "form", "flow_id": flow_id, "handler": entry_id,
                "data_schema": schema, "errors": None,
                "description_placeholders": None, "last_step": True,
                "preview": "template", "step_id": stored["template_type"]}

    def _options_flow(self, request: httpx.Request, path: str) -> httpx.Response:
        base = "/api/config/config_entries/options/flow"
        if path == base and request.method == "POST":
            handler = json.loads(request.content or b"{}").get("handler")
            entry = next((e for e in self.config_entries if e["entry_id"] == handler), None)
            if entry is None:
                # Measured: an unknown entry_id is not a 404 but a 500
                # with a plain-text body.
                return httpx.Response(
                    500, text="500 Internal Server Error\n\nServer got itself in trouble")
            if entry["entry_id"] not in self.template_options:
                if entry.get("supports_options"):
                    return httpx.Response(200, json={"type": "form", "flow_id": "other",
                                                     "handler": handler, "data_schema": [],
                                                     "step_id": "init"})
                return httpx.Response(404, json={"message": "Invalid handler specified"})
            self._next_flow += 1
            flow_id = f"optflow{self._next_flow}"
            self.options_flows[flow_id] = handler
            return httpx.Response(200, json=self._options_form(flow_id, handler))

        flow_id = path.removeprefix(base + "/")
        if flow_id not in self.options_flows:
            return httpx.Response(404, json={"message": "Invalid flow specified"})
        entry_id = self.options_flows[flow_id]
        if request.method == "DELETE":
            del self.options_flows[flow_id]
            return httpx.Response(200, json={"message": "Flow aborted"})
        if request.method == "GET":
            return httpx.Response(200, json=self._options_form(flow_id, entry_id))
        if request.method != "POST":
            return httpx.Response(405, text="405: Method Not Allowed")

        if self.options_submit_raises is not None:
            raise self.options_submit_raises
        body = json.loads(request.content or b"{}")
        stored = self.template_options[entry_id]
        spec = dict(TEMPLATE_OPTION_FIELDS[stored["template_type"]])
        for key, value in body.items():
            if key not in spec:
                return httpx.Response(
                    400, json={"errors": {"base": [f"not a valid option at '{key}'"]}})
            allowed = ({n for n, _ in spec[key]} if isinstance(spec[key], list) else None)
            if allowed is not None and set(value) - allowed:
                bad = sorted(set(value) - allowed)[0]
                return httpx.Response(
                    400, json={"errors": {"base": [f"not a valid option at '{key}.{bad}'"]}})
        for key, required in spec.items():
            if required is True and not body.get(key):
                return httpx.Response(400, json={"errors": {key: "required key not provided"}})
        for key, value in body.items():
            if isinstance(value, str) and value.count("{{") != value.count("}}"):
                return httpx.Response(400, json={"errors": {key: (
                    "invalid template (TemplateSyntaxError: unexpected end of "
                    "template, expected 'end of print statement'.)")}})
        section = body.get("additional_options", {})
        for key in ("delay_on", "delay_off"):
            # Measured: a duration selector takes {"hours", "minutes",
            # "seconds"}, and answers a "00:00:05" string with this.
            if key in section and not isinstance(section[key], dict):
                return httpx.Response(400, json={"errors": {"base": [
                    f"expected dict at 'additional_options.{key}'"]}})
        if "state_class" in body and body["state_class"] not in _STATE_CLASSES:
            return httpx.Response(400, json={"errors": {
                "state_class": f"value must be one of {_STATE_CLASSES}"}})

        # Measured: the submitted form REPLACES the stored options - a
        # field left out of the payload is gone afterwards, not kept.
        del self.options_flows[flow_id]
        if not self.options_submit_discarded:
            stored["options"] = body
        return httpx.Response(200, json={"type": "create_entry", "flow_id": flow_id,
                                         "handler": entry_id, "description": None,
                                         "description_placeholders": None})

    def _history(self, request: httpx.Request, path: str) -> httpx.Response:
        raw_start = urllib.parse.unquote(path.removeprefix("/api/history/period/"))
        start = datetime.fromisoformat(raw_start)
        raw_end = request.url.params.get("end_time")
        end = datetime.fromisoformat(raw_end) if raw_end else start + timedelta(days=1)
        entity_id = request.url.params.get("filter_entity_id", "")
        points = []
        for p in self.history.get(entity_id) or []:
            changed = p.get("last_changed")
            if changed:
                when = datetime.fromisoformat(changed)
                if self.history_oldest and when < self.history_oldest:
                    continue
                if not (start < when <= end):
                    continue
            points.append(p)
        return httpx.Response(200, json=[points] if points else [])

    # ---- WebSocket -----------------------------------------------------
    def ws(self, msg: dict) -> dict:
        self.ws_calls.append(msg)
        kind = msg.get("type")
        if kind in self.ws_responses:
            return self.ws_responses[kind]
        if kind == "config/entity_registry/list":
            return {"id": 1, "type": "result", "success": True,
                    "result": self.registry}
        if kind == "config/entity_registry/get":
            entity_id = msg.get("entity_id")
            for entity in self.registry:
                if entity.get("entity_id") == entity_id:
                    return {"id": 1, "type": "result", "success": True,
                            "result": entity}
            return {"id": 1, "type": "result", "success": False,
                    "error": {"code": "not_found",
                              "message": f"Entity not found: {entity_id}"}}
        if kind.startswith("config/category_registry/"):
            return self._category_registry(kind.rsplit("/", 1)[1], msg)
        if kind == "config/entity_registry/update":
            return self._entity_registry_update(msg)
        if kind == "lovelace/config":
            return self._lovelace_read(msg)
        if kind == "lovelace/config/save":
            return self._lovelace_save(msg)
        if kind == "lovelace/dashboards/list":
            return self._ws_ok(copy.deepcopy(self.dashboards))
        if kind == "system_log/list":
            return self._ws_ok(copy.deepcopy(self.system_log))
        if kind == "manifest/list":
            return {"id": 1, "type": "result", "success": True, "result": self.manifests}
        if kind == "config/device_registry/list":
            return {"id": 1, "type": "result", "success": True,
                    "result": self.devices}
        return {"id": 1, "type": "result", "success": False,
                "error": {"code": "unknown_command",
                          "message": f"No fake response for {kind}"}}

    @staticmethod
    def _ws_ok(result):
        return {"id": 1, "type": "result", "success": True, "result": result}

    @staticmethod
    def _ws_fail(code, message):
        return {"id": 1, "type": "result", "success": False,
                "error": {"code": code, "message": message}}

    def _category_registry(self, action: str, msg: dict) -> dict:
        scope = msg.get("scope", "")
        entries = self.categories.setdefault(scope, [])
        if action == "list":
            return self._ws_ok([dict(e) for e in entries])
        if action == "create":
            name = msg["name"]
            if any(e["name"].casefold() == name.casefold() for e in entries):
                return self._ws_fail("invalid_info", f"The name '{name}' is already in use")
            self._next_category += 1
            entry = {"category_id": f"cat{self._next_category:04d}", "name": name,
                     "icon": msg.get("icon"), "created_at": 0.0, "modified_at": 0.0}
            entries.append(entry)
            return self._ws_ok(dict(entry))
        entry = next((e for e in entries if e["category_id"] == msg.get("category_id")), None)
        if entry is None:
            return self._ws_fail("invalid_info", "Category ID doesn't exist")
        if action == "update":
            if "name" in msg:
                name = msg["name"]
                if any(e is not entry and e["name"].casefold() == name.casefold()
                       for e in entries):
                    return self._ws_fail("invalid_info", f"The name '{name}' is already in use")
                entry["name"] = name
            if "icon" in msg:
                entry["icon"] = msg["icon"]
            return self._ws_ok(dict(entry))
        if action == "delete":
            entries.remove(entry)
            for row in self.registry:
                if row.get("categories", {}).get(scope) == entry["category_id"]:
                    del row["categories"][scope]
            return self._ws_ok(None)
        return self._ws_fail("unknown_command", f"No fake response for {action}")

    def _lovelace_read(self, msg: dict) -> dict:
        url_path = msg.get("url_path") or ""
        if url_path not in self.lovelace:
            return self._ws_fail("config_not_found", f"Unknown config specified: {url_path}")
        self.lovelace_reads += 1
        hook = self.lovelace_mutate_on_read.get(self.lovelace_reads)
        if hook and self.lovelace[url_path] is not None:
            hook(self.lovelace[url_path])
        if self.lovelace[url_path] is None:
            return self._ws_fail("config_not_found", "No config found.")
        return self._ws_ok(copy.deepcopy(self.lovelace[url_path]))

    def _lovelace_save(self, msg: dict) -> dict:
        url_path = msg.get("url_path") or ""
        if url_path not in self.lovelace:
            return self._ws_fail("config_not_found", f"Unknown config specified: {url_path}")
        mode = next((d.get("mode") for d in self.dashboards
                     if d.get("url_path") == url_path), "storage")
        if mode == "yaml":
            return self._ws_fail("error", "Not supported")
        if not self.lovelace_save_discarded:
            self.lovelace[url_path] = copy.deepcopy(msg["config"])
        return self._ws_ok(None)

    def _entity_registry_update(self, msg: dict) -> dict:
        row = next((e for e in self.registry if e.get("entity_id") == msg.get("entity_id")), None)
        if row is None:
            return self._ws_fail("not_found", f"Entity not found: {msg.get('entity_id')}")
        if "labels" in msg:
            # Unknown label ids are dropped, as Home Assistant does since
            # core PR #173438 (lr.async_get_missing_label_ids).
            row["labels"] = [lab for lab in msg["labels"] if lab in self.known_labels]
        if "categories" in msg:
            # Merged per scope, None removes, ids never validated - the
            # handler in homeassistant/components/config/entity_registry.py.
            categories = dict(row.get("categories", {}))
            for scope, category_id in msg["categories"].items():
                if scope in categories and category_id is None:
                    del categories[scope]
                elif category_id is not None:
                    categories[scope] = category_id
            row["categories"] = categories
        return self._ws_ok({"entity_entry": dict(row)})

    def ws_multi(self, msgs: list) -> list:
        """Answer a batch of WebSocket commands the same way `ws` answers one.

        Real `_ws_multi` sends every message over a single connection; the
        fake has no connection to share, so it just routes each message
        through `ws` in order. Tools that use `_ws_multi` (list_areas,
        get_statistics_summary, bulk_set_entity_labels) call it directly
        rather than through `_ws`, so it needs its own patch point — see
        conftest.py.
        """
        return [self.ws(msg) for msg in msgs]

    # ---- helpers for tests -----------------------------------------------
    def fail_ws(self, kind: str, code="not_found", message="nope"):
        """Make one WebSocket command fail, the way Home Assistant does."""
        self.ws_responses[kind] = {"id": 1, "type": "result", "success": False,
                                   "error": {"code": code, "message": message}}

    def ws_result(self, kind: str, payload):
        self.ws_responses[kind] = {"id": 1, "type": "result", "success": True,
                                   "result": payload}

    def fail_ws_transport(self, kind: str,
                          message: str = "Auth failed: {'type': 'auth_invalid'}"):
        """Make one WebSocket command fail the way `_ws()` itself does when
        the connection or authentication fails, not the way Home Assistant
        answers a rejected command.

        A rejected command gets a `{"id", "type", "success": False, "error":
        {...}}` frame — see fail_ws(). A transport/auth failure never gets
        that far: `_ws_commands` returns a bare `{"error": "Auth failed:
        ..."}`, with no "success" key at all. A check written as
        `result.get("success", True)` reads that missing key as a default
        success, which is the bug this fixture exists to reproduce.
        """
        self.ws_responses[kind] = {"error": message}

    def fail_rest(self, path_fragment: str, status: int = 500,
                  message: str = "Internal Server Error"):
        """Make any REST call whose path contains `path_fragment` fail, the
        way Home Assistant does for a rejected service call, a bad auth
        token, or a broken integration."""
        self.rest_responses[path_fragment] = (status, {"message": message})

    def delay_registration(self, entity_id: str, reads: int):
        """Make GET /api/states/<entity_id> answer 404 for the next `reads`
        calls, then fall through to the normal answer.

        Models the gap between a config write (e.g. create_automation())
        returning and Home Assistant's entity platform actually registering
        the entity - the race create_automation()'s enabled=False path
        waits out before disabling it, and delete_automation()'s numeric-id
        resolution and toggle_automation() both also read across.
        """
        self.registration_delay[entity_id] = reads

    def sequence_states(self, entity_id: str, states: list):
        """Make successive GET /api/states/<entity_id> calls return `states`
        in order, repeating the last one once exhausted.

        For testing observe_actuation()'s retry: a service call whose
        read-back only comes to reflect the actuation on the second read
        (e.g. still "locking" on the first, "locked" on the second) -
        self.states alone cannot model that, since it answers identically
        on every read within a test.
        """
        self.state_sequences[entity_id] = list(states)

    def raise_rest(self, path_fragment: str, exc: Exception | None = None):
        """Make any REST call whose path contains `path_fragment` raise
        instead of returning a Response - a connection-level failure (the
        request never reaches Home Assistant), as opposed to fail_rest()
        which simulates a request that arrived and was rejected there.
        Defaults to httpx.ConnectError, the shape httpx raises for a
        refused connection."""
        self.rest_raises[path_fragment] = exc or httpx.ConnectError("Connection refused")
