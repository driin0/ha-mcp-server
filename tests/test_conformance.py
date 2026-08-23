"""Structural checks over every @mcp.tool() in the server.

A 54-file mechanical sweep raises one question a behavioural test cannot
answer: did I miss one. This walks the AST and answers it, and goes on
answering it for tools written later.
"""
import ast
import pathlib

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "tools"

# The three tools that legitimately return a string.
STRING_TOOLS = {"get_addon_logs", "get_error_log", "render_template"}

# Tools still returning a list. Shrinks to empty as the sweep proceeds; a
# second test fails if a name stays here after being converted.
NOT_YET_CONVERTED = {
    # automations.py
    "list_automations", "get_automation_trace", "list_blueprints",
    "list_device_triggers", "list_device_conditions", "list_device_actions",
    "list_schedules",
    # diagnostics.py
    "get_states_by_domain", "get_history", "get_logbook",
    "list_entities_by_integration", "get_entity_exposure", "search_entities",
    "call_service",
    # system.py
    "list_config_entries", "list_repairs", "list_backups", "list_updates",
    "list_config_flows",
    # areas.py
    "list_areas", "get_entity_labels", "list_zones",
    # multi-tool files
    "list_calendars", "get_calendar_events",
    "list_dashboards", "list_lovelace_resources",
    "list_hacs_repos", "search_hacs",
    "list_notify_services", "list_persistent_notifications",
    "get_energy", "list_sensors",
    "get_statistics", "get_statistics_summary",
    "list_todo_lists", "get_todo_items",
    # single-tool files
    "list_addons", "get_alarm_state", "list_alerts", "list_cameras",
    "list_climate", "list_covers", "list_fans", "list_groups", "list_helpers",
    "list_lights", "list_locks", "list_media_players", "list_persons",
    "list_scenes", "list_scripts", "list_switches", "list_tags", "list_users",
}


def _tools():
    """Yield (filename, FunctionDef) for every @mcp.tool() in tools/."""
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any("mcp.tool" in ast.unparse(d) for d in node.decorator_list):
                yield path.name, node


def _return_type(node) -> str:
    return ast.unparse(node.returns) if node.returns else "(none)"


def test_the_tool_census_does_not_silently_shrink():
    """A guard on the guard. A floor, not an equality: plans 2 and 3 add
    tools, and a test that has to be edited to stay true gets edited without
    being read."""
    assert len(list(_tools())) >= 182


def test_every_tool_returns_dict():
    offenders = [
        f"{fname}:{node.lineno} {node.name} -> {_return_type(node)}"
        for fname, node in _tools()
        if _return_type(node) != "dict"
        and node.name not in STRING_TOOLS
        and node.name not in NOT_YET_CONVERTED
    ]
    assert not offenders, (
        "These tools must return dict — wrap them with envelope() or error():\n"
        + "\n".join(offenders)
    )


def test_no_tool_returns_a_bare_list_literal():
    """`return [err]` makes a failure reachable by iterating results."""
    offenders = []
    for fname, node in _tools():
        if node.name in NOT_YET_CONVERTED:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.List):
                offenders.append(f"{fname}:{inner.lineno} in {node.name}")
    assert not offenders, (
        "Return envelope(...) or error(...), not a list literal:\n"
        + "\n".join(offenders)
    )


def test_the_allowlist_has_no_stale_entries():
    converted = {
        node.name for _, node in _tools() if _return_type(node) == "dict"
    }
    stale = NOT_YET_CONVERTED & converted
    assert not stale, (
        "Converted — remove from NOT_YET_CONVERTED:\n" + "\n".join(sorted(stale))
    )
