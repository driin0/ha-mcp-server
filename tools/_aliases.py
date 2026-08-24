"""Normalise between Home Assistant's two automation vocabularies.

Home Assistant accepts two spellings for the same automation structure, and
a real instance commonly contains both - which style a given automation is
stored in depends on when it was last saved, not on anything a caller
controls:

    root:         trigger / condition / action  <->  triggers / conditions / actions
    trigger step: platform: ...                  <->  trigger: ...
    action step:  service: ...                    <->  action: ...

to_modern() renders a config in the modern vocabulary and records exactly
what it renamed; to_stored() reverses exactly those renames, so a caller
can work in one vocabulary while a write goes back in whatever style this
module read the automation in - all three pairs, root and both steps.

Whether that vocabulary survives on disk is not this module's call, though.
Measured live, posting a fully legacy config through Home Assistant's own
REST config-write endpoint and reading it straight back:

    root keys (trigger/condition/action -> triggers/conditions/actions):
      renamed by Home Assistant on every save, whatever is posted
    action step (service: -> action:):    renamed the same way, always
    trigger step (platform: -> trigger:): survives exactly as sent

That renaming is Home Assistant's own config-write endpoint doing it - to
any client, including its own UI editor - not something this module or a
caller of this REST API can prevent. This module still sends back exactly
what it read; a legacy automation edited through it will still come back
with plural root keys and action: instead of service: on its next read,
and that is not this module failing to do its job.

Never touching an automation this module cannot resolve a rename for is
still the price of the one thing it does control: a rename whose recorded
path no longer exists on the way back is skipped rather than raised,
because the restore map is a record of what normalisation did to one
particular config, not a requirement every future version of it must keep
satisfying (see to_stored()'s own docstring).

get_path()/set_path() give dotted-path access into a config
(`conditions.0.value_template`), for a tool that changes one nested value
without reconstructing the structure around it. Both resolve a legacy root
or step key as an alias for its modern equivalent, so a caller who does not
know - or does not care - which vocabulary a particular automation is
stored in can still write one path and have it resolve.

This module imports nothing from the project and touches no network: it is
pure data transformation over dicts and lists, which is what lets a future
offline linter over stored automations reuse it without pulling in httpx,
the MCP server, or a live Home Assistant connection.
"""
from __future__ import annotations

import copy

# Root-level key: legacy name -> modern name.
_ROOT_LEGACY_TO_MODERN = {
    "trigger": "triggers",
    "condition": "conditions",
    "action": "actions",
}

# Step-level key, keyed by the modern root list the step lives in:
# (legacy name, modern name). Only trigger and action steps have a second
# vocabulary for their own "what kind of step is this" key - a condition
# step has no legacy/modern split of its own, so it is not renamed at the
# step level at all, only carried along by the root-level rename above.
_STEP_LEGACY_TO_MODERN = {
    "triggers": ("platform", "trigger"),
    "actions": ("service", "action"),
}

# get_path()/set_path() alias tables, deliberately kept as TWO separate,
# position-gated dicts rather than one flat table - see _resolve_segment()
# for how position decides which one (if either) applies to a given
# segment. Each maps a segment name to every OTHER spelling that could
# legitimately be meant at that position; _resolve_segment() tries them in
# order and takes the first present in the dict actually being resolved
# against.
#
# Why two tables, and why gated by position at all: "action" is both the
# legacy ROOT key (aliasing to "actions", the modern root list) and,
# separately, the modern STEP key for an action step - the same string
# means two different things depending on where it appears, and a single
# flat table (this module's original shape) cannot hold two different
# targets for one key. Position resolves the ambiguity the same way a
# human reading the path would: "action" at the very start of a path
# means the root list; "action" naming a key inside some step dict means
# that step's own type key.
#
# Root aliases apply only at the very first path segment (get_path()/
# set_path() pass at_root=True there and nowhere else) - config's root is
# a single, fixed position, so there is never a reason to alias-resolve
# "trigger"/"condition"/"action"/"triggers"/"conditions"/"actions"
# anywhere else in a path.
_ROOT_ALIASES: dict[str, list[str]] = {
    "trigger": ["triggers"], "triggers": ["trigger"],
    "condition": ["conditions"], "conditions": ["condition"],
    "action": ["actions"], "actions": ["action"],
}

# Step aliases apply only when the dict being resolved against was itself
# reached by indexing into a list (get_path()/set_path() pass
# via_list_index=True there) - which is exactly what every trigger/
# condition/action step is, in HA's own schema, at any nesting depth: a
# top-level triggers[i]/actions[i], but equally choose[i].sequence[j],
# repeat.sequence[j], if.then[j]/else[j], parallel[j], or a
# wait_for_trigger's own nested trigger list - `sequence`, `then`,
# `else`, `choose`'s own branches, `parallel` and wait_for_trigger's own
# list are all always lists of step-shaped dicts, never a keyed dict of
# their own. Gating on "reached via a list index" therefore reaches every
# one of those nested positions with no special-casing needed for any of
# them - the same documented promise patch_automation() already made for
# a step's own spelling, now actually true past the top level too - while
# leaving a non-step dict alone: `target`, `data`, `event_data` and the
# like are always reached by a dict KEY (never a bare list index into
# their own container), so a coincidental key inside one of them named
# "trigger" or "action" is never mistaken for a step's own type key. This
# is a path-resolution concern only, separate from to_modern()'s own,
# deliberate choice to leave wait_for_trigger's nested steps unrenamed in
# the config it returns (see this module's own tests) - get_path()/
# set_path() operate on whatever vocabulary is actually stored there,
# aliased or not.
_STEP_ALIASES: dict[str, list[str]] = {
    "platform": ["trigger"], "trigger": ["platform"],
    "service": ["action"], "action": ["service"],
}


class PathError(KeyError):
    """A dotted path did not resolve against the config it was walked into.

    Never raised in a way that creates a key: get_path() only reads what is
    already there, and set_path() only replaces it - a mistyped path is
    reported, with what IS present named in the message, rather than
    silently adding a new key. That silent-creation failure mode is the
    class of fault this whole server exists to remove; a path that does not
    resolve must be loud, not a fresh, empty branch of the config.
    """


def to_modern(config: dict) -> tuple[dict, dict]:
    """Return (a normalised copy of config in the modern vocabulary, a
    restore map recording exactly what was renamed to produce it).

    config is never mutated - deep-copied first. The restore map is keyed
    by the dotted path of the renamed key *in the returned, normalised
    copy* (e.g. `{"triggers": "trigger", "triggers.0.trigger": "platform"}`)
    rather than by anything positional, so to_stored() can reverse each
    rename independently and exactly - including for a config that mixes
    both vocabularies, a modern root whose steps still say `platform`, or
    the reverse: a legacy root whose steps were already written `trigger`.
    A config already fully in the modern vocabulary round-trips with an
    empty restore map.
    """
    normalised = copy.deepcopy(config)
    restore: dict[str, str] = {}

    for legacy, modern in _ROOT_LEGACY_TO_MODERN.items():
        if legacy in normalised and modern not in normalised:
            normalised[modern] = normalised.pop(legacy)
            restore[modern] = legacy

    for root, (legacy_key, modern_key) in _STEP_LEGACY_TO_MODERN.items():
        steps = normalised.get(root)
        if not isinstance(steps, list):
            continue
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if legacy_key in step and modern_key not in step:
                step[modern_key] = step.pop(legacy_key)
                restore[f"{root}.{i}.{modern_key}"] = legacy_key

    return normalised, restore


def to_stored(config: dict, restore: dict) -> dict:
    """Reverse exactly the renames `restore` records, and return the
    result - config is never mutated.

    Deepest paths first: a step-level rename (`triggers.0.trigger`) must be
    reversed while its parent list is still named `triggers`, so reversing
    root-first (renaming `triggers` back to `trigger` before its children
    are visited) would strand the step-level entries. Independent paths at
    the same depth are reversed in no particular order relative to each
    other, since neither can affect where the other resolves.

    A path that no longer resolves against config is skipped, not raised.
    An update between to_modern() and to_stored() may have replaced the
    whole list a recorded step lived in, or removed it - the restore map
    describes what normalisation did to the config it was built from, not
    a requirement the config must still satisfy by the time it is reversed.
    """
    result = copy.deepcopy(config)
    for path in sorted(restore, key=lambda p: p.count("."), reverse=True):
        legacy_key = restore[path]
        try:
            value = get_path(result, path)
        except PathError:
            continue
        parent_path, _, modern_key = path.rpartition(".")
        parent = get_path(result, parent_path) if parent_path else result
        del parent[modern_key]
        parent[legacy_key] = value
    return result


def stored_format(restore: dict) -> str:
    """"legacy" if the root-level vocabulary (trigger/condition/action) was
    renamed to produce the normalised config that `restore` came from,
    "modern" if it was already written that way.

    Named after the root style specifically, not every rename recorded: a
    config whose root was already modern but whose steps still said
    `platform`/`service` still reports "modern" here, because the root
    spelling is what a caller and an edit tool alike recognise an
    automation's era by. An edit tool sends this spelling back unchanged
    at its own end - see the module docstring above for why that is not
    a promise about what Home Assistant's own config-write endpoint
    leaves on disk afterward.
    """
    root_paths = set(_ROOT_LEGACY_TO_MODERN.values())
    return "legacy" if root_paths & restore.keys() else "modern"


def _resolve_segment(current, segment: str, walked: str, *,
                     at_root: bool, via_list_index: bool):
    """Resolve one dotted-path segment against `current` (a dict or list),
    accepting a legacy key as an alias for its modern equivalent - or a
    modern key as an alias for a legacy one still on disk - when, and
    only when, `current`'s own position makes that alias meaningful (see
    _ROOT_ALIASES/_STEP_ALIASES above). Returns the concrete key/index to
    index `current` with. Raises PathError naming what IS there when the
    segment resolves to nothing - never creates anything.

    at_root: True only for the very first segment of the whole path -
      config's root is the one position _ROOT_ALIASES applies at.
    via_list_index: True when `current` was itself reached by indexing
      into a list on the previous segment - the one position
      _STEP_ALIASES applies at (see its own comment for why this is the
      right proxy for "this is a step dict" without knowing HA's schema
      any more specifically than that).
    """
    if isinstance(current, dict):
        if segment in current:
            return segment
        table = _ROOT_ALIASES if at_root else _STEP_ALIASES if via_list_index else None
        for candidate in (table.get(segment, []) if table else []):
            if candidate in current:
                return candidate
        raise PathError(
            f"{walked!r} has no key {segment!r} - present keys: "
            f"{sorted(current.keys())}"
        )
    if isinstance(current, list):
        if not segment.lstrip("-").isdigit():
            raise PathError(
                f"{walked!r} is a list of {len(current)} item(s) - "
                f"{segment!r} is not a valid index"
            )
        index = int(segment)
        if not (-len(current) <= index < len(current)):
            raise PathError(
                f"{walked!r} is a list of {len(current)} item(s) - index "
                f"{index} is out of range"
            )
        return index
    raise PathError(
        f"{walked!r} is a {type(current).__name__}, not a dict or list - "
        "cannot descend into it"
    )


def _walk_to(config: dict, path: str) -> tuple:
    """Walk dot-separated `path` into config (an empty string means "no
    segments - stay at the root") and return (value_reached, at_root,
    via_list_index): the last two describe the exact position
    `value_reached` is AT, which is precisely the context
    _resolve_segment() needs to resolve one further segment against it.

    Shared by get_path() (which only wants `value_reached`) and
    set_path() (which walks up to the segment BEFORE the last this way,
    then resolves the last segment separately against the (position,
    value) this returns - see set_path()'s own docstring for why the
    last segment is never folded into this same walk).
    """
    if not path:
        return config, True, False
    current = config
    walked = "<root>"
    at_root = True
    via_list_index = False
    for segment in path.split("."):
        key = _resolve_segment(current, segment, walked,
                               at_root=at_root, via_list_index=via_list_index)
        current = current[key]
        at_root = False
        via_list_index = isinstance(key, int)
        walked = segment if walked == "<root>" else f"{walked}.{segment}"
    return current, at_root, via_list_index


def get_path(config: dict, path: str):
    """Walk dot-separated `path` (e.g. `"conditions.0.value_template"`,
    integer segments indexing into a list) into config and return the
    value found there.

    Accepts a legacy root or step key (`trigger`, `condition`, `action`,
    `platform`, `service`) as an alias for its modern equivalent, or the
    modern spelling as an alias for a legacy one still on disk, at any
    segment where that vocabulary actually applies - the automation's
    root, and any trigger/condition/action step, however deeply nested
    (a top-level list item, or one inside `choose`/`if`/`repeat`/
    `parallel`/`wait_for_trigger` - see _STEP_ALIASES's own comment for
    why nesting depth does not matter here) - so a caller does not need
    to know which vocabulary this particular position is written in,
    root or step, top-level or nested. A coincidentally-named key inside
    an unrelated payload dict (`data`, `target`, `event_data`) is never
    aliased, on purpose - see _STEP_ALIASES's own comment for what that
    would otherwise silently target instead. Raises PathError, naming
    what IS present at the point resolution failed, rather than a bare
    KeyError/IndexError/TypeError.
    """
    value, _, _ = _walk_to(config, path)
    return value


def set_path(config: dict, path: str, value) -> None:
    """Replace the value already at `path` with `value`, in place.

    Never creates a key: the full path - including its final segment -
    must already resolve, via the same rules get_path() uses (list
    indices, and either vocabulary accepted wherever it actually applies
    - see get_path()'s own docstring), or this raises PathError instead
    of adding one. A mistyped path must fail loudly, not silently grow
    the config with a new branch nothing else will ever read.
    """
    parent_path, _, last = path.rpartition(".")
    parent, at_root, via_list_index = _walk_to(config, parent_path)
    walked = parent_path if parent_path else "<root>"
    key = _resolve_segment(parent, last, walked,
                           at_root=at_root, via_list_index=via_list_index)
    parent[key] = value
