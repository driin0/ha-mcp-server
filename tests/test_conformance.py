"""Structural checks over every @mcp.tool() in the server.

A 54-file mechanical sweep raises one question a behavioural test cannot
answer: did I miss one. This walks the AST and answers it, and goes on
answering it for tools written later.
"""
import ast
import importlib
import inspect
import pathlib
import warnings

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "tools"

# The three tools that legitimately return a string.
STRING_TOOLS = {"get_addon_logs", "get_error_log", "render_template"}

# Zero-argument tools the runtime sweep below must never actually call, even
# against the fake: they are safe today only because httpx.Client, _ws and
# _ws_multi are all patched by the fake_ha fixture, and a future tool using
# some other transport (httpx.AsyncClient, requests, a raw socket) would
# turn a routine `pytest` run into a real restart - or backup job - against
# whatever HA_URL happens to be set to. See tests/conftest.py for the other
# half of this defence (HA_URL/HA_TOKEN forced, not defaulted).
DESTRUCTIVE_TOOLS = {"restart_homeassistant", "create_backup"}

# Tools still returning a list. Empty: the sweep is complete, and this check
# now applies to all 182 tools, and to every tool written from here on.
NOT_YET_CONVERTED = set()


def _tools():
    """Yield (filename, FunctionDef) for every @mcp.tool() in tools/."""
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any("mcp.tool" in ast.unparse(d) for d in node.decorator_list):
                yield path.name, node


def _return_type(node) -> str:
    return ast.unparse(node.returns) if node.returns else "(none)"


def _direct_returns(node):
    """Yield every ast.Return belonging to `node` itself - not one nested
    inside a helper function or lambda `node` defines internally.

    Plain ast.walk(node) descends into everything, including a nested
    `def matches(s): ...; return False` a tool builds to hand to
    observe_actuation() (see e.g. tools/climate.py's set_climate or
    tools/groups.py's update_group) - a `return False` picking the
    predicate's own answer, not the tool's. Skipping into every
    FunctionDef/AsyncFunctionDef/Lambda a tool's body contains keeps a
    source-level sweep looking at what the tool itself hands back to its
    caller, not at some closure's local control flow that happens to share
    the keyword `return`.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Return):
            yield child
        yield from _direct_returns(child)


def test_the_tool_census_does_not_silently_shrink():
    """A guard on the guard. A floor, not an equality: plans 2 and 3 add
    tools, and a test that has to be edited to stay true gets edited without
    being read."""
    assert len(list(_tools())) >= 182


def test_every_tool_returns_dict():
    offenders = [
        f"{fname}:{node.lineno} {node.name} -> {_return_type(node)}"
        for fname, node in _tools()
        if _return_type(node) != "dict"
        and node.name not in STRING_TOOLS
        and node.name not in NOT_YET_CONVERTED
    ]
    assert not offenders, (
        "These tools must return dict — wrap them with envelope() or error():\n"
        + "\n".join(offenders)
    )


def test_no_tool_returns_a_bare_list_literal():
    """`return [err]` makes a failure reachable by iterating results."""
    offenders = []
    for fname, node in _tools():
        if node.name in NOT_YET_CONVERTED:
            continue
        for inner in _direct_returns(node):
            if isinstance(inner.value, ast.List):
                offenders.append(f"{fname}:{inner.lineno} in {node.name}")
    assert not offenders, (
        "Return envelope(...) or error(...), not a list literal:\n"
        + "\n".join(offenders)
    )


def test_no_tool_returns_a_bare_scalar_literal():
    """The write half of the convention documented in tools/_base.py's module
    docstring: "never a bare bool, a bare string, or None." A dict return
    gives a caller (a language model reading raw JSON with no schema) field
    names to hang a question on; `return True` or `return None` gives it
    nothing to ask about, and `return "ok"` looks identical in shape to an
    error message with no "error" key to tell the two apart.

    A literal `return True`/`False`/`None`/a bare string parses to
    ast.Return(value=ast.Constant(...)) — this walks every tool's own
    returns (see _direct_returns()) the same way
    test_no_tool_returns_a_bare_list_literal does for `ast.List`, so a tool
    that switched from "returns a list" to "returns a scalar" instead of
    "returns a dict" would still be caught. The three tools that
    legitimately return a string (their output IS the payload, not an
    acknowledgement of one) are exempted, same as everywhere else in this
    file that draws that distinction.
    """
    offenders = []
    for fname, node in _tools():
        if node.name in STRING_TOOLS or node.name in NOT_YET_CONVERTED:
            continue
        for inner in _direct_returns(node):
            if isinstance(inner.value, ast.Constant):
                offenders.append(
                    f"{fname}:{inner.lineno} in {node.name} -> "
                    f"{inner.value.value!r}"
                )
    assert not offenders, (
        "Return envelope(...) or error(...), or a dict literal with named "
        "fields - not a bare scalar:\n" + "\n".join(offenders)
    )


def test_the_allowlist_has_no_stale_entries():
    converted = {
        node.name for _, node in _tools() if _return_type(node) == "dict"
    }
    stale = NOT_YET_CONVERTED & converted
    assert not stale, (
        "Converted — remove from NOT_YET_CONVERTED:\n" + "\n".join(sorted(stale))
    )


def test_every_zero_arg_tool_actually_returns_a_dict_at_runtime(fake_ha):
    """Runtime companion to test_every_tool_returns_dict.

    The two checks above read source, not behaviour: one looks at the return
    annotation, the other looks for a literal `return [...]`. Neither
    evaluates the function body. A tool converted by hand across eighteen
    near-identical files can end up annotated `-> dict` while a stray line
    still does `return sorted(rows, key=...)` — a plain `Call`, not an
    `ast.List`, so it is invisible to both static checks and yet returns a
    list at runtime. This test calls the tool for real and looks at what
    comes back.

    What it proves: every tool that (a) is not one of STRING_TOOLS, (b) can
    be called with zero arguments because every one of its parameters has a
    default, and (c) does not raise when called against the fake Home
    Assistant, returns a dict rather than a list or anything else.

    What it does NOT prove: nothing about a tool that requires at least one
    argument — the large majority of the 182, mostly the *_control /
    create_* / delete_* actions — and nothing about a tool that raises here
    (an unmapped WebSocket command, a REST route the fake does not serve, a
    real bug). Both kinds are skipped, not failed. Tools in DESTRUCTIVE_TOOLS
    are skipped too, on purpose: they are not called even against the fake
    (see the comment on DESTRUCTIVE_TOOLS above). The breakdown is emitted
    as a warning (visible in the summary of a plain `pytest tests/` run, no
    flags needed) rather than only printed, so the gap stays visible instead
    of requiring `-s` to see it — a `print()` on a passing test is invisible
    under pytest's default capture, which is exactly how this coverage
    figure went dark the first time. Behavioural coverage for the skipped
    tools is the job of test_tools_shape.py and test_harness.py, not this
    test.
    """
    covered = []
    skipped_needs_args = []
    skipped_raised = []
    skipped_destructive = []

    for fname, node in _tools():
        if node.name in STRING_TOOLS:
            continue
        if node.name in DESTRUCTIVE_TOOLS:
            skipped_destructive.append(node.name)
            continue
        module = importlib.import_module(f"tools.{fname[:-3]}")
        func = getattr(module, node.name)
        sig = inspect.signature(func)
        callable_with_no_args = all(
            p.default is not inspect.Parameter.empty
            or p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for p in sig.parameters.values()
        )
        if not callable_with_no_args:
            skipped_needs_args.append(node.name)
            continue
        try:
            result = func()
        except Exception as exc:
            skipped_raised.append(f"{node.name} ({exc.__class__.__name__}: {exc})")
            continue
        covered.append((node.name, result))

    offenders = [
        f"{name} -> {type(result).__name__}"
        for name, result in covered if not isinstance(result, dict)
    ]
    assert not offenders, (
        "These tools passed both static checks but returned a non-dict at "
        "runtime — the exact hole this test exists to close:\n"
        + "\n".join(offenders)
    )

    total_candidates = sum(
        1 for _, node in _tools() if node.name not in STRING_TOOLS
    )
    assert (len(covered) + len(skipped_needs_args) + len(skipped_raised)
            + len(skipped_destructive)) == total_candidates
    # A regression to near-zero coverage (e.g. every tool starting to raise
    # against the fake) would make the offenders check above vacuously pass.
    assert len(covered) >= 15, (
        f"only {len(covered)} tools were actually exercised at runtime — "
        "too few for this test to mean anything"
    )

    # print() is invisible under pytest's default capture for a passing
    # test - only useful with -s. warnings.warn survives a plain `pytest
    # tests/`: pytest collects it and prints it in the warnings summary at
    # the end of the run, no flag required. That is the actual disclosure;
    # the print() below is a convenience on top of it, not a substitute.
    print(
        f"\nruntime dict-shape sweep: {len(covered)} covered, "
        f"{len(skipped_needs_args)} skipped (need arguments), "
        f"{len(skipped_raised)} skipped (raised against the fake), "
        f"{len(skipped_destructive)} skipped (destructive, never called)"
    )
    print("skipped, need arguments:", sorted(skipped_needs_args))
    print("skipped, raised:", sorted(skipped_raised))
    print("skipped, destructive:", sorted(skipped_destructive))

    warnings.warn(
        f"runtime shape check covered {len(covered)} of {total_candidates} "
        f"tools; {len(skipped_needs_args)} skipped (need arguments), "
        f"{len(skipped_raised)} skipped (raised): {sorted(skipped_raised)}; "
        f"{len(skipped_destructive)} skipped (destructive, never called): "
        f"{sorted(skipped_destructive)}",
        stacklevel=2,
    )


def _success_defaulted_with_literal_sites():
    """Every `<expr>.get("success", <literal>)` call anywhere in tools/*.py,
    for any literal default — True, False, or otherwise.

    AST-based, not a grep, for two reasons: it must not trip on the prose in
    tools/hacs.py's docstring that explains this very bug (a docstring is an
    ast.Constant string, never an ast.Call, so it is structurally invisible
    here — no filename allowlist needed); and it must still catch a rewrite
    with single quotes, `.get('success', True)`, which a text grep tuned to
    double quotes would miss but which parses to the identical ast.Constant
    value "success".

    True and False are both banned, not just True, even though only the
    True direction can turn a failure into a false positive. A literal
    False default is the areas.py bug (see the docstring below): it never
    lies about `success` itself, but it silently discards Home Assistant's
    actual error code and message in favour of a bare False, and it is
    routinely paired with an unrelated field elsewhere in the same dict
    that is NOT conditioned on that success check — the exact shape of
    {"disabled": true, "success": false}, an effect asserted unconditionally
    while success denies it happened. ws_error() is the one path that both
    reports the real failure and lets the rest of the function only build
    that "effect" dict once success is actually confirmed.
    """
    sites = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get"):
                continue
            if len(node.args) < 2:
                continue
            key, default = node.args[0], node.args[1]
            if not (isinstance(key, ast.Constant) and key.value == "success"):
                continue
            if isinstance(default, ast.Constant):
                sites.append(f"{path.name}:{node.lineno}")
    return sites


def test_no_ws_result_reads_success_with_a_literal_default():
    """`.get("success", True)` treats a dict with no "success" key at all as
    a success — but that is exactly the shape `_ws()` returns when the
    connection or the authentication fails: {"error": "Auth failed: ..."},
    no "success" key anywhere in it. Defaulting the missing key to True
    turns that transport failure into a false success instead of an error,
    which is how delete_user(), create_user() and 22 other call sites used
    to report success for a write that never reached Home Assistant.

    `.get("success", False)` does not make that particular mistake — but it
    is the areas.py bug this codebase's audit found: discarding HA's actual
    error code/message in favour of a bare False, usually alongside an
    "effect" field elsewhere in the same dict that is not itself
    conditioned on that check (`{"disabled": true, "success": r.get(
    "success", False)}` asserts `disabled: true` even when `success` comes
    back false). See _success_defaulted_with_literal_sites() for the full
    reasoning on why both directions are banned, not just the historically
    first one found.

    The fix is `if err := ws_error(result): return err` (tools/_base.py),
    which treats a missing "success" key as a failure to be reported, not
    as a default to fall back on, and surfaces HA's real error code/message
    instead of discarding it. This test is the net that keeps the fix from
    being undone by the next tool written by copying a neighbour.
    """
    offenders = _success_defaulted_with_literal_sites()
    assert not offenders, (
        "these sites read a WS result's \"success\" key with a literal "
        "default, which either turns a _ws() transport failure into a "
        "false success (a True default) or silently discards Home "
        "Assistant's actual error code/message (any default) — replace "
        "with `if err := ws_error(result): return err`:\n"
        + "\n".join(offenders)
    )
