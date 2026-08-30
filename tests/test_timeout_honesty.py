"""The message a caller gets when Home Assistant does not answer in time.

A write that times out used to be reported as `Error executing tool X:
timed out` - measured live against the real instance, an automation ran to
completion ten seconds AFTER the caller had been told the tool errored.
"Error executing" is not merely ambiguous; every natural reading of it is
"it did not happen", which is the one thing that is not known.
"""
import concurrent.futures

import httpx
import pytest

from tools._base import WS_READ_COMMANDS, WsTimeout, timeout_message


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _httpx_timeout(method: str, url: str) -> httpx.ReadTimeout:
    """A timeout shaped the way httpx really raises one: carrying its request.

    Real httpx always attaches the request to a transport error, which is
    what lets the boundary net tell a read from a write with no list of
    write tools to maintain anywhere - and therefore no list that can go
    silently stale.
    """
    exc = httpx.ReadTimeout("timed out")
    exc.request = httpx.Request(method, url)
    return exc


def test_a_timed_out_write_never_says_the_call_failed():
    """The word is banned outright, not merely negated.

    "this is not a report that it failed" is true and still wrong to ship:
    a negation is the weakest way to tell a reader something, and a reader
    skimming for a verdict finds the word without the "not". The message
    says what IS known instead.
    """
    msg = timeout_message(
        _httpx_timeout("POST", "http://ha.test/api/services/lock/unlock"),
        "lock_control")

    assert "failed" not in msg.lower()
    assert "may already have been applied" in msg


def test_a_timed_out_write_warns_that_repeating_it_may_apply_it_twice():
    msg = timeout_message(
        _httpx_timeout("POST", "http://ha.test/api/services/lock/unlock"),
        "lock_control")

    assert "twice" in msg.lower()


def test_a_timed_out_write_names_the_code_and_the_tool():
    msg = timeout_message(
        _httpx_timeout("POST", "http://ha.test/api/services/lock/unlock"),
        "lock_control")

    assert "write_outcome_unknown" in msg
    assert "lock_control" in msg


def test_a_timed_out_read_says_it_is_safe_to_repeat():
    msg = timeout_message(
        _httpx_timeout("GET", "http://ha.test/api/states/light.kitchen"),
        "get_entity")

    assert "read_timeout" in msg
    assert "safe to repeat" in msg
    assert "may already have been applied" not in msg


def test_a_websocket_write_command_is_described_as_a_write():
    msg = timeout_message(
        WsTimeout(command_types=("config/area_registry/create",)), "create_area")

    assert "write_outcome_unknown" in msg
    assert "may already have been applied" in msg


def test_a_websocket_read_command_is_described_as_a_read():
    msg = timeout_message(
        WsTimeout(command_types=("config/entity_registry/list",)), "list_areas")

    assert "read_timeout" in msg
    assert "safe to repeat" in msg


def test_an_unrecognised_websocket_command_is_treated_as_a_write():
    """Fail toward caution.

    Home Assistant's WS command names do not classify reliably:
    config/entity_registry/update announces itself, backup/generate does
    not. A read wrongly described as "may have applied" costs one needless
    verification; a write wrongly described as "safe to repeat" costs a
    second actuation. The asymmetry decides the default.
    """
    msg = timeout_message(WsTimeout(command_types=("some/new/command",)),
                          "future_tool")

    assert "write_outcome_unknown" in msg


def test_a_mixed_websocket_batch_is_treated_as_a_write():
    msg = timeout_message(
        WsTimeout(command_types=("config/entity_registry/list",
                                 "config/area_registry/update")),
        "bulk_set_entity_labels")

    assert "write_outcome_unknown" in msg


def test_a_bare_futures_timeout_is_still_described_as_a_write():
    """_ws_multi is not the only thing that can raise a futures timeout, and
    one with no command types attached knows least of all - so it gets the
    cautious wording, not silence."""
    msg = timeout_message(concurrent.futures.TimeoutError(), "some_tool")

    assert "write_outcome_unknown" in msg


def test_something_that_is_not_a_timeout_is_not_described():
    assert timeout_message(ValueError("nope"), "some_tool") is None


def test_the_read_list_holds_only_commands_this_codebase_sends():
    """A read list naming commands nothing sends would rot without failing.

    Read over the AST, not the text. An earlier version searched the source
    as a string and passed on `config_entries/list` - a command no tool has
    ever sent, which appears only as an EXAMPLE inside ws_error()'s
    docstring. instance_health had been written against it. Matching prose
    is how a check comes to confirm something nobody does.

    A dict literal's `"type": "<command>"` is a command actually sent; a
    docstring is one string constant, and its contents never parse into a
    Dict node, so they cannot satisfy this.
    """
    import ast
    import pathlib

    sent = set()
    for path in (pathlib.Path(__file__).resolve().parents[1] / "tools").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "type"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    sent.add(value.value)

    assert sorted(WS_READ_COMMANDS - sent) == []


def test_ws_multi_names_the_commands_that_did_not_come_back(monkeypatch):
    """A WS timeout used to reach the caller as an empty string.

    concurrent.futures.TimeoutError's str() is '', so the SDK rendered
    `Error executing tool <name>: ` - an error with no stated cause. The
    command types are the cheapest thing that makes it say something.
    """
    import tools._base as base

    class _NeverFinishes:
        def submit(self, _fn, coro, *args, **kwargs):
            # Never scheduled, so close it explicitly: an un-awaited
            # coroutine raises a ResourceWarning that belongs to this fake,
            # not to the code under test, and noise in a suite is how a real
            # warning stops being noticed.
            coro.close()

            class _F:
                def result(self, timeout=None):
                    raise concurrent.futures.TimeoutError()
            return _F()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor",
                        lambda *a, **k: _NeverFinishes())

    with pytest.raises(WsTimeout) as caught:
        base._ws_multi([{"type": "config/area_registry/create", "name": "x"}])

    assert caught.value.command_types == ("config/area_registry/create",)
    assert str(caught.value) != ""


class _FakeToolManager:
    def __init__(self, raises=None):
        self._raises = raises
        self.calls = []

    async def call_tool(self, name, arguments, *args, **kwargs):
        self.calls.append(name)
        if self._raises is not None:
            raise self._raises
        return {"ok": True}


class _FakeMcp:
    def __init__(self, raises=None):
        self._tool_manager = _FakeToolManager(raises)


def test_the_net_is_actually_installed():
    """The guard that must fail loudly.

    The net lives on a monkeypatch of mcp._tool_manager.call_tool, a
    PRIVATE SDK attribute. If a future SDK version renames it, the patch
    stops applying in silence - and a correctness guarantee that can stop
    applying without saying so is exactly the failure this repository's
    deny-list documents. This test asserts the installation itself, not
    only that the message reads well once installed.
    """
    import tool_tracking

    fake = _FakeMcp()
    original = fake._tool_manager.call_tool

    installed = tool_tracking.install_call_tracking(fake)

    assert fake._tool_manager.call_tool is installed
    assert fake._tool_manager.call_tool is not original


def test_the_sdk_still_exposes_the_attribute_the_net_hangs_on():
    """If this fails, the SDK moved and the net is no longer installed
    anywhere - regardless of what the rest of this file asserts."""
    from tools._base import mcp

    assert hasattr(mcp, "_tool_manager")
    assert callable(getattr(mcp._tool_manager, "call_tool", None))


@pytest.mark.anyio
async def test_a_timed_out_write_reaches_the_caller_with_the_honest_text():
    import tool_tracking

    fake = _FakeMcp(raises=_httpx_timeout(
        "POST", "http://ha.test/api/services/lock/unlock"))
    tool_tracking.install_call_tracking(fake)

    with pytest.raises(Exception) as caught:
        await fake._tool_manager.call_tool("lock_control", {})

    text = str(caught.value)
    assert "write_outcome_unknown" in text
    assert "may already have been applied" in text
    assert text != "timed out"


@pytest.mark.anyio
async def test_a_websocket_timeout_reaches_the_caller_with_something_to_read():
    """The case that used to arrive as an empty string."""
    import tool_tracking

    fake = _FakeMcp(raises=WsTimeout(
        command_types=("config/area_registry/create",)))
    tool_tracking.install_call_tracking(fake)

    with pytest.raises(Exception) as caught:
        await fake._tool_manager.call_tool("create_area", {})

    assert "write_outcome_unknown" in str(caught.value)


@pytest.mark.anyio
async def test_a_non_timeout_error_passes_through_unchanged():
    import tool_tracking

    fake = _FakeMcp(raises=ValueError("something else entirely"))
    tool_tracking.install_call_tracking(fake)

    with pytest.raises(ValueError, match="something else entirely"):
        await fake._tool_manager.call_tool("some_tool", {})


@pytest.mark.anyio
async def test_a_successful_call_is_returned_untouched():
    import tool_tracking

    fake = _FakeMcp()
    tool_tracking.install_call_tracking(fake)

    assert await fake._tool_manager.call_tool("some_tool", {}) == {"ok": True}


def test_httpx_attaches_the_request_to_a_timeout_it_raises(fake_ha):
    """The library property the whole read/write distinction rests on.

    timeout_message() reads exc.request.method rather than consulting a
    hand-kept list of write tools, precisely so there is no list to go
    stale. That trade is only sound while httpx keeps attaching the request
    to a transport error - it does so in Client._send_single_request, and
    it is not something this codebase controls. If a future httpx stops,
    every write would silently take the cautious default and every read
    would be described as possibly-applied; this test is what says so out
    loud instead.
    """
    from tools.lights import set_light

    fake_ha.raise_rest("/api/services/light/", httpx.ReadTimeout("timed out"))

    with pytest.raises(httpx.ReadTimeout) as caught:
        set_light("light.kitchen", "off")

    assert caught.value.request.method == "POST"


def test_a_real_write_tool_timing_out_is_described_as_a_write(fake_ha):
    from tools.lights import set_light

    fake_ha.raise_rest("/api/services/light/", httpx.ReadTimeout("timed out"))

    with pytest.raises(httpx.ReadTimeout) as caught:
        set_light("light.kitchen", "off")

    assert "write_outcome_unknown" in timeout_message(caught.value, "set_light")


def test_a_real_read_tool_timing_out_is_described_as_a_read(fake_ha):
    from tools.diagnostics import get_entity

    fake_ha.raise_rest("/api/states/", httpx.ReadTimeout("timed out"))

    with pytest.raises(httpx.ReadTimeout) as caught:
        get_entity("light.kitchen")

    assert "read_timeout" in timeout_message(caught.value, "get_entity")
