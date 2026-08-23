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

# Every legacy -> modern rename this module knows, flattened into one table
# keyed by the legacy spelling alone - used by get_path()/set_path() to
# accept either spelling for a single path segment, root or step alike,
# without the caller needing to know which level a given segment names.
_ALIASES: dict[str, str] = dict(_ROOT_LEGACY_TO_MODERN)
for _legacy_step, _modern_step in _STEP_LEGACY_TO_MODERN.values():
    _ALIASES[_legacy_step] = _modern_step


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


def _resolve_segment(current, segment: str, walked: str):
    """Resolve one dotted-path segment against `current` (a dict or list),
    accepting a legacy key as an alias for its modern equivalent. Returns
    the concrete key/index to index `current` with. Raises PathError naming
    what IS there when the segment resolves to nothing - never creates
    anything.
    """
    if isinstance(current, dict):
        if segment in current:
            return segment
        alias = _ALIASES.get(segment)
        if alias is not None and alias in current:
            return alias
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


def get_path(config: dict, path: str):
    """Walk dot-separated `path` (e.g. `"conditions.0.value_template"`,
    integer segments indexing into a list) into config and return the
    value found there.

    Accepts a legacy root or step key (`trigger`, `condition`, `action`,
    `platform`, `service`) as an alias for its modern equivalent at any
    segment, so a caller does not need to know which vocabulary this
    particular config is written in. Raises PathError, naming what IS
    present at the point resolution failed, rather than a bare
    KeyError/IndexError/TypeError.
    """
    current = config
    walked = "<root>"
    for segment in path.split("."):
        key = _resolve_segment(current, segment, walked)
        current = current[key]
        walked = segment if walked == "<root>" else f"{walked}.{segment}"
    return current


def set_path(config: dict, path: str, value) -> None:
    """Replace the value already at `path` with `value`, in place.

    Never creates a key: the full path - including its final segment -
    must already resolve, via the same rules get_path() uses (list
    indices, and a legacy key accepted as an alias for its modern
    equivalent), or this raises PathError instead of adding one. A
    mistyped path must fail loudly, not silently grow the config with a
    new branch nothing else will ever read.
    """
    parent_path, _, last = path.rpartition(".")
    parent = get_path(config, parent_path) if parent_path else config
    walked = parent_path if parent_path else "<root>"
    key = _resolve_segment(parent, last, walked)
    parent[key] = value
