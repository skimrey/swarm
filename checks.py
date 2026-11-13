"""Per-component tests, so a repair loop can say what was actually wrong.

The gates a single generated file used to face were parse and import. Both are
necessary and neither is informative: a wrong algorithm parses and imports
perfectly well, so the writer got told "try again" three times without ever
being told what its code did incorrectly. On the slow box that is half an hour
spent guessing.

The model is not asked to write a test file. It is asked for assertions only,
and they are dropped into a harness written here. Two reasons. Loading a module
out of a package whose __init__ does not exist yet is fiddly and deterministic,
so there is nothing to gain by having a model attempt it. More importantly, a
model that writes both the implementation and the scaffolding around it will
write scaffolding that shares its misconceptions, and a test that agrees with
the bug is worse than no test -- it converts a caught failure into a confident
pass.
"""
import os
import re
from typing import Dict, List, Optional, Tuple

import codegen
import intent
import swarm

TESTS_DIR = os.path.join(".swarm", "tests")

TEST_PROMPT = """Write assertions that decide whether an implementation of the
following specification is correct.

SPECIFICATION
{spec}

The module under test is already imported and bound to the name `mod`. Refer to
everything through it, for example `mod.some_function(...)`.

No imports and no `if __name__` block. You MAY define small helper functions
when a property needs one -- computing an expected quantity independently is
encouraged. Use exactly this call for every assertion:

    check("short description", <expression that must be True>)

To assert that something raises, use:

    check_raises("short description", ValueError, lambda: mod.thing(bad_input))

Assert PROPERTIES that must hold for any correct implementation. Do not write
out expected return values you worked out in your head for anything beyond a
trivial case: if you get one wrong, correct code gets rejected forever and the
error is yours, not the implementation's. Compute the expected side in a helper
instead, from the definition given in the specification.

Do NOT compare a whole return value against a literal you wrote out, except
when the input is empty. Two dangers, both observed. Transcribing a literal is
error-prone, and a typo in the test is indistinguishable from a bug in the
code. Worse, many specifications admit several equally correct answers -- if
two operations may legally occur in either order, demanding one particular
order rejects a correct implementation for making the other choice. Assert what
must be true of the result instead: its length, its relationship to the input,
the fact that reversing or applying it reproduces what it came from.

Cover the ordinary case, the empty or zero case, and every error the
specification says must be raised.

Reply with one fenced python block and nothing else.
"""

_HEAD = '''"""Generated invariant checks for %(path)s. Not hand-written."""
import importlib.util
import os
import sys

MODULE_PATH = %(module)r

_failures = []
_ran = [0]


def _load():
    if not os.path.exists(MODULE_PATH):
        print("missing module: " + MODULE_PATH)
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


def check(name, cond):
    _ran[0] += 1
    if not cond:
        _failures.append(name)


def check_raises(name, exc, fn):
    _ran[0] += 1
    try:
        fn()
    except exc:
        return
    except Exception as other:
        _failures.append("%%s: raised %%s instead of %%s"
                         %% (name, type(other).__name__, exc.__name__))
        return
    _failures.append("%%s: nothing raised" %% name)


'''

_TAIL = '''

if _failures:
    for _f in _failures:
        print("FAIL: " + _f)
    sys.exit(1)
if not _ran[0]:
    print("no assertions ran")
    sys.exit(1)
print("ok: %d invariant(s) held" % _ran[0])
'''


def _strip_scaffolding(body: str) -> str:
    """Drop anything that is not a statement we asked for.

    Models add `import` lines and `if __name__` blocks by reflex. The imports
    are harmless but the guard silently disables every assertion under it,
    which would turn the whole test into a no-op that always passes.
    """
    out: List[str] = []
    skipping = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("if __name__"):
            skipping = True
            continue
        if skipping:
            if line and not line[0].isspace():
                skipping = False
            else:
                out.append(line[4:] if line.startswith("    ") else line)
                continue
        if re.match(r"^\s*(import|from)\s+\S", line):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _invoke_uncalled_tests(body: str) -> str:
    """Call `def test_*()` blocks the model defined but never invoked.

    Asked for bare statements, a capable model still reaches for the shape it
    has seen a million times and wraps everything in a test function. The
    assertions inside are usually fine; nothing runs them. Detected as a fact
    from the syntax tree rather than guessed, and only zero-argument functions
    named like tests are called, so this cannot invent behaviour.
    """
    import ast

    try:
        tree = ast.parse(body)
    except SyntaxError:
        return body

    defined = []
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef)
                and node.name.startswith("test")
                and not node.args.args and not node.args.kwonlyargs):
            defined.append(node.name)
    if not defined:
        return body

    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    pending = [name for name in defined if name not in called]
    if not pending:
        return body
    return body + "\n\n" + "\n".join("%s()" % name for name in pending) + "\n"


def build_test(component: intent.Component, node: swarm.Node, model: str,
               cfg: swarm.Config, state: swarm.State, root: str,
               answer_tokens: int = 1600) -> Optional[str]:
    """Write the invariant test for one component; return its path, or None."""
    prompt = TEST_PROMPT.format(spec=component.spec)
    ctx = swarm.pick_num_ctx(cfg, node, model, swarm.est_tokens(prompt),
                             answer_tokens, reasoning=False)
    try:
        with swarm.node_claim(state, node, model):
            r = swarm.chat_complete(
                node, model, [{"role": "user", "content": prompt}],
                num_ctx=ctx, answer_tokens=answer_tokens, headroom=1.8,
                think=False, temperature=0.1, timeout=420)
        state.record_perf(node.name, model, r["sample"])
        state.mark_resident(node.name, model)
    except swarm.OllamaError:
        return None

    body = _invoke_uncalled_tests(
        _strip_scaffolding(codegen.extract_code(r["text"] or "")))
    if not body or "check(" not in body and "check_raises(" not in body:
        return None

    source = (_HEAD % {"path": component.path, "module": component.path}
              + body + _TAIL)
    # A test that does not compile is not a test. Better to fall back to the
    # plain gates than to fail every attempt against a broken oracle.
    if codegen.gate_syntax(source, "test.py") is not None:
        return None

    directory = os.path.join(root, TESTS_DIR)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(TESTS_DIR, "%s_check.py" % re.sub(r"\W+", "_",
                                                          component.name))
    with open(os.path.join(root, path), "w", encoding="utf-8") as fh:
        fh.write(source)
    return path


def joins(components: List[intent.Component]) -> set:
    """Components that only assemble other components, which get no oracle.

    A re-export module has no behaviour of its own to assert. Asking a model to
    invent invariants for one gets invariants for the things being exported,
    written by a model that has not seen them and is guessing at their
    signatures from a package-level description. Observed: a generated oracle
    for a `textkit/__init__.py` called `wrap(text)` expecting a string back,
    when `wrap(text, width)` returns a list, and called the two-argument
    `edit_script` with one argument. Nothing could satisfy it, so the writer
    rewrote a correct file until its repair budget ran out -- nine minutes on
    the slowest box, against a gate that could not be passed.

    The join is exactly what the integration check already covers, so it is
    left to that. Defined structurally: it depends on others and nothing
    depends on it.
    """
    depended_on = {d for c in components for d in c.deps}
    return {c.name for c in components
            if len(c.deps) >= 2 and c.name not in depended_on}


def build_tests(components: List[intent.Component], pool: List[Tuple],
                cfg: swarm.Config, state: swarm.State, root: str,
                verbose: bool = True) -> Dict[str, str]:
    """Write every component's test up front, spread across the pool.

    Hardest first: the expensive component is the one that gains most from
    being told what it got wrong, and it is the one whose writer we least want
    waiting on a test that has not been produced yet.
    """
    import threading

    if not pool:
        return {}
    skip = joins(components)
    ordered = sorted([c for c in components if c.name not in skip],
                     key=lambda c: -c.floor)
    tests: Dict[str, str] = {}
    lock = threading.Lock()
    queue = list(ordered)

    def worker(node: swarm.Node, model: str) -> None:
        while True:
            with lock:
                if not queue:
                    return
                component = queue.pop(0)
            path = build_test(component, node, model, cfg, state, root)
            if path:
                with lock:
                    tests[component.name] = path
            if verbose:
                print("  test for %-18s %s"
                      % (component.name[:18],
                         path if path else "unavailable, using parse+import"))

    threads = [threading.Thread(target=worker, args=(n, m), daemon=True)
               for n, m in pool]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return tests
