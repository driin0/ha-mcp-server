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
from tools._base import (
    mcp, _ws, _ws_multi, envelope, error, ws_error, ws_transport_error, WsTimeout,
)

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
        if not err and any(e["category_id"] == category
                           or e["name"].casefold() == category.casefold()
                           for e in other_entries):
            elsewhere.append(other)
    detail = f"No category {category!r} in scope {scope!r}."
    if elsewhere:
        detail += (f" A category with that name or id exists in {', '.join(elsewhere)} - "
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

    Returns: {scope, category_id, name, icon}, or an error() envelope -
    "invalid_scope", or Home Assistant's own error (e.g. a duplicate name).
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


# ---- assignment -------------------------------------------------------------

# What the frontend's helpers page lists by domain (frontend
# src/data/helpers/const.ts HELPER_DOMAINS). Deliberately local: _base's
# HELPER_DOMAINS drives list_helpers()/set_helper() and has no `schedule`.
_HELPER_DOMAINS = frozenset({
    "input_boolean", "input_button", "input_text", "input_number",
    "input_datetime", "input_select", "counter", "timer", "schedule",
})
_DOMAIN_SCOPES = {"automation": "automation", "script": "script", "scene": "scene"}

# Same bound as bulk_set_entity_labels() (tools/areas.py) and for the same
# reason: every command of a batch is sent before any reply is read.
_BULK_CATEGORY_MAX = 200


def _helper_platforms() -> tuple[set[str], dict | None]:
    """Integrations whose manifest says integration_type == "helper"
    (template, group, utility_meter, derivative, ...). The frontend lists
    their entities on the helpers page, so they take the "helpers" scope."""
    r = _ws({"type": "manifest/list"})
    if err := ws_error(r):
        return set(), err
    return {m["domain"] for m in r["result"] if m.get("integration_type") == "helper"}, None


def _scope_for(entity_id: str, entry: dict | None,
               helper_platforms: set[str]) -> tuple[str | None, dict | None]:
    """The category scope the UI shows `entity_id` under, or an error.

    automation/script/scene by domain; the helper domains and entities of a
    helper integration -> "helpers". Anything else is "not_categorizable":
    Home Assistant would store a category on it, but no page shows it.
    """
    if entry is None:
        return None, error(
            "not_in_registry",
            f"{entity_id!r} is not in the entity registry (an entity without a "
            "unique_id, e.g. defined in YAML without one). Categories are "
            "stored in the registry, so it cannot have one.",
            entity_id=entity_id)
    domain = entity_id.split(".", 1)[0]
    if domain in _DOMAIN_SCOPES:
        return _DOMAIN_SCOPES[domain], None
    if domain in _HELPER_DOMAINS or entry.get("platform") in helper_platforms:
        return "helpers", None
    return None, error(
        "not_categorizable",
        f"{entity_id!r} (integration {entry.get('platform')!r}) is not an "
        "automation, script, scene or helper. Home Assistant would accept a "
        "category on it, but no page in the UI would ever show it. Use "
        "labels (set_entity_labels) to group arbitrary entities.",
        entity_id=entity_id, platform=entry.get("platform"))


def _needs_manifests(entity_id: str) -> bool:
    domain = entity_id.split(".", 1)[0]
    return domain not in _DOMAIN_SCOPES and domain not in _HELPER_DOMAINS


def _named(category_id: str | None, entries: list[dict]) -> dict | None:
    if not category_id:
        return None
    name = next((e["name"] for e in entries if e["category_id"] == category_id), None)
    return {"category_id": category_id, "name": name}


@mcp.tool()
def set_entity_category(entity_id: str, category: str) -> dict:
    """
    Put one automation, script, scene or helper in a category, or take it
    out of its category.

    entity_id: e.g. 'automation.lavatrice_fine', 'timer.lavatrice'.
    category:  by category_id or exact name (case-insensitive) in the
               entity's scope; "" removes the entity from its category.

    The scope is derived from the entity: automation.* -> "automation",
    script.* -> "script", scene.* -> "scene"; input_*, counter, timer,
    schedule and entities of a helper integration (template, group,
    utility_meter, ...) -> "helpers". A name is looked up in THAT scope,
    so "Elettrodomestici" picks the helpers-scope id for a timer even if
    you only ever saw the automation-scope id. Only this scope changes;
    any category the entity has in another scope stays.

    Home Assistant stores any category id it is sent without checking it,
    so a wrong one would be saved and silently never shown. This tool
    resolves the category first and sends nothing when it does not exist in
    the entity's scope.

    Returns: {entity_id, scope, old: {category_id, name} | null, new:
    {category_id, name} | null}, or an error() envelope -
    "not_in_registry", "not_categorizable" (any other entity: use labels),
    "category_not_found" (with the names available in the scope and the
    scopes where that name exists), Home Assistant's own error, or
    "config_write_unverified" (the update was accepted but the registry
    entry Home Assistant returned does not carry the intended category).
    """
    r = _ws({"type": "config/entity_registry/get", "entity_id": entity_id})
    if err := ws_error(r):
        if err["error"] != "not_found":
            return err
        entry = None
    else:
        entry = r["result"]
    helper_platforms: set[str] = set()
    if entry is not None and _needs_manifests(entity_id):
        helper_platforms, err = _helper_platforms()
        if err:
            return err
    scope, err = _scope_for(entity_id, entry, helper_platforms)
    if err:
        return err
    entries, err = _list_categories(scope)
    if err:
        return err
    target_id = None
    if category:
        target, err = _resolve_category(scope, category, entries)
        if err:
            err["entity_id"] = entity_id
            return err
        target_id = target["category_id"]
    old_id = (entry.get("categories") or {}).get(scope)

    w = _ws({"type": "config/entity_registry/update", "entity_id": entity_id,
             "categories": {scope: target_id}})
    if err := ws_error(w):
        return err
    stored = ((w["result"].get("entity_entry") or {}).get("categories") or {}).get(scope)
    if stored != target_id:
        return error(
            "config_write_unverified",
            f"Home Assistant accepted the update but returned {entity_id!r} "
            f"with category {stored!r} in scope {scope!r}, not {target_id!r}. "
            "The write may or may not have landed - read it back with "
            "get_entity_registry() before retrying.",
            entity_id=entity_id, scope=scope, intended=target_id, stored=stored)
    return {"entity_id": entity_id, "scope": scope,
            "old": _named(old_id, entries), "new": _named(target_id, entries)}


@mcp.tool()
def bulk_set_entity_category(entity_ids: list[str], category: str) -> dict:
    """
    Put many entities in a category (or take them out with category="").

    entity_ids: up to 200 automations, scripts, scenes and helpers - they
                may mix scopes. Repeated ids are counted and sent once.
    category:   a NAME resolves separately in each entity's scope, so
                "Elettrodomestici" lands on the automation-scope id for
                automations and the helpers-scope id for helpers. An ID
                belongs to one scope only: entities of other scopes fail.

    Everything is resolved before anything is sent; entities that cannot
    take the category (not in the registry, not categorisable, category
    missing in their scope) are listed in `failed` with the reason and are
    not written. The rest go in one WebSocket batch, and each reply is
    checked: an update Home Assistant accepted but returned without the
    intended category is in `failed` as config_write_unverified, the same
    check set_entity_category() makes.

    Returns: {total, succeeded, failed: [{entity_id, reason}]} - each reason
    starts with a code: not_in_registry, not_categorizable,
    category_not_found, home_assistant_error or config_write_unverified,
    as set_entity_category() describes them. Or an error() envelope - "too_many_entities" (over 200, nothing sent), a
    registry/manifest read failure, or ws_transport_error()'s code when the
    connection never carried the batch (one systemic failure, not "every
    entity failed").
    """
    entity_ids = list(dict.fromkeys(entity_ids))  # each entity once, order kept
    if len(entity_ids) > _BULK_CATEGORY_MAX:
        return error(
            "too_many_entities",
            f"{len(entity_ids)} entity_ids given, {_BULK_CATEGORY_MAX} max per "
            "call - split the list into batches.",
            entity_count=len(entity_ids), max_entities=_BULK_CATEGORY_MAX)
    r = _ws({"type": "config/entity_registry/list"})
    if err := ws_error(r):
        return err
    registry = {e["entity_id"]: e for e in r["result"]}
    helper_platforms: set[str] = set()
    if any(_needs_manifests(eid) for eid in entity_ids):
        helper_platforms, err = _helper_platforms()
        if err:
            return err

    resolved: dict[str, tuple[str | None, str | None]] = {}  # scope -> (id, reason)
    failed, to_send = [], []
    for eid in entity_ids:
        scope, err = _scope_for(eid, registry.get(eid), helper_platforms)
        if err:
            failed.append({"entity_id": eid, "reason": f"{err['error']}: {err['detail']}"})
            continue
        if scope not in resolved:
            if not category:
                resolved[scope] = (None, None)
            else:
                target, err = _resolve_category(scope, category)
                if err and err["error"] != "category_not_found":
                    # The registry itself could not be read: one systemic
                    # failure, not a per-entity verdict. Nothing sent yet.
                    return err
                resolved[scope] = ((target["category_id"], None) if target
                                   else (None, f"{err['error']}: {err['detail']}"))
        target_id, reason = resolved[scope]
        if reason:
            failed.append({"entity_id": eid, "reason": reason})
            continue
        to_send.append((eid, scope, target_id,
                        {"type": "config/entity_registry/update", "entity_id": eid,
                         "categories": {scope: target_id}}))

    results = _ws_multi([m for *_, m in to_send]) if to_send else []
    if results and (transport_err := ws_transport_error(results[0])):
        return transport_err
    succeeded = 0
    for (eid, scope, target_id, _), res in zip(to_send, results):
        if not res.get("success"):
            msg = (res.get("error") or {}).get("message", "rejected")
            failed.append({"entity_id": eid, "reason": f"home_assistant_error: {msg}"})
            continue
        stored = (((res.get("result") or {}).get("entity_entry") or {})
                  .get("categories") or {}).get(scope)
        if stored != target_id:
            failed.append({"entity_id": eid, "reason":
                           f"config_write_unverified: accepted, but returned with "
                           f"{stored!r} in scope {scope!r} instead of {target_id!r}"})
            continue
        succeeded += 1
    return {"total": len(entity_ids), "succeeded": succeeded, "failed": failed}


# ---- shared by the list tools -------------------------------------------------

def category_context(scope: str, category: str = "",
                     registry: dict | None = None) -> dict:
    """What a list tool needs to show and filter by category in `scope`.

    registry: {entity_id: registry entry}, when the caller already read it.

    Returns {"error": envelope} when a `category` filter was given and it
    cannot be honoured (unknown in this scope, or a read failed) - a filter
    must never degrade into "nothing matched"; a timeout with a filter is
    raised, as for any read. Otherwise {"names": {id: name}, "assigned":
    {entity_id: id}, "registry": {entity_id: entry}, "filter_id": id |
    None, "note": str}. Without a filter, ANY failure of these reads -
    an error answer, a timeout, a dropped connection - yields empty maps
    and a note: the category is an optional column, and list_scripts /
    list_scenes / list_helpers listed fine before it existed.
    """
    def failed(code, detail):
        return {"names": {}, "assigned": {}, "registry": registry or {},
                "filter_id": None,
                "note": f"categories could not be read ({code}: {detail}) - "
                        "category shown as null"}

    msgs = [{"type": "config/category_registry/list", "scope": scope}]
    if registry is None:
        msgs.append({"type": "config/entity_registry/list"})
    try:
        results = _ws_multi(msgs) if len(msgs) > 1 else [_ws(msgs[0])]
    except (WsTimeout, OSError) as exc:
        if category:
            raise
        return failed(type(exc).__name__, str(exc))
    for res in results:
        if err := ws_error(res):
            return {"error": err} if category else failed(err["error"], err["detail"])
    entries = list(results[0]["result"])
    if registry is None:
        registry = {e["entity_id"]: e for e in results[1]["result"]}
    filter_id = None
    if category:
        target, err = _resolve_category(scope, category, entries)
        if err:
            return {"error": err}
        filter_id = target["category_id"]
    assigned = {eid: cid for eid, e in registry.items()
                if (cid := (e.get("categories") or {}).get(scope))}
    return {"names": {e["category_id"]: e["name"] for e in entries},
            "assigned": assigned, "registry": registry, "filter_id": filter_id, "note": ""}


def with_note(out: dict, note: str) -> dict:
    """Append `note` to an envelope's own note instead of replacing it."""
    if note:
        out["note"] = f"{out['note']}; {note}" if out.get("note") else note
    return out
