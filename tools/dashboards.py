import copy
import json

from tools import _jsonpath
from tools._base import mcp, _ws, envelope, error, ws_error, WsTimeout


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
    if label := _label(node):
        parts.append(repr(label))
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
            recovery = (" (Home Assistant answers the same way in recovery mode, "
                        "when no dashboard config is loaded at all.)")
            if not url_path:
                hint = ("The default dashboard has never been saved from the UI: "
                        "Home Assistant generates it on the fly and has no stored "
                        "config to edit. Take control of it in the UI first." + recovery)
            elif "No config found" in err["detail"]:
                hint = ("This dashboard has never been saved, so there is no "
                        "stored config to edit yet." + recovery)
            else:
                hint = "No dashboard with that url_path. Use list_dashboards()."
            return None, error("not_found", f"{hint} ({err['detail']})",
                               url_path=url_path or "default")
        return None, error("config_read_failed",
                           f"Could not read the dashboard config: {err['error']}: "
                           f"{err['detail']}. Nothing was written.",
                           url_path=url_path or "default")
    return result["result"], None


def _same(a, b) -> bool:
    """JSON equality: True is not 1, 1 is not "1"."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _expect_mismatch(target, expect: dict) -> str | None:
    """None when every key of `expect` is in `target` with an equal value."""
    if not isinstance(target, dict):
        return f"the target is not a card or object but {_summary(target)}"
    wrong = [k for k, v in expect.items() if k not in target or not _same(target[k], v)]
    if wrong:
        shown = {k: target.get(k) for k in wrong}
        return (f"the target is {_summary(target)} - {', '.join(wrong)} "
                f"differ (found {json.dumps(shown, ensure_ascii=False)})")
    return None


_CARD_LISTS = ("views", "sections", "cards", "badges")


def _contained_cards(target, path: str) -> list[str]:
    """Every view/section/card inside `target` (at `path`), as "path (summary)".
    Empty when `target` holds none - then replacing or removing it cannot
    take other cards with it."""
    out: list[str] = []

    def walk(node, at):
        if isinstance(node, dict):
            for key in _CARD_LISTS:
                if isinstance(node.get(key), list):
                    walk_list(node[key], f"{at}.{key}")
            if isinstance(node.get("card"), dict):
                out.append(f"{at}.card ({_summary(node['card'])})")
                walk(node["card"], f"{at}.card")

    def walk_list(items, at):
        for i, item in enumerate(items):
            out.append(f"{at}.{i} ({_summary(item)})")
            walk(item, f"{at}.{i}")

    if isinstance(target, list) and path.rsplit(".", 1)[-1] in _CARD_LISTS:
        walk_list(target, path)
    else:
        walk(target, path)
    return out


@mcp.tool()
def patch_dashboard(
    url_path: str,
    path: str,
    value: dict | list | str | int | float | bool | None,
    op: str = "set",
    expect: dict | None = None,
    replace_container: bool = False,
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
    value:    required. The new value for set and insert (null is a real
              value and is written as null); null for remove.
    op:       "set"    - replace the value at `path`; the path must exist.
              "insert" - into a list: before index N, N == length appends;
                         into an object: a new key that must not exist yet.
                         The only way this tool ever creates anything.
              "remove" - delete the list element or key at `path`; `value`
                         must be null.
    expect:   strongly recommended for set/remove on list elements: keys the
              CURRENT target must have with these exact values, e.g.
              {"type": "tile", "entity": "sensor.lavatrice"}. If the card at
              that index is anything else, nothing is written. For an insert
              into a list it is checked against the element now at that
              index (an append, or a new object key, has none, so expect is
              refused there). An empty expect is refused.
    replace_container: set and remove refuse, by default, a target that
              holds other cards - a view, a section, a stack, a `cards`
              list - and name every card that would go with it. Matching
              the container's own `type` says nothing about what is inside
              it. Pass True only after reading that list.

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
    all. A registered dashboard's mode is checked before anything is read;
    the default dashboard ("") is not listed by lovelace/dashboards/list,
    so a YAML-mode default is only recognised when Home Assistant refuses
    the save - still "not_editable", still nothing written. To replace a
    whole config use update_dashboard_config().

    Returns: {url_path, op, path, old, new, saved: true, shifted?} - `old`
    is the value replaced or removed (null for insert), `new` the value
    written (null for remove). Or an error() envelope: "invalid_op",
    "container_target" (the target holds other cards - nothing written),
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
        return error("invalid_op", "op='remove' takes value=null.",
                     url_path=shown, path=path)
    if expect is not None and not expect:
        return error("invalid_op", "An empty expect checks nothing - name at least "
                     "one key the target must have, or omit expect.",
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
            if isinstance(parent, list) and not _jsonpath.is_index(last):
                raise _jsonpath.PathError(f"{last!r} is not a list index - "
                                          f"{parent_path or '<root>'} is a list of "
                                          f"length {len(parent)}")
            if isinstance(parent, dict) and expect is not None:
                return error("invalid_op",
                             "expect checks an existing target, and a new key has "
                             "none. Omit expect when inserting a key.",
                             url_path=shown, path=path)
            if isinstance(parent, list) and expect is not None:
                if int(last) >= len(parent):
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
            if not replace_container and (inside := _contained_cards(current, path)):
                return error(
                    "container_target",
                    f"Nothing written: {path} holds {len(inside)} other "
                    f"view(s)/section(s)/card(s) that this {op} would "
                    f"{'replace' if op == 'set' else 'remove'}: "
                    + "; ".join(inside)
                    + ". Patch the one card you mean (its path is above), or pass "
                    "replace_container=True if all of these should go.",
                    url_path=shown, path=path, would_affect=inside)
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
                         "supported\") - it is in YAML mode. Nothing was written.",
                         url_path=shown)
        if "recovery mode" in err["detail"]:
            return error("not_editable",
                         "Home Assistant is in recovery mode and refuses to save "
                         "dashboards. Nothing was written; restart it normally first.",
                         url_path=shown)
        return error("home_assistant_error",
                     f"Home Assistant rejected the save: {err['detail']}. Nothing "
                     "was written.", url_path=shown, path=path, ha_code=err["error"])

    try:
        stored, err = _read_config(url_path)
    except WsTimeout as exc:
        # The save already landed. Left to propagate, this read's timeout
        # would be reported as "a read, nothing changed, safe to repeat" -
        # and a retried insert would add the card twice.
        stored, err = None, error("read_timeout", str(exc))
    if err:
        return error("config_write_unverified",
                     "The save was accepted, so the write DID happen, but reading "
                     f"the dashboard back failed ({err['error']}). Check it with "
                     "get_dashboard() before retrying - an insert retried blindly "
                     "would add the card twice.",
                     url_path=shown, path=path, old=old, new=new)
    if stored != config:
        return error("config_write_unverified",
                     "Home Assistant accepted the save, but the stored dashboard "
                     "does not contain this change - it was discarded, or another "
                     "write replaced it straight after. Check it with get_dashboard() "
                     "and patch again if the change is missing.",
                     url_path=shown, path=path, old=old, new=new)

    out = {"url_path": shown, "op": op, "path": path, "old": old, "new": new,
           "saved": True}
    parent_path, _, last = path.rpartition(".")
    if op in ("insert", "remove") and _jsonpath.is_index(last) and isinstance(
            _jsonpath.get(config, parent_path) if parent_path else config, list):
        out["shifted"] = (f"every element of {parent_path} after index {last} moved "
                          f"{'down' if op == 'insert' else 'up'} by one - paths into "
                          f"{parent_path} from before this call are stale")
    return out


# ---- finding things in a dashboard -------------------------------------------
#
# A CARD is a dict reached through a `cards` list, a `card` key or a `badges`
# list, at any depth. Views and sections are containers. Every other dict -
# rows of an entities card, tile features, header/footer, custom-card
# internals - is content of the card holding it: searched as part of that
# card, never a match or a sibling of its own (otherwise one entity yields
# several matches for the same card and `siblings` fills with rows).

_NESTED_CARD_KEYS = ("cards", "card")
_CONTAINER_KEYS = ("cards", "sections", "badges")


def _own(node: dict, nested: tuple) -> dict:
    return {k: v for k, v in node.items() if k not in nested}


def _section_heading(section: dict) -> str | None:
    if isinstance(section.get("title"), str):
        return section["title"]
    for card in section.get("cards") or []:
        if isinstance(card, dict) and card.get("type") == "heading":
            heading = card.get("heading")
            return heading if isinstance(heading, str) else None
    return None


def _walk_cards(config: dict):
    """Yield every card as {path, node, depth, container_path, view_index,
    section_index}. Containers are walked, not yielded."""
    def cards_in(items, base, depth, ctx, strings=False):
        for i, card in enumerate(items if isinstance(items, list) else []):
            if isinstance(card, str) and strings:
                # Old-style badge: just an entity_id.
                yield {"path": f"{base}.{i}", "node": card, "depth": depth,
                       "container_path": base, **ctx}
                continue
            if not isinstance(card, dict):
                continue
            path = f"{base}.{i}"
            yield {"path": path, "node": card, "depth": depth, "container_path": base, **ctx}
            yield from nested(card, path, depth + 1, ctx)

    def one(card, path, depth, container, ctx):
        yield {"path": path, "node": card, "depth": depth, "container_path": container, **ctx}
        yield from nested(card, path, depth + 1, ctx)

    def nested(card, path, depth, ctx):
        if isinstance(card.get("cards"), list):
            yield from cards_in(card["cards"], f"{path}.cards", depth, ctx)
        if isinstance(card.get("card"), dict):
            yield from one(card["card"], f"{path}.card", depth, path, ctx)
        fields = card.get("custom_fields")
        if isinstance(fields, dict):  # custom:button-card embedding cards
            for name, field in fields.items():
                if isinstance(field, dict) and isinstance(field.get("card"), dict):
                    yield from one(field["card"], f"{path}.custom_fields.{name}.card",
                                   depth, f"{path}.custom_fields.{name}", ctx)

    for v, view in enumerate(config.get("views") or []):
        if not isinstance(view, dict):
            continue
        ctx = {"view_index": v, "section_index": None}
        header = view.get("header")
        if isinstance(header, dict) and isinstance(header.get("card"), dict):
            yield from one(header["card"], f"views.{v}.header.card", 0,
                           f"views.{v}.header", ctx)
        yield from cards_in(view.get("cards"), f"views.{v}.cards", 0, ctx)
        for s, section in enumerate(view.get("sections") or []):
            if isinstance(section, dict):
                yield from cards_in(section.get("cards"), f"views.{v}.sections.{s}.cards", 0,
                                    {"view_index": v, "section_index": s})
        yield from cards_in(view.get("badges"), f"views.{v}.badges", 0, ctx, strings=True)


def _references(card, entity: str) -> bool:
    """The card itself (not a nested card) names `entity`: entity /
    entity_id / target.entity_id, a row of `entities`, a picture-elements
    element, or a conditional card's condition."""
    if isinstance(card, str):  # old-style badge
        return card == entity
    if not isinstance(card, dict):
        return False

    def hit(value):
        return value == entity or (isinstance(value, list) and entity in value)

    if hit(card.get("entity")) or hit(card.get("entity_id")):
        return True
    target = card.get("target")
    if isinstance(target, dict) and hit(target.get("entity_id")):
        return True
    for key in ("entities", "elements", "conditions"):
        rows = card.get(key)
        for row in rows if isinstance(rows, list) else []:
            if row == entity or (isinstance(row, dict) and hit(row.get("entity"))):
                return True
    return False


def _is_js_template(value) -> bool:
    """button-card style `[[[ ... ]]]` JavaScript, not a human label."""
    return isinstance(value, str) and value.lstrip().startswith("[[[")


def _label(node) -> str:
    """The card's human-readable heading/title/name, skipping JS templates
    (searched by `text`, not `heading`)."""
    if not isinstance(node, dict):
        return ""
    for key in ("heading", "title", "name"):
        value = node.get(key)
        if isinstance(value, str) and value and not _is_js_template(value):
            return value
    return ""


def _view_info(config: dict, index: int) -> dict:
    view = config["views"][index]
    return {"index": index, "title": view.get("title"), "path": view.get("path")}


def _type_of(node):
    return node.get("type") if isinstance(node, dict) else "badge"


def _own_text(node) -> str:
    own = _own(node, _NESTED_CARD_KEYS) if isinstance(node, dict) else node
    return json.dumps(own, ensure_ascii=False).casefold()


def _card_brief(card: dict) -> dict:
    return {"path": card["path"], "type": _type_of(card["node"]),
            "summary": _summary(card["node"])}


def _load_for_reading(url_path: str) -> tuple[dict | None, dict | None]:
    mode, err = _dashboard_mode(url_path)
    if err:
        return None, err
    if mode is None:
        return None, error("not_found", "No dashboard with that url_path. Use list_dashboards().",
                           url_path=url_path)
    return _read_config(url_path)


@mcp.tool()
def find_dashboard_cards(url_path: str = "", entity: str = "", heading: str = "",
                         type: str = "", text: str = "", limit: int = 50) -> dict:
    """
    Find cards in a dashboard and return their paths, for patch_dashboard().

    url_path: as get_dashboard() - "" is the default dashboard.
    Filters (at least one; all given filters must match):
      entity:  the card itself names this entity_id - `entity`, `entity_id`,
               a row of `entities`, or target.entity_id. Exact match. A
               stack does not match through the cards inside it; the inner
               card is its own match.
      heading: case-insensitive substring of a card's heading/title/name
               (JS templates excluded - use text for those). When heading
               is the ONLY filter, a SECTION whose title or first heading
               card contains it matches too - kind "section", with its
               cards in `children` (so a section and its heading card can
               both appear).
      type:    exact card type, e.g. "tile", "custom:button-card".
      text:    case-insensitive substring anywhere in the card's own JSON
               (not its nested cards) - finds templates/JS that mention an
               entity without referencing it as a field.
    limit: max matches (default 50).

    A card is anything in a `cards` list, under a `card` key (also in a
    sections view's `header` and a button-card's `custom_fields`), or in
    `badges` (including old-style entity_id strings). Rows of an entities
    card, picture-elements elements and tile features are part of their
    card, not cards of their own. Root `button_card_templates` entries are
    searched too (kind "template"; a name containing "." has path null
    and a `reason`).

    Each match: {path, kind: "card", type, summary, view: {index, title,
    path}, section: {index, heading} | null, container_path, siblings:
    [{path, type, summary}]} - siblings are the OTHER cards in the same
    list. Read them before replacing or removing anything: a section often
    holds more than the block you are looking for. A card nested inside a
    stack also has `section_cards`: the top-level cards of its section. A section match has
    kind "section" and `children` instead of siblings.

    Returns: {total, returned, offset, note?, matches: [...]}, or an error()
    envelope ("missing_filter", "not_found", "config_read_failed").
    """
    if not (entity or heading or type or text):
        return error("missing_filter", "Give at least one of entity, heading, type, text.")
    config, err = _load_for_reading(url_path)
    if err:
        return err
    cards = list(_walk_cards(config))
    by_container: dict[str, list] = {}
    for c in cards:
        by_container.setdefault(c["container_path"], []).append(c)

    def section_info(c):
        if c["section_index"] is None:
            return None
        section = config["views"][c["view_index"]]["sections"][c["section_index"]]
        return {"index": c["section_index"], "heading": _section_heading(section)}

    matches = []
    if heading and not (entity or type or text):
        for v, view in enumerate(config.get("views") or []):
            if not isinstance(view, dict):
                continue
            for s, section in enumerate(view.get("sections") or []):
                if not isinstance(section, dict):
                    continue
                title = _section_heading(section) or ""
                if heading.casefold() in title.casefold():
                    base = f"views.{v}.sections.{s}"
                    matches.append({
                        "path": base, "kind": "section", "type": section.get("type"),
                        "summary": f"section {title!r}", "view": _view_info(config, v),
                        "section": {"index": s, "heading": title},
                        "container_path": f"views.{v}.sections",
                        "children": [_card_brief(c) for c in by_container.get(f"{base}.cards", [])],
                    })
    for c in cards:
        node = c["node"]
        if entity and not _references(node, entity):
            continue
        if type and _type_of(node) != type:
            continue
        if heading and heading.casefold() not in _label(node).casefold():
            continue
        if text and text.casefold() not in _own_text(node):
            continue
        matches.append({
            "path": c["path"], "kind": "card", "type": _type_of(node),
            "summary": _summary(node), "view": _view_info(config, c["view_index"]),
            "section": section_info(c), "container_path": c["container_path"],
            "siblings": [_card_brief(o) for o in by_container[c["container_path"]]
                         if o["path"] != c["path"]],
        })
        if c["depth"] > 0 and c["section_index"] is not None:
            # Nested in a stack: siblings are the stack's children. The
            # section around it is where neighbours get lost, so show it too.
            top = f"views.{c['view_index']}.sections.{c['section_index']}.cards"
            matches[-1]["section_cards"] = [_card_brief(o) for o in by_container.get(top, [])]
    templates = config.get("button_card_templates")
    if isinstance(templates, dict) and not heading:
        for name, tpl in templates.items():
            if entity and not _references(tpl, entity):
                continue
            if type and _type_of(tpl) != type:
                continue
            if text and text.casefold() not in _own_text(tpl):
                continue
            match = {"path": None if "." in name else f"button_card_templates.{name}",
                     "kind": "template", "type": _type_of(tpl), "summary": f"template {name!r}",
                     "view": None, "section": None, "container_path": "button_card_templates",
                     "siblings": []}
            if match["path"] is None:
                match["reason"] = (f"the template name {name!r} contains '.', so no "
                                   "dotted path can address it - edit it with "
                                   "update_dashboard_config()")
            matches.append(match)
    return envelope(matches, key="matches", limit=limit)


@mcp.tool()
def get_dashboard_outline(url_path: str = "") -> dict:
    """
    A compact map of a dashboard: every view, section and card with its
    path and a one-line summary - a few hundred bytes per view instead of
    the full config get_dashboard() returns.

    url_path: as get_dashboard() - "" is the default dashboard.

    Returns: {url_path, views: [{path, title, url, sections: [{path,
    heading, cards}], cards, badges}], root_keys, strategy?} where each
    card is {path, type, summary, depth} (depth 1+ = inside a stack or
    another card, listed right after its parent). root_keys lists the
    other top-level keys - e.g. {"button_card_templates": ["wm_btn"]}
    (the template names, addressable as button_card_templates.<name>);
    `unaddressable` lists the entries whose name contains "." - no dotted
    path can reach them, so they need update_dashboard_config().
    A strategy dashboard has no stored views: `strategy` holds its config.
    Or an error() envelope ("not_found", "config_read_failed").
    """
    config, err = _load_for_reading(url_path)
    if err:
        return err
    # Group cards by the top-level list they live under (view cards, a
    # section's cards, badges), keeping walk order so children follow
    # their parent.
    groups: dict[str, list] = {}
    for c in _walk_cards(config):
        parts = c["path"].split(".")
        if parts[2] == "sections":
            root = ".".join(parts[:5])          # views.V.sections.S.cards
        else:
            root = ".".join(parts[:3])          # views.V.cards / views.V.badges
        groups.setdefault(root, []).append({
            "path": c["path"], "type": _type_of(c["node"]),
            "summary": _summary(c["node"]), "depth": c["depth"]})
    views = []
    for v, view in enumerate(config.get("views") or []):
        if not isinstance(view, dict):
            continue
        views.append({
            "path": f"views.{v}", "title": view.get("title"), "url": view.get("path"),
            "sections": [{"path": f"views.{v}.sections.{s}",
                          "heading": _section_heading(section),
                          "cards": groups.get(f"views.{v}.sections.{s}.cards", [])}
                         for s, section in enumerate(view.get("sections") or [])
                         if isinstance(section, dict)],
            "header": groups.get(f"views.{v}.header", []),
            "cards": groups.get(f"views.{v}.cards", []),
            "badges": groups.get(f"views.{v}.badges", []),
        })
    root_keys = {k: (sorted(val) if isinstance(val, dict) else type(val).__name__)
                 for k, val in config.items() if k not in ("views", "strategy")}
    unaddressable = [f"{k}.{name}" for k, val in config.items()
                     if isinstance(val, dict) and k not in ("views", "strategy")
                     for name in sorted(val) if "." in name]
    out = {"url_path": url_path or "default", "views": views, "root_keys": root_keys,
           "unaddressable": unaddressable}
    if "strategy" in config:
        out["strategy"] = config["strategy"]
    return out
