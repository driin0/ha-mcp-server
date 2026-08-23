"""MCP_ALLOW_NO_AUTH and UI_ALLOW_NO_AUTH are two separate decisions, and
UI_SECRET is its own secret rather than a fallback to MCP_SECRET.

Security review finding I3: MCP_ALLOW_NO_AUTH only suppresses the startup
RuntimeError about the MCP endpoint (tools/_base.py), which is what it is
documented to do - but it USED to also silently satisfy web.py's own,
separate guard for the status dashboard's Basic Auth, with nothing saying
so. Someone who set it to get past the MCP-side startup message lost the
dashboard's authentication too.

Finding I4: UI_SECRET used to default to MCP_SECRET when unset, so the
dashboard's Basic Auth password WAS the full-admin MCP bearer token, sent
unencrypted (this project ships no TLS) - a passive LAN observer who saw
one dashboard page load recovered it. UI_SECRET no longer falls back to
MCP_SECRET at all: it is either set to its own value, or the dashboard
needs HA_INGRESS_MODE or UI_ALLOW_NO_AUTH like any other case of it being
unset.

Both tools/_base.py's and web.py's guards run at import time (module-level
code, not inside a function), so - like test_registration_gate.py's
apply_registration_gate() tests - the only way to observe the *actual*
effect is to import the module fresh in a subprocess with a controlled
environment, not to read the source and assume.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_ENV = {
    "HA_URL": "http://fake-ha.test:8123",
    "HA_TOKEN": "fake-token",
}


def _import_web(extra_env: dict) -> subprocess.CompletedProcess:
    """Import web.py fresh in a subprocess with `extra_env` layered onto a
    minimal working environment, and report what happened - the actual,
    end-to-end effect of its module-level auth guard, not just a reading of
    the source."""
    env = dict(_BASE_ENV)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", "import web"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_mcp_allow_no_auth_does_not_also_unlock_the_dashboard():
    """The exact regression this fix closes: setting MCP_ALLOW_NO_AUTH to
    get past the MCP endpoint's own startup error must NOT also silently
    disable the dashboard's Basic Auth. Before the fix, this combination
    imported web.py without error and left the dashboard unauthenticated -
    the bug I3 describes."""
    result = _import_web({
        "MCP_SECRET": "",
        "MCP_ALLOW_NO_AUTH": "true",
        "UI_SECRET": "",
        # UI_ALLOW_NO_AUTH deliberately unset
    })

    assert result.returncode != 0, (
        "web.py imported cleanly with no UI authentication configured - "
        "MCP_ALLOW_NO_AUTH must not satisfy the dashboard's own guard"
    )
    assert "RuntimeError" in result.stderr
    assert "UI_SECRET" in result.stderr


def test_ui_allow_no_auth_is_the_separate_opt_in_for_the_dashboard():
    result = _import_web({
        "MCP_SECRET": "",
        "MCP_ALLOW_NO_AUTH": "true",
        "UI_SECRET": "",
        "UI_ALLOW_NO_AUTH": "true",
    })

    assert result.returncode == 0, result.stderr


def test_ui_secret_set_needs_no_opt_in():
    result = _import_web({
        "MCP_SECRET": "",
        "MCP_ALLOW_NO_AUTH": "true",
        "UI_SECRET": "a-strong-dashboard-secret",
    })

    assert result.returncode == 0, result.stderr


def test_ui_secret_no_longer_falls_back_to_mcp_secret():
    """The exact regression I4's fix closes: MCP_SECRET being set must NOT,
    on its own, give the dashboard anything to authenticate against -
    UI_SECRET is read as its own, independent value."""
    env = dict(_BASE_ENV)
    env.update({"MCP_SECRET": "a-strong-mcp-secret", "UI_SECRET": ""})
    result = subprocess.run(
        [sys.executable, "-c",
         "from tools._base import UI_SECRET; assert UI_SECRET == '', repr(UI_SECRET)"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_mcp_secret_alone_no_longer_satisfies_the_dashboards_guard():
    """Before I4's fix, MCP_SECRET being set was enough for the dashboard
    too, via the UI_SECRET fallback - this combination imported web.py
    cleanly with no UI_SECRET, no UI_ALLOW_NO_AUTH, and no ingress mode.
    After the fix it must refuse to start, the same as any other case of
    UI_SECRET being genuinely unset."""
    result = _import_web({
        "MCP_SECRET": "a-strong-mcp-secret",
        "UI_SECRET": "",
        # UI_ALLOW_NO_AUTH deliberately unset
    })

    assert result.returncode != 0, (
        "web.py imported cleanly with MCP_SECRET set but no UI_SECRET - "
        "UI_SECRET must not fall back to MCP_SECRET"
    )
    assert "RuntimeError" in result.stderr
    assert "UI_SECRET" in result.stderr


def test_ha_ingress_mode_needs_no_opt_in_either():
    result = _import_web({
        "MCP_SECRET": "",
        "MCP_ALLOW_NO_AUTH": "true",
        "UI_SECRET": "",
        "HA_INGRESS_MODE": "true",
    })

    assert result.returncode == 0, result.stderr


def test_mcp_endpoint_startup_message_names_the_dashboard_as_unaffected():
    """tools/_base.py's own RuntimeError - importing it directly, since
    web.py never even gets a chance to run when this one fires first."""
    env = dict(_BASE_ENV)
    env.update({"MCP_SECRET": "", "MCP_ALLOW_NO_AUTH": ""})
    result = subprocess.run(
        [sys.executable, "-c", "import tools._base"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "MCP endpoint" in result.stderr
    assert "dashboard" in result.stderr
