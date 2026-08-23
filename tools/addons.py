import os
import urllib.parse

import httpx

from tools._base import mcp, HA_URL, HA_TOKEN, envelope, error

# Supervisor API is available only in HA OS / Supervised add-on context.
# In the add-on, HA_URL = "http://supervisor/core" and the token is the SUPERVISOR_TOKEN.
# We detect this by checking if HA_URL points to the supervisor proxy.
_SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN", "") or HA_TOKEN
_SUPERVISOR_BASE = "http://supervisor" if "supervisor" in HA_URL else None
_SUPERVISOR_HEADERS = {
    "Authorization": f"Bearer {_SUPERVISOR_TOKEN}",
    "Content-Type": "application/json",
}


def _check_supervisor():
    """Return an error dict if Supervisor API is not available, else None."""
    if not _SUPERVISOR_BASE:
        return {
            "error": "supervisor_not_available",
            "detail": (
                "Add-on management requires Home Assistant OS or Supervised installation. "
                "This feature is not available in standalone mode."
            ),
        }
    return None


def _contains_dotdot(value: str) -> bool:
    """True if `value` carries a '..' path segment, raw or percent-encoded.

    Checked against both the raw string and its percent-decoded form,
    unquoted up to three times so a double- or triple-encoded ``%252e%252e``
    is caught too, not just a single ``%2e%2e``. Three is generous — one
    extra pass beyond a single level of encoding is already more than any
    legitimate caller would produce — and bounded so a pathological input
    cannot loop.
    """
    decoded = value
    for _ in range(3):
        new = urllib.parse.unquote(decoded)
        if new == decoded:
            break
        decoded = new
    return ".." in value or ".." in decoded


@mcp.tool()
def list_addons(search: str = "") -> dict:
    """
    List all installed add-ons with their current state and version.

    search: optional substring filter on name or slug (case-insensitive)

    Returns: {total, returned, offset, note?, addons: [{slug, name, version,
             version_latest, state, update_available, repository}]}
    Requires: Home Assistant OS or Supervised installation.
    """
    err = _check_supervisor()
    if err:
        return error(err["error"], err["detail"])
    with httpx.Client() as client:
        r = client.get(f"{_SUPERVISOR_BASE}/addons", headers=_SUPERVISOR_HEADERS, timeout=15)
        r.raise_for_status()
    addons = r.json().get("data", {}).get("addons", [])
    out = []
    for a in addons:
        if search and search.lower() not in a.get("name", "").lower() and search.lower() not in a.get("slug", "").lower():
            continue
        out.append({
            "slug": a.get("slug"),
            "name": a.get("name"),
            "version": a.get("version"),
            "version_latest": a.get("version_latest"),
            "state": a.get("state"),           # started | stopped | unknown
            "update_available": a.get("update_available", False),
            "repository": a.get("repository"),
            "description": a.get("description", ""),
        })
    return envelope(sorted(out, key=lambda x: (x.get("name") or "").lower()), key="addons")


# Option names whose value is a credential. Matched as substrings, so
# "mcp_secret" and "telegram_api_token" are both caught — but not
# "alexa_keywords", which a bare "key" would have swallowed.
_SECRET_HINTS = (
    "secret", "token", "password", "passwd", "passphrase",
    "credential", "api_key", "apikey", "private_key", "access_key",
)
# Names that are a credential on their own rather than as part of a word.
_SECRET_EXACT = {"key", "auth", "pass", "pin", "certificate", "cert"}


def _redact_options(options: dict) -> dict:
    """Replace option values that look like credentials.

    Add-on options routinely hold secrets — this add-on's own mcp_secret among
    them — and get_addon can be called by any client that reaches the MCP
    endpoint. Returning them verbatim hands a credential to whoever asks, so
    the value is replaced while the key stays visible: the caller still learns
    that the option is set.
    """
    redacted = {}
    for name, value in (options or {}).items():
        lowered = name.lower()
        sensitive = lowered in _SECRET_EXACT or any(h in lowered for h in _SECRET_HINTS)
        if sensitive and value not in (None, "", False):
            redacted[name] = "<redacted>"
        else:
            redacted[name] = value
    return redacted


@mcp.tool()
def get_addon(slug: str) -> dict:
    """
    Get detailed information about a specific add-on.

    slug: add-on slug, e.g. 'core_mosquitto', 'a0d7b954_zigbee2mqtt'
    Use list_addons() to discover slugs.

    Option values that look like credentials are returned as "<redacted>".
    The option name is still shown, so it is clear whether it is set.
    """
    err = _check_supervisor()
    if err:
        return err
    with httpx.Client() as client:
        r = client.get(f"{_SUPERVISOR_BASE}/addons/{slug}/info", headers=_SUPERVISOR_HEADERS, timeout=15)
        r.raise_for_status()
    d = r.json().get("data", {})
    return {
        "slug": d.get("slug"),
        "name": d.get("name"),
        "description": d.get("description"),
        "version": d.get("version"),
        "version_latest": d.get("version_latest"),
        "update_available": d.get("update_available", False),
        "state": d.get("state"),
        "boot": d.get("boot"),         # auto | manual
        "options": _redact_options(d.get("options", {})),
        "network": d.get("network"),
        "homeassistant_api": d.get("homeassistant_api", False),
        "ingress": d.get("ingress", False),
        "ingress_url": d.get("ingress_url"),
        "watchdog": d.get("watchdog", False),
        "auto_update": d.get("auto_update", False),
    }


@mcp.tool()
def start_addon(slug: str) -> dict:
    """
    Start an add-on.

    slug: add-on slug, e.g. 'core_mosquitto'. Use list_addons() to find slugs.

    Returns: {slug, action: "start", result} once the Supervisor accepts
    the request - `result` is the Supervisor's own response field ("ok" by
    default), passed through rather than re-verified: use get_addon(slug)
    afterward to confirm the add-on's state actually changed, since
    starting can still fail after being accepted (a port conflict, a
    missing dependency).
    """
    err = _check_supervisor()
    if err:
        return err
    with httpx.Client() as client:
        r = client.post(f"{_SUPERVISOR_BASE}/addons/{slug}/start", headers=_SUPERVISOR_HEADERS, timeout=30)
        r.raise_for_status()
    return {"slug": slug, "action": "start", "result": r.json().get("result", "ok")}


@mcp.tool()
def stop_addon(slug: str) -> dict:
    """
    Stop a running add-on.

    slug: add-on slug, e.g. 'core_mosquitto'. Use list_addons() to find slugs.

    Returns: {slug, action: "stop", result} once the Supervisor accepts
    the request. See start_addon() for why `result` is passed through
    rather than re-verified.
    """
    err = _check_supervisor()
    if err:
        return err
    with httpx.Client() as client:
        r = client.post(f"{_SUPERVISOR_BASE}/addons/{slug}/stop", headers=_SUPERVISOR_HEADERS, timeout=30)
        r.raise_for_status()
    return {"slug": slug, "action": "stop", "result": r.json().get("result", "ok")}


@mcp.tool()
def restart_addon(slug: str) -> dict:
    """
    Restart an add-on (stop then start).

    slug: add-on slug, e.g. 'core_mosquitto'. Use list_addons() to find slugs.

    Interrupts whatever the add-on was doing (a running MQTT broker drops
    its connections, a Node-RED flow stops mid-run) for as long as the
    restart takes.

    Returns: {slug, action: "restart", result} once the Supervisor accepts
    the request. See start_addon() for why `result` is passed through
    rather than re-verified.
    """
    err = _check_supervisor()
    if err:
        return err
    with httpx.Client() as client:
        r = client.post(f"{_SUPERVISOR_BASE}/addons/{slug}/restart", headers=_SUPERVISOR_HEADERS, timeout=30)
        r.raise_for_status()
    return {"slug": slug, "action": "restart", "result": r.json().get("result", "ok")}


@mcp.tool()
def get_addon_logs(slug: str, lines: int = 100) -> str:
    """
    Get the latest log output from an add-on.

    slug:  add-on slug, e.g. 'core_mosquitto'. Use list_addons() to find slugs.
    lines: number of recent log lines to return (default 100)
    """
    err = _check_supervisor()
    if err:
        # This tool returns str (its output IS the log), so a dict error()
        # envelope cannot be returned directly - str(err) used to hand the
        # model a Python dict repr as prose ("{'error': ..., 'detail':
        # ...}"). Formatted as "code: message" instead, the same prose
        # convention get_error_log() (tools/diagnostics.py) already uses
        # for its own str-typed error case.
        return f"{err['error']}: {err['detail']}"
    with httpx.Client() as client:
        r = client.get(f"{_SUPERVISOR_BASE}/addons/{slug}/logs", headers=_SUPERVISOR_HEADERS, timeout=15)
        r.raise_for_status()
    return "\n".join(r.text.splitlines()[-lines:])


@mcp.tool()
def get_supervisor_info() -> dict:
    """
    Get Home Assistant Supervisor and OS info: version, update availability,
    channel (stable/beta/dev), and system architecture.

    Requires: Home Assistant OS or Supervised installation.
    """
    err = _check_supervisor()
    if err:
        return err
    with httpx.Client() as client:
        sup_r = client.get(f"{_SUPERVISOR_BASE}/supervisor/info", headers=_SUPERVISOR_HEADERS, timeout=10)
        os_r = client.get(f"{_SUPERVISOR_BASE}/os/info", headers=_SUPERVISOR_HEADERS, timeout=10)
    sup = sup_r.json().get("data", {}) if sup_r.status_code == 200 else {}
    os_info = os_r.json().get("data", {}) if os_r.status_code == 200 else {}
    return {
        "supervisor_version": sup.get("version"),
        "supervisor_latest": sup.get("version_latest"),
        "supervisor_update_available": sup.get("update_available", False),
        "channel": sup.get("channel"),
        "arch": sup.get("arch"),
        "ha_os_version": os_info.get("version"),
        "ha_os_latest": os_info.get("version_latest"),
        "ha_os_update_available": os_info.get("update_available", False),
        "board": os_info.get("board"),
    }


@mcp.tool()
def call_addon_api(
    slug: str,
    path: str,
    method: str = "GET",
    data: dict = None,
) -> dict:
    """
    Call an add-on's internal HTTP API via the Supervisor proxy.

    slug:   add-on slug, e.g. 'a0d7b954_zigbee2mqtt'. Use list_addons() to find slugs.
    path:   API path within the add-on, e.g. '/api/devices', '/health', '/api/permit'
    method: HTTP method — 'GET' (default), 'POST', 'PUT', 'DELETE'
    data:   optional request body dict for POST/PUT requests

    Examples:
      Zigbee2MQTT devices:   slug='a0d7b954_zigbee2mqtt', path='/api/devices'
      ESPHome health:        slug='5c53de3b_esphome', path='/health'
      Node-RED flows:        slug='a0d7b954_nodered', path='/flows'

    Requires: Home Assistant OS or Supervised installation.

    ⚠️ This is a proxy into the *named add-on's own* HTTP API and nothing
    else - `slug` and `path` are rejected outright if either carries a
    '..' path segment (raw or percent-encoded), and the request URL is
    then re-checked after being built to confirm it still resolves inside
    `/addons/{slug}/api/` before anything is sent. That is what keeps this
    scoped to one add-on's API rather than the Supervisor API as a whole -
    the Supervisor proxy that serves this endpoint carries a manager-role
    token in the Home Assistant app deployment, and `/host/shutdown`,
    `/backups/{slug}/download` (the whole configuration, secrets.yaml
    included) and `/store/addons/{slug}/install` (arbitrary add-on
    install) all sit on that same token one path segment away. Within the
    named add-on's own API, method=PUT/POST/DELETE can still change or
    delete data the add-on manages (a Zigbee2MQTT device pairing, a
    Node-RED flow) with no verification here of what that path actually
    does; this tool has no way to know. Treat an unfamiliar add-on's API
    as untrusted before calling it with anything other than GET.

    Returns: the add-on's own JSON response, or {text, status_code} when
    the response body is not JSON. Passed through unexamined, like
    call_service() - this tool does not know the add-on's response shape
    any more than call_service() knows a Home Assistant service's.
    Returns {error: "invalid_slug"/"invalid_path", ...} instead of making
    any request when `slug` or `path` fails the checks above.
    """
    err = _check_supervisor()
    if err:
        return err
    if _contains_dotdot(slug):
        return error("invalid_slug",
                     f"slug must not contain a '..' path segment: {slug!r}")
    path = path.lstrip("/")
    if _contains_dotdot(path):
        return error("invalid_path",
                     f"path must not contain a '..' path segment: {path!r}")

    expected_prefix = f"/addons/{slug}/api/"
    url = f"{_SUPERVISOR_BASE}{expected_prefix}{path}"
    # Belt and braces: the checks above reject the traversal itself, this
    # confirms where the request actually resolves to - httpx (like a
    # browser) collapses '../' segments when a URL is built from one, so a
    # dot-segment sequence that neither check above caught would still be
    # visible here as a resolved path that has left expected_prefix. See
    # this module's earlier version (and the security review that found
    # it) for why relying on the string checks alone was not enough: they
    # are what a future encoding trick could slip past, not what decides
    # where the request goes.
    resolved_path = httpx.URL(url).path
    if not resolved_path.startswith(expected_prefix) or ".." in resolved_path:
        return error(
            "invalid_path",
            f"Resolved request path {resolved_path!r} would leave the "
            f"add-on's own API ({expected_prefix}...) - refusing to send it.",
        )
    with httpx.Client() as client:
        if method.upper() == "GET":
            r = client.get(url, headers=_SUPERVISOR_HEADERS, timeout=15)
        elif method.upper() == "POST":
            r = client.post(url, headers=_SUPERVISOR_HEADERS, json=data or {}, timeout=15)
        elif method.upper() == "PUT":
            r = client.put(url, headers=_SUPERVISOR_HEADERS, json=data or {}, timeout=15)
        elif method.upper() == "DELETE":
            r = client.delete(url, headers=_SUPERVISOR_HEADERS, timeout=15)
        else:
            return {"error": f"unsupported_method: {method}"}
        r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"text": r.text, "status_code": r.status_code}
