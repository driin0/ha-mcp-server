import httpx

from tools._aliases import PathError, get_path, set_path, stored_format, to_modern, to_stored
from tools._base import (
    mcp, HA_URL, HEADERS, _slug, _ws, confirm_entity_exists, envelope, error,
    observe_actuation, rest_error, wait_for_entity, ws_error,
)

# patch_automation() path segments (root only - not e.g. "actions.0.id",
# an action step's own optional step id, which is unrelated) that must
# never be written, because they hold the automation's identity rather
# than its behaviour - see patch_automation()'s own docstring and
# _refuse_protected_path() for why "id" specifically is here.
#
# "use_blueprint.path" - which blueprint file an automation follows - was
# deliberately NOT added: swapping the blueprint a caller explicitly asked
# to swap is a legitimate edit that leaves the automation's own id and
# entity_id untouched, unlike changing `id` itself, which orphans both. A
# caller who changes it without also revisiting `use_blueprint.input` may
# get a broken automation, but not a second, silently armed one under a
# different id - the specific, structural failure this set exists to
# prevent.
_PROTECTED_PATCH_ROOT_PATHS = {"id"}


def _resolve_automation_id(entity_id: str, client: httpx.Client) -> tuple[str | None, dict | None]:
    """Resolve the config id Home Assistant's automation config API is
    keyed by, from entity_id's own registered state - and hand back that
    same state object, since a caller that also needs one of its other
    attributes (get_automation()'s `mode`, for a blueprint automation -
    see its own docstring) would otherwise have to read the identical URL
    a second time.

    A UI-created automation carries a numeric timestamp config id,
    unrelated to its entity_id's own object_id, in the entity's `id`
    attribute - see delete_automation()'s docstring for how this was
    measured live. An automation created by create_automation() in this
    codebase has the two equal by construction, so falling back to the
    slug when the entity has no `id` attribute is correct for both
    origins.

    Returns (config_id, state). Both None together mean entity_id has no
    registered state at all - the caller decides what that means (does
    not exist, or a config might still be found by slug alone; see
    _fetch_config()).
    """
    r = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    state = r.json()
    numeric_id = state.get("attributes", {}).get("id")
    config_id = str(numeric_id) if numeric_id else entity_id.removeprefix("automation.")
    return config_id, state


def _fetch_config(automation_id: str, slug: str, client: httpx.Client) -> tuple[str | None, dict | None]:
    """GET an automation's stored config, trying automation_id first and
    falling back to slug (entity_id's own object_id) when that 404s and
    the two differ.

    The id read from an entity's state can be stale, absent, or simply
    not yet how a not-yet-created automation is addressed - a config can
    exist under its slug with no numeric id anywhere to have resolved.

    Returns (id_that_worked, config) on success, (None, None) when
    neither id resolves to a stored config.
    """
    r = client.get(f"{HA_URL}/api/config/automation/config/{automation_id}",
                   headers=HEADERS, timeout=10)
    if r.status_code != 404:
        r.raise_for_status()
        return automation_id, r.json()
    if automation_id != slug:
        r2 = client.get(f"{HA_URL}/api/config/automation/config/{slug}",
                        headers=HEADERS, timeout=10)
        if r2.status_code != 404:
            r2.raise_for_status()
            return slug, r2.json()
    return None, None


def _resolve_and_fetch(entity_id: str, slug: str) -> tuple[str, str | None, dict | None, dict | None, dict | None]:
    """Resolve entity_id's config id, read its state and fetch its stored
    config in one guarded call - the two-step read get_automation(),
    update_automation() and patch_automation() all start with, before
    they can do anything else.

    Wraps _resolve_automation_id() and _fetch_config() so a transient
    failure while reading (a revoked token, a 500 from an overloaded
    instance) is folded into an error() envelope instead of escaping as an
    uncaught httpx.HTTPStatusError. create_automation()'s own pre-create
    collision check already gets this guarantee for its own read (see its
    "collision_check_failed" branch and the comment explaining why a check
    that cannot run must not proceed as if it had); until now every OTHER
    reader of a stored automation config in this module - this one's three
    callers, and delete_automation()'s own _resolve_automation_id() call,
    guarded the same way independently in its own code, since it never
    calls _fetch_config() at all - let that same class of failure raise
    uncaught instead.

    Returns (automation_id, resolved_id, raw, state, error). `error` is
    non-None only when the read itself failed outright - never for an
    ordinary "no such automation", which is still `raw is None` with
    `error` None, exactly as before this existed. When `error` is set,
    the other four elements are meaningless; every caller returns `error`
    immediately without reading them. `state` is entity_id's own state
    object (or None when it has none) - get_automation() reads its `mode`
    attribute from this rather than performing a second GET of the
    identical URL _resolve_automation_id() already read.
    """
    try:
        with httpx.Client() as client:
            resolved_from_state, state = _resolve_automation_id(entity_id, client)
            automation_id = resolved_from_state or slug
            resolved_id, raw = _fetch_config(automation_id, slug, client)
    except httpx.HTTPStatusError as exc:
        return slug, None, None, None, error(
            "config_read_failed",
            f"Could not read {entity_id!r}'s stored config - the read "
            f"itself failed ({exc.response.status_code}), which is not "
            "the same as the automation not existing. Nothing was "
            "changed.",
            entity_id=entity_id, status=exc.response.status_code,
        )
    return automation_id, resolved_id, raw, state, None


def _set_and_verify_enabled(entity_id: str, enabled: bool, *,
                            arm_when_enabling: bool = True) -> dict:
    """Send automation.turn_on/turn_off and confirm the entity's state
    actually reflects `enabled` afterward, the way create_automation()'s own
    docstring measured necessary live: ten automations created with
    enabled=False, config POST and turn_off sent back-to-back with no wait,
    left 9 of 10 still armed - the disable landed on an entity_id that had
    not registered yet and was accepted as a 200 [] no-op, the same way any
    call at a target that is not there yet is (see confirm_entity_exists()).
    wait_for_entity() closes that race before the toggle is sent at all.

    Shared by create_automation() and update_automation() - both need
    exactly this guarantee, not two independent implementations that could
    silently drift apart. An automation that reports itself disabled while
    still armed is this project's founding bug; every caller that can
    change `enabled` gets the same treatment.

    arm_when_enabling: whether an enabled=True request actively sends
      automation.turn_on before verifying, or only waits for the entity
      and observes whatever state is already there. True (the default) is
      right for a caller whose `enabled` is only ever an explicit ask -
      update_automation()'s `enabled` parameter defaults to None ("leave
      it alone"), so a caller passing True here always deliberately asked
      to (re-)arm it, and the toggle is the honest way to do that. False
      is right for create_automation(), whose `enabled` parameter defaults
      to True with no sentinel to tell "the caller explicitly wants it
      armed" apart from "the caller did not say anything" - and Home
      Assistant already arms every genuinely new automation by construction,
      so no active toggle is ever needed for that case anyway. What False
      prevents: re-running create_automation() (overwrite=True) over an
      automation a person had deliberately turned off must not silently
      re-arm it just because `enabled` defaulted to True - measured live,
      before this parameter existed, it did exactly that. A disable
      (`enabled=False`) is always actively sent regardless of this flag -
      turning something off on request has no equivalent "maybe the caller
      did not mean it" ambiguity, and is the direction this project's
      founding bug is actually about.

    Returns one of:
      error("automation_not_registered", ...) - entity_id never registered
        a state at all, so `enabled` could not be changed or confirmed.
        Usually the registration race wait_for_entity() waits out - but
        not always: an instance whose configuration.yaml has no
        `automation:` key at all (nothing loads automations.yaml, though
        `default_config:` alone does not add it - measured live against a
        hand-built configuration.yaml) never registers ANY automation
        entity, no matter how long this waits. A stock instance set up
        through Home Assistant's own onboarding flow always has this key;
        a minimal or hand-written configuration.yaml might not. This error
        looks identical either way from here - a caller that keeps seeing
        it after a config write may be looking at the second case, not a
        slow retry.
      error("automation_not_disabled"|"automation_state_unverified", ...,
        enabled=bool, state=str) - the entity exists, but its state after
        the service call does not match what was requested. The code names
        the safety-relevant direction explicitly: "not_disabled" for a
        disable that did not take, "state_unverified" for an enable that
        did not.
      {"enabled": bool, "verified": True, "state": str} - confirmed.

    Callers add their own identifying fields (automation_id, entity_id,
    whatever else belongs in their own result shape) - this helper only
    reports what it itself determined, so two callers with different
    surrounding context do not have to agree on one error shape.
    """
    if not wait_for_entity(entity_id):
        return error(
            "automation_not_registered",
            f"{entity_id} has no registered state, so its enabled state "
            "could not be changed or confirmed - it may still be in its "
            "previous state. Check manually before relying on it.",
        )
    if not enabled or arm_when_enabling:
        with httpx.Client() as client:
            client.post(
                f"{HA_URL}/api/services/automation/"
                f"{'turn_on' if enabled else 'turn_off'}",
                headers=HEADERS,
                json={"entity_id": entity_id},
                timeout=10,
            )

    expected = "on" if enabled else "off"
    obs = observe_actuation(entity_id, lambda s: s["state"] == expected)
    if not obs["exists"]:
        return error("automation_not_registered",
                     f"{entity_id} has no registered state.")
    if not obs["verified"]:
        return error(
            "automation_not_disabled" if not enabled else "automation_state_unverified",
            f"{entity_id}'s state could not be confirmed as {expected!r} - "
            f"observed {obs['state']['state']!r}. Treat its enabled/disabled "
            "state as unknown until verified manually.",
            enabled=obs["state"]["state"] == "on", state=obs["state"]["state"],
        )
    return {"enabled": obs["state"]["state"] == "on", "verified": True,
            "state": obs["state"]["state"]}


@mcp.tool()
def list_automations(search: str = "", label: str = "", limit: int = 50,
                     offset: int = 0) -> dict:
    """
    List automations with their state and last triggered time.

    search: optional substring filter on automation name (case-insensitive)
    label:  filter by label ID (use list_labels() to find label IDs)
    limit:  max automations to return (default 50, 0 for no limit)
    offset: skip the first N (for pagination)

    Returns: {total, returned, offset, note?, automations: [{entity_id, name,
             state, last_triggered, labels}]}

    `total` counts what matched the filters, not what was returned: when it
    exceeds `returned`, `note` says so. An instance with a few hundred
    automations is normal, so filter by label before raising the limit.
    """
    r = _ws({"type": "config/entity_registry/list"})
    registry_err = ws_error(r)
    if registry_err and label:
        # Filtering by a label we could not read would silently return
        # nothing, which is the failure mode this work removes.
        return registry_err
    registry = ({} if registry_err
                else {e["entity_id"]: e for e in r["result"]})

    with httpx.Client() as client:
        resp = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
        resp.raise_for_status()

    automations = []
    for s in resp.json():
        if not s["entity_id"].startswith("automation."):
            continue
        attrs = s.get("attributes", {})
        name = attrs.get("friendly_name", s["entity_id"])
        if search and search.lower() not in name.lower():
            continue
        labels = list(registry.get(s["entity_id"], {}).get("labels", []))
        if label and label not in labels:
            continue
        automations.append({
            "entity_id": s["entity_id"],
            "name": name,
            "state": s["state"],
            "last_triggered": attrs.get("last_triggered"),
            "labels": labels,
        })

    automations.sort(key=lambda x: x["name"])
    return envelope(automations, key="automations", limit=limit, offset=offset,
                    offset_paginated=True)


@mcp.tool()
def create_automation(
    name: str,
    trigger: list,
    action: list,
    condition: list = None,
    description: str = "",
    mode: str = "single",
    enabled: bool = True,
    overwrite: bool = False,
) -> dict:
    """
    Create a new automation, or replace one this same tool created earlier
    under the exact same name (see `overwrite`). The automation's id is
    derived from `name` through a lossy slug - which is also what this
    tool cannot get past: it can never reach a UI-created automation, no
    matter what `overwrite` is set to, because a UI-created automation's
    config id is a numeric timestamp unrelated to its name, and this tool
    only ever addresses the slug it derives. To change an existing
    automation in place - UI-created or not, without touching its id -
    use update_automation() (whole fields) or patch_automation() (one
    value by dotted path) instead; this tool starts a config from scratch
    every time it is called.

    trigger, condition and action must be valid HA trigger/condition/action objects.

    mode: 'single' (default), 'restart', 'queued', or 'parallel' - Home
      Assistant's own automation run modes, passed straight through.

    overwrite: the automation id is derived from `name` through a lossy
      slug (see _slug()) - "Morning lights" and "Morning, lights!" both
      become "morning_lights", so two different names can collide on one
      id. By default a name that collides with an existing automation
      under a *different* alias is refused ("id_collision") rather than
      silently replacing its definition - the id cannot be made unique
      without changing the scheme, so refusing is the honest default. Pass
      overwrite=True to replace it deliberately. Calling again with the
      exact same `name` is treated as an intentional update, not a
      collision, and always succeeds without this flag. Even with
      overwrite=True, this can only ever replace an automation this tool's
      own slug scheme already owns - never a UI-created automation, whose
      id this tool has no way to derive or address (see above).

    Example — turn on a light at sunset:
      name: "Turn on light at sunset"
      trigger: [{"platform": "sun", "event": "sunset"}]
      action: [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}]

    Example — notify when door opens:
      name: "Notify door open"
      trigger: [{"platform": "state", "entity_id": "binary_sensor.front_door", "to": "on"}]
      action: [{"service": "notify.mobile_app_myphone", "data": {"message": "Door open!"}}]

    Returns: {automation_id, entity_id, enabled, verified, state, result} on
    a config Home Assistant accepted, or an error() envelope - "id_collision"
    (see `overwrite` above), "collision_check_failed" when that check
    itself could not be performed (a transient error reading the existing
    config - nothing is created; pass overwrite=True to proceed without
    the check if you are sure), or "automation_not_registered"/
    "automation_not_disabled"/"automation_state_unverified" when the
    entity's state after creation could not confirm what was requested.

    `enabled` and `state` report what was actually observed after creation,
    not the request. Home Assistant arms every new automation ("on") the
    instant it registers, and disabling it is a second, separate service
    call - sending that call before the entity has registered lands on an
    entity_id that does not exist yet and is accepted as a 200 [] no-op,
    the same way any call at a target that is not there yet is (see
    confirm_entity_exists()), leaving the automation silently armed.
    Measured live: ten automations created with enabled=False, config POST
    and turn_off sent back-to-back with no wait - 9 of 10 stayed "on". This
    tool waits for the entity to register before sending turn_off, then
    reads its state back to confirm the request actually landed; `verified`
    is true only when it did. A safety-relevant automation created disabled
    must be checked by its actual state, not assumed from the request - so
    a state that cannot be confirmed is an error() return, never a bare
    success.

    enabled=True (the default) never actively arms anything - it only
    waits for the entity to register and then observes whatever state is
    already there, the same way `enabled` had no default to distinguish
    "explicitly requested" from "just the default" before this parameter
    existed at all. This matters specifically for `overwrite=True`: Home
    Assistant does not reset an existing automation's armed state on a
    config-only update (see tests/fakeha.py's POST handler for the
    live-measured behaviour this models), so re-running create_automation()
    over an automation a person had deliberately turned off, without
    touching `enabled`, correctly reports it still off -
    error("automation_state_unverified") - rather than silently re-arming
    it because the parameter defaulted to True. Only enabled=False is ever
    actively sent as a service call; a genuinely new automation is armed
    by Home Assistant's own construction the instant it registers, so no
    active turn_on is ever needed for that case either.
    """
    automation_id = _slug(name)
    entity_id = f"automation.{automation_id}"

    if not overwrite:
        # automation_id doubles as its own slug here (nothing exists yet
        # to read a different config id from), so this is a single GET
        # with no fallback attempt: automation_id == slug, so
        # _fetch_config()'s `if automation_id != slug` guard never fires
        # and no second, identical request is sent - see its own
        # docstring. A 404 (no such id) still falls through as "no
        # collision", same as always.
        #
        # Every OTHER non-2xx status (a transient 500, an unauthorized
        # 401) used to fall through the same way as a 404 - the old
        # comment here said a transient failure "should not block a
        # legitimate create". That was wrong: this check exists so a
        # lossy slug cannot silently replace someone else's automation,
        # and a check that cannot run and proceeds anyway has re-enabled
        # exactly the silent replacement it guards against - the same
        # shape of fault as an is_state() read on a renamed entity, in a
        # different costume. It is not a fallthrough any more:
        # _fetch_config() raises via its own raise_for_status() for
        # anything but a 404, and that is caught here and reported as
        # its own named error() - "the check could not be performed" -
        # instead of either silently vouching for a state it never
        # confirmed, or escaping as a bare, uncaught HTTPStatusError.
        try:
            with httpx.Client() as client:
                _, existing = _fetch_config(automation_id, automation_id, client)
        except httpx.HTTPStatusError as exc:
            return error(
                "collision_check_failed",
                f"Could not confirm whether {entity_id!r} already holds a "
                "different automation - the collision check itself failed "
                f"({exc.response.status_code}), so nothing was created. "
                "Pass overwrite=True to proceed deliberately without this "
                "check, if you are sure no other automation occupies this id.",
                automation_id=automation_id, entity_id=entity_id,
                status=exc.response.status_code,
            )
        if existing is not None:
            existing_alias = existing.get("alias", "")
            if existing_alias != name:
                return error(
                    "id_collision",
                    f"{entity_id!r} already holds a different automation "
                    f"({existing_alias!r}) - {name!r} slugs to the same id "
                    "and would silently replace its definition. Pass "
                    "overwrite=True to replace it deliberately, or choose a "
                    "name that slugs differently.",
                    automation_id=automation_id, entity_id=entity_id,
                    existing_alias=existing_alias, requested_name=name,
                )

    payload = {
        "alias": name,
        "description": description,
        "trigger": trigger,
        "condition": condition or [],
        "action": action,
        "mode": mode,
    }
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        create_result = r.json()

    # _set_and_verify_enabled() waits for the entity to register before
    # sending turn_on/turn_off - see its own docstring for the race this
    # closes, measured live against exactly this call site.
    #
    # arm_when_enabling=False: `enabled` defaults to True here with no way
    # to tell "the caller explicitly wants it armed" apart from "the
    # caller did not say anything" - and Home Assistant already arms a
    # genuinely new automation by construction. Sending an active turn_on
    # regardless (the previous behaviour) meant re-running this over an
    # automation a person had deliberately turned off - overwrite=True,
    # same name - silently re-armed it; see this function's own
    # docstring for the measurement. Observing without asserting is what
    # this parameter buys back.
    outcome = _set_and_verify_enabled(entity_id, enabled, arm_when_enabling=False)
    if "error" in outcome:
        outcome["automation_id"] = automation_id
        outcome["entity_id"] = entity_id
        outcome["result"] = create_result
        return outcome
    return {
        "automation_id": automation_id,
        "entity_id": entity_id,
        **outcome,
        "result": create_result,
    }


@mcp.tool()
def delete_automation(entity_id: str) -> dict:
    """
    Delete an automation by entity_id (e.g. 'automation.turn_on_light_at_sunset').

    Home Assistant's delete endpoint is keyed by the automation's own config
    id, not by entity_id. An automation created by create_automation() in
    this tool has the two equal - the same slug is used as both - but one
    created through the Home Assistant UI editor gets a numeric timestamp
    config id that is independent of its entity_id's object_id, since the
    UI derives the entity_id from the alias separately. Measured live: an
    automation saved from the UI with alias "Morning Lights UI Style" got
    entity_id 'automation.morning_lights_ui_style' and config id
    '1690221234567' - deleting by the entity_id's own slug answers 400
    ("Resource not found") even though the automation exists; deleting by
    the numeric id read from its `id` attribute succeeds. This tool reads
    that attribute from the entity's own state before deleting, so it works
    for automations created via the HA UI too, not only ones this tool
    created itself.

    Only works for automations stored in the UI-editable automation
    registry. A YAML-defined automation has no config id, and Home
    Assistant refuses to delete it via this API - measured live, with a
    400 ("Resource not found"), not a 404: this endpoint answers 400 both
    for "no such config id" and for "this automation cannot be deleted
    here", so the two are reported the same way below.

    ⚠️ This is irreversible.

    Returns: {deleted: entity_id, status: <HTTP status code>} on success,
    or an error() envelope ("entity_not_found", "not_deletable", or
    "config_read_failed" when resolving the config id itself failed
    outright - a transient 500, a revoked token - rather than answering
    "no such entity"; not the same thing, and nothing is deleted either
    way) on failure.
    """
    try:
        with httpx.Client() as client:
            automation_id, _state = _resolve_automation_id(entity_id, client)
    except httpx.HTTPStatusError as exc:
        # Same class of bug create_automation()'s own pre-create collision
        # check already guards against on its own read (see
        # "collision_check_failed"), extended here to the read every other
        # edit tool in this module depends on - see _resolve_and_fetch()'s
        # own docstring for the fuller account.
        return error(
            "config_read_failed",
            f"Could not resolve {entity_id!r}'s config id - reading its "
            f"state failed ({exc.response.status_code}), which is not the "
            "same as the entity not existing. Nothing was deleted.",
            entity_id=entity_id, status=exc.response.status_code,
        )
    if automation_id is None:
        return error("entity_not_found",
                     f"{entity_id} does not exist on this Home Assistant instance.",
                     entity_id=entity_id)

    with httpx.Client() as client:
        r = client.delete(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 400:
            return error(
                "not_deletable",
                "Home Assistant refused the delete (400) - this automation "
                "is likely defined in YAML, which has no config id and "
                "cannot be deleted via this API. Only automations editable "
                "in the HA UI can be deleted with this tool.",
                entity_id=entity_id, automation_id=automation_id,
                ha_response=r.text[:300],
            )
        r.raise_for_status()
        return {"deleted": entity_id, "status": r.status_code}


@mcp.tool()
def trigger_automation(entity_id: str) -> dict:
    """Manually trigger an automation regardless of its trigger conditions.

    Returns: {entity_id, accepted: true, verified: null, detail} once Home
    Assistant accepts the call, or {error: "entity_not_found", ...} when
    entity_id has no state at all. What the automation's action sequence
    actually does has no single state here to confirm it happened - check
    the entities it acts on, or get_automation_trace() for its own record
    of the run.
    """
    if missing := confirm_entity_exists(entity_id):
        return missing
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/automation/trigger",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
    return {
        "entity_id": entity_id,
        "accepted": True,
        "verified": None,
        "detail": "Home Assistant accepted the trigger; what the "
                  "automation's actions do has no single state here to "
                  "confirm - check get_automation_trace() or the entities "
                  "it acts on.",
    }


@mcp.tool()
def toggle_automation(entity_id: str) -> dict:
    """Enable or disable an automation.

    Returns: {entity_id, new_state} once the entity's state is read back
    after the toggle - "on" (armed) or "off" (disabled) - or
    {entity_id, new_state: null, detail} when the entity has no state yet
    (it may not exist, or may still be registering; see create_automation()'s
    docstring for the registration race this can surface).
    """
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/services/automation/toggle",
            headers=HEADERS,
            json={"entity_id": entity_id},
            timeout=10,
        )
        r.raise_for_status()
        # The service call succeeds even for an entity that does not exist, and a
        # freshly created automation takes a moment to register, so report the
        # missing state instead of raising on the read-back.
        s = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if s.status_code == 404:
            return {"entity_id": entity_id, "new_state": None,
                    "detail": "Toggle sent, but the entity has no state yet — it may "
                              "not exist, or may still be registering."}
        s.raise_for_status()
        return {"entity_id": entity_id, "new_state": s.json().get("state")}


@mcp.tool()
def get_automation(entity_id: str) -> dict:
    """
    Get an automation's stored config by entity_id, normalised to the
    modern vocabulary (triggers/conditions/actions, trigger:/action: steps).

    Resolves the config id from the entity's own state the same way
    delete_automation() does - a UI-created automation's config id is a
    numeric timestamp unrelated to its entity_id - falling back to
    entity_id's own slug when there is no registered id or entity to read
    one from.

    Returns: {automation_id, entity_id, name, mode?, stored_format, config}.
    `stored_format` names what vocabulary the config actually had on disk
    ("legacy" or "modern") at the moment this call read it; `config` is
    normalised to the modern vocabulary at the level the vocabulary
    actually applies - root keys, and the direct trigger/action list
    items - the same level update_automation()/patch_automation() write
    at (see tools/_aliases.py's module docstring). A trigger or action
    step nested deeper (inside `choose`, `if`/`then`/`else`, `repeat`,
    `parallel`) is carried through exactly as stored, in whichever
    vocabulary it was last saved in - `config` is not a promise that
    every nested step is modern-spelled, only that the top-level lists
    and their direct items are.

    `mode` is read from the entity's own state attribute when it has one,
    falling back to the config's own root `mode` key, and omitted
    entirely when neither has it. This is deliberately NOT
    `config.get("mode", "single")`: a blueprint automation's `mode` comes
    from the blueprint, not a root key in its own config - measured live,
    a blueprint whose own metadata says `mode: restart` (matching the
    entity's `mode` attribute) had no root `mode` key in its stored
    config at all, so defaulting to `"single"` there was not a fallback,
    it was a wrong answer reported as fact - and an actionable one, since
    a caller "correcting" it by writing `mode: single` to the config root
    would genuinely change the automation's concurrency behaviour.

    update_automation() and patch_automation() send a config back in the
    vocabulary `stored_format` names here - but whether it stays that way
    is Home Assistant's call, not theirs. Measured live, posting a fully
    legacy config through Home Assistant's own REST config-write endpoint
    and reading it straight back: the root keys and an action step's
    service: are renamed on every save, whatever is posted - only a
    trigger step's platform: key survives as sent. That is Home
    Assistant's own config-write endpoint doing it, not a migration
    either edit tool performs or can prevent through this API - see
    tools/_aliases.py's module docstring for the full breakdown.
    `stored_format` is therefore a snapshot of what was read, not a
    guarantee about what a later edit leaves on disk.

    Returns an error() envelope ("not_found") when neither the resolved
    id nor entity_id's own slug has a stored config - a YAML-defined
    automation, or an entity_id with no corresponding automation at all -
    or ("config_read_failed") when the read itself failed outright (a
    transient 500, a revoked token) rather than answering "no such
    config" - not the same thing, and no longer reported the same way;
    see _resolve_and_fetch().
    """
    slug = entity_id.removeprefix("automation.")
    automation_id, resolved_id, raw, state, read_err = _resolve_and_fetch(entity_id, slug)
    if read_err:
        return read_err

    if raw is None:
        return error(
            "not_found",
            f"No stored automation config found for {entity_id} (tried "
            f"id {automation_id!r} and slug {slug!r}). It may not exist, "
            "or may be defined in YAML, which has no config id.",
            entity_id=entity_id,
        )

    config, restore = to_modern(raw)
    # The entity's own `mode` attribute is the true value for a blueprint
    # automation - its config has no root `mode` key at all, since mode
    # comes from the blueprint (measured live: a blueprint whose metadata
    # says mode: restart produced a stored config with no "mode" key
    # anywhere, and an entity state attribute "mode": "restart"). Falling
    # back to config.get("mode") covers a non-blueprint automation read
    # before its entity has registered (raw exists via the slug fallback,
    # state is None); the key is omitted entirely only when neither has
    # an answer, rather than inventing "single" as if it were read.
    mode = (state or {}).get("attributes", {}).get("mode", config.get("mode"))
    result = {
        "automation_id": resolved_id,
        "entity_id": entity_id,
        "name": config.get("alias", ""),
        "mode": mode,
        "stored_format": stored_format(restore),
        "config": config,
    }
    if mode is None:
        del result["mode"]
    return result


def _not_found_for_edit(entity_id: str, automation_id: str, slug: str,
                        verb: str) -> dict:
    """The error() update_automation() and patch_automation() both return
    when _fetch_config() cannot resolve a stored config: no such entity,
    or a YAML-defined automation, which has no config id and so cannot be
    reached through this API at all. get_automation() reports the same
    situation with its own hand-written error() (it predates this
    helper); this one exists so an edit tool's refusal names what it was
    trying to DO, not just that it could not find something to read.

    verb: what the caller was trying to do ("updated", "patched"), for a
    message specific to that tool rather than one written for reading.
    """
    return error(
        "not_found",
        f"No stored automation config found for {entity_id} (tried "
        f"id {automation_id!r} and slug {slug!r}). It may not exist, or "
        "may be defined in YAML, which has no config id - only "
        f"automations editable in the HA UI can be {verb} this way.",
        entity_id=entity_id,
    )


@mcp.tool()
def update_automation(
    entity_id: str,
    name: str = "",
    triggers: list = None,
    conditions: list = None,
    actions: list = None,
    mode: str = "",
    description: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """
    Update an existing automation in place, preserving its id.

    Read-modify-write, never reconstruction: fetches the automation's real
    stored config, overwrites only the fields actually passed, and posts
    the same object back - unlike create_automation(), whose payload IS
    the whole automation from scratch. A device trigger, a nested
    if/then, a branch marked enabled: false all survive untouched,
    because they are never parsed - only the top-level fields this tool
    knows about (alias, triggers, conditions, actions, mode, description)
    are ever replaced; everything else in the fetched config is carried
    through exactly as read.

    entity_id: the automation to update, e.g. 'automation.morning_lights'.
      Its config id is never changed by this tool - labels, dashboards and
      cross-references stay attached to it. This is the reason this tool
      exists at all: create_automation() derives an id from `name`, so
      "editing" an automation by creating a second one under a new name
      leaves both live, with the same trigger - the incident this whole
      module exists because of.

    name, mode: default to "" - an empty string means "not passed", so
      neither can be cleared to empty through this tool. Deliberate: a
      sentinel object reads worse than it helps for two fields that would
      never legitimately be emptied out.
    triggers, conditions, actions: each replaces that whole list when
      passed (None means "leave alone" - pass [] to deliberately clear
      one, e.g. conditions=[] to remove every condition). Either
      vocabulary is accepted for the objects inside the list
      (platform:/service: or trigger:/action:) - what matters for the
      stored file is described below, not what you pass in here.
    description: None means "not passed" - unlike name/mode, "" is a
      legitimate description to write (clearing an existing one), so this
      field needs a real sentinel instead of the empty-string convention.
    enabled: None leaves the automation's current armed state alone -
      calling this tool to rename an automation does not also silently
      re-arm or disarm it (measured live: reconfiguring an automation
      through this same write endpoint does NOT reset its enabled state
      the way a fresh create does - see tests/fakeha.py's POST handler for
      the live-measured behaviour this models). True/False requests a
      specific state and is verified exactly the way create_automation()
      verifies enabled=False: wait for the entity, send the toggle, read
      the state back, and report an error() rather than a bare success
      when it cannot be confirmed (see _set_and_verify_enabled()). An
      automation that reports itself disabled while still armed is this
      project's founding bug; this tool changing `enabled` gets the same
      treatment, not a weaker one.

    Nothing is written when no field was passed at all (including
    `enabled`) - a no-op call makes no request, rather than silently
    resubmitting an unchanged config through Home Assistant's own
    normaliser (see the vocabulary paragraph below).

    Writes go back in the vocabulary this tool read, at the levels it
    controls: `stored_format` below names the root/step spelling the
    fetched config actually used, and anything this call does not
    explicitly replace keeps that spelling on the way out. What lands on
    disk after that is Home Assistant's call, not this tool's. Measured
    live, posting a fully legacy config through this same REST endpoint
    and reading it straight back:

      root keys (trigger/condition/action -> triggers/conditions/actions):
        renamed by Home Assistant on every save, whatever is posted
      action step (service: -> action:):    renamed the same way, always
      trigger step (platform: -> trigger:): survives exactly as sent

    So a legacy automation edited through this tool comes back with
    plural root keys and action: instead of service: on its next read -
    that is Home Assistant's own config-write endpoint doing it, to any
    client including its own UI editor, not a migration this tool
    performs or can prevent through this API. `stored_format` reports
    what this tool read and sent back unchanged; it is not a promise
    about what Home Assistant's own validator leaves on disk afterward.

    Only automations editable in the HA UI can be updated this way - a
    YAML-defined automation has no config id and returns an error()
    envelope ("not_found") mentioning YAML, the same distinction
    get_automation() and delete_automation() already draw.

    Returns: {automation_id, entity_id, updated: [...], stored_format,
    enabled?, verified?, state?} on success - `updated` lists which of
    name/triggers/conditions/actions/mode/description were actually
    written; `enabled`/`verified`/`state` are present only when `enabled`
    was passed. Or an error() envelope: "not_found"; "config_read_failed"
    when the read itself failed outright rather than answering "no such
    config" (see _resolve_and_fetch()); "home_assistant_error" when Home
    Assistant rejects the write itself - its own validation message is
    reported directly (see rest_error()), and nothing is written, the same
    as any other refusal here; or "automation_not_registered"/
    "automation_not_disabled"/"automation_state_unverified" when `enabled`
    was requested and could not be confirmed.
    """
    slug = entity_id.removeprefix("automation.")
    automation_id, resolved_id, raw, _state, read_err = _resolve_and_fetch(entity_id, slug)
    if read_err:
        return read_err

    if raw is None:
        return _not_found_for_edit(entity_id, automation_id, slug, "updated")

    config, restore = to_modern(raw)
    fmt = stored_format(restore)

    updated = []
    if name:
        config["alias"] = name
        updated.append("name")
    if triggers is not None:
        config["triggers"] = triggers
        updated.append("triggers")
    if conditions is not None:
        config["conditions"] = conditions
        updated.append("conditions")
    if actions is not None:
        config["actions"] = actions
        updated.append("actions")
    if mode:
        config["mode"] = mode
        updated.append("mode")
    if description is not None:
        config["description"] = description
        updated.append("description")

    if updated:
        payload = to_stored(config, restore)
        with httpx.Client() as client:
            r = client.post(
                f"{HA_URL}/api/config/automation/config/{resolved_id}",
                headers=HEADERS,
                json=payload,
                timeout=15,
            )
            # Home Assistant validates the whole config on write and
            # answers a rejected one with 400 and a plain-text explanation
            # (e.g. "Service ZZZ does not match format <domain>.<name>") -
            # exactly what a caller needs to correct itself. Letting
            # r.raise_for_status() raise here discarded that message as an
            # uncaught httpx.HTTPStatusError; rest_error() reports it
            # instead, the same way delete_automation()'s own 400 branch
            # already reports HA's rejection of a delete.
            if write_err := rest_error(r):
                write_err["entity_id"] = entity_id
                write_err["updated"] = updated
                return write_err

    result = {
        "automation_id": resolved_id,
        "entity_id": entity_id,
        "updated": updated,
        "stored_format": fmt,
    }

    if enabled is not None:
        # arm_when_enabling defaults to True here (unlike
        # create_automation()'s own call site) - `enabled` has no default
        # of its own on this tool (None means "not passed"), so reaching
        # this branch at all means the caller explicitly asked for True or
        # False. An explicit "enable this" is the actual ask, not a
        # parameter default nobody set - see _set_and_verify_enabled()'s
        # own docstring for the distinction this flag exists to draw.
        outcome = _set_and_verify_enabled(entity_id, enabled)
        if "error" in outcome:
            outcome["automation_id"] = resolved_id
            outcome["entity_id"] = entity_id
            outcome["updated"] = updated
            outcome["stored_format"] = fmt
            return outcome
        result.update(outcome)

    return result


@mcp.tool()
def patch_automation(
    entity_id: str,
    path: str,
    value: dict | list | str | int | float | bool | None,
) -> dict:
    """
    Change exactly one value inside an automation's stored config, by
    dotted path, without touching - or restating - anything else.

    entity_id: the automation to patch, e.g. 'automation.nas_shutdown'.
    path: dotted, with integer list indices - e.g.
      'conditions.0.value_template', 'actions.2.target.entity_id'. Written
      in the modern vocabulary (triggers/conditions/actions,
      trigger:/action: steps), but the legacy spelling (trigger/condition/
      action, platform/service) is also accepted at any segment - a caller
      does not need to know which one this particular automation is
      stored in (see tools/_aliases.py's get_path()/set_path()). Exactly
      one path notation is accepted, on purpose: dotted, not JSON Pointer
      - supporting both doubles the ways to get a path wrong.
    value: the new value at `path`, replacing whatever was there.

    A path that does not resolve against the config actually fetched is an
    error, never a creation - refused before anything is written, naming
    what IS present at the point resolution failed (see PathError). This
    is what makes a targeted patch safe to send blind: it cannot silently
    grow the config with an empty branch nothing will ever read.

    `path="id"` is refused outright, before anything is read or written -
    see error() "protected_path" below. `id` is not a piece of the
    automation's behaviour, it is the value Home Assistant's automation
    config API is keyed by and the entity's own unique_id. Measured live:
    patching it does not rename anything - the existing entity_id is left
    pointing at nothing (still in the registry, with its labels and area,
    but "unavailable") while the same write immediately registers a
    second, independent automation under the new id, carrying the exact
    same trigger and already armed. Two armed automations sharing a
    trigger is the incident this project exists to prevent, so this is
    refused unconditionally rather than left to "know what you are
    doing" - there is no supported way to rename an automation's id
    through this API at all; update_automation() does not expose it as a
    field either. Patch `name` (or `alias`) to rename the automation
    itself. `use_blueprint.path` (which blueprint an automation follows)
    is deliberately NOT protected - unlike `id`, changing it does not
    orphan the entity or create a second automation.

    No dry_run parameter: get_automation() plus this call's own `old`
    field already cover it - inspect first if you want to see the value
    before changing it, or make the change and read `old` back from the
    result to confirm what was actually there.

    Writes go back in the stored vocabulary the same way update_automation()
    does - see its own docstring for the live-measured caveat that Home
    Assistant's config-write endpoint renames the root and action-step
    spelling on every save regardless of what is posted.

    Only automations editable in the HA UI can be patched this way - a
    YAML-defined automation returns an error() envelope ("not_found")
    mentioning YAML, the same as update_automation() and get_automation().

    Returns: {automation_id, entity_id, path, old, new, stored_format} on
    success, or an error() envelope - "not_found" (no such automation, or
    YAML-defined), "protected_path" (`path` is "id" - see above),
    "config_read_failed" (the read itself failed outright, rather than
    answering "no such config" - see _resolve_and_fetch()), "bad_path"
    (the path resolved against nothing - nothing was written), or
    "home_assistant_error" when Home Assistant rejects the write itself -
    its own validation message is reported directly (see rest_error()),
    and nothing was written, the same as "bad_path".
    """
    if path.split(".", 1)[0] in _PROTECTED_PATCH_ROOT_PATHS:
        return error(
            "protected_path",
            f"{path!r} would change this automation's own config id, not "
            "its behaviour - the value Home Assistant's automation config "
            "API is keyed by and the entity's unique_id. This is refused "
            "unconditionally: changing it does not rename anything, it "
            "orphans the current entity_id (which keeps its labels and "
            "area but stops resolving to any config) while the write "
            "itself registers a second, independent automation under the "
            "new id - still armed, with the same trigger. Two armed "
            "automations sharing a trigger is the incident this project "
            "exists to prevent. Nothing was read or written. If you meant "
            "to rename the automation, patch 'name' (or 'alias') instead "
            "- an automation's id cannot be changed through this API, by "
            "either edit tool.",
            entity_id=entity_id, path=path,
        )

    slug = entity_id.removeprefix("automation.")
    automation_id, resolved_id, raw, _state, read_err = _resolve_and_fetch(entity_id, slug)
    if read_err:
        return read_err

    if raw is None:
        return _not_found_for_edit(entity_id, automation_id, slug, "patched")

    config, restore = to_modern(raw)
    fmt = stored_format(restore)

    try:
        old = get_path(config, path)
        set_path(config, path, value)
    except PathError as exc:
        return error("bad_path", str(exc), entity_id=entity_id, path=path)

    payload = to_stored(config, restore)
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/automation/config/{resolved_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        # See update_automation()'s identical guard for why this is
        # rest_error() and not a bare r.raise_for_status(): Home Assistant
        # validates the whole config on write, and its rejection message
        # (e.g. "Service ZZZ does not match format <domain>.<name>") is
        # exactly what a caller needs to correct the value it just sent.
        if write_err := rest_error(r):
            write_err["entity_id"] = entity_id
            write_err["path"] = path
            return write_err

    return {
        "automation_id": resolved_id,
        "entity_id": entity_id,
        "path": path,
        "old": old,
        "new": value,
        "stored_format": fmt,
    }


@mcp.tool()
def get_automation_trace(entity_id: str, limit: int = 5) -> dict:
    """
    Get the latest execution traces for an automation.
    Useful for debugging why an automation did or did not trigger.

    entity_id: e.g. 'automation.living_room_lights'
    limit: number of recent traces to return (default 5)

    Returns: {total, returned, offset, note?, traces: [...]}

    Traces are held in memory by Home Assistant and are lost on restart, so an
    empty result means "no traces available", never "the automation never ran".
    """
    result = _ws({
        "type": "trace/list",
        "domain": "automation",
        "item_id": entity_id.replace("automation.", ""),
    })
    if err := ws_error(result):
        return err
    traces = [
        {
            "run_id": t.get("run_id"),
            "state": t.get("state"),
            "timestamp": t.get("timestamp"),
            "last_step": t.get("last_step"),
            "error": t.get("error"),
            "script_execution": t.get("script_execution"),
        }
        for t in (result["result"] or [])
    ]
    note = ("no traces available - Home Assistant keeps them in memory and "
            "loses them on restart") if not traces else ""
    return envelope(traces, key="traces", limit=limit, note=note)


@mcp.tool()
def list_blueprints(domain: str = "automation") -> dict:
    """
    List available blueprints.

    domain: 'automation' (default) or 'script'
    Returns: {total, returned, offset, note?, blueprints: [...]}
    """
    result = _ws({"type": "blueprint/list", "domain": domain})
    if err := ws_error(result):
        return err
    blueprints = result["result"] or {}
    rows = [
        {
            "path": path,
            "name": data.get("metadata", {}).get("name", path),
            "description": data.get("metadata", {}).get("description", ""),
            "domain": data.get("metadata", {}).get("domain", domain),
            "input": list((data.get("metadata", {}).get("input") or {}).keys()),
        }
        for path, data in blueprints.items()
    ]
    return envelope(rows, key="blueprints")


@mcp.tool()
def create_automation_from_blueprint(
    blueprint_path: str,
    alias: str,
    input_values: dict,
) -> dict:
    """
    Create an automation from a blueprint.

    blueprint_path: e.g. 'homeassistant/motion_trigger.yaml'
    alias: name for the new automation
    input_values: dict of blueprint input variables

    Home Assistant has no WebSocket command for saving an automation config -
    only a REST endpoint, the same one create_automation() uses.

    Returns: {automation_id, entity_id, alias, blueprint, result} once Home
    Assistant accepts the config. Unlike create_automation(), this does not
    wait for the entity to register or read its state back - a blueprint
    automation is armed the instant it registers, the same way any new
    automation is; check get_states_by_domain('automation') afterward if
    you need to confirm it exists.
    """
    automation_id = _slug(alias)
    payload = {
        "alias": alias,
        "use_blueprint": {
            "path": blueprint_path,
            "input": input_values,
        },
    }
    with httpx.Client() as client:
        r = client.post(
            f"{HA_URL}/api/config/automation/config/{automation_id}",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        return {
            "automation_id": automation_id,
            "entity_id": f"automation.{automation_id}",
            "alias": alias,
            "blueprint": blueprint_path,
            "result": r.json(),
        }


@mcp.tool()
def list_device_triggers(device_id: str) -> dict:
    """
    List the triggers a device offers (device automation).

    device_id: from list_devices()
    Returns: {total, returned, offset, note?, triggers: [...]}
    """
    result = _ws({"type": "device_automation/trigger/list", "device_id": device_id})
    if err := ws_error(result):
        return err
    return envelope(result["result"], key="triggers")


@mcp.tool()
def list_device_conditions(device_id: str) -> dict:
    """
    List the conditions a device offers (device automation).

    device_id: from list_devices()
    Returns: {total, returned, offset, note?, conditions: [...]}
    """
    result = _ws({"type": "device_automation/condition/list", "device_id": device_id})
    if err := ws_error(result):
        return err
    return envelope(result["result"], key="conditions")


@mcp.tool()
def list_device_actions(device_id: str) -> dict:
    """
    List the actions a device offers (device automation).

    device_id: from list_devices()
    Returns: {total, returned, offset, note?, actions: [...]}
    """
    result = _ws({"type": "device_automation/action/list", "device_id": device_id})
    if err := ws_error(result):
        return err
    return envelope(result["result"], key="actions")


@mcp.tool()
def import_blueprint(url: str) -> dict:
    """
    Import a blueprint from a URL (GitHub, HA Community, etc.) and save it
    to disk, ready to use.

    url: direct URL to the blueprint YAML file.
    Examples:
      'https://raw.githubusercontent.com/user/repo/main/blueprints/automation/my_blueprint.yaml'
      'https://community.home-assistant.io/t/some-blueprint/123456'

    Home Assistant's `blueprint/import` command is only the preview step
    the blueprint editor uses before showing you what it found - it parses
    and validates the YAML but writes nothing to disk. Saving is a second,
    separate command, `blueprint/save`. Verified live: blueprint/list was
    unchanged immediately after blueprint/import alone, and only showed the
    new path once blueprint/save was also called with the id and raw YAML
    the import step returned. This tool performs both steps, so a caller
    does not have to know that split exists or do it themselves.

    After importing, use list_blueprints() to see it and
    create_automation_from_blueprint() to use it.

    Returns: {imported, saved, url, path, domain, name, overrides_existing}
    once both steps succeed, or an error() envelope - the ws_error() from
    whichever WebSocket command failed, or "invalid_blueprint" when Home
    Assistant's own parser reports validation errors on the imported YAML
    (nothing is saved in that case), or "incomplete_import" when the import
    step succeeded but did not return enough information (domain or a
    suggested path) to save it.
    """
    imported = _ws({"type": "blueprint/import", "url": url})
    if err := ws_error(imported):
        return err
    data = imported["result"] or {}

    validation_errors = data.get("validation_errors")
    if validation_errors:
        return error("invalid_blueprint",
                     "Home Assistant could not validate this blueprint - nothing was saved.",
                     url=url, validation_errors=validation_errors)

    metadata = data.get("blueprint", {}).get("metadata", {})
    domain = metadata.get("domain", "")
    path = data.get("suggested_filename") or data.get("path", "")
    if not domain or not path:
        return error("incomplete_import",
                     "Home Assistant's import did not return enough information "
                     "to save this blueprint (missing domain or suggested path).",
                     url=url, result=data)

    saved = _ws({
        "type": "blueprint/save",
        "domain": domain,
        "path": path,
        "yaml": data.get("raw_data", ""),
        "source_url": url,
    })
    if err := ws_error(saved):
        return err
    save_result = saved["result"] or {}

    return {
        "imported": True,
        "saved": True,
        "url": url,
        "path": path,
        "domain": domain,
        "name": metadata.get("name", ""),
        "overrides_existing": save_result.get("overrides_existing", False),
    }


@mcp.tool()
def list_schedules() -> dict:
    """
    List all schedules from the Scheduler integration (HACS custom component).

    Returns: {total, returned, offset, note?, schedules: [...]}
    Returns an error if the Scheduler custom component is not installed, or if
    the call otherwise fails — the two are not the same thing and are reported
    differently.
    """
    r = _ws({"type": "scheduler/items"})
    if err := ws_error(r):
        # HA answers an unregistered command type when the custom component
        # is not loaded; any other failure is a different problem and must
        # not be reported as a missing integration (see tools/hacs.py's
        # _hacs_check for the same distinction made for HACS).
        if err.get("error") in ("unknown_command", "not_found"):
            return error("scheduler_not_available",
                         "The Scheduler custom component is not installed.",
                         detail_from_ha=err.get("detail", ""))
        return err
    items = r["result"]
    rows = [
        {
            "schedule_id": item.get("schedule_id"),
            "entity_id": item.get("entity_id"),
            "name": item.get("name", ""),
            "enabled": item.get("enabled", True),
            "next_trigger": item.get("next_trigger"),
            "timeslots": item.get("timeslots", []),
            "actions": item.get("actions", []),
        }
        for item in items
    ]
    rows.sort(key=lambda x: x.get("name", ""))
    return envelope(rows, key="schedules")
