#!/usr/bin/env python3
"""Sabotage-validated check for `codegen.seed_parent_refs`.

Live bug this guards against: a chained step's `main.py` reads
`../01_create_project/output.json` (exactly what `LOCAL.md` tells every
multi-step request to do), the smoke sandbox never mirrored a real sibling
directory, the `open()` raised, the file's own error handling caught it and
exited 0, and the gate recorded a clean pass on a component that sent zero
real traffic to the mock.

Offline. No node, no mock server -- this only exercises the filesystem
mirroring `gate_smoke`'s `bare()` helper relies on.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import codegen

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- " + str(detail)) if detail else ""))
        FAILURES.append(name)


print("referenced_parent_paths finds the literal a generated file would open")

SOURCE = '''
import json
with open("../01_create_project/output.json") as f:
    data = json.load(f)
print(data["project_id"])
'''
refs = codegen.referenced_parent_paths(SOURCE)
check("the ../sibling/output.json literal is found",
      refs == ["../01_create_project/output.json"], refs)

check("a source with no such reference finds nothing",
      codegen.referenced_parent_paths("print('hello')") == [])

# --------------------------------------------------------------------------
print("\nseed_parent_refs mirrors the real file at the matching relative path")

root = tempfile.mkdtemp(prefix="parent-ref-check-")
try:
    real_project = os.path.join(root, "01_create_project")
    os.makedirs(real_project)
    with open(os.path.join(real_project, "output.json"), "w") as f:
        json.dump({"project_id": "real-id-123"}, f)

    real_task_dir = os.path.join(root, "02_create_task")
    os.makedirs(real_task_dir)

    # The scratch sandbox `gate_smoke.bare()` actually runs in: named after
    # the real directory, one level under some temp root, exactly the shape
    # `bare()` now constructs.
    temp_root = tempfile.mkdtemp(prefix="smoke-sim-")
    scratch = os.path.join(temp_root, "02_create_task")
    os.makedirs(scratch)

    copied = codegen.seed_parent_refs(SOURCE, real_task_dir, scratch)
    check("seed_parent_refs reports the file it copied",
          copied == ["../01_create_project/output.json"], copied)

    mirrored = os.path.normpath(os.path.join(scratch, "..",
                                              "01_create_project", "output.json"))
    check("the file actually landed where '..' from scratch resolves to it",
          os.path.isfile(mirrored), mirrored)
    with open(mirrored) as f:
        check("...with the real content, not a stub",
              json.load(f) == {"project_id": "real-id-123"})

    # Sabotage: a reference to a file that does not exist on disk must be
    # skipped silently, never invented or crashed on.
    unrelated_root = tempfile.mkdtemp(prefix="unrelated-")
    unrelated_cwd = os.path.join(unrelated_root, "no_such_step")
    os.makedirs(unrelated_cwd)
    missing_scratch = os.path.join(tempfile.mkdtemp(prefix="smoke-sim2-"),
                                    "no_such_step")
    os.makedirs(missing_scratch)
    copied_missing = codegen.seed_parent_refs(
        SOURCE, unrelated_cwd, missing_scratch)
    check("a reference with no real file on disk copies nothing and does not raise",
          copied_missing == [], copied_missing)

    # Sabotage in the other direction: without this fix, the old scratch
    # directory was random-named (`smoke-<random>`), so `..` from inside it
    # never pointed at anything containing `01_create_project` no matter
    # what got copied where -- reproduce that exact miss to prove this test
    # would have caught the live bug.
    old_style_root = tempfile.mkdtemp(prefix="smoke-")  # random name, old shape
    old_mirrored = os.path.normpath(os.path.join(old_style_root, "..",
                                                  "01_create_project", "output.json"))
    check("sabotage: the old random-named scratch dir could never have "
          "resolved '..' to the real sibling -- proving the basename-match "
          "fix is load-bearing, not incidental",
          not os.path.isfile(old_mirrored), old_mirrored)
finally:
    shutil.rmtree(root, ignore_errors=True)

if FAILURES:
    print("\n%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("\nall seed_parent_refs checks passed")
