#!/usr/bin/env python3
"""A CLI over tools/validation.py: run validate_all_automations() against a
live Home Assistant instance, print every dead reference, restored
reference, unavailable reference and fail-open wait_for_trigger it finds,
and exit non-zero when a dead reference or a fail-open wait was found - the
two faults tools/_refs.py's own module docstring describes destroying a
NAS. That exit code is the actual point: it is what makes this runnable in
CI, on a schedule, or as a pre-commit hook and actually TRUSTED to catch a
regression later, not just print one and exit 0 regardless.

    HA_URL=https://your-instance:8123 HA_TOKEN=... \\
        python3 scripts/lint_automations.py [--limit N]

## No offline mode, on purpose

Resolving a reference (tools/validation.py's job, over tools/_refs.py's
purely static extraction) needs the entity/device registry and the current
state machine - "does this id still exist here" is not a question a YAML
file lying on disk can answer about itself, no matter how it is parsed. An
offline mode could only ever run the structural half of the check -
find_fail_open_waits() (tools/_refs.py), which needs nothing but the config
- while silently skipping the half that actually needs a live instance,
which is worse than having no offline mode at all: a green run that only
ever ran half the check prints identically to a green run that ran all of
it, and nothing in its output would say which one just happened. Adding one
would also mean parsing YAML from a file this script does not otherwise
need to read at all - pulling PyYAML into the runtime image for a
developer/CI-only script, in exchange for a mode that would be misleading
to ship. Point this at a real (or throwaway) Home Assistant instance
instead.

## Why the exit code is the whole point

A check that can be silenced by accident - a copy-paste that changes
dialect without anyone noticing, a refactor that keeps printing but stops
returning a real status - does not fail; it just goes quiet, and the
quiet looks identical to "nothing is wrong" until whatever it used to
catch gets through anyway with nothing left to say so. Printing findings
to stdout is not itself a guarantee against that: a human has to keep
reading a log nobody is paid to reread on every green build. An exit code
is what a scheduler, a CI job, or a pre-commit hook actually acts on
without a human in the loop, so that is the one thing this script commits
to keeping honest:

- exit 0  - the sweep ran, and found zero dead references and zero
            fail-open waits (there may still be `restored`/`unavailable`
            warnings or unreadable configs printed above - see below for
            why those do not fail the build on their own).
- exit 1  - the sweep ran and found at least one dead reference or
            fail-open wait - see the printed report for exactly which
            automation and which one.
- exit 2  - the sweep could not run at all: HA_URL/HA_TOKEN (or the other
            variables tools/_base.py's own startup check requires) are not
            set, or the sweep itself failed outright (a transport or
            WebSocket failure reading the registry/states).

`restored` and `unavailable` outcomes (see tools/validation.py's own
docstring for what each means) are printed for visibility but deliberately
do NOT affect the exit code: both describe an INTEGRATION problem on this
instance right now (not loaded, or reporting itself offline), not a defect
in the automation's own config - failing a build over that would make this
script noisy about something a config change cannot fix, which is exactly
the kind of false alarm that trains people to stop reading it. A config a
config change CAN fix - a stale id, a fail-open wait ahead of something
destructive - is what exits non-zero.
"""
import argparse
import sys
from pathlib import Path

# Running this file directly (`python3 scripts/lint_automations.py`) puts
# scripts/ itself on sys.path[0], not the repository root - "import
# tools.validation" would fail with ModuleNotFoundError without this, the
# same fix tests/conftest.py applies for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _import_validate_all_automations():
    """Import tools.validation.validate_all_automations, translating
    tools._base's own import-time RuntimeError into one legible line on
    stderr instead of a traceback surfacing from three modules deep in an
    import chain this script's own caller never wrote.

    tools/_base.py (imported transitively the moment tools.validation is)
    raises RuntimeError at IMPORT time - not when a tool is called - when
    HA_URL or HA_TOKEN is unset, and separately when neither MCP_SECRET nor
    MCP_ALLOW_NO_AUTH is set (that second check exists for the MCP
    endpoint's own auth, which this script never starts or uses, but the
    module-level check runs regardless of which of tools._base's names are
    actually needed by whatever imported it). Both raise the identical
    exception type with a message that already says what to set - see
    tools/_base.py's own two checks - so this catches RuntimeError broadly
    rather than pattern-matching the message, and simply relays it.

    Returns the function on success, or None after printing the failure -
    a caller checks for None rather than letting an exception escape this
    function at all.
    """
    try:
        from tools.validation import validate_all_automations
    except RuntimeError as exc:
        print(f"lint_automations: cannot start - {exc}", file=sys.stderr)
        return None
    return validate_all_automations


def _format_issue(issue: dict) -> str:
    return (
        f"    [{issue['severity']}] {issue['outcome']} "
        f"at {issue['where']} ({issue['source']}) -> {issue['id']!r}\n"
        f"        {issue['detail']}"
    )


def _format_wait(wait: dict) -> str:
    return (
        f"    [fail-open wait] {wait['wait_where']} (timeout "
        f"{wait['timeout']!r}) can silently carry into {wait['action_where']} "
        f"({wait['service']})"
    )


def _print_report(result: dict) -> int:
    """Print one block per automation validate_all_automations() (called
    with only_issues=True, its own default - see main()) actually
    returned - every one of them already has something to report, since
    a clean automation was already filtered out before this function ever
    sees it.

    Returns the count of dead_reference issues plus fail_open_waits across
    every automation printed - the number main() uses to decide the exit
    code. restored/unavailable issues and read_error entries are printed
    (so nothing found is hidden), but are not counted here - see this
    module's own docstring for why only dead references and fail-open
    waits are what fails the build.
    """
    blocking = 0
    for row in result["results"]:
        if "read_error" in row:
            err = row["read_error"]
            print(f"{row['entity_id']} ({row['name']}): config could not be "
                  f"read - {err['error']}: {err['detail']}")
            continue

        print(f"{row['entity_id']} ({row['name']}):")
        for issue in row["issues"]:
            print(_format_issue(issue))
            if issue["outcome"] == "dead_reference":
                blocking += 1
        for wait in row["fail_open_waits"]:
            print(_format_wait(wait))
            blocking += 1
    return blocking


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate every automation's entity/device references and "
            "wait_for_trigger control flow against a live Home Assistant "
            "instance (HA_URL/HA_TOKEN), and exit non-zero when something "
            "needs fixing - see this module's own docstring for exactly "
            "which findings do and do not affect the exit code."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help=(
            "check only the first N automations (0, the default, checks "
            "all of them). validate_all_automations() makes one HTTP "
            "request per automation checked - see its own docstring "
            "(tools/validation.py) - so this is here for a very large "
            "instance, the same knob that tool already exposes."
        ),
    )
    args = parser.parse_args(argv)

    validate_all_automations = _import_validate_all_automations()
    if validate_all_automations is None:
        return 2

    result = validate_all_automations(only_issues=True, limit=args.limit)
    if "error" in result:
        print(
            f"lint_automations: could not run the sweep - "
            f"{result['error']}: {result.get('detail', '')}",
            file=sys.stderr,
        )
        return 2

    blocking = _print_report(result)

    summary = result["summary"]
    print(
        f"\nChecked {summary['checked']} automation(s): "
        f"{summary['dead_references']} dead reference(s), "
        f"{summary['restored']} restored (integration not loaded), "
        f"{summary['unavailable']} unavailable, "
        f"{summary['fail_open_waits']} fail-open wait(s), "
        f"{summary['read_errors']} config(s) could not be read."
    )

    if blocking:
        print(f"FAIL: {blocking} dead reference(s)/fail-open wait(s) found.")
        return 1

    print("OK: no dead references, no fail-open waits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
