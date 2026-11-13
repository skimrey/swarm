"""Validates the fix for the live RecursionError in intent.critical_depth.

Two COMPONENT blocks sharing the same `component:` name label ("main" for
both a CLI wrapper and the real entry point) collapsed onto one dict entry
in every name-keyed structure downstream, so a DEPS token naming the shared
label read as a literal self-loop and `critical_depth.compute` recursed
until the interpreter's stack gave out -- verbatim as observed live:

    main   update_applet_cli.py   floor 2  after update_applet_service, api_client
    main   main.py                floor 2  after update_applet_service, api_client, applet_models, main

Two independent fixes are checked here:
  1. `parse_components` renames every duplicate `component:` name after the
     first, so this can no longer happen from parsing a raw plan.
  2. `critical_depth.compute` no longer recurses unboundedly even if a
     cycle reaches it anyway -- checked directly against a hand-built
     Component list that reproduces the exact self-loop, bypassing
     `parse_components` entirely so the two fixes are proven independently.
"""
import sys

import intent


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("ok:", msg)


# --- 1. parse_components renames a duplicate `component:` label ---------

PLAN_TEXT = """\
CONTRACT: api_client.py owns Client(...) with update_applet(...)

COMPONENT: update_applet_service
FILE: update_applet.py
FLOOR: 4
DEPS: NONE
SPEC: implement the update logic

COMPONENT: api_client
FILE: api_client.py
FLOOR: 2
DEPS: NONE
SPEC: the API client

COMPONENT: applet_models
FILE: applet_models.py
FLOOR: 2
DEPS: NONE
SPEC: shared dataclasses

COMPONENT: main
FILE: update_applet_cli.py
FLOOR: 2
DEPS: update_applet_service, api_client
SPEC: a CLI wrapper

COMPONENT: main
FILE: main.py
FLOOR: 2
DEPS: update_applet_service, api_client, applet_models, main
SPEC: the real entry point
"""

components = intent.parse_components(PLAN_TEXT)
names = [c.name for c in components]
check(len(names) == len(set(names)),
      "parse_components gives every component a unique name (%r)" % names)

cli = next(c for c in components if c.path == "update_applet_cli.py")
entry = next(c for c in components if c.path == "main.py")
check(cli.name != entry.name,
      "the two originally-'main' components no longer share a name")
check(cli.name == "main",
      "the first occurrence keeps the original name (got %r)" % cli.name)
check(entry.name != "main" and entry.name != entry.path,
      "the second occurrence was renamed, not left colliding (got %r)"
      % entry.name)
check(entry.name not in entry.deps,
      "the renamed entry component no longer lists itself as a dependency "
      "(deps=%r, name=%r)" % (entry.deps, entry.name))

# This is the actual regression check: computing scheduling priority over
# this exact plan must terminate at all, let alone quickly.
depth = intent.critical_depth(components)
check(set(depth) == set(names),
      "critical_depth returns a depth for every component, no crash")


# --- 2. critical_depth survives a cycle even if one reaches it anyway ---

class FakeComponent:
    def __init__(self, name, deps):
        self.name = name
        self.deps = deps


# Hand-built, bypassing parse_components entirely: a direct self-loop, the
# same shape the live bug produced (a component naming itself as its own
# dependency) fed straight to critical_depth.
selfloop = [FakeComponent("main", ["main", "api_client"]),
            FakeComponent("api_client", [])]
depth2 = intent.critical_depth(selfloop)  # must return, not recurse forever
check(depth2.get("main") is not None and depth2.get("api_client") is not None,
      "critical_depth terminates on a direct self-loop instead of "
      "recursing until RecursionError (got %r)" % depth2)

# A longer cycle: a -> b -> c -> a.
cyclic = [FakeComponent("a", ["b"]),
          FakeComponent("b", ["c"]),
          FakeComponent("c", ["a"])]
depth3 = intent.critical_depth(cyclic)
check(set(depth3) == {"a", "b", "c"},
      "critical_depth terminates on a 3-node cycle (got %r)" % depth3)


# --- sabotage: without the dedup fix, the exact live plan recurses forever

class NoDedupComponent:
    """Mirrors what `out` looked like before the fix: two objects that
    share a `.name`, exactly as two raw COMPONENT blocks with the same
    label used to produce."""
    def __init__(self, name, path, deps):
        self.name = name
        self.path = path
        self.deps = deps


def old_critical_depth(components):
    """The pre-fix body of critical_depth, reproduced verbatim (no
    dedup upstream, no visiting-set guard), to prove this test would have
    caught the live bug rather than passing by accident."""
    dependents = {c.name: [] for c in components}
    for c in components:
        for d in c.deps:
            if d in dependents:
                dependents[d].append(c.name)
    depth = {}

    def compute(name):
        if name in depth:
            return depth[name]
        depth[name] = 1 + max([compute(n) for n in dependents[name]] or [0])
        return depth[name]

    for c in components:
        compute(c.name)
    return depth


dupe_plan = [
    NoDedupComponent("update_applet_service", "update_applet.py", []),
    NoDedupComponent("api_client", "api_client.py", []),
    NoDedupComponent("applet_models", "applet_models.py", []),
    NoDedupComponent("main", "update_applet_cli.py",
                      ["update_applet_service", "api_client"]),
    NoDedupComponent("main", "main.py",
                      ["update_applet_service", "api_client",
                       "applet_models", "main"]),
]
old_limit = sys.getrecursionlimit()
sys.setrecursionlimit(200)
try:
    old_critical_depth(dupe_plan)
    print("FAIL: sabotage -- old_critical_depth should have recursed "
          "forever on the duplicate-name plan")
    sys.exit(1)
except RecursionError:
    print("ok: sabotage -- the unpatched logic really does crash on the "
          "exact live plan shape (proves this test is not vacuous)")
finally:
    sys.setrecursionlimit(old_limit)

print("all checks passed")
