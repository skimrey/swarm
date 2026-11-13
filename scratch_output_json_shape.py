"""A step's own output.json must be a JSON object, not a bare scalar --
see `codegen.gate_output_json_shape`.

Measured live: `01_create_project/main.py` wrote `output.json` as a bare
JSON string -- the raw project id, no `{"project_id": ...}` wrapper --
despite the request saying, verbatim, "save its id as project_id in this
directory's own output.json". `02_create_task` then failed with
`AttributeError: 'str' object has no attribute 'get'` on its first line,
and `06_push_applet_version` failed the same way with `TypeError: string
indices must be integers` -- two different consumers, two different
exceptions, one root cause.
"""
import codegen

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


BAD_SUBSCRIPT_THEN_DUMP = '''
import json

def main():
    resp = create_project()
    project_id = resp["id"]
    with open("output.json", "w") as f:
        json.dump(project_id, f)
'''

BAD_SUBSCRIPT_DIRECT = '''
import json

def main():
    resp = create_project()
    with open("output.json", "w") as f:
        json.dump(resp["id"], f)
'''

BAD_STRING_LITERAL = '''
import json

def main():
    with open("output.json", "w") as f:
        json.dump("abc-123", f)
'''

BAD_VIA_WRITE_DUMPS = '''
import json

def main():
    resp = create_project()
    project_id = resp.get("id")
    f = open("output.json", "w")
    f.write(json.dumps(project_id))
'''

BAD_LIST = '''
import json

def main():
    with open("output.json", "w") as f:
        json.dump(["a", "b"], f)
'''

GOOD_DICT_LITERAL = '''
import json

def main():
    resp = create_project()
    project_id = resp["id"]
    with open("output.json", "w") as f:
        json.dump({"project_id": project_id}, f)
'''

GOOD_DICT_VIA_VARIABLE = '''
import json

def main():
    resp = create_project()
    project_id = resp["id"]
    data = {"project_id": project_id}
    with open("output.json", "w") as f:
        json.dump(data, f)
'''

GOOD_UNKNOWN_SHAPE_LEFT_ALONE = '''
import json

def main():
    payload = build_payload()
    with open("output.json", "w") as f:
        json.dump(payload, f)
'''

GOOD_READ_ONLY_OF_SIBLING = '''
import json

def main():
    with open("../01_create_project/output.json") as f:
        data = json.load(f)
'''

check("a scalar assigned via subscript, then dumped bare, is caught",
      codegen.gate_output_json_shape(BAD_SUBSCRIPT_THEN_DUMP) is not None)
check("a subscript expression dumped directly (no intermediate variable) "
     "is caught too",
      codegen.gate_output_json_shape(BAD_SUBSCRIPT_DIRECT) is not None)
check("a bare string literal dumped directly is caught",
      codegen.gate_output_json_shape(BAD_STRING_LITERAL) is not None)
check("the write(json.dumps(...)) shape is caught, not only json.dump(...)",
      codegen.gate_output_json_shape(BAD_VIA_WRITE_DUMPS) is not None)
check("a bare list is also not a JSON object with a named key, and is "
     "caught the same way a bare scalar is",
      codegen.gate_output_json_shape(BAD_LIST) is not None)
check("wrapping the id in a dict literal directly is never flagged",
      codegen.gate_output_json_shape(GOOD_DICT_LITERAL) is None)
check("wrapping the id in a dict assigned to a variable first is never "
     "flagged either",
      codegen.gate_output_json_shape(GOOD_DICT_VIA_VARIABLE) is None)
check("a value of genuinely unknown shape (a function call this cannot "
     "classify) is left alone rather than guessed at",
      codegen.gate_output_json_shape(GOOD_UNKNOWN_SHAPE_LEFT_ALONE) is None)
check("reading a sibling's output.json is not this gate's concern at all",
      codegen.gate_output_json_shape(GOOD_READ_ONLY_OF_SIBLING) is None)
check("a syntax error is not this check's problem to report",
      codegen.gate_output_json_shape("def f(:\n") is None)

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
