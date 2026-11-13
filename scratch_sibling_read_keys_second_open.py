#!/usr/bin/env python3
"""`codegen._sibling_output_read_keys`: the "trust the one read-mode open()
in the whole file" fallback is defeated the instant a file makes a SECOND,
unrelated read-mode open() call (e.g. reopening its own just-written dummy
CSV in "rb" mode for a multipart upload) -- even when the FIRST open's
literal path is an unambiguous exact match for the sibling ref being
searched for. The fix adds a dedicated exact-literal-match pass that takes
priority over that fallback, and a companion fix so the
`parse_calls > 1 and not single_open` guard does not discard a result that
was actually derived from an exact match.

SAFETY NOTE, read before touching anything in this file: every temp
directory this test creates comes from `tempfile.mkdtemp()`, never from a
hand-built path under this project's own directory, and every cleanup call
asserts the path it is about to remove is NOT this file's own directory (or
any ancestor of it) before calling `shutil.rmtree`. This is deliberate,
following a real incident where a hand-built `os.path.dirname(scratch_dir)`
cleanup path collapsed back to the project root and deleted nearly the
entire repository. Do not "simplify" this back to a hardcoded subdirectory
name under `os.path.dirname(__file__)`.

Run directly: `python3 scratch_sibling_read_keys_second_open.py`.
"""
import os
import shutil
import sys
import tempfile

import codegen

HERE = os.path.dirname(os.path.abspath(__file__))

passed = 0


def ok(msg):
    global passed
    passed += 1
    print("ok:", msg)


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def safe_rmtree(path):
    """Never removes this project's own directory or anything above it."""
    real = os.path.realpath(path)
    real_here = os.path.realpath(HERE)
    assert real != real_here, "refusing to remove the project directory itself"
    assert real_here.startswith(real + os.sep) is False or real == real_here, \
        "unexpected path relationship"
    assert not real_here == real, "refusing to remove the project directory"
    # The temp dir must not be an ancestor of HERE, and HERE must not be an
    # ancestor of the temp dir in a way that would delete unrelated project
    # files -- the only safe relationship is "no relationship at all",
    # which is what tempfile.mkdtemp() (outside the project) guarantees.
    assert not real.startswith(real_here + os.sep), \
        "refusing to remove a path inside the project directory"
    shutil.rmtree(real, ignore_errors=True)


# ---------------------------------------------------------------------
# The exact live shape: a file that opens the real sibling ref ONCE, in
# "r" mode, reads task_id out of it -- and then opens a second, entirely
# unrelated file (its own just-written dummy CSV) in "rb" mode for a
# multipart upload. The single-open fallback sees TWO read opens and
# gives up entirely; the exact-match pass must still find task_id.
# ---------------------------------------------------------------------
LIVE_SHAPE = '''
import csv
import json
import requests

DUMMY_FILE_PATH = "dummy_units.csv"


def main():
    with open("../02_create_task/output.json", "r") as f:
        parent = json.load(f)
    task_id = parent["task_id"]

    with open(DUMMY_FILE_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "value"])
        writer.writerow(["placeholder", "1"])

    with open(DUMMY_FILE_PATH, "rb") as f:
        resp = requests.post(
            "http://api/tasks/%s/units/import-csv" % task_id,
            files={"file": (DUMMY_FILE_PATH, f, "text/csv")},
            headers=HEADERS,
        )
    resp.raise_for_status()


if __name__ == "__main__":
    main()
'''

keys = codegen._sibling_output_read_keys(
    LIVE_SHAPE, "../02_create_task/output.json")
check(keys == {"task_id"},
      "the exact-match pass must recover 'task_id' despite the second, "
      "unrelated read-mode open; got %r" % keys)
ok("live shape: exact-literal-match recovers the key despite a second, "
   "unrelated read-mode open()")

# ---------------------------------------------------------------------
# A file that reads its sibling ambiguously (no exact match, and more
# than one candidate open with no way to disambiguate) still correctly
# returns nothing -- this project's "no evidence, no claim" rule.
# ---------------------------------------------------------------------
AMBIGUOUS_SHAPE = '''
import json

PATH_A = "../01_create_project/output.json"
PATH_B = "../02_create_task/output.json"


def main():
    with open(PATH_A, "r") as fa:
        a = json.load(fa)
    with open(PATH_B, "r") as fb:
        b = json.load(fb)
    print(a, b)
'''

keys = codegen._sibling_output_read_keys(
    AMBIGUOUS_SHAPE, "../01_create_project/output.json")
check(keys == set(),
      "two genuinely ambiguous reads via variables (no exact literal "
      "match against either open call) must return nothing; got %r" % keys)
ok("an ambiguous multi-file read via variables still returns nothing "
   "(no false positive)")

# ---------------------------------------------------------------------
# Two DIFFERENT sibling files, each opened by exact literal path, each
# resolve to their own keys independently -- the parse_calls > 1 guard
# must not discard results that came from an exact match.
# ---------------------------------------------------------------------
TWO_SIBLING_READS_SHAPE = '''
import json


def main():
    with open("../01_create_project/output.json", "r") as f1:
        project = json.load(f1)
    with open("../02_create_task/output.json", "r") as f2:
        task = json.load(f2)
    print(project["project_id"], task["task_id"])
'''

keys_a = codegen._sibling_output_read_keys(
    TWO_SIBLING_READS_SHAPE, "../01_create_project/output.json")
check(keys_a == {"project_id"},
      "exact match for the first sibling file must survive a second, "
      "different sibling read elsewhere in the file; got %r" % keys_a)
ok("two different sibling reads: the first resolves via exact match "
   "independently of the second")

keys_b = codegen._sibling_output_read_keys(
    TWO_SIBLING_READS_SHAPE, "../02_create_task/output.json")
check(keys_b == {"task_id"},
      "exact match for the second sibling file must survive a second, "
      "different sibling read elsewhere in the file; got %r" % keys_b)
ok("two different sibling reads: the second resolves via exact match "
   "independently of the first")

# ---------------------------------------------------------------------
# End-to-end: codegen._seed_placeholder_sibling_output on the exact live
# shape actually seeds a usable placeholder output.json.
# ---------------------------------------------------------------------
scratch_root = tempfile.mkdtemp(prefix="swarm_scratch_sibling_test_")
try:
    seeded_ref = codegen._seed_placeholder_sibling_output(
        LIVE_SHAPE, "../02_create_task/output.json", scratch=scratch_root)
    check(seeded_ref is not None, "must actually seed a placeholder file")
    if seeded_ref:
        seeded_path = os.path.normpath(os.path.join(scratch_root, seeded_ref))
        check(os.path.exists(seeded_path),
              "the seeded file must exist on disk at %r" % seeded_path)
        import json as _json
        with open(seeded_path) as fh:
            seeded_data = _json.load(fh)
        check("task_id" in seeded_data,
              "the seeded placeholder must carry a 'task_id' key, got %r"
              % seeded_data)
    ok("end-to-end: _seed_placeholder_sibling_output seeds a usable "
       "placeholder for the exact live shape")
finally:
    safe_rmtree(scratch_root)

# ---------------------------------------------------------------------
# Sabotage: reproduce the pre-fix function inline (no exact-match pass,
# and the unguarded parse_calls check) and confirm it fails on the live
# shape the real fix now resolves.
# ---------------------------------------------------------------------
import ast


def pre_fix_sibling_output_read_keys(source, ref):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    def _is_write_open(call):
        mode = None
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            mode = call.args[1].value
        for kw in call.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        return isinstance(mode, str) and any(c in mode for c in "wax")

    def _is_env_loader_open(call):
        return bool(call.args) and isinstance(call.args[0], ast.Constant) \
            and isinstance(call.args[0].value, str) \
            and call.args[0].value.endswith(".env")

    read_opens = 0
    file_vars = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open" and not _is_write_open(node) \
                and not _is_env_loader_open(node):
            read_opens += 1

    single_open = read_opens == 1
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem):
            call = node.context_expr
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                    and call.func.id == "open" and not _is_write_open(call) \
                    and not _is_env_loader_open(call) \
                    and single_open and isinstance(node.optional_vars, ast.Name):
                file_vars.add(node.optional_vars.id)

    dict_vars = set()
    parse_calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("load", "loads")
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "json"):
            continue
        parse_calls += 1
        if not call.args:
            dict_vars.add(node.targets[0].id)
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Name) and (arg.id in file_vars or single_open):
            dict_vars.add(node.targets[0].id)
        elif single_open:
            dict_vars.add(node.targets[0].id)

    if not dict_vars or parse_calls > 1 and not single_open:
        return set()

    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in dict_vars and isinstance(node.slice, ast.Constant):
            keys.add(node.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in dict_vars and node.args \
                and isinstance(node.args[0], ast.Constant):
            keys.add(node.args[0].value)
    return keys


sabotage_keys = pre_fix_sibling_output_read_keys(
    LIVE_SHAPE, "../02_create_task/output.json")
check(sabotage_keys == set(),
      "sabotage check: the pre-fix function must return nothing on the "
      "live shape (defeated by the second, unrelated read-mode open); "
      "got %r -- if this is non-empty, the fixture no longer exercises "
      "the real bug" % sabotage_keys)
ok("sabotage: the pre-fix function reproduces the exact live failure "
   "(returns nothing) on the shape the real fix now resolves")

print()
print("%d/%d checks passed" % (passed, passed))
