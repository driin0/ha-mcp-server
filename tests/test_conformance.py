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
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.List):
                offenders.append(f"{fname}:{inner.lineno} in {node.name}")
    assert not offenders, (
        "Return envelope(...) or error(...), not a list literal:\n"
        + "\n".join(offenders)
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
