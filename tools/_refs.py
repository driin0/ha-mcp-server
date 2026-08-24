"""What a Home Assistant automation config references, and one specific
failure shape that has nothing to do with what it references at all.

Pure: no httpx, no `_ws`, nothing imported from `tools._base` or anywhere
else in this project. extract_refs() and find_fail_open_waits() take a
plain dict and return plain data. That is what lets a validator tool
(`tools/validation.py`, resolving against a live registry) and a CI script
(`scripts/lint_automations.py`, pointed at a YAML file on a machine with no
Home Assistant on it) share the exact same extraction code, and what lets
this module be tested with no mocks and no network at all.

## Why this module exists

A Home Assistant automation cut mains power to a running NAS mid-write and
corrupted 245 GB. The guard protecting it was

    {{ not is_state("button.nas_shutdown", "unavailable") }}

and the entity had been renamed to `button.nas_shut_down`. In Home
Assistant the state of an entity that does not exist is `None`, never the
string `"unavailable"` — so `is_state()` returned `False`, `not False` is
`True`, and the guard passed. It did not stop working; it began failing
open, silently, with no error, no log line and no repair issue. The second
fault was a `wait_for_trigger` with a timeout and no
`continue_on_timeout: false`, which then carried execution into
`switch.turn_off` against a machine that was still writing.

extract_refs() finds every entity/device a config points at — the raw
material a caller (tools/validation.py) needs to check each one still
exists. find_fail_open_waits() finds the second fault directly: a
wait_for_trigger that can silently let execution continue into something
destructive.

## Why extract_refs() does not need to know Home Assistant's two vocabularies

Home Assistant accepts two spellings for a stored automation (see
tools/_aliases.py's own module docstring for the full breakdown):
root keys `trigger/condition/action` vs. `triggers/conditions/actions`,
and step keys `platform`/`trigger`, `service`/`action`. extract_refs()
reads none of those key names — it looks for `entity_id`, `device_id` and
template strings, and those three are spelled identically in both
vocabularies. Walking the whole object, rather than enumerating which
shapes can hold a reference, is what makes that true without this module
having to know the difference at all — and it means a structure nobody
anticipated (a new HA feature, an integration's own custom action) is
still searched, not silently skipped because it wasn't on a list.

The one exception is find_fail_open_waits(): to judge whether a step is
destructive it must read the service name, and that lives under
`service:` or `action:` depending on when the automation was last saved.
It is the one place in this module that reads both spellings — see its own
docstring.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# extract_refs()
# ---------------------------------------------------------------------------

# A real entity_id: exactly one dot, lowercase letters/digits/underscore on
# both sides. This is deliberately what excludes "all", "none" and a
# template ({{ ... }}, {% ... %} both contain characters this cannot match)
# from being reported as a reference — see extract_refs()'s own docstring
# for why those three must never be reported.
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

_TEMPLATE_MARKERS = ("{{", "{%")

# Every Jinja helper Home Assistant provides that takes an entity_id as its
# first argument, in the vocabulary a template author actually writes: the
# "is_state_attr" before "is_state" ordering below is cosmetic, not load
# bearing — _TEMPLATE_CALL_RE's own trailing "(" after the name means a
# regex engine's ordinary alternation backtracking already picks the right
# one regardless of order (see this module's own tests).
_TEMPLATE_FUNCTIONS = (
    "is_state_attr", "is_state", "state_attr", "states", "expand", "has_value",
)
_TEMPLATE_CALL_RE = re.compile(
    r"\b(?:" + "|".join(_TEMPLATE_FUNCTIONS) + r")\(\s*['\"]([^'\"]+)['\"]"
)
# The states.<domain>.<object_id> Jinja *attribute* form — no call, no
# quotes (e.g. `states.sensor.temp.state`). Independent of
# _TEMPLATE_CALL_RE's `states(...)` *call* form above; the two never
# collide because a literal "(" immediately follows a call, never a ".".
_STATES_ATTR_RE = re.compile(r"\bstates\.([a-z0-9_]+)\.([a-z0-9_]+)\b")


def _is_template(value: str) -> bool:
    """Whether `value` contains a Jinja delimiter. No maintained list of
    which fields can hold a template — see this module's own docstring for
    why: any string in Home Assistant may be one, and a list of which
    fields can is exactly the kind of thing that goes stale in silence."""
    return any(marker in value for marker in _TEMPLATE_MARKERS)


def _looks_like_entity_id(value: str) -> bool:
    return bool(_ENTITY_ID_RE.fullmatch(value))


def _field_ref(value, kind: str, where: str, refs: list) -> None:
    """Append one field reference at `where` if `value` is a plausible,
    specific id of `kind` - never for a template (rendered by Home
    Assistant before dispatch, not knowable statically), and never for
    "all"/"none" (valid Home Assistant, targeting every entity of a
    domain or none at all - not a reference to any one entity)."""
    if not isinstance(value, str):
        return
    if _is_template(value):
        return
    if value in ("all", "none"):
        return
    if kind == "entity" and not _looks_like_entity_id(value):
        return
    if kind == "device" and not value.strip():
        return
    refs.append({"id": value, "kind": kind, "where": where, "source": "field"})


def _template_refs(value: str, where: str, refs: list) -> None:
    """Append one template reference for every entity a template string
    names via a recognised Jinja helper or the states.<domain>.<object_id>
    attribute form - see the module-level regexes above."""
    if not _is_template(value):
        return
    for match in _TEMPLATE_CALL_RE.finditer(value):
        candidate = match.group(1)
        if _looks_like_entity_id(candidate):
            refs.append({"id": candidate, "kind": "entity",
                        "where": where, "source": "template"})
    for match in _STATES_ATTR_RE.finditer(value):
        refs.append({"id": f"{match.group(1)}.{match.group(2)}", "kind": "entity",
                    "where": where, "source": "template"})


def _walk(node, path: str, refs: list) -> None:
    """Recurse into every dict and list in `node`, in no particular order
    tied to Home Assistant's own schema - see the module docstring for why
    that is the point, not a shortcut."""
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in ("entity_id", "device_id"):
                kind = "entity" if key == "entity_id" else "device"
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        _field_ref(item, kind, f"{child_path}.{i}", refs)
                else:
                    _field_ref(value, kind, child_path, refs)
            _walk(value, child_path, refs)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            child_path = f"{path}.{i}" if path else str(i)
            _walk(item, child_path, refs)
    elif isinstance(node, str):
        _template_refs(node, path, refs)


def extract_refs(config: dict) -> list[dict]:
    """Every entity/device an automation config references - by a field
    named entity_id/device_id, or by name inside a template string.

    Walks the whole object: no list of which shapes ("a trigger", "an
    action target") can hold a reference, so a structure this module was
    never told about is still searched, not silently skipped. Vocabulary
    agnostic by construction - see the module docstring for why
    entity_id/device_id/template strings need no translation between
    Home Assistant's legacy and modern spellings.

    Returns a list of {"id", "kind", "where", "source"}:
      id:     the entity_id or device_id string itself, exactly as
              written in the config (a template reference to a stale,
              renamed id is reported under that stale id - resolving
              whether it still exists is tools/validation.py's job, not
              this one).
      kind:   "entity" or "device".
      where:  a dotted path from the config's own root to the field or
              template string this reference was found in (e.g.
              "action.1.target.entity_id", "condition.0.value_template"),
              using whichever root/step key names the config actually
              used - this module never renames anything.
      source: "field" (an entity_id/device_id key) or "template" (found
              inside a string containing {{ or {%).

    Never reports "all", "none", or a template as a reference: none of
    the three names one specific entity or device - see _field_ref()'s
    own docstring. A single string can produce more than one ref (several
    is_state() calls in one template); a list-valued entity_id/device_id
    field produces one ref per element, at an indexed path.
    """
    refs: list[dict] = []
    _walk(config, "", refs)
    return refs


# ---------------------------------------------------------------------------
# find_fail_open_waits()
# ---------------------------------------------------------------------------

# Home Assistant's own fixed action-step wrapper vocabulary - if/then,
# if/else, choose's own branches and its default, repeat's own sequence,
# and parallel's own branches. Unlike the open-ended set of fields that can
# hold a template, this is a small, stable set of control-flow keywords
# HA's own schema defines; naming them here carries none of the
# maintained-list drift risk the module docstring warns about for
# templates.
_DIRECT_SEQUENCE_KEYS = ("then", "else", "default", "sequence")


def _destructive_service(service: str) -> bool:
    """Whether calling `service` is the kind of action a fail-open wait
    must not be allowed to silently carry execution into: *.turn_off (any
    domain - a light, a climate device, a media player, a switch),
    switch.* (any switch service, not only turn_off - toggling a switch
    can cut power exactly the way turn_off can), homeassistant.stop or
    .restart, and hassio.host_* (host_shutdown, host_reboot, ...)."""
    if not isinstance(service, str) or "." not in service:
        return False
    domain, _, action = service.partition(".")
    if action == "turn_off":
        return True
    if domain == "switch":
        return True
    if domain == "homeassistant" and action in ("stop", "restart"):
        return True
    if domain == "hassio" and action.startswith("host_"):
        return True
    return False


def _step_service(step: dict) -> str | None:
    """The service a step calls, in whichever vocabulary it was written -
    'service:' (legacy) or 'action:' (modern) - the one place in this
    module both spellings matter, because judging a step's
    destructiveness needs its actual service name. Every other key this
    module reads (entity_id, device_id, wait_for_trigger, then/else/
    default/sequence/repeat/choose/parallel) is spelled identically in
    both vocabularies."""
    service = step.get("service", step.get("action"))
    return service if isinstance(service, str) else None


def _wait_fails_open(step: dict) -> bool:
    """A wait_for_trigger step fails open when it carries a timeout and
    does not explicitly set continue_on_timeout: false. No timeout at all
    is not reported: with nothing to time out on, the step blocks forever
    waiting for its own trigger, which fails closed by construction."""
    if "timeout" not in step:
        return False
    return step.get("continue_on_timeout") is not False


def _nested_sequences(step: dict, step_path: str):
    """Yield (steps, path) for every nested sequence of steps reachable
    from one step, however Home Assistant wraps it - if/then, if/else,
    choose's own branches and its default, repeat's own sequence, and
    parallel's own branches (each of which is itself either a bare list
    of steps or a further {"sequence": [...]} wrapper)."""
    for key in _DIRECT_SEQUENCE_KEYS:
        value = step.get(key)
        if isinstance(value, list):
            yield value, f"{step_path}.{key}"

    repeat = step.get("repeat")
    if isinstance(repeat, dict) and isinstance(repeat.get("sequence"), list):
        yield repeat["sequence"], f"{step_path}.repeat.sequence"

    choose = step.get("choose")
    if isinstance(choose, list):
        for i, branch in enumerate(choose):
            if isinstance(branch, dict) and isinstance(branch.get("sequence"), list):
                yield branch["sequence"], f"{step_path}.choose.{i}.sequence"

    parallel = step.get("parallel")
    if isinstance(parallel, list):
        for i, branch in enumerate(parallel):
            if isinstance(branch, list):
                yield branch, f"{step_path}.parallel.{i}"
            elif isinstance(branch, dict) and isinstance(branch.get("sequence"), list):
                yield branch["sequence"], f"{step_path}.parallel.{i}.sequence"


def _scan_sequence(steps, path: str, results: list, pending: dict | None = None) -> None:
    """Walk one flat list of steps looking for a fail-open wait_for_trigger
    followed - later in this exact list, OR inside any sequence nested
    inside a LATER step of this list (an if/then, a choose branch's own
    sequence, ...) - by a destructive action. A destructive action nested
    inside a branch that sits BEFORE the wait, or inside a sibling branch
    the wait itself is nested in, is a different case - see
    find_fail_open_waits()'s own docstring for why the two are not the
    same and must not be conflated.

    `pending` names the closest fail-open wait behind the step currently
    being looked at, or None. A fresh top-level scan (the call
    find_fail_open_waits() makes) starts with pending=None; every
    recursive call this function makes into a nested sequence is started
    with the CURRENT value of `pending` at the step being recursed from -
    never reinitialised to None - because a step nested inside a LATER
    step in this list (the `then` branch of an `if`, one `choose`
    branch's own `sequence`, ...) is still reachable through that same
    fail-open wait's own timeout: whichever inner branch execution
    happens to take, it is still on the path the wait's timeout opened up.
    Reinitialising `pending` to None at the top of every call - the bug
    this module shipped with - is exactly what let a wait followed by an
    `if:`/`then:` or a `choose:`/`sequence:` slip past this check
    silently: the destructive action sat one level of nesting below the
    wait, fully reachable from it, and a fresh, wait-blind `pending`
    inside the recursive call never saw it.

    It is set on every wait_for_trigger step, fail-open or not: reaching
    *any* wait_for_trigger re-gates whatever follows it, because getting
    past it now requires either that wait's own trigger to fire or (only
    if it fails open) its own timeout - a later, safely-blocking wait is
    not exposed by an earlier fail-open one, so a fail-open wait is only
    ever pending until the next wait_for_trigger, whichever kind it is
    (see this module's own tests for why). It is *not* cleared on the
    first destructive action found after it: every destructive step
    reachable before the next wait_for_trigger is equally exposed by that
    same open guard, not just the first one.

    A nested call's own local changes to `pending` (its own
    wait_for_trigger steps, fail-open or not) are never propagated back
    out to the caller: once a recursive call returns, the scan that made
    it continues with whatever `pending` it already had, because a
    branch's own inner waits do not change whether the OUTER wait's
    timeout can still reach a step later in the OUTER list - it already
    could, before that branch was ever entered.
    """
    if not isinstance(steps, list):
        return
    for i, step in enumerate(steps):
        step_path = f"{path}.{i}" if path else str(i)
        if not isinstance(step, dict):
            continue

        if "wait_for_trigger" in step:
            pending = {"where": step_path, "timeout": step.get("timeout")} \
                if _wait_fails_open(step) else None
        else:
            service = _step_service(step)
            if pending is not None and service and _destructive_service(service):
                results.append({
                    "wait_where": pending["where"],
                    "timeout": pending["timeout"],
                    "action_where": step_path,
                    "service": service,
                })

        for nested_steps, nested_path in _nested_sequences(step, step_path):
            _scan_sequence(nested_steps, nested_path, results, pending)


def find_fail_open_waits(config: dict) -> list[dict]:
    """Every wait_for_trigger in `config` that can silently let execution
    continue - via its own timeout, with no `continue_on_timeout: false`
    to stop it - into a destructive action later in the same sequence.

    This is the second half of the incident this module exists for (see
    the module docstring): a wait_for_trigger with a timeout and no
    continue_on_timeout: false, that then carried execution into
    switch.turn_off against a machine still writing to disk. Detecting it
    needs no knowledge of what any entity's state actually is - it is a
    property of the config's own control flow, checked entirely
    statically.

    Recurses into every nested sequence of steps Home Assistant's own
    schema defines - if/then, if/else, choose's own branches and its
    default, repeat's own sequence, and parallel's own branches - and a
    fail-open wait's `pending` state (see _scan_sequence()'s own
    docstring) is threaded into every one of those nested sequences that
    sits AFTER the wait in the same flat list, not reset to None at each
    level of nesting. Concretely, two shapes that sound alike are not the
    same:

    - REACHABLE, and reported: a destructive action nested inside a LATER
      step of the same list the wait sits in - the `then` branch of an
      `if` that comes after the wait, one `choose` branch's own
      `sequence` - because whichever inner branch execution takes, it is
      still on the path the wait's own timeout opened up. `wait, then an
      if:/then: containing switch.turn_off` and `wait, then a
      choose:/sequence: containing switch.turn_off` are both this shape,
      and both are reported - this is the exact shape Home Assistant's own
      UI editor produces for "wait, then act", and silently missing it
      was the mechanism this check most needed to catch.
    - NOT reachable, and not reported: a destructive action living in a
      *different branch than the wait itself* - a step in a sibling
      `choose` branch, when the wait is nested inside a *different*
      branch of that same `choose` - because execution that takes the
      sibling branch never runs the branch holding the wait at all, and
      so can never reach that destructive action through this wait's
      timeout either.

    The first shape being unreachable was this module's own bug (fixed by
    threading `pending` through the recursive calls - see
    _scan_sequence()); the second is correct by construction, not a gap.

    Returns a list of:
      {"wait_where": <path to the wait_for_trigger step>,
       "timeout": <its timeout value, exactly as stored>,
       "action_where": <path to the destructive step found after it>,
       "service": <that step's own service name>}

    A wait with no timeout is never reported: with nothing to time out
    on, it blocks forever waiting for its own trigger, which fails
    closed, not open - see _wait_fails_open()'s own docstring. Every
    destructive action reachable after a fail-open wait, not only the
    first, gets its own entry - see _scan_sequence()'s own docstring for
    why.
    """
    results: list[dict] = []
    actions = config.get("actions", config.get("action"))
    root_path = "actions" if "actions" in config else "action"
    _scan_sequence(actions, root_path, results)
    return results
