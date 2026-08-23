# Imported here, at module scope, so it lands in sys.modules during test
# collection — before any fake_ha fixture has run. That is the exact case
# the sys.modules loop in conftest.py exists to handle: tools.system._ws is
# already bound to the real function by the time the fixture below patches
# tools._base._ws, and only the loop reaches back to repoint it.
from tools.system import list_repairs


def test_fake_ha_reaches_a_rest_tool(fake_ha):
    from tools.lights import list_lights

    result = list_lights()

    assert [row["entity_id"] for row in result] == ["light.kitchen", "light.study"]
    assert any(call.url.path == "/api/states" for call in fake_ha.rest_calls)


def test_fake_ha_reaches_a_ws_tool(fake_ha):
    from tools.areas import get_entity_labels

    fake_ha.ws_responses["config/entity_registry/get"] = {
        "id": 1, "type": "result", "success": True,
        "result": {"entity_id": "automation.nas_shutdown", "labels": ["power"]},
    }

    assert get_entity_labels("automation.nas_shutdown") == ["power"]


def test_fake_ha_reaches_a_ws_tool_imported_before_the_fixture_ran(fake_ha):
    """Proves the sys.modules loop, not just the direct-import path.

    tools.system was imported at module scope above, so its `_ws` was bound
    to the real function long before this fixture ran. If the loop in
    conftest.py were removed, this call would try to reach a real Home
    Assistant and fail — it can only pass because the loop repoints
    tools.system._ws at the fake.
    """
    fake_ha.ws_result("repairs/list_issues", {
        "issues": [
            {"issue_id": "low_battery", "domain": "sensor", "severity": "warning",
             "translation_key": "low_battery", "created": "2026-08-22T00:00:00+00:00"},
        ],
    })

    result = list_repairs()

    assert [row["issue_id"] for row in result] == ["low_battery"]
    assert any(call.get("type") == "repairs/list_issues" for call in fake_ha.ws_calls)
