import copy
import json

from tools import _jsonpath
from tools._base import mcp, _ws, envelope, error, ws_error


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

    ⚠️ This REPLACES the entire dashboard config - this is not undoable once
    saved, other than by writing the previous config back. For an
    incremental change (one card, one value, one template field) use
    patch_dashboard() instead: it changes only one path, refuses a target
    that is not what you expected, and checks for concurrent edits. Find
    the path with find_dashboard_cards() or get_dashboard_outline().

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


# ---- partial edits -------------------------------------------------------------
#
# Lovelace has no conditional write: lovelace/config/save replaces the whole
# config, last write wins (homeassistant/components/lovelace/dashboard.py,
# LovelaceStorage.async_save - no hash, no version). patch_dashboard() builds
# the same compare-and-swap approximation as patch_automation(): re-read just
# before saving, refuse if anything changed, read back after. It shrinks the
# unguarded window to one round trip; it does not close it.

_PATCH_OPS = ("set", "insert", "remove")


def _summary(node) -> str:
    """One line naming a card or value: type plus heading/title/name and entity."""
    if not isinstance(node, dict):
        text = json.dumps(node, ensure_ascii=False)
        return text if len(text) <= 60 else text[:57] + "..."
    parts = [str(node.get("type", "(no type)"))]
    for key in ("heading", "title", "name"):
        label = node.get(key)
        if isinstance(label, str) and label and not label.lstrip().startswith("[[["):
            parts.append(repr(label))
            break
    if isinstance(node.get("entity"), str):
        parts.append(node["entity"])
    return " ".join(parts)


def _dashboard_mode(url_path: str) -> tuple[str | None, dict | None]:
    """'storage' / 'yaml' for a registered dashboard, 'storage' for the
    default one (url_path ""), None when no dashboard has that url_path."""
    if not url_path:
        return "storage", None
    result = _ws({"type": "lovelace/dashboards/list"})
    if err := ws_error(result):
        return None, err
    for d in result["result"]:
        if d.get("url_path") == url_path:
            return d.get("mode", "storage"), None
    return None, None


def _read_config(url_path: str) -> tuple[dict | None, dict | None]:
    msg: dict = {"type": "lovelace/config", "force": True}
    if url_path:
        msg["url_path"] = url_path
    result = _ws(msg)
    if err := ws_error(result):
        if err["error"] == "config_not_found":
            if not url_path:
                hint = ("The default dashboard has never been saved from the UI: "
                        "Home Assistant generates it on the fly and has no stored "
                        "config to edit. Take control of it in the UI first.")
            elif "No config found" in err["detail"]:
                hint = ("This dashboard has never been saved, so there is no "
                        "stored config to edit yet.")
            else:
                hint = "No dashboard with that url_path. Use list_dashboards()."
            return None, error("not_found", f"{hint} ({err['detail']})",
                               url_path=url_path or "default")
        return None, error("config_read_failed",
                           f"Could not read the dashboard config: {err['error']}: "
                           f"{err['detail']}. Nothing was written.",
                           url_path=url_path or "default")
    return result["result"], None


def _expect_mismatch(target, expect: dict) -> str | None:
    """None when every key of `expect` is in `target` with an equal value."""
    if not isinstance(target, dict):
        return f"the target is not a card or object but {_summary(target)}"
    wrong = [k for k, v in expect.items() if target.get(k, object()) != v]
    if wrong:
        shown = {k: target.get(k) for k in wrong}
        return (f"the target is {_summary(target)} - {', '.join(wrong)} "
                f"differ (found {json.dumps(shown, ensure_ascii=False)})")
    return None


@mcp.tool()
def patch_dashboard(
    url_path: str,
    path: str,
    value: dict | list | str | int | float | bool | None = None,
    op: str = "set",
    expect: dict | None = None,
) -> dict:
    """
    Change one value, card, section or view of a storage-mode dashboard by
    dotted path, without resending the whole config.

    url_path: as get_dashboard() - "" is the default dashboard.
    path:     dotted, integer indices into lists, e.g.
              'views.0.sections.1.cards.5.title',
              'button_card_templates.wm_btn.show_icon'. Use
              find_dashboard_cards() or get_dashboard_outline() to get paths
              rather than counting indices by hand. A key containing "." is
              not addressable.
    value:    the new value (for set and insert). null is a real value.
    op:       "set"    - replace the value at `path`; the path must exist.
              "insert" - into a list: before index N, N == length appends;
                         into an object: a new key that must not exist yet.
                         The only way this tool ever creates anything.
              "remove" - delete the list element or key at `path`; `value`
                         must be omitted.
    expect:   strongly recommended for set/remove on list elements: keys the
              CURRENT target must have with these exact values, e.g.
              {"type": "tile", "entity": "sensor.lavatrice"}. If the card at
              that index is anything else, nothing is written. For an insert
              into a list it is checked against the element now at that
              index (an append has none, so expect is refused there).

    A path that does not resolve is refused before anything is written, and
    the error names what IS there. Inserting into or removing from a list
    moves every later index of that list; the result's `shifted` says so -
    paths obtained before this call into that list are stale.

    Read-modify-write with the same compare-and-swap approximation as
    patch_automation(): the config is re-read right before the save and the
    call refuses if it changed since it was first read; after the save it
    is read back and compared. Home Assistant offers no conditional write,
    so a write landing in the one round trip between the re-read and the
    save is still lost.

    YAML-mode dashboards cannot be written through Home Assistant's API at
    all. To replace a whole config use update_dashboard_config().

    Returns: {url_path, op, path, old, new, saved: true, shifted?} - `old`
    is the value replaced or removed (null for insert), `new` the value
    written (null for remove). Or an error() envelope: "invalid_op",
    "protected_path" (path ""), "not_found" (no such dashboard, or one never
    saved), "not_editable" (YAML mode), "bad_path", "path_exists" (insert on
    an existing key), "unexpected_target" (expect did not match - nothing
    written), "config_read_failed", "concurrent_modification" (changed
    since read - nothing written), "home_assistant_error" (the save was
    rejected - nothing written), or "config_write_unverified" (the save was
    accepted but the read-back differs - the write DID happen).
    """
    shown = url_path or "default"
    if op not in _PATCH_OPS:
        return error("invalid_op", f"op must be one of {', '.join(_PATCH_OPS)}, got {op!r}.",
                     url_path=shown, path=path)
    if op == "remove" and value is not None:
        return error("invalid_op", "op='remove' takes no value - omit it.",
                     url_path=shown, path=path)
    if not path:
        return error("protected_path",
                     "The empty path is the whole config. Replacing it is "
                     "update_dashboard_config()'s job, after get_dashboard().",
                     url_path=shown, path=path)

    mode, err = _dashboard_mode(url_path)
    if err:
        return err
    if mode is None:
        return error("not_found", "No dashboard with that url_path. Use list_dashboards().",
                     url_path=shown)
    if mode == "yaml":
        return error("not_editable",
                     "This dashboard is in YAML mode: its config lives in a file "
                     "Home Assistant's API cannot write. Edit the YAML file instead.",
                     url_path=shown)

    original, err = _read_config(url_path)
    if err:
        return err
    config = copy.deepcopy(original)
    try:
        if op == "insert":
            parent_path, _, last = path.rpartition(".")
            parent = _jsonpath.get(config, parent_path) if parent_path else config
            if isinstance(parent, list) and expect is not None:
                if not last.isdigit() or int(last) >= len(parent):
                    return error("unexpected_target",
                                 "expect was given for an append: there is no element "
                                 "at that index to check. Drop expect, or insert at an "
                                 "existing index.", url_path=shown, path=path)
                if why := _expect_mismatch(parent[int(last)], expect):
                    return error("unexpected_target", f"Nothing written: {why}.",
                                 url_path=shown, path=path, expect=expect)
            try:
                _jsonpath.insert(config, path, value)
            except _jsonpath.PathError as exc:
                code = "path_exists" if "already exists" in str(exc) else "bad_path"
                return error(code, str(exc), url_path=shown, path=path)
            old, new = None, value
        else:
            current = _jsonpath.get(config, path)
            if expect is not None and (why := _expect_mismatch(current, expect)):
                return error("unexpected_target", f"Nothing written: {why}.",
                             url_path=shown, path=path, expect=expect)
            if op == "set":
                old, new = _jsonpath.set_(config, path, value), value
            else:
                old, new = _jsonpath.remove(config, path), None
    except _jsonpath.PathError as exc:
        return error("bad_path", str(exc), url_path=shown, path=path)

    now, err = _read_config(url_path)
    if err:
        return err
    if now != original:
        return error("concurrent_modification",
                     "The dashboard changed between this call's read and its "
                     "save - someone else edited it. Nothing was written. Re-read "
                     "it (find_dashboard_cards / get_dashboard_outline) and retry.",
                     url_path=shown, path=path)

    msg: dict = {"type": "lovelace/config/save", "config": config}
    if url_path:
        msg["url_path"] = url_path
    result = _ws(msg)
    if err := ws_error(result):
        if err["detail"] == "Not supported":
            return error("not_editable",
                         "Home Assistant refused to save this dashboard (\"Not "
                         "supported\") - it is in YAML mode or recovery mode. "
                         "Nothing was written.", url_path=shown)
        return error("home_assistant_error",
                     f"Home Assistant rejected the save: {err['detail']}. Nothing "
                     "was written.", url_path=shown, path=path, ha_code=err["error"])

    stored, err = _read_config(url_path)
    if err or stored != config:
        return error("config_write_unverified",
                     "The save was accepted, but reading the dashboard back "
                     + ("failed" if err else "does not show the intended config")
                     + " - the write DID happen; check it with get_dashboard().",
                     url_path=shown, path=path, old=old, new=new)

    out = {"url_path": shown, "op": op, "path": path, "old": old, "new": new,
           "saved": True}
    parent_path, _, last = path.rpartition(".")
    if op in ("insert", "remove") and last.isdigit() and isinstance(
            _jsonpath.get(config, parent_path) if parent_path else config, list):
        out["shifted"] = (f"every element of {parent_path} after index {last} moved "
                          f"{'down' if op == 'insert' else 'up'} by one - paths into "
                          f"{parent_path} from before this call are stale")
    return out
