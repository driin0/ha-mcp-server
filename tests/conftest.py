"""Test configuration.

The os.environ block MUST run before anything imports tools._base, which
raises RuntimeError when HA_URL or HA_TOKEN is missing. conftest.py is
imported by pytest before the test modules, which is what makes this work.

HA_URL and HA_TOKEN are forced, not defaulted with setdefault(): a
maintainer with a real HA_URL exported in their shell (pointed at their
own Home Assistant) would otherwise keep it, and the conformance sweep in
test_conformance.py calls every zero-argument tool - including
restart_homeassistant and create_backup. It is safe today only because
httpx.Client, _ws and _ws_multi are all patched onto the in-process fake;
forcing these two values here is the second, independent guard, in case a
future tool ever reaches Home Assistant some other way (httpx.AsyncClient,
requests, a raw socket) that the fixture in this file does not patch.
MCP_SECRET is left as setdefault() - it never selects a real instance.
"""
import os

os.environ["HA_URL"] = "http://fake-ha.test:8123"
os.environ["HA_TOKEN"] = "fake-token"
os.environ.setdefault("MCP_SECRET", "fake-secret")

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from tests.fakeha import FakeHA  # noqa: E402


@pytest.fixture
def fake_ha(monkeypatch):
    """Route every REST and WebSocket call to an in-process fake."""
    state = FakeHA()
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(state.handle)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)

    # Tool modules do `from tools._base import _ws`, binding the function into
    # their own namespace. Patching tools._base._ws alone would not reach them.
    # _ws_multi gets the same treatment: it is not implemented in terms of
    # _ws (both are separate wrappers around _ws_commands), and a few tools
    # (list_areas, get_statistics_summary, bulk_set_entity_labels) call it
    # directly to batch several commands over one connection — patching only
    # _ws would leave those hitting the real network.
    import tools._base
    real_ws = tools._base._ws
    real_ws_multi = tools._base._ws_multi
    monkeypatch.setattr(tools._base, "_ws", state.ws)
    monkeypatch.setattr(tools._base, "_ws_multi", state.ws_multi)
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if name.startswith("tools."):
            if hasattr(module, "_ws"):
                monkeypatch.setattr(module, "_ws", state.ws, raising=False)
            if hasattr(module, "_ws_multi"):
                monkeypatch.setattr(module, "_ws_multi", state.ws_multi, raising=False)

    yield state

    # A module imported for the first time *during* a test (e.g. via an
    # import inside the test body) binds _ws/_ws_multi through its own
    # `from tools._base import ...`, not through monkeypatch.setattr — so
    # monkeypatch has no record of it and will not undo it. Left alone, that
    # module would keep pointing at this test's fake forever, and the loop
    # above in the *next* test would repoint it at the next fake instead of
    # the real function. Restore every tools.* module to the real functions
    # here so each test starts from a clean, real baseline.
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if name.startswith("tools."):
            if hasattr(module, "_ws"):
                setattr(module, "_ws", real_ws)
            if hasattr(module, "_ws_multi"):
                setattr(module, "_ws_multi", real_ws_multi)
