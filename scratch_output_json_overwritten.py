"""Validates `codegen.gate_output_json_overwritten`.

Live failure: `01_create_project`'s request said only "create a project;
save its id as project_id" -- but the generated file also created a task
(an endpoint present in its API slice, never asked for by its own
request) and saved *that* id through the same helper,
`print_and_save_id(key, id_value)`, which rebuilt `output_data = {}` and
overwrote `output.json` on every call. The project-creation call wrote
`{"project_id": ...}` first; the task-creation call, later in the same
`main()`, overwrote it with `{"task_id": ...}`. `output.json` ended the
real run holding only the task id, and the very next chain step's
`KeyError: 'project_id'` was the first anyone could see it -- every
mechanical check that ran at generation time passed, including
`gate_output_json_key`, because the dict is built through a variable and
a later subscript assignment, not an inline literal.
"""
import sys

import codegen


LIVE_SHAPE = '''
import json

def print_and_save_id(key, id_value):
    print(f"Created {key}: {id_value}")
    output_data = {}
    output_data[f"{key}_id"] = id_value
    with open("output.json", "w") as f:
        json.dump(output_data, f)

def create_project(name):
    project_id = "real-project-id"
    print_and_save_id("project", project_id)
    return project_id

def create_task(project_id, name):
    task_id = "real-task-id"
    print_and_save_id("task", task_id)
    return task_id

def main():
    project_id = create_project("Initial Project Setup")
    task_id = create_task(project_id, "Initial Task Setup")

if __name__ == "__main__":
    main()
'''

CALLED_ONCE = '''
import json

def save_id(key, id_value):
    output_data = {}
    output_data[f"{key}_id"] = id_value
    with open("output.json", "w") as f:
        json.dump(output_data, f)

def main():
    project_id = "real-project-id"
    save_id("project", project_id)

if __name__ == "__main__":
    main()
'''

MERGES_BEFORE_WRITING = '''
import json
import os

def save_id(key, id_value):
    output_data = {}
    if os.path.isfile("output.json"):
        with open("output.json", "r") as f:
            output_data = json.load(f)
    output_data[f"{key}_id"] = id_value
    with open("output.json", "w") as f:
        json.dump(output_data, f)

def main():
    save_id("project", "p1")
    save_id("task", "t1")

if __name__ == "__main__":
    main()
'''

INLINE_LITERAL_TWICE_BUT_MERGED = '''
import json
import os

def main():
    with open("output.json", "w") as f:
        json.dump({"project_id": "p1"}, f)
    data = {}
    if os.path.exists("output.json"):
        with open("output.json") as fh:
            data = json.load(fh)
    data["task_id"] = "t1"
    with open("output.json", "w") as f:
        json.dump(data, f)

if __name__ == "__main__":
    main()
'''

NO_OUTPUT_JSON_AT_ALL = '''
def main():
    print("hello")

if __name__ == "__main__":
    main()
'''


def check(cond, msg):
    if not cond:
        print("FAIL: " + msg)
        sys.exit(1)
    print("ok: " + msg)


def main():
    result = codegen.gate_output_json_overwritten(LIVE_SHAPE)
    check(result is not None, "flags the exact live 01_create_project shape")
    check("print_and_save_id" in result and "2 times" in result,
          "names the repeated writer and how many times it was called")

    check(codegen.gate_output_json_overwritten(CALLED_ONCE) is None,
          "a writer helper called exactly once is fine")

    check(codegen.gate_output_json_overwritten(MERGES_BEFORE_WRITING) is None,
          "a writer that reads output.json back before writing is fine "
          "to call more than once")

    check(codegen.gate_output_json_overwritten(
              INLINE_LITERAL_TWICE_BUT_MERGED) is None,
          "two separate write call sites are fine when the second one "
          "reads and merges before writing again")

    check(codegen.gate_output_json_overwritten(NO_OUTPUT_JSON_AT_ALL) is None,
          "a file with no output.json write at all is untouched")

    # Sabotage: an existing, unrelated gate does not happen to catch this
    # by accident -- confirming this is a genuinely new check.
    check(codegen.gate_output_json_key(
              "create a project; save its id as project_id in this "
              "directory's own output.json", LIVE_SHAPE) is None,
          "sabotage: gate_output_json_key stays silent on this exact "
          "shape (the dict is built through a variable, which it "
          "already declines to guess about) -- proving the new gate is "
          "not a duplicate of an existing one")

    print("\nall gate_output_json_overwritten checks passed")


if __name__ == "__main__":
    main()
