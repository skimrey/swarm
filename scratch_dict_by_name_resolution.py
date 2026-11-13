"""codegen._dict_literal_by_name / _resolve_dict_literal.

Measured live: `01_create_project`'s own request read, verbatim, "create
a project; save its id as first_id in a new output.json in the same
directory as the script". The generated file wrote

    output_data = {"project_id": project_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)

-- the promised key was "first_id"; the key actually written was
"project_id". `gate_output_json_key` exists precisely to catch this and
did not: its own dict-reading helper only ever looked at the literal
passed directly to `json.dump(...)`, and here that argument is a bare
variable name, not an inline `{...}`. The helper read that as "cannot
tell what this contains" and gate_output_json_key backs off entirely the
moment any write in the file is unreadable -- so the file passed every
gate on the first attempt, `02_create_task` trusted the promised key
name (correctly, on its own end), and got `KeyError: 'first_id'` against
a file that had a real id in it under a different name.

`_dict_literal_by_name` closes this by resolving a name assigned a dict
literal exactly once anywhere in the file back to that literal, so
`json.dump(output_data, f)` is read exactly as if `output_data` had been
written inline. Run directly: `python3 scratch_dict_by_name_resolution.py`.
"""
import codegen


LIVE_BUG_SPEC = ("create a project; save its id as first_id in a new "
                 "output.json in the same directory as the script")

LIVE_BUG_CODE = '''
import json
import requests

def main():
    resp = requests.post("http://x/api/projects", json={"name": "p"})
    data = resp.json()
    project_id = data.get("id")

    output_data = {"project_id": project_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)

if __name__ == "__main__":
    main()
'''

CORRECT_CODE = LIVE_BUG_CODE.replace(
    '{"project_id": project_id}', '{"first_id": project_id}')

DYNAMICALLY_MUTATED_CODE = '''
import json

def main():
    project_id = "abc-123"
    data = {}
    data["project_id"] = project_id
    with open("output.json", "w") as f:
        json.dump(data, f)

if __name__ == "__main__":
    main()
'''

AMBIGUOUS_DOUBLE_ASSIGN_CODE = '''
import json

def main():
    task_id = "t-1"
    project_id = "p-1"
    output_data = {"first_id": project_id}
    if True:
        output_data = {"first_id": task_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)

if __name__ == "__main__":
    main()
'''


def test_live_bug_is_now_caught_via_variable_resolution():
    msg = codegen.gate_output_json_key(LIVE_BUG_SPEC, LIVE_BUG_CODE)
    assert msg is not None, (
        "the live first_id/project_id mismatch, written through a "
        "variable rather than an inline literal, was not caught")
    assert "first_id" in msg and "project_id" in msg, msg
    print("  the live first_id/project_id mismatch (written via a "
         "variable, not an inline literal) is now caught")


def test_correct_code_via_the_same_variable_shape_passes():
    assert codegen.gate_output_json_key(LIVE_BUG_SPEC, CORRECT_CODE) is None
    print("  the same variable-held-dict shape, with the right key, "
         "passes clean")


def test_a_dict_mutated_after_its_literal_assignment_is_still_unreadable():
    # `data = {}` is a dict literal, but `data["project_id"] = ...` right
    # after it means the literal's own (empty) contents are stale by the
    # time it is written -- must stay unresolved, not report zero keys.
    assert codegen.gate_output_json_key(
        LIVE_BUG_SPEC, DYNAMICALLY_MUTATED_CODE) is None
    print("  a dict literal later mutated via subscript assignment is "
         "still treated as unreadable, not resolved to its stale, "
         "pre-mutation contents")


def test_a_name_assigned_a_dict_literal_twice_is_left_unresolved():
    # Two different literals assigned to the same name is genuinely
    # ambiguous -- which one is live at the write depends on control
    # flow this scan does not simulate, so it must not guess either way.
    assert codegen.gate_output_json_key(
        LIVE_BUG_SPEC, AMBIGUOUS_DOUBLE_ASSIGN_CODE) is None
    print("  a name assigned a dict literal more than once is left "
         "unresolved rather than picking either assignment")


def test_sabotage_the_old_inline_only_behavior_missed_the_live_bug():
    # Confirms the fixture actually exercises the fix: reproducing the
    # pre-fix inline-only reader (no variable resolution at all) directly
    # against the same live shape shows it reporting nothing, exactly as
    # the real bug shipped.
    import ast
    tree = ast.parse(LIVE_BUG_CODE)
    handle_names = codegen._handles_for_local_output(tree, "wax")
    inline_only_found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "dump"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Name)
                and node.args[1].id in handle_names):
            inline_only_found.append(isinstance(node.args[0], ast.Dict))
    assert inline_only_found == [False], (
        "the fixture must pass a bare variable name to json.dump, not an "
        "inline literal, or this test proves nothing about the fix")
    print("  sabotage: confirmed the live shape genuinely passes a bare "
         "variable (not an inline literal) to json.dump -- the old "
         "inline-only reader would have had nothing to read here")


def main():
    test_live_bug_is_now_caught_via_variable_resolution()
    test_correct_code_via_the_same_variable_shape_passes()
    test_a_dict_mutated_after_its_literal_assignment_is_still_unreadable()
    test_a_name_assigned_a_dict_literal_twice_is_left_unresolved()
    test_sabotage_the_old_inline_only_behavior_missed_the_live_bug()
    print("OK")


if __name__ == "__main__":
    main()
