"""Fail the image build when the image could not start.

2.2.0 shipped without tool_tracking.py and crashed on its first line, past
a green build, a green CI and 652 passing tests - all of which import from
the source tree, none of which had ever executed anything inside the image.

Two checks, because each one alone passes on exactly the tree that crashed:

1. Every module server.py imports must import. A plain `import server` is
   NOT enough and looks like it is: server.py imports tool_tracking inside
   its `if __name__ == "__main__":` block, which an import never executes.
   So the imports are read from the AST, which sees the whole file.

2. Every module present must import. This catches a module that is shipped
   but broken, which check 1 misses whenever server.py does not name it.

Run as a script; importing it does nothing.
"""
import ast
import importlib
import os
import pathlib
import sys

APP = pathlib.Path(__file__).resolve().parent

# tools/_base.py and web.py both refuse to load without these, by design -
# a server that starts with no credentials and no dashboard password is the
# failure those checks exist to prevent. Placeholders let the import
# succeed; nothing reached at import time makes a network call, so they are
# never used for anything.
#
# They live here rather than on the Dockerfile's RUN line because this
# repository's own pre-commit hook refuses a literal HA_TOKEN= assignment
# in a tracked file, and it is right to: a guard that can be worked around
# by whoever finds it inconvenient is not a guard. setdefault also means a
# real environment wins, so this file is safe to run anywhere.
for _name, _placeholder in (
    ("HA_URL", "http://build-time-check"),
    ("HA_TOKEN", "build-time-check"),
    ("MCP_SECRET", "build-time-check"),
    ("UI_SECRET", "build-time-check"),
):
    os.environ.setdefault(_name, _placeholder)


def named_by(path: pathlib.Path) -> set[str]:
    """Top-level module names imported anywhere in `path`, __main__ included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def check() -> None:
    wanted = named_by(APP / "server.py")
    wanted |= {p.stem for p in APP.glob("*.py")}
    wanted.discard(pathlib.Path(__file__).stem)

    for name in sorted(wanted):
        importlib.import_module(name)

    print(f"build check: {len(wanted)} modules import cleanly inside the image")


if __name__ == "__main__":
    try:
        check()
    except Exception as exc:
        print(f"BUILD CHECK FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
