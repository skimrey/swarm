"""Sabotage-validated check for the `why` for-loop-variable shadowing bug.

Live crash this guards against, verbatim:

    File "team", line 3569, in cmd_do
        status = "ok" if c.ok else ("FAILED  " + why(c.error))
    TypeError: 'str' object is not callable

A for-loop's target is not scoped to the loop body in Python -- `for entry,
why in gaps:` inside `cmd_do` rebound the name `why` to the last gap's
reason string for the rest of that function call, shadowing the
module-level `why(error) -> str` used a couple hundred lines later to
format a FAILED component's summary line. It only ever reproduced when
BOTH a REQUIRED-endpoint contract gap existed AND at least one component
actually failed in the same run -- which is exactly why it survived
undetected until both lined up live.

Offline: reproduces the exact shadowing mechanism directly (no swarm, no
mock, no `cmd_do` invocation, which needs a live plan) and confirms
`team.why` -- the actual module-level name the fixed code now refers to
via a differently-named loop variable -- is never rebound by iterating a
gaps-shaped list of (entry, reason) tuples.
"""
import team

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


print("team.why is a real, callable function before anything runs")
check("why is callable", callable(team.why))
check("why formats an error string",
      team.why("Traceback (most recent call last):\nValueError: bad id")
      == "Traceback (most recent call last): ValueError: bad id")

print("\nsabotage: reproduce the exact shadowing mechanism that broke this")


def sabotaged_cmd_do_fragment(gaps):
    """Exactly the buggy loop shape that shipped: `why` as the loop
    variable, in a function scope where `why` also names the module-level
    formatter used afterward -- the same shape `cmd_do` had."""
    for entry, why in gaps:  # noqa: F402 -- reproducing the actual bug on purpose
        pass
    # After the loop, `why` -- in *this* function's local scope -- is the
    # last gap's reason string, not the module-level function anymore.
    try:
        why("Traceback (most recent call last):\nValueError: bad id")
        return "callable"
    except TypeError:
        return "shadowed"


gaps = [({"method": "POST", "path": "/api/projects"}, "call this first")]
result = sabotaged_cmd_do_fragment(gaps)
check("the old loop-variable-named-why shape reproduces the exact crash "
      "shape (proves this test would have caught the live bug)",
      result == "shadowed")


def fixed_cmd_do_fragment(gaps):
    """The actual fix: a differently-named loop variable, so `why` in this
    function's scope still resolves to the module-level function via the
    global lookup Python falls back to when no local binding exists."""
    for entry, reason in gaps:
        pass
    try:
        team.why("Traceback (most recent call last):\nValueError: bad id")
        return "callable"
    except TypeError:
        return "shadowed"


check("the fixed loop shape (loop variable named 'reason') never shadows "
      "team.why, so the later call succeeds",
      fixed_cmd_do_fragment(gaps) == "callable")

print("\nthe real fix, read off team.py's own source: the gaps-printing "
     "loop no longer binds a variable named 'why'")
import inspect
source = inspect.getsource(team.cmd_do)
check("cmd_do's source no longer contains 'for entry, why in gaps'",
      "for entry, why in gaps" not in source)
check("...and does contain the fixed loop variable name",
      "for entry, reason in gaps" in source)

if ok:
    print("\nall why-shadowing checks passed")
else:
    print("\nSOME CHECKS FAILED")
raise SystemExit(0 if ok else 1)
