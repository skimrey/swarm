#!/usr/bin/env python3
"""Sabotage-validated check for `intent.low_parallelism_note`.

Live cost this exists to flag, before it is paid: a single-component step
whose request reads a sibling step's output.json -- one link in a strictly
serial chain -- burned 8m15s to fail six attempts, a full plan restart, and
5m20s more to "pass," on a file that calls one REST endpoint. Nothing about
this shape can gain from the pool no matter how the plan is written, and it
is decidable off the finished plan and the request text with no model asked.

Offline. No node, no swarm state.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import intent

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- " + str(detail)) if detail else ""))
        FAILURES.append(name)


def component(path, name=None):
    return intent.Component(name=name or path, path=path, floor=2, deps=[],
                            spec="")


print("fires: one component, request reads a sibling step's output")
req = ("write client.py and main.py exposing create a task in the project "
       "whose id is read from ../01_create_project/output.json; save its "
       "id to output.json")
note = intent.low_parallelism_note(req, [component("main.py")])
check("the note fires", note == "main.py", note)

print("\nsilent: more than one component (real parallelism exists)")
note2 = intent.low_parallelism_note(
    req, [component("client.py"), component("main.py")])
check("no note when there is more than one component", note2 is None, note2)

print("\nsilent: single component, but no sibling-step reference at all")
plain_req = "write client.py and main.py exposing create a project"
note3 = intent.low_parallelism_note(plain_req, [component("main.py")])
check("no note for a self-contained single-component request",
      note3 is None, note3)

print("\nsilent: no components at all (planner failure, unrelated case)")
note4 = intent.low_parallelism_note(req, [])
check("no note when there is nothing to point at", note4 is None, note4)

# Sabotage: prove the detector actually depends on both conditions jointly,
# not just component count -- a two-component plan referencing a sibling
# step must not fire either, since that IS a case with real parallelism.
print("\nsabotage: sibling reference alone is not enough without the count check")
note5 = intent.low_parallelism_note(
    req, [component("client.py"), component("main.py")])
check("a chained request with 2 components still does not fire",
      note5 is None, note5)

if FAILURES:
    print("\n%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("\nall low_parallelism_note checks passed")
