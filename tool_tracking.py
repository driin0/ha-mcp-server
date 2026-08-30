"""Instrumentation wrapped around every MCP tool call.

Two jobs, one wrapper, because there is only one place every tool call
passes through: recording latency and errors for the status dashboard, and
replacing the SDK's description of a timeout with an honest one.

This lives in its own module rather than inside server.py's __main__ block
so it can be imported by a test. That is not tidiness: the net hangs on
mcp._tool_manager.call_tool, a private SDK attribute, and a guard that can
stop applying in silence needs a test that fails loudly when it does - see
test_the_net_is_actually_installed(). Importing server.py instead would run
its 37 tool imports and apply_registration_gate(), mutating the registered
tool set for every other test in the session, which is why
tests/test_registration_gate.py uses subprocesses.
"""
import time

import stats
from tools._base import timeout_message


def install_call_tracking(mcp_server):
    """Wrap `mcp_server._tool_manager.call_tool` and return the wrapper.

    Returned rather than only installed so a caller - in practice a test -
    can assert that the attribute really is the wrapper afterwards.
    """
    original = mcp_server._tool_manager.call_tool

    async def _tracked_call(name: str, arguments: dict, *args, **kwargs):
        t0 = time.monotonic()
        try:
            result = await original(name, arguments, *args, **kwargs)
            stats.record_call(name, (time.monotonic() - t0) * 1000)
            return result
        except Exception as exc:
            stats.record_call(name, (time.monotonic() - t0) * 1000)
            stats.record_error(name, exc)
            honest = timeout_message(exc, name)
            if honest is not None:
                # A plain TimeoutError, not a reconstruction of exc's own
                # type: WsTimeout takes only a keyword argument, and every
                # other timeout type would be one more constructor signature
                # to be right about forever. `from exc` keeps the original
                # type and traceback in the log, which is where that
                # information is useful; the caller needs the text.
                raise TimeoutError(honest) from exc
            raise

    mcp_server._tool_manager.call_tool = _tracked_call
    return _tracked_call
