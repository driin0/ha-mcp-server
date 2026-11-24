"""Categories: Home Assistant's per-scope grouping of automations, scripts,
scenes and helpers.

Three Home Assistant facts shape everything here (source: core `dev`,
read 2026-11-24 - components/config/category_registry.py,
helpers/category_registry.py, components/config/entity_registry.py):

- Categories are keyed by SCOPE. The frontend uses exactly four:
  "automation", "script", "scene" and "helpers". The same name has a
  different category_id in each scope - "Elettrodomestici" for automations
  and "Elettrodomestici" for helpers are two unrelated categories.
- Names are unique within a scope, compared case-insensitively.
- config/entity_registry/update stores whatever category_id it is sent,
  without checking it exists or belongs to that scope. A wrong id is saved
  and then never shown anywhere in the UI. Resolving and validating the
  category is therefore this module's job, done before anything is sent.
"""
from tools._base import mcp, _ws, envelope, error, ws_error

CATEGORY_SCOPES = ("automation", "script", "scene", "helpers")


def _check_scope(scope: str) -> dict | None:
    if scope in CATEGORY_SCOPES:
        return None
    return error("invalid_scope",
                 f"{scope!r} is not a category scope. Use one of: "
                 f"{', '.join(CATEGORY_SCOPES)} ('helpers' covers timers, "
                 "input_* helpers, template and other helper entities).",
                 scope=scope)


def _list_categories(scope: str) -> tuple[list[dict], dict | None]:
    """Raw category entries of one scope, or an error() envelope."""
    r = _ws({"type": "config/category_registry/list", "scope": scope})
    if err := ws_error(r):
        return [], err
    return list(r["result"]), None


def _public(entry: dict) -> dict:
    return {"category_id": entry["category_id"], "name": entry["name"],
            "icon": entry.get("icon")}


def _resolve_category(scope: str, category: str,
                      entries: list[dict] | None = None) -> tuple[dict | None, dict | None]:
    """Find `category` in `scope`: an exact category_id first, otherwise a
    case-insensitive exact name. Never a partial match.

    Returns (entry, None) or (None, error envelope). On no match the
    envelope is "category_not_found", carrying `available` (the names in
    this scope) and `exists_in_scopes` (other scopes where that name does
    exist) - the usual mistake is a category created for automations and
    then applied to a helper, whose scope has no category of that name.
    """
    if entries is None:
        entries, err = _list_categories(scope)
        if err:
            return None, err
    for e in entries:
        if e["category_id"] == category:
            return e, None
    for e in entries:
        if e["name"].casefold() == category.casefold():
            return e, None
    elsewhere = []
    for other in CATEGORY_SCOPES:
        if other == scope:
            continue
        other_entries, err = _list_categories(other)
        if not err and any(e["name"].casefold() == category.casefold()
                           for e in other_entries):
            elsewhere.append(other)
    detail = f"No category {category!r} in scope {scope!r}."
    if elsewhere:
        detail += (f" A category with that name exists in {', '.join(elsewhere)} - "
                   "category ids differ per scope; create it in "
                   f"{scope!r} too with create_category().")
    return None, error("category_not_found", detail, scope=scope, category=category,
                       available=sorted((e["name"] for e in entries), key=str.casefold),
                       exists_in_scopes=elsewhere)


def _entities_in_category(scope: str, category_id: str) -> tuple[list[str], dict | None]:
    r = _ws({"type": "config/entity_registry/list"})
    if err := ws_error(r):
        return [], err
    return sorted(e["entity_id"] for e in r["result"]
                  if (e.get("categories") or {}).get(scope) == category_id), None


@mcp.tool()
def list_categories(scope: str, with_counts: bool = False) -> dict:
    """
    List the categories of one scope.

    scope: "automation", "script", "scene" or "helpers" ("helpers" covers
           timers, input_* helpers, template, utility_meter and other
           helper entities). Category ids differ per scope: the same name
           in two scopes is two categories with two ids.
    with_counts: also count the entities assigned to each category (one
           extra entity-registry read).

    Returns: {total, returned, offset, note?, categories: [{category_id,
    name, icon, entity_count?}]} sorted by name, or an error() envelope
    ("invalid_scope", or Home Assistant's own error when the registry
    cannot be read - never an empty list in that case).
    """
    if err := _check_scope(scope):
        return err
    entries, err = _list_categories(scope)
    if err:
        return err
    rows = [_public(e) for e in sorted(entries, key=lambda e: e["name"].casefold())]
    if with_counts:
        r = _ws({"type": "config/entity_registry/list"})
        if err := ws_error(r):
            return err
        counts: dict[str, int] = {}
        for ent in r["result"]:
            cid = (ent.get("categories") or {}).get(scope)
            if cid:
                counts[cid] = counts.get(cid, 0) + 1
        for row in rows:
            row["entity_count"] = counts.get(row["category_id"], 0)
    return envelope(rows, key="categories")


@mcp.tool()
def create_category(scope: str, name: str, icon: str = "") -> dict:
    """
    Create a category in one scope.

    scope: "automation", "script", "scene" or "helpers".
    name:  unique within the scope, case-insensitively - Home Assistant
           refuses a duplicate ("invalid_info", "The name '...' is already
           in use"). The same name may exist in other scopes; each gets its
           own id.
    icon:  optional MDI icon, e.g. "mdi:washing-machine".

    Returns: {scope, category_id, name, icon}, or an error() envelope.
    """
    if err := _check_scope(scope):
        return err
    msg = {"type": "config/category_registry/create", "scope": scope, "name": name}
    if icon:
        msg["icon"] = icon
    r = _ws(msg)
    if err := ws_error(r):
        return err
    return {"scope": scope, **_public(r["result"])}


@mcp.tool()
def update_category(scope: str, category: str, name: str = "", icon: str | None = None) -> dict:
    """
    Rename a category or change its icon.

    category: the category, by category_id or by exact name
              (case-insensitive), within `scope`.
    name:     new name; empty leaves it unchanged.
    icon:     new MDI icon; "" removes the icon; omitted leaves it unchanged.

    Assignments are by id, so a rename keeps every entity in the category.

    Returns: {scope, category_id, old: {name, icon}, new: {name, icon}}, or
    an error() envelope - "invalid_scope", "category_not_found" (with the
    names available in the scope and the scopes where that name does
    exist), "nothing_to_update" (neither name nor icon given; nothing
    sent), or Home Assistant's own error (e.g. a duplicate name).
    """
    if err := _check_scope(scope):
        return err
    entry, err = _resolve_category(scope, category)
    if err:
        return err
    msg = {"type": "config/category_registry/update", "scope": scope,
           "category_id": entry["category_id"]}
    if name:
        msg["name"] = name
    if icon is not None:
        msg["icon"] = icon or None
    if len(msg) == 3:
        return error("nothing_to_update", "Give a new name, an icon, or both.",
                     scope=scope, category_id=entry["category_id"])
    r = _ws(msg)
    if err := ws_error(r):
        return err
    new = r["result"]
    return {"scope": scope, "category_id": entry["category_id"],
            "old": {"name": entry["name"], "icon": entry.get("icon")},
            "new": {"name": new["name"], "icon": new.get("icon")}}


@mcp.tool()
def delete_category(scope: str, category: str) -> dict:
    """
    Delete a category from one scope.

    category: by category_id or exact name (case-insensitive) in `scope`.

    ⚠️ Home Assistant removes the category from every entity that had it;
    those entities become uncategorised in this scope. Their other scopes
    are untouched. The entities affected are read just before deleting and
    reported in `cleared_from`, so the assignment can be redone if the
    deletion was a mistake.

    Returns: {scope, category_id, name, deleted: true, cleared_from:
    [entity_id, ...]}, or an error() envelope ("invalid_scope",
    "category_not_found", or Home Assistant's own error).
    """
    if err := _check_scope(scope):
        return err
    entry, err = _resolve_category(scope, category)
    if err:
        return err
    affected, err = _entities_in_category(scope, entry["category_id"])
    if err:
        return err
    r = _ws({"type": "config/category_registry/delete", "scope": scope,
             "category_id": entry["category_id"]})
    if err := ws_error(r):
        return err
    return {"scope": scope, "category_id": entry["category_id"], "name": entry["name"],
            "deleted": True, "cleared_from": affected}
