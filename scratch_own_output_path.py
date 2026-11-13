"""A step's creation output must land in its own output.json, not a
sibling's -- see `codegen.gate_own_output_path`.

Measured live: `02_create_task/main.py` correctly read `project_id` from
`../01_create_project/output.json`, then saved the `task_id` it created
back to that *same* path instead of its own `output.json`, overwriting
the producer's file. `03_upload_units` then failed with "Required file
not found at ../02_create_task/output.json" -- the id was sitting one
directory over, in a file with a different meaning. The request text
already distinguished the two paths clearly; the writer read past it
anyway.
"""
import codegen

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


BAD_LITERAL = '''
import json

def main():
    with open("../01_create_project/output.json") as f:
        data = json.load(f)
    task_id = create_task(data["project_id"])
    with open("../01_create_project/output.json", "w") as f:
        json.dump({"task_id": task_id}, f)
'''

BAD_VIA_VARIABLE = '''
import json
OUT = "../01_create_project/output.json"

def main():
    with open(OUT) as f:
        data = json.load(f)
    task_id = create_task(data["project_id"])
    with open(OUT, "w") as f:
        json.dump({"task_id": task_id}, f)
'''

BAD_APPEND_MODE = '''
def main():
    f = open("../02_create_task/output.json", "a")
    f.write("x")
'''

GOOD_LOCAL_WRITE = '''
import json

def main():
    with open("../01_create_project/output.json") as f:
        data = json.load(f)
    task_id = create_task(data["project_id"])
    with open("output.json", "w") as f:
        json.dump({"task_id": task_id}, f)
'''

GOOD_EXPLICIT_READ_MODE = '''
def main():
    with open("../01_create_project/output.json", "r") as f:
        pass
'''

GOOD_UNRELATED_WRITE = '''
def main():
    with open("../assets/survey.html", "w") as f:
        f.write("<html></html>")
'''

check("a literal write to a sibling's output.json is caught",
      codegen.gate_own_output_path(BAD_LITERAL) is not None)
check("...and the finding names the sibling path and the write mode",
      "../01_create_project/output.json" in
      (codegen.gate_own_output_path(BAD_LITERAL) or "")
      and "'w'" in (codegen.gate_own_output_path(BAD_LITERAL) or ""))
check("a write through a variable holding the sibling path is caught too "
      "(same-module assignment lookup, not just inline literals)",
      codegen.gate_own_output_path(BAD_VIA_VARIABLE) is not None)
check("append mode ('a') is caught, not only 'w'",
      codegen.gate_own_output_path(BAD_APPEND_MODE) is not None)
check("writing locally after reading a sibling is the correct pattern "
      "and is never flagged",
      codegen.gate_own_output_path(GOOD_LOCAL_WRITE) is None)
check("an explicit read-mode open of a sibling's output.json is fine",
      codegen.gate_own_output_path(GOOD_EXPLICIT_READ_MODE) is None)
check("a write to an unrelated sibling file (not output.json) is left "
      "alone -- this only ever concerns the convention's own filename",
      codegen.gate_own_output_path(GOOD_UNRELATED_WRITE) is None)
check("a syntax error is not this check's problem to report",
      codegen.gate_own_output_path("def f(:\n") is None)

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
