"""Resolve what tools/_refs.py extracts against a live Home Assistant
instance, and classify what it finds.

tools/_refs.py answers "what does this automation's config NAME" — purely,
with no network at all. This module answers the next question, the one
that needs a live instance to answer: "does each of those names still
point at something real, and if so, is that something currently
answering?" Three outcomes, kept deliberately distinct because the right
response to each is opposite one another:

  dead_reference (error)   the id is absent from BOTH the entity/device
    registry AND the state machine. It does not exist here at all — a
    stale id, almost always left behind by a rename. Fix the automation.
  restored (error)         the id IS in the registry, but has no current
    state. The id is correct; its integration is not loaded (failed to
    start, its config entry was removed, an add-on is stopped). Fix the
    integration, not the automation.
  unavailable (warning)    the id has a state, but that state is
    "unavailable" or "unknown" — present, but not currently answering
    (an offline device, a degraded connection). Investigate the
    integration, not the automation.

## Why the dead-reference detail text is the most important string this
## module returns

In Home Assistant the state of an entity that does not exist is the
Python value `None` — never the string `"unavailable"`. So

    is_state("button.nas_shutdown", "unavailable")

returns `False` when that entity does not exist, exactly as it would for
an entity that exists and is simply in some OTHER state. A guard written

    {{ not is_state("button.nas_shutdown", "unavailable") }}

to fail closed once the button becomes unavailable therefore does the
opposite when the id is stale: `not False` is `True`, and the guard
PASSES. Nothing about this raises an error, writes a log line, or trips
a repair issue — the automation does not stop working, it silently
starts failing open. That mechanism, not just the bare fact "this id
does not exist", is what _classify() below spells out for a dead
reference found inside a template (`source: "template"`) — the whole
reason this tool exists is to make that failure mode visible before it
does damage, the way it was not for the NAS shutdown guard that
destroyed 245 GB.

## What this module deliberately does NOT do

It does not evaluate any template. `render_template()` (tools/assist.py)
is the companion tool for that — hand it a template you already suspect
and it renders it against live state right now, the way Home Assistant's
own template editor would. This module answers a different, narrower,
*static* question instead: for every entity/device name an automation's
config actually contains, does that id currently exist? "What does this
template return today" and "does the entity it names still exist" are
different questions — only the second has one answer regardless of
current conditions, and only the second is what this module checks.
Evaluating templates to decide "is this reference dead" would also be
wrong on its own terms: a template can reference an id inside a branch
that is not the one currently taken, so its live output says nothing
reliable about whether every id it names is still real.

## Cost

`validate_automation()` reads the target automation's own config (one or
two HTTP requests — see get_automation()) plus one fixed-size live
snapshot (states + both registries — see _live_snapshot()) regardless of
how many references the config holds. `validate_all_automations()` shares
ONE such snapshot across every automation it checks and reads each
automation's own config once — see its own docstring for why that,
not the snapshot, is the cost that scales with the size of the instance.
"""
import httpx

from tools._aliases import to_modern
from tools._base import HA_URL, HEADERS, _ws_multi, envelope, error, mcp, ws_error
from tools._refs import extract_refs, find_fail_open_waits
from tools.automations import _fetch_config, get_automation, list_automations


def _live_snapshot() -> tuple[dict, dict, dict, dict | None]:
    """Fetch everything _classify() needs to resolve any reference on this
    instance, in one small, FIXED-size batch — not one call per reference,
    and (see validate_all_automations()'s own docstring) not once per
    automation either when several are being checked in the same call.

    Returns (states, entity_registry, device_registry, error_envelope):
      states:          entity_id -> its raw GET /api/states row. Home
                       Assistant's /api/states only lists entities that
                       currently HAVE a state at all — an id absent here
                       has none, the same fact confirm_entity_exists()
                       (tools/_base.py) checks one id at a time via a 404;
                       reading the whole list once is what lets this
                       module resolve every reference in a config (or
                       every reference in every automation, from
                       validate_all_automations()) without a request per
                       id.
      entity_registry: entity_id -> its config/entity_registry/list row.
      device_registry: device_id -> its config/device_registry/list row.
      error_envelope:  an error() envelope when any of the three reads
                       failed outright, else None — a caller returns this
                       immediately without looking at the other three
                       values, the same contract ws_error() itself uses.
    """
    ws_results = _ws_multi([
        {"type": "config/entity_registry/list"},
        {"type": "config/device_registry/list"},
    ])
    if err := ws_error(ws_results[0]):
        return {}, {}, {}, err
    if err := ws_error(ws_results[1]):
        return {}, {}, {}, err
    entity_registry = {e["entity_id"]: e for e in ws_results[0]["result"]}
    device_registry = {d["id"]: d for d in ws_results[1]["result"]}

    with httpx.Client() as client:
        r = client.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=15)
    r.raise_for_status()
    states = {s["entity_id"]: s for s in r.json()}

    return states, entity_registry, device_registry, None


def _classify(ref: dict, states: dict, entity_registry: dict, device_registry: dict) -> dict | None:
    """Resolve one extract_refs() reference (tools/_refs.py) against the
    live snapshot _live_snapshot() built, and return one issues[] entry —
    or None when the reference resolves cleanly, so validate_automation()
    never reports the (overwhelming, on a real instance) majority of
    references that are simply fine. Crying wolf on a correct automation
    is how a validator gets ignored.

    A device reference gets only two outcomes, not three: a device
    carries no live "state" of its own the way an entity does — state
    belongs to the entities a device groups, not to the device row
    itself — so the restored/unavailable split (fundamentally about
    whether a *state-machine* entry exists) has nothing to apply to for
    a device. A device_id is either in config/device_registry/list or it
    is not; this is a deliberate scope decision, not an oversight.
    """
    if ref["kind"] == "device":
        if ref["id"] in device_registry:
            return None
        return {**ref, "outcome": "dead_reference", "severity": "error", "detail": (
            f"{ref['id']} is not in this instance's device registry — it "
            "does not exist here (removed, never existed, or belongs to "
            "a different Home Assistant instance). Fix the automation."
        )}

    state = states.get(ref["id"])
    if state is not None:
        if state["state"] not in ("unavailable", "unknown"):
            return None
        return {**ref, "outcome": "unavailable", "severity": "warning", "detail": (
            f"{ref['id']} is registered and has a state, but that state "
            f"is currently {state['state']!r} — its own integration is "
            "reporting it as not answering right now (an offline device, "
            "a degraded connection, mid-reconnect). The reference itself "
            "is correct; investigate the integration, not this "
            "automation."
        )}

    if ref["id"] in entity_registry:
        return {**ref, "outcome": "restored", "severity": "error", "detail": (
            f"{ref['id']} is in the entity registry but has no state at "
            "all right now — its integration is not loaded (failed to "
            "start, its config entry was removed, an add-on is "
            "stopped). The id itself is correct; investigate the "
            "integration, not this automation."
        )}

    if ref["source"] == "template":
        detail = (
            f"{ref['id']} does not exist on this instance — absent from "
            "both the entity registry and the state machine. This "
            f"reference is inside a TEMPLATE (at {ref['where']}), which "
            "is more dangerous than the same missing id in a plain "
            "field: in Home Assistant the state of an entity that does "
            "not exist is the value None, never the string "
            "\"unavailable\" — so "
            f"is_state({ref['id']!r}, \"unavailable\") returns False, "
            "not True, for a nonexistent entity. A guard written "
            "`{{ not is_state(...) }}` to fail CLOSED once the entity "
            "becomes unavailable therefore does the opposite when the "
            "id is stale: it PASSES. Nothing here raises an error or "
            "writes a log line — the automation does not stop working, "
            "it silently starts failing open. This id is almost "
            "certainly stale (check for a rename) and the automation "
            "needs fixing, not the integration."
        )
    else:
        detail = (
            f"{ref['id']} does not exist on this instance — absent from "
            "both the entity registry and the state machine (field at "
            f"{ref['where']}). Home Assistant accepts a service call "
            "naming a missing entity_id and answers it 200 with no "
            "effect — no error, no log line. This id is almost "
            "certainly stale (check for a rename) and the automation "
            "needs fixing, not the integration."
        )
    return {**ref, "outcome": "dead_reference", "severity": "error", "detail": detail}


def _validate_config(automation_id: str | None, entity_id: str, name: str, config: dict,
                     states: dict, entity_registry: dict, device_registry: dict) -> dict:
    """Shared by validate_automation() and validate_all_automations(): given
    one automation's already-fetched, already-modernised config and an
    already-fetched live snapshot, extract every reference and fail-open
    wait and classify each reference. No HTTP or WebSocket call happens
    in here — both already happened by the time this runs, which is what
    lets validate_all_automations() share one snapshot across every
    automation it checks instead of re-fetching it per automation.
    """
    refs = extract_refs(config)
    waits = find_fail_open_waits(config)
    issues = [
        issue for issue in
        (_classify(ref, states, entity_registry, device_registry) for ref in refs)
        if issue is not None
    ]

    return {
        "automation_id": automation_id,
        "entity_id": entity_id,
        "name": name,
        "issues": issues,
        "fail_open_waits": waits,
        "summary": {
            "refs_checked": len(refs),
            "dead_references": sum(1 for i in issues if i["outcome"] == "dead_reference"),
            "restored": sum(1 for i in issues if i["outcome"] == "restored"),
            "unavailable": sum(1 for i in issues if i["outcome"] == "unavailable"),
            "fail_open_waits": len(waits),
        },
    }


@mcp.tool()
def validate_automation(entity_id: str) -> dict:
    """
    Check every entity/device one automation references against this
    instance's live entity/device registries and current state machine,
    and report any wait_for_trigger that can silently carry a timeout
    into a destructive action. See this module's own docstring for the
    full reasoning — in short: a reference can be dead_reference
    (does not exist anywhere — fix the automation), restored (exists in
    the registry but has no state — its integration is not loaded, fix
    THAT instead) or unavailable (has a state, but it is "unavailable"/
    "unknown" — also an integration problem, not this automation's).

    The single most important thing this tool reports is a dead
    reference found INSIDE A TEMPLATE: in Home Assistant the state of an
    entity that does not exist is `None`, never the string
    `"unavailable"`, so `is_state("gone.id", "unavailable")` returns
    `False` for a nonexistent entity — a guard written
    `{{ not is_state(...) }}` to fail closed therefore PASSES instead,
    silently, with no error and no log line. See this module's own
    docstring for the incident that mechanism caused. Every
    `dead_reference` issue whose `source` is `"template"` explains this
    in its own `detail` text, not just the bare fact that the id is
    missing.

    This tool is deliberately STATIC: it does not evaluate any template,
    only checks whether the ids a template names still exist.
    render_template() (tools/assist.py) is the companion for the
    opposite job — actually rendering one template you already suspect,
    against live state, right now. "What does this template currently
    return" and "does the entity it names still exist" are different
    questions; this tool answers only the second, for every reference in
    the automation at once, with no side effects.

    Returns an error() envelope ("not_found" or "config_read_failed")
    when entity_id does not resolve to a stored automation config — see
    get_automation()'s own docstring (tools/automations.py) for exactly
    when each happens; this tool reuses that same resolution and reports
    the identical error rather than inventing its own.

    Returns, on success:
      {automation_id, entity_id, name, issues: [...],
       fail_open_waits: [...],
       summary: {refs_checked, dead_references, restored, unavailable,
                 fail_open_waits}}

    Each `issues` entry is one problem reference — never a reference
    that resolved cleanly, see _classify()'s own docstring for why a
    validator that lists everything it checked, correct or not, gets
    ignored on a real instance with hundreds of correct automations:
      {id, kind, where, source, outcome, severity, detail}
    - id/kind/where/source: exactly as extract_refs() (tools/_refs.py)
      reported them — `where` is the dotted config path the reference
      was found at, `source` is "field" or "template".
    - outcome: "dead_reference" | "restored" | "unavailable".
    - severity: "error" for dead_reference/restored, "warning" for
      unavailable — the two errors mean the automation is currently
      wrong right now; the warning means something to keep an eye on.
    - detail: the sentence explaining what was found and what to do
      about it — see this module's own docstring for why the
      dead-reference-inside-a-template case spells out the mechanism.

    Each `fail_open_waits` entry is exactly find_fail_open_waits()'s
    (tools/_refs.py) own shape: {wait_where, timeout, action_where,
    service} — a wait_for_trigger with a timeout and no
    continue_on_timeout: false ahead of a destructive action (*.turn_off,
    switch.*, homeassistant.stop/restart, hassio.host_*) in the same
    sequence. Unrelated to whether any entity exists at all — the second,
    independent half of the incident this tool exists for.
    """
    result = get_automation(entity_id)
    if "error" in result:
        return result

    states, entity_registry, device_registry, snapshot_err = _live_snapshot()
    if snapshot_err:
        return snapshot_err

    return _validate_config(
        result["automation_id"], result["entity_id"], result["name"],
        result["config"], states, entity_registry, device_registry,
    )


@mcp.tool()
def validate_all_automations(only_issues: bool = True, limit: int = 0, offset: int = 0) -> dict:
    """
    Run validate_automation()'s own checks over every automation on this
    instance (or a page of them — see limit/offset).

    Makes ONE HTTP request per automation checked (occasionally two — see
    _fetch_config()'s own docstring, tools/automations.py — only when the
    resolved config id itself 404s and the automation's entity_id slug
    differs from it): there is no bulk "every automation's stored config"
    endpoint in Home Assistant's own API, so each one's config is read
    individually, the same read get_automation() itself does for one
    automation. That per-automation cost is real and is the reason for
    `limit`/`offset`: on an instance with a few hundred automations this
    call can take a while. What is NOT per-automation is the live
    snapshot (registries + current states) the classification itself
    needs — that is fetched exactly ONCE for the whole call and shared
    across every automation checked, not rebuilt per automation the way
    calling validate_automation() in a loop would.

    only_issues: when True (the default), an automation with no issues
      and no fail-open waits is left out of `results` entirely — only
      the abnormal ones are worth a reader's attention on an instance
      where most automations are fine. `summary.checked` still counts
      every automation this call actually read and validated, whether
      or not it ended up in `results` — see `summary` below for why that
      is NOT the same number as `total`.
    limit, offset: which page of list_automations()'s own ordering to
      check (0 = no limit, matching list_automations()'s own default).

    Returns: {total, returned, offset, note?, results: [...],
    summary: {checked, with_issues, read_errors, dead_references,
    restored, unavailable, fail_open_waits}}.

    `total`/`returned`/`offset`/`note` describe `results` exactly the
    way every other paginated tool in this codebase does (see
    envelope()'s own docstring, tools/_base.py) — `total` is how many
    automations are actually IN `results`. `summary.checked` is a
    DIFFERENT number: how many automations this call actually read and
    validated, regardless of whether they ended up in `results`. The two
    only agree when only_issues=False. With the default only_issues=True,
    `total` can be far smaller than `summary.checked` — on an instance
    where nothing is currently broken, `total` would be 0 while
    `summary.checked` still reports the full sweep. A caller who reads
    `total` alone and assumes it means "how many automations were
    examined" undercounts, potentially by the whole instance.

    Each `results` entry is ordinarily validate_automation()'s own return
    shape (see its docstring) with `automation_id` filled in from
    whichever id this tool's own read resolved. An automation whose own
    config could not be read at all (same failure modes get_automation()
    reports as "not_found"/"config_read_failed") is still included in
    `results` — regardless of `only_issues`, since a config this tool
    could not even read is itself worth surfacing, not silently dropped
    from the sweep — as {entity_id, name, read_error: <error() envelope>}
    instead, and counted under `summary.read_errors` rather than under
    the outcome counts (which describe a config that WAS read).
    """
    listing = list_automations(limit=limit, offset=offset)
    if "error" in listing:
        return listing

    states, entity_registry, device_registry, snapshot_err = _live_snapshot()
    if snapshot_err:
        return snapshot_err

    results = []
    checked = 0
    for row in listing["automations"]:
        entity_id = row["entity_id"]
        slug = entity_id.removeprefix("automation.")
        # Derive the config id the same way _resolve_automation_id()
        # (tools/automations.py) does, from the entity's own `id`
        # attribute — but read out of the states snapshot already
        # fetched above instead of performing that function's own GET
        # /api/states/<entity_id>. That snapshot already holds every
        # entity's state, automations included, so re-reading this one
        # entity's state a second time here would be a second HTTP
        # request this docstring promises is not made.
        live_state = states.get(entity_id)
        numeric_id = (live_state or {}).get("attributes", {}).get("id")
        automation_id = str(numeric_id) if numeric_id else slug

        try:
            with httpx.Client() as client:
                resolved_id, raw = _fetch_config(automation_id, slug, client)
        except httpx.HTTPStatusError as exc:
            checked += 1
            results.append({
                "entity_id": entity_id, "name": row["name"],
                "read_error": error(
                    "config_read_failed",
                    f"Could not read {entity_id!r}'s stored config — the "
                    f"read itself failed ({exc.response.status_code}), "
                    "which is not the same as the automation not "
                    "existing.",
                    entity_id=entity_id, status=exc.response.status_code,
                ),
            })
            continue

        checked += 1
        if raw is None:
            results.append({
                "entity_id": entity_id, "name": row["name"],
                "read_error": error(
                    "not_found",
                    f"No stored automation config found for {entity_id} "
                    f"(tried id {automation_id!r} and slug {slug!r}).",
                    entity_id=entity_id,
                ),
            })
            continue

        config, _restore = to_modern(raw)
        outcome = _validate_config(
            resolved_id, entity_id, config.get("alias", row["name"]),
            config, states, entity_registry, device_registry,
        )
        if only_issues and not outcome["issues"] and not outcome["fail_open_waits"]:
            continue
        results.append(outcome)

    out = envelope(results, key="results")
    out["summary"] = {
        "checked": checked,
        "with_issues": sum(
            1 for r in results
            if "summary" in r and (r["issues"] or r["fail_open_waits"])
        ),
        "read_errors": sum(1 for r in results if "read_error" in r),
        "dead_references": sum(
            r["summary"]["dead_references"] for r in results if "summary" in r),
        "restored": sum(
            r["summary"]["restored"] for r in results if "summary" in r),
        "unavailable": sum(
            r["summary"]["unavailable"] for r in results if "summary" in r),
        "fail_open_waits": sum(
            r["summary"]["fail_open_waits"] for r in results if "summary" in r),
    }
    return out
