from tools._base import mcp, _ws, envelope, ws_error


def _dashboard_id(url_path: str) -> tuple[str, dict | None]:
    """Resolve a dashboard url_path to the id the WebSocket API expects.

    The lovelace/dashboards/update and /delete commands are keyed by
    dashboard_id and reject url_path outright, while url_path is what a user
    sees and what list_dashboards() reports — so it is translated here.

    Returns (dashboard_id, error_envelope_or_None). `result.get("result") or
    []` used to fold a failed lovelace/dashboards/list read into an empty
    list, so a dead connection looked exactly like "no dashboard has that
    url_path" to both callers (update_dashboard, delete_dashboard) — they
    reported not_found for a registry they never actually got to check.
    Routing the read through ws_error() lets a genuine absence stay
    not_found while a failure surfaces as itself.
    """
    result = _ws({"type": "lovelace/dashboards/list"})
    if err := ws_error(result):
        return "", err
    for d in result["result"]:
        if d.get("url_path") == url_path:
            return d.get("id", ""), None
    return "", None


@mcp.tool()
def list_dashboards() -> dict:
    """
    List all Lovelace dashboards configured in Home Assistant.

    Returns: {total, returned, offset, note?, dashboards: [{url_path, title,
             mode, icon, show_in_sidebar, require_admin}]}
    mode is 'storage' (UI-managed) or 'yaml' (file-based).
    """
    result = _ws({"type": "lovelace/dashboards/list"})
    if err := ws_error(result):
        return err
    out = []
    for d in result["result"]:
        out.append({
            "url_path": d.get("url_path"),
            "title": d.get("title") or d.get("url_path") or "default",
            "mode": d.get("mode", "storage"),
            "icon": d.get("icon", ""),
            "show_in_sidebar": d.get("show_in_sidebar", True),
            "require_admin": d.get("require_admin", False),
        })
    out.sort(key=lambda x: (x.get("title") or "").lower())
    return envelope(out, key="dashboards")


@mcp.tool()
def get_dashboard(url_path: str = "") -> dict:
    """
    Get the full configuration (views and cards) of a Lovelace dashboard.

    url_path: dashboard URL path (e.g. 'lovelace', 'mobile', 'energia').
              Leave empty for the default dashboard.

    Returns the raw dashboard config. For large dashboards this can be verbose —
    use a specific url_path to limit output.
    """
    msg: dict = {"type": "lovelace/config"}
    if url_path:
        msg["url_path"] = url_path
    msg["force"] = False
    result = _ws(msg)
    if err := ws_error(result):
        return err
    return result["result"]


@mcp.tool()
def create_dashboard(
    url_path: str,
    title: str,
    icon: str = "",
    show_in_sidebar: bool = True,
    require_admin: bool = False,
) -> dict:
    """
    Create a new Lovelace dashboard (storage mode).

    url_path:        unique URL slug for the dashboard (e.g. 'mobile', 'energia', 'admin')
    title:           display title shown in the sidebar
    icon:            MDI icon, e.g. 'mdi:solar-power' (optional)
    show_in_sidebar: show in left navigation (default: True)
    require_admin:   restrict access to admins only (default: False)

    After creating, use update_dashboard_config() to populate views and cards.

    Returns the created dashboard object from Home Assistant, or an
    error() envelope on failure.
    """
    msg: dict = {
        "type": "lovelace/dashboards/create",
        "url_path": url_path,
        "title": title,
        "mode": "storage",
        "show_in_sidebar": show_in_sidebar,
        "require_admin": require_admin,
    }
    if icon:
        msg["icon"] = icon
    result = _ws(msg)
    if err := ws_error(result):
        return err
    return result["result"]


@mcp.tool()
def update_dashboard(
    url_path: str,
    title: str = "",
    icon: str = "",
    show_in_sidebar: bool = None,
    require_admin: bool = None,
) -> dict:
    """
    Update a Lovelace dashboard's metadata (title, icon, sidebar visibility).

    url_path: dashboard URL path to update (use list_dashboards() to find url_paths).
    Only fields with non-None/non-empty values are updated.

    To update the actual views and cards content, use update_dashboard_config() instead.

    Returns the updated dashboard object from Home Assistant, or an
    error() envelope ("not_found" when url_path does not exist, or Home
    Assistant's own error otherwise) on failure.
    """
    dashboard_id, err = _dashboard_id(url_path)
    if err:
        return err
    if not dashboard_id:
        return {"error": "not_found", "url_path": url_path,
                "detail": "No dashboard with that url_path. Use list_dashboards()."}
    msg: dict = {"type": "lovelace/dashboards/update", "dashboard_id": dashboard_id}
    if title:
        msg["title"] = title
    if icon:
        msg["icon"] = icon
    if show_in_sidebar is not None:
        msg["show_in_sidebar"] = show_in_sidebar
    if require_admin is not None:
        msg["require_admin"] = require_admin
    result = _ws(msg)
    if err := ws_error(result):
        return err
    return result["result"]


@mcp.tool()
def update_dashboard_config(url_path: str, config: dict) -> dict:
    """
    Save the full configuration (views and cards) of a Lovelace dashboard.

    url_path: dashboard URL path (use empty string '' for the default dashboard).
    config:   complete Lovelace config dict with a 'views' list. Example:
    {
      "views": [
        {
          "title": "Home",
          "path": "home",
          "icon": "mdi:home",
          "cards": [
            {"type": "entities", "title": "Lights", "entities": ["light.living_room", "light.kitchen"]},
            {"type": "weather-forecast", "entity": "weather.home"}
          ]
        }
      ]
    }

    ⚠️ This REPLACES the entire dashboard config. Call get_dashboard() first
    to read the current config if you want to make incremental changes -
    this is not undoable once saved, other than by writing the previous
    config back.

    Returns: {saved: true, url_path} on success, or an error() envelope
    with Home Assistant's actual error code/message on failure.
    """
    msg: dict = {"type": "lovelace/config/save", "config": config}
    if url_path:
        msg["url_path"] = url_path
    result = _ws(msg)
    if err := ws_error(result):
        return err
    return {"saved": True, "url_path": url_path or "default"}


@mcp.tool()
def delete_dashboard(url_path: str) -> dict:
    """
    Delete a Lovelace dashboard.

    url_path: dashboard URL path (use list_dashboards() to find url_paths).
    Note: the default dashboard cannot be deleted.

    ⚠️ This is irreversible. The dashboard's views and card layout are gone.

    Returns: {deleted: url_path, success: true} on success, or an error()
    envelope ("not_found" when url_path does not exist, or Home
    Assistant's own error otherwise) on failure.
    """
    dashboard_id, err = _dashboard_id(url_path)
    if err:
        return err
    if not dashboard_id:
        return {"error": "not_found", "url_path": url_path,
                "detail": "No dashboard with that url_path. Use list_dashboards()."}
    result = _ws({"type": "lovelace/dashboards/delete", "dashboard_id": dashboard_id})
    if err := ws_error(result):
        return err
    return {"deleted": url_path, "success": True}


# ─── Lovelace frontend resources ─────────────────────────────────────────────

@mcp.tool()
def list_lovelace_resources() -> dict:
    """
    List all Lovelace frontend resources (JavaScript modules and CSS stylesheets).

    These are the custom card JS files and theme CSS files loaded by the HA frontend.
    Useful to audit what's installed or to add new custom cards manually.

    Returns: {total, returned, offset, note?, resources: [{id, url, type}]}
    type is 'module' (JS) or 'css'
    """
    result = _ws({"type": "lovelace/resources/list"})
    if err := ws_error(result):
        return err
    out = [
        {
            "id": r.get("id"),
            "url": r.get("url"),
            "type": r.get("res_type", r.get("type", "")),
        }
        for r in (result["result"] or [])
    ]
    return envelope(out, key="resources")


@mcp.tool()
def add_lovelace_resource(url: str, resource_type: str = "module") -> dict:
    """
    Add a Lovelace frontend resource (custom card JS or CSS stylesheet).

    url:           URL to the resource, e.g. '/hacsfiles/button-card/button-card.js'
                   or 'https://cdn.example.com/my-card.js'
    resource_type: 'module' (default, for ES module JS files) or 'css'

    After adding a JS module, reload the browser to load the new card.
    Note: HACS-installed cards are added automatically — use this for manual installs.

    Returns the created resource object from Home Assistant (or
    {added: true, url, type} when Home Assistant's response is empty), or
    an error() envelope on failure.
    """
    result = _ws({"type": "lovelace/resources/create", "url": url, "res_type": resource_type})
    if err := ws_error(result):
        return err
    return result["result"] or {"added": True, "url": url, "type": resource_type}


@mcp.tool()
def remove_lovelace_resource(resource_id: str) -> dict:
    """
    Remove a Lovelace frontend resource by its ID.

    resource_id: opaque hex string ID, e.g. '9ed6e7503f1549e6bf3b73f079b7542d'
                 (use list_lovelace_resources() to find IDs)

    ⚠️ This is irreversible. Any dashboard card relying on this resource
    (a custom card's JS, a theme's CSS) stops rendering until it is
    re-added.

    Returns: {deleted: resource_id, success: true} on success, or an
    error() envelope with Home Assistant's actual error code/message on
    failure.
    """
    result = _ws({"type": "lovelace/resources/delete", "resource_id": resource_id})
    if err := ws_error(result):
        return err
    return {"deleted": resource_id, "success": True}
