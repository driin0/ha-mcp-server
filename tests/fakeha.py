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
        # Everything the tools sent, for assertions.
        self.rest_calls = []
        self.ws_calls = []

    # ---- REST ----------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.rest_calls.append(request)
        path = request.url.path
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

    # ---- helpers for tests -----------------------------------------------
    def fail_ws(self, kind: str, code="not_found", message="nope"):
        """Make one WebSocket command fail, the way Home Assistant does."""
        self.ws_responses[kind] = {"id": 1, "type": "result", "success": False,
                                   "error": {"code": code, "message": message}}

    def ws_result(self, kind: str, payload):
        self.ws_responses[kind] = {"id": 1, "type": "result", "success": True,
                                   "result": payload}
