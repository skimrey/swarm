#!/usr/bin/env python3
"""Validates codegen.gate_missing_own_output_write and its helper
spec_requires_own_output_write against the live failure that motivated it:
a `create an applet; save its id in this directory's own output.json`
component that created the applet for real and never wrote output.json at
all -- no sibling-path mistake, no bare-scalar mistake, just no write, so
neither gate_own_output_path nor gate_output_json_shape had anything to
object to.
"""
import codegen

CREATES_BUT_NEVER_SAVES = '''
import json
import requests

def main():
    resp = requests.post("http://127.0.0.1:1/api/applets", json={"name": "x"})
    resp.raise_for_status()
    applet_id = resp.json()["id"]
    print(applet_id)

if __name__ == "__main__":
    main()
'''

CORRECTLY_SAVES = '''
import json
import requests

def main():
    resp = requests.post("http://127.0.0.1:1/api/applets", json={"name": "x"})
    resp.raise_for_status()
    applet_id = resp.json()["id"]
    with open("output.json", "w") as f:
        json.dump({"applet_id": applet_id}, f)

if __name__ == "__main__":
    main()
'''

# A pure consumer: reads a sibling's id, does something with it, has
# nothing of its own to hand forward. Must never be flagged.
PURE_CONSUMER_SPEC = (
    "upload 3 units to the task whose task_id is read from "
    "../02_create_task/output.json"
)
PURE_CONSUMER_CODE = '''
import json
import requests

def main():
    with open("../02_create_task/output.json") as f:
        task_id = json.load(f)["task_id"]
    requests.post("http://127.0.0.1:1/api/tasks/%s/units" % task_id, json={})

if __name__ == "__main__":
    main()
'''

SAVE_SPEC = ("create an applet; save its id in this directory's own "
             "output.json")

CHAINED_SPEC = ("create a task in the project whose project_id is read "
                "from ../01_create_project/output.json; save its id as "
                "task_id in this directory's own output.json")

failures = []


def check(name, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print("%s %s%s" % (status, name, ("  -- " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# --- spec_requires_own_output_write ---
check("a save-to-own-output.json spec is detected",
      codegen.spec_requires_own_output_write(SAVE_SPEC))
check("a chained producer/consumer spec is still detected as owning a write",
      codegen.spec_requires_own_output_write(CHAINED_SPEC))
check("a pure-consumer spec (only a sibling reference) is not flagged as "
      "owning a write",
      not codegen.spec_requires_own_output_write(PURE_CONSUMER_SPEC))
check("a spec mentioning output.json nowhere is not flagged",
      not codegen.spec_requires_own_output_write(
          "print hello world"))

# --- gate_missing_own_output_write ---
check("the live failure shape (creates, never saves) is caught",
      codegen.gate_missing_own_output_write(SAVE_SPEC, CREATES_BUT_NEVER_SAVES)
      is not None)
check("a file that actually writes output.json passes clean",
      codegen.gate_missing_own_output_write(SAVE_SPEC, CORRECTLY_SAVES) is None)
check("a pure consumer with no output.json write of its own is not flagged",
      codegen.gate_missing_own_output_write(PURE_CONSUMER_SPEC,
                                            PURE_CONSUMER_CODE) is None)
check("a component whose spec never asked for a save is not flagged even "
      "with no write",
      codegen.gate_missing_own_output_write("print hello world",
                                            "print('hello world')") is None)

# --- sabotage: prove the test would have caught the bug if the gate were
# absent / disabled ---
def sabotage():
    # Simulate "gate not wired in" by checking that CREATES_BUT_NEVER_SAVES
    # passes every *pre-existing* gate clean -- reproducing exactly why the
    # live failure went unnoticed before this gate existed.
    own_path = codegen.gate_own_output_path(CREATES_BUT_NEVER_SAVES)
    shape = codegen.gate_output_json_shape(CREATES_BUT_NEVER_SAVES)
    check("sabotage: the live failure passes both pre-existing output "
          "gates clean, which is exactly why it needed a third gate",
          own_path is None and shape is None,
          "own_path=%r shape=%r" % (own_path, shape))


sabotage()

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
