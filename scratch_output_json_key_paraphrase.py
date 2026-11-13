"""Validates the fix for the exact `run_11` live failure: a chain step's
request read, verbatim, "create a project; save its id as project_id in
a new output.json in the same directory as the script", the planner's
own SPEC for that component paraphrased it to "...writing the resulting
project ID to output.json" (dropping the literal `project_id` token),
`own_output_write_block` fell back to a hardcoded example key
(`result_id`), the writer copied it verbatim, and every gate that only
ever checked `spec` -- never the original request -- stayed silent.

Three things are checked here, matching the three places this bug
actually lived:

1. `own_output_write_block` no longer fabricates a specific wrong key
   when `spec` alone has no promise, whether or not `original_request`
   is supplied -- the literal string "result_id" must never appear in
   its output.
2. `gate_output_json_key` catches the mismatch when given the real
   planner SPEC (which has no promise) plus the original request (which
   does) -- something the 2-argument call alone cannot do.
3. `autofix_output_json_key` performs the exact 1:1 rename mechanically,
   with no model call, and declines whenever the rename would be a
   guess -- including the specific danger raised when this fix was
   proposed: a step's own output key is legitimately allowed to differ
   from what a naive "just check the request" scan might expect (e.g. a
   version being created, not the parent entity), which is why the
   autofix only ever acts on an exact 1-missing/1-extra correspondence
   and never invents a rename from a folder or file name.

Backward compatibility: every existing call site of `gate_output_json_
key(spec, code)` and `own_output_write_block(spec)` in this project
passes exactly 2/1 positional args respectively, so both new parameters
must default to values that reproduce the old, pre-fix behaviour when
omitted.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen


# The exact live shapes, transcribed from
# .swarm/plans/6f0cdd542abc5ddd.json and the run_11 chain step.
LIVE_REQUEST = ("create a project; save its id as project_id in a new "
                "output.json in the same directory as the script")
LIVE_PARAPHRASED_SPEC = (
    "This file will contain the main execution logic, calling the API "
    "to create a project, and then writing the resulting project ID to "
    "output.json. It must import `requests` and use the pre-defined "
    "`API_BASE_URL`, `API_KEY`, and `HEADERS` from the environment "
    "setup. It will use the `POST /api/projects` endpoint, providing a "
    "unique name and description, and upon success, it must print the "
    "resulting project ID to stdout and write it to a file named "
    "\"output.json\" in the same directory.")

WRONG_KEY_CODE = """
import json

def main():
    project_id = create_project()
    output_data = {"result_id": project_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)

if __name__ == "__main__":
    main()
"""

RIGHT_KEY_CODE = WRONG_KEY_CODE.replace(
    '{"result_id": project_id}', '{"project_id": project_id}')


# --------------------------------------------------------------------
# 1. own_output_write_block must not fabricate a wrong example key.
# --------------------------------------------------------------------

def _code_block_body(block: str) -> str:
    """The fenced ```python ...``` snippet's own body, excluding the
    surrounding prose -- naming "result_id" in the prose as a
    forbidden example is fine and intended; the defect this guards
    against is the fenced snippet itself using it as the answer."""
    if "```python" not in block:
        return ""
    return block.split("```python", 1)[1].split("```", 1)[0]


def test_no_promise_anywhere_gives_no_fabricated_key():
    block = codegen.own_output_write_block(LIVE_PARAPHRASED_SPEC)
    assert '"result_id"' not in _code_block_body(block), (
        "must never present a specific fabricated key as the copy-"
        "pasteable answer when no promise was recoverable")
    assert "output.json" in block  # still says something useful


def test_promise_only_in_original_request_is_recovered():
    block = codegen.own_output_write_block(
        LIVE_PARAPHRASED_SPEC, LIVE_REQUEST)
    assert "project_id" in block
    assert "result_id" not in block


def test_promise_in_spec_alone_still_works_unchanged():
    spec = ("create a project; save its id as project_id in this "
            "directory's own output.json")
    block = codegen.own_output_write_block(spec)
    assert "project_id" in block
    assert "result_id" not in block


def test_no_promise_at_all_omits_result_id_regardless_of_caller():
    # No spec promise, no request text supplied at all (the old,
    # pre-fix call shape) -- must still not present it as the answer.
    block = codegen.own_output_write_block(LIVE_PARAPHRASED_SPEC, "")
    assert '"result_id"' not in _code_block_body(block)


# --------------------------------------------------------------------
# 2. gate_output_json_key must catch the mismatch using both texts.
# --------------------------------------------------------------------

def test_gate_silent_on_paraphrased_spec_alone():
    # This is the exact blind spot that let the bug ship: with only
    # the planner's SPEC (no literal promise), the gate correctly has
    # nothing to compare against and must stay silent -- proving the
    # gap this fix closes is real, not already covered.
    assert codegen.gate_output_json_key(
        LIVE_PARAPHRASED_SPEC, WRONG_KEY_CODE) is None


def test_gate_catches_mismatch_with_original_request_supplied():
    result = codegen.gate_output_json_key(
        LIVE_PARAPHRASED_SPEC, WRONG_KEY_CODE, LIVE_REQUEST)
    assert result is not None
    assert "project_id" in result
    assert "result_id" in result


def test_gate_clean_when_code_already_correct():
    assert codegen.gate_output_json_key(
        LIVE_PARAPHRASED_SPEC, RIGHT_KEY_CODE, LIVE_REQUEST) is None


# --------------------------------------------------------------------
# 3. autofix_output_json_key performs the exact mechanical rename, or
#    declines when it would be a guess.
# --------------------------------------------------------------------

def test_autofix_renames_the_exact_live_shape():
    fixed = codegen.autofix_output_json_key(
        LIVE_PARAPHRASED_SPEC, WRONG_KEY_CODE, LIVE_REQUEST)
    assert fixed is not None
    assert '"project_id"' in fixed
    assert '"result_id"' not in fixed
    # Must still be valid, gate-clean Python.
    import ast
    ast.parse(fixed)
    assert codegen.gate_output_json_key(
        LIVE_PARAPHRASED_SPEC, fixed, LIVE_REQUEST) is None
    # The value expression itself is untouched -- only the key changed.
    assert "project_id = create_project()" in fixed


def test_autofix_declines_with_no_promise_anywhere():
    assert codegen.autofix_output_json_key(
        LIVE_PARAPHRASED_SPEC, WRONG_KEY_CODE) is None


def test_autofix_declines_on_multiple_writes():
    two_writes_code = WRONG_KEY_CODE + """
def again():
    with open("output.json", "w") as f:
        json.dump({"other_id": "x"}, f)
"""
    assert codegen.autofix_output_json_key(
        LIVE_PARAPHRASED_SPEC, two_writes_code, LIVE_REQUEST) is None


def test_autofix_declines_when_more_than_one_key_is_missing():
    spec_two_promises = (
        "create a project; save its id as project_id and its owner id "
        "as owner_id in output.json")
    assert codegen.autofix_output_json_key(
        spec_two_promises, WRONG_KEY_CODE) is None


def test_autofix_declines_when_extra_keys_the_request_never_promised_exist():
    # A component legitimately saving more than the one promised key
    # (e.g. a version id alongside metadata) must not have a "wrong"
    # key guessed at out of several candidates.
    code = """
import json

def main():
    project_id = create_project()
    output_data = {"result_id": project_id, "created_at": "now"}
    with open("output.json", "w") as f:
        json.dump(output_data, f)
"""
    assert codegen.autofix_output_json_key(
        LIVE_PARAPHRASED_SPEC, code, LIVE_REQUEST) is None


def test_autofix_declines_when_write_is_already_correct():
    assert codegen.autofix_output_json_key(
        LIVE_PARAPHRASED_SPEC, RIGHT_KEY_CODE, LIVE_REQUEST) is None


# --------------------------------------------------------------------
# 4. The negative control the folder-name-convention proposal would
#    have broken: a step whose correct output key is a sub-resource's
#    id (a new applet *version*), not the parent entity's id the
#    folder name might suggest. Nothing in this fix renames based on a
#    folder or file name at all, so this must be completely unaffected
#    by any of the above.
# --------------------------------------------------------------------

VERSION_REQUEST = (
    "read applet_id from ../05_create_applet/output.json; read "
    "survey.html from ../04_create_survey/survey.html; update the "
    "applet with the html; save its new version id as version_id in "
    "a new output.json in the same directory as the script")

VERSION_CODE_CORRECT = """
import json

def main():
    applet_id = read_applet_id()
    version_id = push_new_version(applet_id)
    with open("output.json", "w") as f:
        json.dump({"version_id": version_id}, f)

if __name__ == "__main__":
    main()
"""


def test_correct_version_id_output_is_never_flagged():
    # A folder-name-convention fix would have expected "applet_id"
    # here (from "06_update_applet") and wrongly flagged or rewritten
    # a file that is already exactly correct.
    assert codegen.gate_output_json_key(
        "", VERSION_CODE_CORRECT, VERSION_REQUEST) is None
    assert codegen.autofix_output_json_key(
        "", VERSION_CODE_CORRECT, VERSION_REQUEST) is None
    block = codegen.own_output_write_block(
        "save its new version id as version_id in output.json",
        VERSION_REQUEST)
    assert "version_id" in block


# --------------------------------------------------------------------
# 5. Backward compatibility: old 2-arg / 1-arg call shapes unaffected.
# --------------------------------------------------------------------

def test_gate_two_positional_args_unchanged():
    spec = ("create a project; save its id as project_id in this "
            "directory's own output.json")
    assert codegen.gate_output_json_key(spec, RIGHT_KEY_CODE) is None
    assert codegen.gate_output_json_key(spec, WRONG_KEY_CODE) is not None


def test_own_output_write_block_one_positional_arg_unchanged():
    spec = ("create a project; save its id as project_id in this "
            "directory's own output.json")
    block = codegen.own_output_write_block(spec)
    assert "project_id" in block


# --------------------------------------------------------------------
# 6. Sabotage: reproduce the pre-fix behaviour directly, proving each
#    fixture above actually exercises the live bug rather than merely
#    resembling it.
# --------------------------------------------------------------------

def test_sabotage_pre_fix_gate_never_sees_the_mismatch():
    # The pre-fix call shape passed only `spec` -- never the original
    # request -- to gate_output_json_key, which is exactly
    # `test_gate_silent_on_paraphrased_spec_alone` above. Restated here
    # explicitly as the sabotage proof: calling the gate the *old* way
    # on the *exact* live code reproduces the live failure (silence)
    # precisely, confirming this fixture is not vacuous.
    old_way_result = codegen.gate_output_json_key(
        LIVE_PARAPHRASED_SPEC, WRONG_KEY_CODE)  # no 3rd arg -- pre-fix
    assert old_way_result is None, (
        "the old call shape must reproduce the live silent failure; "
        "if this now fires, the fixture is not testing what it claims")


def test_sabotage_fallback_key_reintroduced_would_fail_this_suite():
    # Confirms the fabricated-fallback-key defect, if reintroduced,
    # is exactly what test_no_promise_anywhere_gives_no_fabricated_key
    # would catch: build the block the old way (hardcoded "result_id")
    # and show it fails the same assertion the real function now
    # passes.
    old_style_block = (
        "```python\nimport json\n\nwith open(\"output.json\", \"w\") "
        "as f:\n    json.dump({\"result_id\": some_value}, f)\n```")
    try:
        assert '"result_id"' not in _code_block_body(old_style_block)
        raised = False
    except AssertionError:
        raised = True
    assert raised, "the old fabricated-key shape must fail this check"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
            if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok   ", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL ", t.__name__, "--", e)
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
