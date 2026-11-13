#!/usr/bin/env python3
"""Validate codegen.gate_interfaces: catch a call into a dependency's missing
method before a live crash does, not after.

Reproduces the live failure this was built for: a main.py calling
`client.create_applet_version(...)` where the already-written MavenClient in
api_client.py has no such method. `interface_gate` (team.py) already knows
how to name this from the syntax tree -- but only runs after every
component's own repair budget is exhausted, so the live run burned 6
attempts plus a full whole-loop restart (12 attempts total) hitting the same
runtime AttributeError before ever reaching it. `gate_interfaces` runs the
same check per attempt, before the costly import/smoke subprocesses, so the
very first attempt gets the precise repair instruction instead of a bare
traceback.

Offline: no pool, no LAN.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import ast

import codegen
import crossref

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- %s" % detail) if detail else ""))
        FAILURES.append(name)


def write(root, name, body):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return name


API_CLIENT = '''
class MavenClient:
    def __init__(self, api_base_url, api_key):
        self.api_base_url = api_base_url
        self.api_key = api_key

    def create_applet(self, slug, name):
        return {"id": "x"}

    def create_project(self, name, description, applet_version_id):
        return {"id": "y"}

    def create_task(self, project_id, name):
        return {"id": "z"}

    def import_units(self, task_id, units):
        return {}

    def get_ui_url(self, api_base_url, project_id):
        return api_base_url + "/projects/" + project_id
'''

# The live defect: calls a method the class above never defines.
MAIN_BROKEN = '''
from api_client import MavenClient

def main():
    client = MavenClient("http://x", "k")
    applet = client.create_applet("slug", "name")
    version = client.create_applet_version(applet["id"], "1.0", "url", "sha")
    project = client.create_project("p", "d", version["id"])

if __name__ == "__main__":
    main()
'''

# The fixed shape: calls only what the class actually defines.
MAIN_OK = '''
from api_client import MavenClient

def main():
    client = MavenClient("http://x", "k")
    applet = client.create_applet("slug", "name")
    project = client.create_project("p", "d", applet["id"])
    task = client.create_task(project["id"], "t")
    client.import_units(task["id"], [])

if __name__ == "__main__":
    main()
'''

root = tempfile.mkdtemp(prefix="interface_gate_")
try:
    print("gate_interfaces catches a call to a method the dependency lacks")
    write(root, "api_client.py", API_CLIENT)
    main_path = os.path.join(root, "main.py")
    write(root, "main.py", MAIN_BROKEN)

    failure = codegen.gate_interfaces(main_path, root, ["api_client.py"])
    check("a call to a nonexistent dependency method is caught",
          failure is not None, failure)
    check("...naming the class and the file that owns it",
          failure and "MavenClient" in failure and "api_client.py" in failure,
          failure)
    check("...naming the missing method",
          failure and "create_applet_version" in failure, failure)

    print("\ncorrect code is not flagged")
    write(root, "main.py", MAIN_OK)
    failure_ok = codegen.gate_interfaces(main_path, root, ["api_client.py"])
    check("a file that only calls real methods passes clean",
          failure_ok is None, failure_ok)

    print("\nscoped correctly: no dependency, no crash, no false positive")
    check("no dep_paths means no check at all",
          codegen.gate_interfaces(main_path, root, []) is None)
    check("a non-python out_path is never checked",
          codegen.gate_interfaces(os.path.join(root, "page.html"), root,
                                  ["api_client.py"]) is None)

    print("\na dependency that hasn't been written yet is not a false alarm")
    # `dep_paths` is only ever populated with already-completed components in
    # team.py, but the gate itself must not explode if handed a path that
    # does not exist on disk -- crossref's own Index already swallows that.
    write(root, "main.py", MAIN_BROKEN)
    failure_missing = codegen.gate_interfaces(main_path, root,
                                              ["not_written_yet.py"])
    check("a dependency that doesn't exist yet does not crash the gate, "
          "and finds nothing to compare against",
          failure_missing is None, failure_missing)

    print("\nsabotage: disabling the check must reproduce silence")
    # Simulate the old behaviour (no interface check at all) by calling with
    # dep_paths=None, which is exactly what every call site did before this
    # gate existed.
    write(root, "main.py", MAIN_BROKEN)
    old_behaviour = codegen.gate_interfaces(main_path, root, None)
    check("with the gate disabled (dep_paths=None), the same broken file is "
          "silently accepted -- confirming the check above is what catches it",
          old_behaviour is None, old_behaviour)
finally:
    shutil.rmtree(root, ignore_errors=True)

print("\nassigning a new attribute is not the same claim as calling a "
      "missing one")

# Measured live: `client.api_jwt_token = api_jwt_token` -- a plain, legal
# attribute assignment -- was flagged as "calls client.api_jwt_token, but
# Client has no such attribute", and the repair loop, reading that as an
# instruction, subclassed the client and added a method just to satisfy a
# finding that was never a real defect. Assigning a new attribute onto an
# instance is legal Python regardless of what the class pre-declared; only
# reading one that was never assigned is a defect.
MAIN_ASSIGNS = '''
from api_client import MavenClient

def main():
    client = MavenClient("http://x", "k")
    client.api_jwt_token = "some-token"
    applet = client.create_applet("slug", "name")

if __name__ == "__main__":
    main()
'''
root2 = tempfile.mkdtemp(prefix="interface_gate_assign_")
try:
    write(root2, "api_client.py", API_CLIENT)
    main_path2 = write(root2, "main.py", MAIN_ASSIGNS)
    failure = codegen.gate_interfaces(os.path.join(root2, main_path2), root2,
                                      ["api_client.py"])
    check("assigning a new attribute is not flagged as a missing method",
          failure is None, failure)

    # Sabotage: without the ctx==Load restriction, the assignment above is
    # exactly what used to be (wrongly) flagged. Reproduce the old check
    # inline to prove the fix in crossref.check_attributes is what matters,
    # not some other difference between this test and the live failure.
    index = crossref.Index(["main.py", "api_client.py"], root2)
    findings_without_ctx_filter = []
    for module in index.modules.values():
        for scope in crossref._scopes(module.tree):
            bindings = {}
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    cls = crossref._called_name(node.value.func)
                    owner = index.owner_of_class(cls) if cls else None
                    if owner is None:
                        continue
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings[target.id] = (cls, owner)
            for node in ast.walk(scope):
                if not isinstance(node, ast.Attribute):   # no ctx filter
                    continue
                key = crossref._binding_key(node.value)
                if key is None or key not in bindings:
                    continue
                cls, owner = bindings[key]
                surface = owner.methods_of(cls, index)
                if surface is None:
                    continue
                if node.attr not in surface:
                    findings_without_ctx_filter.append((module.path, node.attr))
    check("...and the sabotaged (ctx-blind) version reproduces the exact "
          "false positive from the live run, confirming the ctx==Load "
          "restriction is what fixes it",
          ("main.py", "api_jwt_token") in findings_without_ctx_filter,
          findings_without_ctx_filter)
finally:
    shutil.rmtree(root2, ignore_errors=True)

print()
if FAILURES:
    print("%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
    raise SystemExit(1)
print("all checks passed")
