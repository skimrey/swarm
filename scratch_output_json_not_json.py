"""Validates codegen.gate_output_json_not_json against the exact live bug
(a project-id saved as plain text via an atomic tempfile-write helper),
the direct-open shape, and the negative cases that must stay clean, plus
sabotage confirming the check is load-bearing."""
import codegen

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


# --- The exact live shape: a helper called with a literal "output.json"
# filename, writing plain text via an atomic tempfile-and-replace pattern.
LIVE_BUG = '''
import os
import tempfile


def save_project_id(project_id: str, filename: str) -> None:
    content = f"project_id: {project_id}"
    target_dir = os.path.dirname(os.path.abspath(filename))
    os.makedirs(target_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=target_dir, text=True)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        os.replace(temp_path, filename)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def main():
    project_id = "abc-123"
    output_filename = "output.json"
    save_project_id(project_id=project_id, filename=output_filename)


if __name__ == "__main__":
    main()
'''

# --- Direct-open shape: no helper indirection at all.
DIRECT_BUG = '''
def main():
    project_id = "abc-123"
    with open("output.json", "w") as f:
        f.write(f"project_id: {project_id}")


if __name__ == "__main__":
    main()
'''

# --- Correct: direct open, json.dump.
GOOD_DIRECT = '''
import json


def main():
    project_id = "abc-123"
    with open("output.json", "w") as f:
        json.dump({"project_id": project_id}, f)


if __name__ == "__main__":
    main()
'''

# --- Correct: helper called with literal "output.json", serializes with
# json.dumps inside a write() call.
GOOD_HELPER_DUMPS = '''
import json


def save_project_id(project_id: str, filename: str) -> None:
    with open(filename, "w") as f:
        f.write(json.dumps({"project_id": project_id}))


def main():
    save_project_id(project_id="abc-123", filename="output.json")


if __name__ == "__main__":
    main()
'''

# --- Correct: helper called with literal "output.json", uses json.dump
# directly against the handle (no .write() call at all).
GOOD_HELPER_DUMP = '''
import json


def save_project_id(project_id: str, filename: str) -> None:
    with open(filename, "w") as f:
        json.dump({"project_id": project_id}, f)


def main():
    save_project_id(project_id="abc-123", filename="output.json")


if __name__ == "__main__":
    main()
'''

# --- Must NOT fire: a helper writing some other file entirely (not
# output.json), even though it writes plain text.
OTHER_FILE_HELPER = '''
def write_log(message: str, filename: str) -> None:
    with open(filename, "w") as f:
        f.write(message)


def main():
    write_log("hello", "debug.log")


if __name__ == "__main__":
    main()
'''

# --- Must NOT fire: a helper called with output.json that does not write
# any file handle at all (e.g. only prints).
NO_WRITE_HELPER = '''
def announce(filename: str) -> None:
    print(f"would write to {filename}")


def main():
    announce("output.json")


if __name__ == "__main__":
    main()
'''

print("\nthe exact live bug: an atomic tempfile helper writing plain text, "
      "called with a literal output.json filename")
check("LIVE_BUG is caught",
      codegen.gate_output_json_not_json(LIVE_BUG) is not None,
      codegen.gate_output_json_not_json(LIVE_BUG))

print("\nthe simpler direct-open shape with no helper indirection")
check("DIRECT_BUG is caught",
      codegen.gate_output_json_not_json(DIRECT_BUG) is not None,
      codegen.gate_output_json_not_json(DIRECT_BUG))

print("\ncorrect code must stay clean")
check("GOOD_DIRECT (json.dump, direct open) passes",
      codegen.gate_output_json_not_json(GOOD_DIRECT) is None,
      codegen.gate_output_json_not_json(GOOD_DIRECT))
check("GOOD_HELPER_DUMPS (json.dumps inside .write(), via helper) passes",
      codegen.gate_output_json_not_json(GOOD_HELPER_DUMPS) is None,
      codegen.gate_output_json_not_json(GOOD_HELPER_DUMPS))
check("GOOD_HELPER_DUMP (json.dump against handle, via helper) passes",
      codegen.gate_output_json_not_json(GOOD_HELPER_DUMP) is None,
      codegen.gate_output_json_not_json(GOOD_HELPER_DUMP))

print("\nmust not fire on unrelated writes")
check("a helper writing some other named file is untouched",
      codegen.gate_output_json_not_json(OTHER_FILE_HELPER) is None,
      codegen.gate_output_json_not_json(OTHER_FILE_HELPER))
check("a function called with output.json that writes no file handle at "
      "all is untouched",
      codegen.gate_output_json_not_json(NO_WRITE_HELPER) is None,
      codegen.gate_output_json_not_json(NO_WRITE_HELPER))

print("\nsabotage: sanity-check the fixture actually exercises a bug a "
      "disabled check would miss")
# Simulate "no gate at all" by asserting the live bug's own code, run for
# real, produces exactly the JSONDecodeError chain-reaction this gate
# exists to prevent -- i.e. the written file is not valid JSON.
import ast
tree = ast.parse(LIVE_BUG)
# Extract and execute save_project_id + main against a temp dir to prove
# the live shape really does write non-JSON, so the gate above is refuting
# a real defect and not a strawman.
import tempfile as _tempfile
import os as _os
import json as _json

ns = {}
exec(compile(tree, "<live_bug>", "exec"), ns)
with _tempfile.TemporaryDirectory() as d:
    out = _os.path.join(d, "output.json")
    ns["save_project_id"](project_id="abc-123", filename=out)
    with open(out) as f:
        raw = f.read()
    try:
        _json.loads(raw)
        wrote_json = True
    except Exception:
        wrote_json = False
check("sabotage: the live fixture, actually executed, writes content "
      "that is not valid JSON -- confirming the gate is refuting a real "
      "defect, not a strawman",
      not wrote_json, raw)

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    raise SystemExit(1)
