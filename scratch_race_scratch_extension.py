"""Validates the fix for a live bug: every race/hedge candidate in
team.py wrote to a scratch path built as `<out_path>.race0.tmp` /
`<real_path>.hedge0.tmp`, which strips the real `.py` extension. Every
Python-only gate in codegen.generate_file's per-attempt chain --
gate_fabricated_client, gate_output_json_key, gate_placeholder_id_literal,
gate_required_cli_args, and several more -- is gated on
`codegen.is_python(out_path)`, i.e. `out_path.endswith(".py")`, so all of
them were silently disabled for any component that ever raced or hedged.

Measured live: a `create_task` step won a hedge race and shipped a
hand-written `MockClient` whose `create_task` method never called
`requests` at all -- exactly the shape `gate_fabricated_client` exists to
catch, and does catch when run against the same file's source directly.

Fix: `team._race_scratch_path` now builds the scratch name by splitting
the extension off first and re-appending it after a marker segment
(`intent.RACE_SCRATCH_MARKER`), so `main.py` becomes
`main.__swarmrace_hedge0__.py` -- still ends in `.py`, so every gate
still runs -- rather than `main.py.hedge0.tmp`. The marker keeps a
straggler left behind by a losing candidate identifiable as scratch, so
`team.py`'s and `session.py`'s own directory scans for real, existing
project content (used to seed a later request's "edit this file in
place" context) do not read it back as something the operator wrote.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import codegen
import intent
import session
import team

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + name)
    else:
        FAIL += 1
        print("FAIL " + name + (": " + str(detail) if detail else ""))


FABRICATED_CLIENT_CODE = '''\
import os
import requests


class MockClient:
    def create_task(self, project_id, name):
        print(f"MOCK: Calling POST .../projects/{project_id}/tasks")
        return {"id": "task-fake-123"}


def main():
    client = MockClient()
    resp = client.create_task("p1", "n1")
    print(resp["id"])


if __name__ == "__main__":
    main()
'''

print("_race_scratch_path preserves the real extension, for both race "
      "and hedge labels and any index")
for label, i in (("race", 0), ("race", 3), ("hedge", 0), ("hedge", 2)):
    scratch = team._race_scratch_path("/proj/main.py", label, i)
    check("%s%d ends in .py" % (label, i), scratch.endswith(".py"), scratch)
    check("%s%d is recognised as python by codegen.is_python" % (label, i),
          codegen.is_python(scratch), scratch)

print("\nintent.is_race_scratch_name identifies scratch names and only "
      "scratch names")
scratch_name = os.path.basename(team._race_scratch_path("main.py", "hedge", 1))
check("a real scratch name is flagged",
      intent.is_race_scratch_name(scratch_name), scratch_name)
for ordinary in ("main.py", "client.py", "output.json", "survey.html"):
    check("an ordinary file %r is not flagged" % ordinary,
          not intent.is_race_scratch_name(ordinary), ordinary)

print("\nthe live bug: gate_fabricated_client is only ever consulted "
      "when codegen.is_python(out_path) is true -- this reproduces the "
      "exact per-attempt condition and shows the old naming disabled it")
old_style_scratch = "/proj/main.py" + ".hedge0.tmp"     # the pre-fix shape
new_style_scratch = team._race_scratch_path("/proj/main.py", "hedge", 0)

old_python = codegen.is_python(old_style_scratch)
new_python = codegen.is_python(new_style_scratch)
check("sabotage: the pre-fix scratch name is NOT recognised as python "
      "(so the gate below would never even be called on it)",
      old_python is False, old_style_scratch)
check("the fixed scratch name IS recognised as python",
      new_python is True, new_style_scratch)

# Reproduce generate_file's own gating logic verbatim for both shapes.
old_failure = None
if old_failure is None and old_python:
    old_failure = codegen.gate_fabricated_client(FABRICATED_CLIENT_CODE)
new_failure = None
if new_failure is None and new_python:
    new_failure = codegen.gate_fabricated_client(FABRICATED_CLIENT_CODE)

check("sabotage: under the pre-fix naming, the fabricated client is "
      "NEVER caught (python flag was false, so the gate call itself "
      "never ran) -- this is the live bug reproduced end to end",
      old_failure is None, old_failure)
check("under the fixed naming, the same fabricated client IS caught",
      new_failure is not None and "self-invented fake client" in new_failure,
      new_failure)

print("\na straggler race/hedge file left in the project directory is "
      "excluded from team.py's own existing-.py-file scan")
tmp = tempfile.mkdtemp()
try:
    with open(os.path.join(tmp, "main.py"), "w") as f:
        f.write("def main():\n    pass\n")
    straggler = team._race_scratch_path(
        os.path.join(tmp, "main.py"), "hedge", 0)
    with open(straggler, "w") as f:
        f.write(FABRICATED_CLIENT_CODE)

    existing_code = {}
    for name in os.listdir(tmp):
        full = os.path.join(tmp, name)
        if (name.endswith(".py") and os.path.isfile(full)
                and not intent.is_race_scratch_name(name)):
            with open(full, encoding="utf-8", errors="replace") as fh:
                existing_code[os.path.normpath(name)] = fh.read()
    check("real main.py is present in the existing-code snapshot",
          "main.py" in existing_code, list(existing_code))
    check("the straggler scratch file is excluded from the snapshot",
          os.path.basename(straggler) not in existing_code,
          list(existing_code))

    print("\n...and from session.py's existing_files_block rendering")
    block = session.existing_files_block(tmp, lambda src: [])
    check("existing_files_block mentions the real main.py",
          "main.py" in block, block)
    check("existing_files_block does not mention the straggler",
          os.path.basename(straggler) not in block, block)
finally:
    shutil.rmtree(tmp)

print("\nintent.race_scratch_display_name recovers the real component "
      "name a straggler's advisory note should read as")
straggler_name = team._race_scratch_path("main.py", "race", 1)
check("main.__swarmrace_race1__.py -> main.py",
      intent.race_scratch_display_name(straggler_name) == "main.py",
      intent.race_scratch_display_name(straggler_name))
check("an ordinary path is left untouched",
      intent.race_scratch_display_name("client.py") == "client.py")
check("a nested path keeps its directory",
      intent.race_scratch_display_name(
          os.path.join("sub", team._race_scratch_path(
              "sub/main.py", "hedge", 3)))
      == os.path.join("sub", "sub", "main.py"),
      intent.race_scratch_display_name(
          os.path.join("sub", team._race_scratch_path(
              "sub/main.py", "hedge", 3))))

print("\ncodegen.gate_smoke's advisory notes use the real display name, "
      "not the scratch path, when probed with a race/hedge scratch "
      "out_path -- reproduces the live confusion directly")
tmp2 = tempfile.mkdtemp()
try:
    real_path = os.path.join(tmp2, "main.py")
    scratch_path = team._race_scratch_path(real_path, "race", 1)
    sibling_reading_code = '''\
import json

def main():
    with open("../02_create_task/output.json") as f:
        data = json.load(f)
    print(data["task_id"])

if __name__ == "__main__":
    main()
'''
    with open(scratch_path, "w") as f:
        f.write(sibling_reading_code)

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        result = codegen.gate_smoke(scratch_path, tmp2)
    printed = buf.getvalue()
    check("gate_smoke does not fail the attempt for a genuinely missing "
          "sibling (unchanged behaviour)", result is None, result)
    check("the printed note names the real 'main.py', not the scratch "
          "filename",
          "main.py could not find" in printed
          and os.path.basename(scratch_path) not in printed,
          printed)
finally:
    shutil.rmtree(tmp2)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
