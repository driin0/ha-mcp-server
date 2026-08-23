"""The env-gated registration filter documented in tools/_base.py.

Two layers are tested separately. disabled_tool_names()/list_disabled_tools()
are pure functions of os.environ - no side effect on the live tool registry -
so they are tested in-process, freely, without touching the global `mcp`
singleton every other test in this suite shares. apply_registration_gate()
DOES mutate that singleton (it removes tools from it), so its effect is
tested by actually importing server.py in a subprocess with a controlled
environment: mutating the shared `mcp` in this process would leak into every
test that runs after it in the same pytest session, since ToolManager has no
"add it back" beyond re-running the @mcp.tool() decorator.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_ENV = {
    "HA_URL": "http://fake-ha.test:8123",
    "HA_TOKEN": "fake-token",
    "MCP_ALLOW_NO_AUTH": "true",
}


def test_user_management_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MCP_ENABLE_USER_MANAGEMENT", raising=False)
    from tools._base import disabled_tool_names

    assert disabled_tool_names() >= {"create_user", "update_user", "delete_user"}


def test_user_management_enabled_when_flag_is_true(monkeypatch):
    monkeypatch.setenv("MCP_ENABLE_USER_MANAGEMENT", "true")
    from tools._base import disabled_tool_names

    assert disabled_tool_names().isdisjoint({"create_user", "update_user", "delete_user"})


def test_user_management_enabled_accepts_1_and_yes(monkeypatch):
    from tools._base import disabled_tool_names

    for truthy in ("1", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("MCP_ENABLE_USER_MANAGEMENT", truthy)
        assert disabled_tool_names().isdisjoint({"create_user", "update_user", "delete_user"}), (
            f"{truthy!r} should enable the group"
        )


def test_user_management_disabled_by_any_other_value(monkeypatch):
    from tools._base import disabled_tool_names

    for falsy in ("false", "0", "no", "garbage"):
        monkeypatch.setenv("MCP_ENABLE_USER_MANAGEMENT", falsy)
        assert disabled_tool_names() >= {"create_user", "update_user", "delete_user"}


def test_list_disabled_tools_reports_the_gated_group(monkeypatch):
    monkeypatch.delenv("MCP_ENABLE_USER_MANAGEMENT", raising=False)
    from tools.system import list_config_entries  # noqa: F401 - ensure tools import cleanly first
    from tools._base import list_disabled_tools

    result = list_disabled_tools()

    assert "error" not in result
    groups = {g["group"]: g for g in result["groups"]}
    assert "user_management" in groups
    um = groups["user_management"]
    assert um["enabled"] is False
    assert set(um["tools"]) == {"create_user", "update_user", "delete_user"}
    assert um["env"] == "MCP_ENABLE_USER_MANAGEMENT"
    assert um["reason"]  # non-empty, explains the tier


# ---- physical_security: lock_control, alarm_control - security review item 5 ------
#
# Unlike user_management, this group defaults ON: locks and the alarm panel
# are a normal reason to run this server, so disabling them by default would
# break the common case. It is still gated because they are the two tools a
# prompt-injection payload would most want to reach (see get_live_context()'s
# docstring). Gated using the exact same mechanism as user_management, so
# these tests mirror that group's tests one for one.

def test_physical_security_enabled_by_default(monkeypatch):
    monkeypatch.delenv("MCP_ENABLE_PHYSICAL_SECURITY", raising=False)
    from tools._base import disabled_tool_names

    assert "lock_control" not in disabled_tool_names()
    assert "alarm_control" not in disabled_tool_names()


def test_physical_security_disabled_when_flag_is_false(monkeypatch):
    monkeypatch.setenv("MCP_ENABLE_PHYSICAL_SECURITY", "false")
    from tools._base import disabled_tool_names

    assert disabled_tool_names() >= {"lock_control", "alarm_control"}


def test_physical_security_disabled_accepts_0_and_no(monkeypatch):
    from tools._base import disabled_tool_names

    for falsy in ("0", "no", "FALSE", "No"):
        monkeypatch.setenv("MCP_ENABLE_PHYSICAL_SECURITY", falsy)
        assert disabled_tool_names() >= {"lock_control", "alarm_control"}, (
            f"{falsy!r} should disable the group"
        )


def test_physical_security_explicitly_enabled_confirms_the_default(monkeypatch):
    from tools._base import disabled_tool_names

    for truthy in ("true", "1", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("MCP_ENABLE_PHYSICAL_SECURITY", truthy)
        assert "lock_control" not in disabled_tool_names()
        assert "alarm_control" not in disabled_tool_names()


def test_physical_security_unrecognized_value_fails_closed(monkeypatch):
    """_group_enabled() treats any non-empty value it does not recognize as
    truthy the same way as an explicit falsy one - a typo disables the
    group rather than silently keeping its default. For user_management
    (default off) that coincides with the default; for physical_security
    (default ON) it does not, so this is the one place the two groups
    actually behave differently and is worth its own test."""
    from tools._base import disabled_tool_names

    monkeypatch.setenv("MCP_ENABLE_PHYSICAL_SECURITY", "garbage")
    assert disabled_tool_names() >= {"lock_control", "alarm_control"}


def test_list_disabled_tools_reports_physical_security(monkeypatch):
    monkeypatch.setenv("MCP_ENABLE_PHYSICAL_SECURITY", "false")
    from tools.system import list_config_entries  # noqa: F401
    from tools._base import list_disabled_tools

    result = list_disabled_tools()

    groups = {g["group"]: g for g in result["groups"]}
    assert "physical_security" in groups
    ps = groups["physical_security"]
    assert ps["enabled"] is False
    assert set(ps["tools"]) == {"lock_control", "alarm_control"}
    assert ps["env"] == "MCP_ENABLE_PHYSICAL_SECURITY"
    assert ps["reason"]


def test_gate_keeps_physical_security_tools_by_default():
    names = _registered_tool_names({})

    assert "lock_control" in names
    assert "alarm_control" in names


def test_gate_removes_physical_security_tools_when_flag_is_false():
    names = _registered_tool_names({"MCP_ENABLE_PHYSICAL_SECURITY": "false"})

    assert "lock_control" not in names
    assert "alarm_control" not in names
    # call_service is deliberately NOT covered by this gate - see
    # GATED_TOOL_GROUPS["physical_security"]["reason"].
    assert "call_service" in names


# ---- addon_api: call_addon_api - security review item 5 ---------------------------
#
# Off by default, and a genuine capability removal when disabled: unlike
# physical_security, neither call_service nor fire_event can reach the
# Supervisor's per-add-on API endpoint at all.

def test_addon_api_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MCP_ENABLE_ADDON_API", raising=False)
    from tools._base import disabled_tool_names

    assert "call_addon_api" in disabled_tool_names()


def test_addon_api_enabled_when_flag_is_true(monkeypatch):
    monkeypatch.setenv("MCP_ENABLE_ADDON_API", "true")
    from tools._base import disabled_tool_names

    assert "call_addon_api" not in disabled_tool_names()


def test_gate_removes_addon_api_by_default():
    names = _registered_tool_names({})

    assert "call_addon_api" not in names


def test_gate_keeps_addon_api_when_flag_is_set():
    names = _registered_tool_names({"MCP_ENABLE_ADDON_API": "true"})

    assert "call_addon_api" in names


def _registered_tool_names(extra_env: dict) -> set[str]:
    """Import server.py in a fresh subprocess with `extra_env` layered onto
    a minimal working environment, and report which tool names ended up
    registered - the actual, end-to-end effect of apply_registration_gate(),
    not just what disabled_tool_names() says it should be.
    """
    env = dict(_BASE_ENV)
    env.update(extra_env)
    code = (
        "import server\n"
        "from tools._base import mcp\n"
        "print('\\n'.join(sorted(mcp._tool_manager._tools.keys())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"importing server.py failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return set(result.stdout.split())


def test_gate_removes_user_management_tools_by_default():
    names = _registered_tool_names({})

    assert "create_user" not in names
    assert "update_user" not in names
    assert "delete_user" not in names
    # Everything else - including other destructive tools - stays registered.
    assert "delete_automation" in names
    assert "lock_control" in names
    assert "list_disabled_tools" in names
    assert "list_users" in names  # read-only, never gated


def test_gate_keeps_user_management_tools_when_flag_is_set():
    names = _registered_tool_names({"MCP_ENABLE_USER_MANAGEMENT": "true"})

    assert "create_user" in names
    assert "update_user" in names
    assert "delete_user" in names
