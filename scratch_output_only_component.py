"""Validates `intent.output_only_components`, the mechanical backstop for
the live failure: a planner splitting "create an applet; save its id as
applet_id in a new output.json in the same directory as the script" into
a dedicated `output_utils.py` component whose entire SPEC is the
output-saving boilerplate and nothing else.

That component structurally cannot pass `codegen.gate_missing_own_output_
write`: the gate demands a literal write to "output.json" inside the file
whose SPEC asked for one, and the natural way to write a reusable output
helper is a small function taking a path/dict parameter with no literal
filename in its own source. Measured live, it failed the same gate on
every rung -- initial attempts, a raced restart, and a model escalation.

`DECOMPOSE_PROMPT` now tells the planner not to plan this file at all.
This test covers the mechanical backstop that flags it if the planner
does anyway, and confirms it stays silent on real components.
"""
import intent


def _component(name, path, spec, deps=None):
    return intent.Component(name, path, 2, deps or [], spec)


def test_flags_dedicated_output_helper():
    comps = [
        _component("client", "client.py",
                   "Client class with create_applet(name: str) -> dict"),
        _component("output_utils", "output_utils.py",
                   "save its id as applet_id in a new output.json in the "
                   "same directory as the script"),
        _component("main", "main.py",
                   "entry point: create an applet using client.py, then "
                   "call output_utils to save its id",
                   deps=["client", "output_utils"]),
    ]
    flagged = intent.output_only_components(comps)
    assert flagged == ["output_utils"], flagged


def test_does_not_flag_a_real_component_that_also_writes_output():
    comps = [
        _component("main", "main.py",
                   "create an applet by calling POST /api/applets with "
                   "name and description, then save its id as applet_id "
                   "in a new output.json in the same directory as the "
                   "script"),
    ]
    assert intent.output_only_components(comps) == []


def test_does_not_flag_a_pure_reader_with_no_output_json_mention():
    comps = [
        _component("main", "main.py",
                   "read project_id from ../01_create_project/output.json "
                   "and create a task under it"),
    ]
    assert intent.output_only_components(comps) == []


def test_sibling_read_alone_is_not_flagged_even_with_boilerplate_words():
    # Reading a sibling's output.json is stripped before the check, so a
    # spec that ONLY reads a sibling (no local write clause at all) must
    # never be flagged -- it has a real job (reading) even though every
    # remaining word after stripping the sibling ref is empty.
    comps = [
        _component("main", "main.py",
                   "read task_id from ../02_create_task/output.json"),
    ]
    assert intent.output_only_components(comps) == []


def test_empty_spec_is_not_flagged():
    comps = [_component("x", "x.py", "")]
    assert intent.output_only_components(comps) == []


def test_multiple_helpers_all_named():
    comps = [
        _component("output_utils", "output_utils.py",
                   "save its id as applet_id in a new output.json in the "
                   "same directory as the script"),
        _component("result_saver", "result_saver.py",
                   "write the result to output.json under the same key "
                   "the API response uses for it"),
    ]
    flagged = intent.output_only_components(comps)
    assert set(flagged) == {"output_utils", "result_saver"}, flagged


def test_sabotage_stripping_sibling_ref_only_would_miss_pure_write_case():
    # Sanity: the live failure text itself (no sibling ref anywhere) is
    # exactly the shape that must be caught -- prove the regex used to
    # strip sibling refs does not accidentally eat the local clause too.
    spec = ("save its id as applet_id in a new output.json in the same "
            "directory as the script")
    stripped = intent._OUTPUT_HELPER_SIBLING_RE.sub(" ", spec)
    assert "output.json" in stripped.lower()


if __name__ == "__main__":
    test_flags_dedicated_output_helper()
    test_does_not_flag_a_real_component_that_also_writes_output()
    test_does_not_flag_a_pure_reader_with_no_output_json_mention()
    test_sibling_read_alone_is_not_flagged_even_with_boilerplate_words()
    test_empty_spec_is_not_flagged()
    test_multiple_helpers_all_named()
    test_sabotage_stripping_sibling_ref_only_would_miss_pure_write_case()
    print("ok")
