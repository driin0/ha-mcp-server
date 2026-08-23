"""A fake Home Assistant, in process.

Tools reach Home Assistant two ways: httpx for the REST API and `_ws` for the
WebSocket API. This module answers both from the same in-memory dataset, so a
test can say "the registry contains X" once and have every tool see it.
"""
import copy
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
        if path.startswith("/api/services/"):
            return httpx.Response(200, json=[])
        if path.startswith("/api/history/period/"):
            entity_id = request.url.params.get("filter_entity_id", "")
            points = self.history.get(entity_id)
            return httpx.Response(200, json=[points] if points else [])
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
        if kind == "config/device_registry/list":
            return {"id": 1, "type": "result", "success": True,
                    "result": self.devices}
        return {"id": 1, "type": "result", "success": False,
                "error": {"code": "unknown_command",
                          "message": f"No fake response for {kind}"}}

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
