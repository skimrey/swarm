"""Validates `codegen.gate_output_json_key`, the mechanical check that a
producer's own `output.json` write actually uses the key its own request
promised.

This exists because every mechanical gate this project had built before
it -- `gate_own_output_path` (right directory), `gate_output_json_shape`
(a dict, not a bare scalar), `gate_missing_own_output_write` (a write
exists at all) -- passed a live producer that created a real project,
printed `project_id: <uuid>`, and wrote `output.json` as `{"id": "<uuid>"}`.
The consumer's own request, independently and correctly, read `project_id
from ../01_create_project/output.json`; `intent.chain_output_json_
problems` even confirmed the two requests *agreed in text* that the key
was `project_id`. The run still failed with `KeyError: 'project_id'`,
because nothing had ever compared what the code actually wrote against
what the request actually promised -- every existing gate answered a
different question.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen


LIVE_SPEC = ("create a project; save its id as project_id in this "
             "directory's own output.json")

AUTOFIX_SPEC = ("create a project and print its id; save project_id to "
                "output.json as {\"project_id\": ...}")

WRONG_KEY_CODE = """
import json

def main():
    project_id = "abc-123"
    print("project_id:", project_id)
    with open("output.json", "w") as f:
        json.dump({"id": project_id}, f)

if __name__ == "__main__":
    main()
"""

RIGHT_KEY_CODE = WRONG_KEY_CODE.replace(
    '{"id": project_id}', '{"project_id": project_id}')


def test_wrong_key_is_caught_against_the_live_failure_shape():
    result = codegen.gate_output_json_key(LIVE_SPEC, WRONG_KEY_CODE)
    assert result is not None, "must catch the exact live defect"
    assert "project_id" in result
    assert "id" in result


def test_correct_key_passes_clean():
    assert codegen.gate_output_json_key(LIVE_SPEC, RIGHT_KEY_CODE) is None


def test_autofix_generated_promise_phrasing_is_also_recognised():
    """`intent.autofix_chain_output_json` appends a differently-worded
    promise ('save X to output.json as {"X": ...}') than the
    hand-written LOCAL.md phrasing -- both have to be recognised, since
    a component built from an autofixed request is exactly as likely to
    drift as one built from a hand-written one."""
    assert codegen.gate_output_json_key(
        AUTOFIX_SPEC, WRONG_KEY_CODE) is not None
    assert codegen.gate_output_json_key(
        AUTOFIX_SPEC, RIGHT_KEY_CODE) is None


def test_no_promise_in_the_request_means_nothing_to_check():
    """A request naming no concrete key ("save the id to output.json")
    gives this gate nothing to compare against -- reporting a mismatch
    here would be a guess, which this project has refused to make
    everywhere else in this file."""
    vague_spec = "create a project; save the id to output.json"
    assert codegen.gate_output_json_key(vague_spec, WRONG_KEY_CODE) is None


def test_no_local_output_write_is_left_to_a_different_gate():
    """A file with no local output.json write at all is
    `gate_missing_own_output_write`'s problem, not this one -- this gate
    must stay silent rather than double-report the same underlying gap
    with a confusing message about a write that does not exist."""
    no_write_code = "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
    assert codegen.gate_output_json_key(LIVE_SPEC, no_write_code) is None


def test_dynamically_built_dict_is_left_alone():
    """A dict built by assignment (`data = {}; data[key] = value`)
    cannot be read into a fixed set of keys by this scan -- reporting a
    mismatch here would risk flagging a file that is actually correct,
    which this project has decided costs more than the bug it might
    catch."""
    dynamic_code = """
import json

def main():
    project_id = "abc-123"
    data = {}
    data["project_id"] = project_id
    with open("output.json", "w") as f:
        json.dump(data, f)

if __name__ == "__main__":
    main()
"""
    assert codegen.gate_output_json_key(LIVE_SPEC, dynamic_code) is None


def test_dict_call_form_is_understood_the_same_as_a_literal():
    dict_call_code = """
import json

def main():
    project_id = "abc-123"
    with open("output.json", "w") as f:
        json.dump(dict(id=project_id), f)

if __name__ == "__main__":
    main()
"""
    assert codegen.gate_output_json_key(
        LIVE_SPEC, dict_call_code) is not None
    dict_call_right = dict_call_code.replace(
        "dict(id=project_id)", "dict(project_id=project_id)")
    assert codegen.gate_output_json_key(LIVE_SPEC, dict_call_right) is None


def test_json_dumps_then_write_form_is_also_checked():
    """The write-half of `gate_output_json_shape`'s own coverage --
    `f.write(json.dumps({...}))` instead of `json.dump({...}, f)` -- must
    be checked identically, since a writer choosing one spelling over
    the other is not a reason for this gate to have an opinion about one
    and not the other."""
    dumps_code = """
import json

def main():
    project_id = "abc-123"
    with open("output.json", "w") as f:
        f.write(json.dumps({"id": project_id}))

if __name__ == "__main__":
    main()
"""
    assert codegen.gate_output_json_key(LIVE_SPEC, dumps_code) is not None
    dumps_right = dumps_code.replace('"id"', '"project_id"')
    assert codegen.gate_output_json_key(LIVE_SPEC, dumps_right) is None


def test_a_second_extra_key_alongside_the_right_one_is_not_flagged():
    """The request only pins down the one key it names -- a file that
    also writes other, unpromised keys alongside the correct one is not
    a defect this gate exists to catch."""
    extra_key_code = """
import json

def main():
    project_id = "abc-123"
    with open("output.json", "w") as f:
        json.dump({"project_id": project_id, "created_at": "now"}, f)

if __name__ == "__main__":
    main()
"""
    assert codegen.gate_output_json_key(LIVE_SPEC, extra_key_code) is None


def test_sabotage_a_check_that_never_reads_the_spec_misses_everything():
    """Proves the request-reading half is load-bearing: a version of
    this gate that only inspected the code (ignoring `spec` entirely,
    e.g. by always treating the promised-key list as empty) would never
    catch the live defect this exists for."""
    def sabotaged(spec, code):
        return None  # never reads spec at all -- the pre-fix world

    assert sabotaged(LIVE_SPEC, WRONG_KEY_CODE) is None, (
        "sabotage fixture should never catch anything")
    real_result = codegen.gate_output_json_key(LIVE_SPEC, WRONG_KEY_CODE)
    assert real_result is not None, (
        "the real gate must catch what the sabotaged version cannot")


def run():
    test_wrong_key_is_caught_against_the_live_failure_shape()
    test_correct_key_passes_clean()
    test_autofix_generated_promise_phrasing_is_also_recognised()
    test_no_promise_in_the_request_means_nothing_to_check()
    test_no_local_output_write_is_left_to_a_different_gate()
    test_dynamically_built_dict_is_left_alone()
    test_dict_call_form_is_understood_the_same_as_a_literal()
    test_json_dumps_then_write_form_is_also_checked()
    test_a_second_extra_key_alongside_the_right_one_is_not_flagged()
    test_sabotage_a_check_that_never_reads_the_spec_misses_everything()
    print("scratch_output_json_key: all checks passed")


if __name__ == "__main__":
    run()
