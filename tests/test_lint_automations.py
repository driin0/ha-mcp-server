"""scripts/lint_automations.py: the exit-code contract that module's own
docstring says is the one thing it commits to keeping honest.

No coverage existed for this script at all before this file. The two
scenarios below are the ones a scheduled job or CI run is most likely to
hit that are NOT "an automation is actually broken": an expired/revoked
HA_TOKEN, and an unreachable instance. Both used to surface as an uncaught
exception and exit code 1 - identical to "the sweep ran and found a real
problem" - instead of the documented exit code 2. Reproduced through
fake_ha exactly as it happens against a real instance: list_automations()
(tools/automations.py), which validate_all_automations() calls before
tools/validation.py's own live-snapshot read, has its own unguarded
`resp.raise_for_status()` on GET /api/states - so a 401 or a connection
failure there raises straight through validate_all_automations() and into
this script's main(), uncaught, unless main() itself catches it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
# scripts/ is a dev/CI-only tool, not part of the runtime image - the
# Dockerfile copies only server.py/web.py/stats.py and tools/, so
# `import lint_automations` fails when this file runs against the built
# image with only tests/ mounted in (the project's own verification
# command does exactly that). Skip cleanly there rather than failing the
# whole suite collection over a module this image was never meant to
# ship; a full repo checkout (where these tests normally run) always has
# scripts/ present.
lint_automations = pytest.importorskip(
    "lint_automations",
    reason="scripts/ is not copied into the production image (see Dockerfile)",
)


def test_expired_or_revoked_token_exits_2_not_1(fake_ha, capsys):
    fake_ha.fail_rest("/api/states", status=401, message="Unauthorized")

    exit_code = lint_automations.main([])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "401" in err
    assert "HA_TOKEN" in err


def test_unreachable_instance_exits_2_not_1(fake_ha, capsys):
    fake_ha.raise_rest("/api/states", exc=OSError("Connect call failed"))

    exit_code = lint_automations.main([])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "could not reach" in err.lower()


def test_a_connect_error_is_also_caught_not_only_raw_oserror(fake_ha, capsys):
    """httpx itself raises httpx.ConnectError (a subclass of
    httpx.RequestError, not of OSError) for a refused connection - the
    default raise_rest() shape. Both this and a raw OSError must exit 2."""
    fake_ha.raise_rest("/api/states")  # defaults to httpx.ConnectError

    exit_code = lint_automations.main([])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "could not reach" in err.lower()


def test_a_clean_sweep_exits_0(fake_ha, capsys):
    # DEFAULT_STATES/DEFAULT_AUTOMATION_CONFIGS (tests/fakeha.py) seed one
    # automation whose stale template reference and fail-open wait were
    # already fixed (continue_on_timeout: True is still fail-open, but its
    # target - button.nas_shutdown - resolves live in the fixture's own
    # default states, so nothing here is actually dead or destructive
    # enough to trip on for THIS test; only automation.morning is truly
    # clean). Limiting to that one keeps this a true "nothing wrong" case.
    fake_ha.states = [s for s in fake_ha.states if s["entity_id"] != "automation.nas_shutdown"]
    fake_ha.automation_configs.pop("1684270733500", None)

    exit_code = lint_automations.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "OK: no dead references, no fail-open waits." in out


def test_a_dead_reference_exits_1(fake_ha, capsys):
    # automation.nas_shutdown's own seeded config (tests/fakeha.py) guards
    # on a template naming button.nas_shutdown while nothing in
    # DEFAULT_STATES/DEFAULT_REGISTRY defines that entity - a genuine dead
    # reference by construction, left in place here rather than seeding a
    # new one.
    exit_code = lint_automations.main([])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL:" in out
