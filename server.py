import tools._base  # triggers load_dotenv and mcp init
import tools.diagnostics
import tools.automations
import tools.scripts
import tools.validation
import tools.scenes
import tools.helpers
import tools.notifications
import tools.cameras
import tools.areas
import tools.lights
import tools.switches
import tools.sensors
import tools.climate
import tools.media_players
import tools.locks
import tools.fans
import tools.covers
import tools.vacuum
import tools.weather
import tools.persons
import tools.alarm
import tools.system
import tools.calendar
import tools.todo
import tools.statistics
import tools.buttons
import tools.addons
import tools.dashboards
import tools.hacs
import tools.assist
import tools.groups
import tools.users
import tools.tags
import tools.alerts
import tools.prompts

from tools._base import apply_registration_gate

# Every tools.* module above has now run its @mcp.tool() decorators, so every
# tool that exists at all is registered - only now can a gated one actually
# be found and removed. See apply_registration_gate()'s docstring
# (tools/_base.py) for why this happens here, after the fact, rather than by
# changing how any individual tool is declared.
DISABLED_TOOLS = apply_registration_gate()

def _split_hosts(raw: str) -> set[str]:
    """Parse a comma-separated MCP_ALLOWED_HOSTS value into a lowercase set."""
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _host_without_port(value: str) -> str:
    """Strip a trailing ':port' from a bare 'host[:port]' value, IPv6-aware.

    Handles the three shapes a Host header or an Origin's netloc can take:
    'example.com:47821', '[::1]:47821' (a literal IPv6 address needs the
    brackets to disambiguate its own colons from the port separator), and
    a bare host/address with no port at all.
    """
    value = value.strip()
    if value.startswith("["):
        return value[1:value.index("]")] if "]" in value else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


if __name__ == "__main__":
    import hmac
    import sys
    import time
    import threading
    from urllib.parse import urlsplit

    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response as StarletteResponse
    from tools._base import mcp, MCP_ALLOWED_HOSTS, MCP_PORT, MCP_SECRET
    from web import start as start_web_ui
    import stats

    # Patch tool manager to track call counts, latency and errors
    _orig_call = mcp._tool_manager.call_tool

    async def _tracked_call(name: str, arguments: dict, *args, **kwargs):
        t0 = time.monotonic()
        try:
            result = await _orig_call(name, arguments, *args, **kwargs)
            stats.record_call(name, (time.monotonic() - t0) * 1000)
            return result
        except Exception as e:
            stats.record_call(name, (time.monotonic() - t0) * 1000)
            stats.record_error(name, e)
            raise

    mcp._tool_manager.call_tool = _tracked_call

    threading.Thread(target=start_web_ui, daemon=True, name="web-ui").start()

    # 2.0 defaults the path to /mcp and the host to 127.0.0.1; this add-on serves
    # on / for reverse proxies and must listen on every interface.
    app = mcp.streamable_http_app(streamable_http_path="/", host="0.0.0.0")

    # DNS rebinding: a page in a browser on the LAN can be made to `fetch()`
    # this server after its hostname's DNS record is repointed at the LAN
    # IP - the browser still sends the ORIGINAL Origin/Host (the attacker's
    # domain), but the TCP connection now lands here. The MCP spec requires
    # Origin validation for exactly this. It matters most precisely when
    # MCP_SECRET is empty - the configuration MCP_ALLOW_NO_AUTH exists to
    # create - because after the rebind the attacker's page is same-origin
    # with this server, so neither CORS nor the bearer check (when a secret
    # IS set, this still adds a second, independent layer) sees anything
    # wrong. Applied unconditionally - unlike BearerAuthMiddleware below,
    # this does not depend on MCP_SECRET being set.
    #
    # A request with no Origin header at all is left alone: that is the
    # ordinary shape of a non-browser MCP client (Claude Code, a script, an
    # HTTP library) reaching this server directly, and Origin cannot be
    # forged by a browser into "absent" - only a real browser-driven fetch
    # sends one, which is exactly the case this exists to catch. When an
    # Origin IS present, both it and Host must resolve to a hostname this
    # server is actually meant to be reached at: localhost, or one of the
    # extra hostnames/IPs the operator lists in MCP_ALLOWED_HOSTS.
    _allowed_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0"} | _split_hosts(MCP_ALLOWED_HOSTS)

    class OriginHostMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            origin = request.headers.get("origin", "")
            if origin:
                origin_host = _host_without_port(urlsplit(origin).netloc).lower()
                host_header = _host_without_port(request.headers.get("host", "")).lower()
                if origin_host not in _allowed_hosts or host_header not in _allowed_hosts:
                    print(
                        f"[ha-mcp-server] Rejected request: Origin={origin!r} "
                        f"Host={request.headers.get('host', '')!r} not in allowed hosts "
                        f"{sorted(_allowed_hosts)} - set MCP_ALLOWED_HOSTS to add one.",
                        file=sys.stderr,
                    )
                    return StarletteResponse("Forbidden: Origin/Host not allowed", status_code=403)
            return await call_next(request)

    app.add_middleware(OriginHostMiddleware)

    if MCP_SECRET:
        _secret_bytes = MCP_SECRET.encode("utf-8")

        class BearerAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                header = request.headers.get("Authorization", "")
                token = header[7:] if header.startswith("Bearer ") else ""
                if not hmac.compare_digest(token.encode("utf-8"), _secret_bytes):
                    return StarletteResponse("Unauthorized", status_code=401)
                return await call_next(request)

        app.add_middleware(BearerAuthMiddleware)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=MCP_PORT,
        log_level=mcp.settings.log_level.lower(),
    )
