"""A fake Home Assistant, in process.

Tools reach Home Assistant two ways: httpx for the REST API and `_ws` for the
WebSocket API. This module answers both from the same in-memory dataset, so a
test can say "the registry contains X" once and have every tool see it.
"""
import httpx

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
    {"entity_id": "light.kitchen", "area_id": "kitchen", "labels": []},
    {"entity_id": "light.study", "area_id": "study", "labels": []},
    {"entity_id": "automation.nas_shutdown", "area_id": None, "labels": ["power"]},
    {"entity_id": "automation.morning", "area_id": None, "labels": []},
]


class FakeHA:
    """Answers REST and WebSocket calls from one dataset."""

    def __init__(self):
        self.states = [dict(s) for s in DEFAULT_STATES]
        self.registry = [dict(e) for e in DEFAULT_REGISTRY]
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
            for s in self.states:
                if s["entity_id"] == wanted:
                    return httpx.Response(200, json=s)
            return httpx.Response(404, json={"message": "Entity not found."})
        if path == "/api/config":
            return httpx.Response(200, json=self.config)
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

    def fail_rest(self, path_fragment: str, status: int = 500,
                  message: str = "Internal Server Error"):
        """Make any REST call whose path contains `path_fragment` fail, the
        way Home Assistant does for a rejected service call, a bad auth
        token, or a broken integration."""
        self.rest_responses[path_fragment] = (status, {"message": message})

    def raise_rest(self, path_fragment: str, exc: Exception | None = None):
        """Make any REST call whose path contains `path_fragment` raise
        instead of returning a Response - a connection-level failure (the
        request never reaches Home Assistant), as opposed to fail_rest()
        which simulates a request that arrived and was rejected there.
        Defaults to httpx.ConnectError, the shape httpx raises for a
        refused connection."""
        self.rest_raises[path_fragment] = exc or httpx.ConnectError("Connection refused")
