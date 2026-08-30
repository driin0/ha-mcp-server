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

    Every entry must appear as a `"type": "<command>"` literal somewhere in
    tools/ - otherwise it is a guess, and a guess in a fail-toward-caution
    list is the one place a guess is expensive.
    """
    import pathlib

    sources = "\n".join(
        p.read_text()
        for p in (pathlib.Path(__file__).resolve().parents[1] / "tools").glob("*.py"))

    unsent = [cmd for cmd in WS_READ_COMMANDS if f'"{cmd}"' not in sources]
    assert unsent == []
