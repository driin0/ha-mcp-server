"""Plain dotted paths into a JSON document (a Lovelace dashboard config).

`views.0.sections.1.cards.5.title`: each segment is a dict key, or - when
the node it applies to is a list - a non-negative integer index. A
digit-only segment applied to a dict is a key ("1" in
button_card_templates). A key containing "." cannot be addressed.

Unlike tools/_aliases.py, nothing is aliased: that module's
trigger/action vocabulary belongs to automations and must not leak into
dashboards, where a card can legitimately have a key called `action`.

Every operation either succeeds completely or raises PathError before
changing anything; the message names the segment that failed and what IS
there (the dict's keys, or the list's length), so a caller can correct the
path without re-reading the whole document.
"""


class PathError(Exception):
    """A path that does not resolve the way the operation needs."""


def _describe(node) -> str:
    if isinstance(node, dict):
        keys = ", ".join(sorted(map(str, node))) or "(none)"
        return f"a dict with keys: {keys}"
    if isinstance(node, list):
        return f"a list of length {len(node)}"
    return f"a {type(node).__name__} value"


def _step(node, segment: str, walked: str, *, allow_end: bool = False):
    """Resolve one segment against `node`; return the key/index to use."""
    where = walked or "<root>"
    if isinstance(node, dict):
        return segment
    if isinstance(node, list):
        if not segment.isdigit():
            raise PathError(f"{segment!r} at {where} is not a list index - "
                            f"{where} is {_describe(node)}")
        index = int(segment)
        limit = len(node) + (1 if allow_end else 0)
        if index >= limit:
            raise PathError(f"index {index} at {where} is out of range - "
                            f"{where} is {_describe(node)}")
        return index
    raise PathError(f"cannot go into {segment!r}: {where} is {_describe(node)}")


def _parent(doc, path: str):
    """Walk to the node holding the last segment; return (parent, last, walked)."""
    if not path:
        raise PathError("empty path - the document root cannot be addressed")
    segments = path.split(".")
    node, walked = doc, ""
    for segment in segments[:-1]:
        key = _step(node, segment, walked)
        if isinstance(node, dict) and key not in node:
            raise PathError(f"{segment!r} not found at {walked or '<root>'} - "
                            f"it is {_describe(node)}")
        node = node[key]
        walked = f"{walked}.{segment}" if walked else segment
    return node, segments[-1], walked


def _existing(parent, last: str, walked: str):
    key = _step(parent, last, walked)
    if isinstance(parent, dict) and key not in parent:
        raise PathError(f"{last!r} not found at {walked or '<root>'} - "
                        f"it is {_describe(parent)}")
    return key


def get(doc, path: str):
    """The value at `path`."""
    parent, last, walked = _parent(doc, path)
    return parent[_existing(parent, last, walked)]


def set_(doc, path: str, value):
    """Replace the existing value at `path`; never creates. Returns the old value."""
    parent, last, walked = _parent(doc, path)
    key = _existing(parent, last, walked)
    old = parent[key]
    parent[key] = value
    return old


def insert(doc, path: str, value) -> None:
    """Explicit creation. Into a list: before index N (N == len appends).
    Into a dict: a key that must not exist yet."""
    parent, last, walked = _parent(doc, path)
    key = _step(parent, last, walked, allow_end=True)
    if isinstance(parent, dict):
        if key in parent:
            raise PathError(f"{last!r} already exists at {walked or '<root>'} - "
                            "use op='set' to replace it")
        parent[key] = value
    else:
        parent.insert(key, value)


def remove(doc, path: str):
    """Delete the list element or dict key at `path`. Returns what was removed."""
    parent, last, walked = _parent(doc, path)
    key = _existing(parent, last, walked)
    return parent.pop(key)
