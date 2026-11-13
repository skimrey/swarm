"""codegen.own_output_write_block / sibling_output_read_block.

A component that repeatedly failed `gate_missing_own_output_write` --
four rungs running, including two genuinely fresh rolls and an escalation
to a stronger model -- with the always-on `context/policy-output-json.md`
policy card already in its system prompt on every single one of them.
Prose describing the convention was not the missing piece; a literal
example built from this component's own real key name is the fix already
proven once for the identical class of problem (`openapi.
connection_setup_block`, for auth). These two functions are the output.json
side of the same move.

Run directly: `python3 scratch_output_json_boilerplate.py`.
"""
import codegen


LIVE_SPEC = (
    "read project_id from ../01_create_project/output.json; create a "
    "task in that project; save its id as task_id in a new output.json "
    "in the same directory as the script")

NO_HANDOFF_SPEC = (
    "print a friendly greeting to stdout; this is the last step in its "
    "chain and nothing downstream reads anything from it")

PURE_CONSUMER_SPEC = (
    "read project_id from ../01_create_project/output.json and print a "
    "link to the project; nothing here needs to be saved for a later step")

MULTI_KEY_SPEC = (
    "read project_id from ../01_create_project/output.json and read "
    "applet_id from ../03_create_applet/output.json, then assign the "
    "applet to the project")


def test_live_bug_gets_a_real_write_example():
    block = codegen.own_output_write_block(LIVE_SPEC)
    assert block, "the live spec promises a local write and got nothing"
    assert 'json.dump({"task_id": task_id}, f)' in block, block
    assert 'open("output.json", "w")' in block
    print("  own_output_write_block: the live task_id shape is rendered "
         "literally, not generically")


def test_live_bug_also_gets_a_real_read_example():
    block = codegen.sibling_output_read_block(LIVE_SPEC)
    assert block, "the live spec names a sibling read and got nothing"
    assert 'open("../01_create_project/output.json")' in block, block
    assert 'json.load(f)["project_id"]' in block, block
    print("  sibling_output_read_block: the live project_id shape is "
         "rendered literally, from the real path and key")


def test_a_component_with_no_handoff_gets_neither_block():
    assert codegen.own_output_write_block(NO_HANDOFF_SPEC) == "", (
        "a component with nothing to hand forward must not be shown "
        "output-writing code it has no reason to write")
    assert codegen.sibling_output_read_block(NO_HANDOFF_SPEC) == ""
    print("  a component with no hand-off gets neither boilerplate block")


def test_a_pure_consumer_gets_the_read_block_and_not_the_write_block():
    # Mirrors `spec_requires_own_output_write`'s own documented case: a
    # step that only reads a sibling's file and has nothing of its own
    # to produce must not be pushed toward writing one anyway.
    assert codegen.own_output_write_block(PURE_CONSUMER_SPEC) == "", (
        "a pure consumer must not be shown local-write boilerplate")
    read_block = codegen.sibling_output_read_block(PURE_CONSUMER_SPEC)
    assert 'json.load(f)["project_id"]' in read_block, read_block
    print("  a pure consumer gets only the read block, never the write one")


def test_multiple_sibling_reads_each_get_their_own_real_line():
    block = codegen.sibling_output_read_block(MULTI_KEY_SPEC)
    assert 'open("../01_create_project/output.json")' in block, block
    assert 'json.load(f)["project_id"]' in block, block
    assert 'open("../03_create_applet/output.json")' in block, block
    assert 'json.load(f)["applet_id"]' in block, block
    print("  two sibling reads in one spec each get their own real line, "
         "not just the first")


def test_a_spec_with_no_recoverable_key_falls_back_but_still_writes_something():
    # `_promised_output_json_keys` only recognizes this project's own two
    # fixed phrasings (see `gate_output_json_key`'s docstring). A spec
    # that promises a write in free-form language recognized by
    # `spec_requires_own_output_write` (which only checks for the bare
    # word "output.json") but not by the stricter key-recovery regex
    # must still get a block, with a generic placeholder key rather than
    # silently producing nothing.
    vague = "create a widget and remember to write output.json for later"
    block = codegen.own_output_write_block(vague)
    assert block, "a write promise with no recoverable key must still render something"
    assert "json.dump(" in block and "output.json" in block, block
    print("  a write promise with no recoverable key still renders a "
         "generic (not blank) example")


def test_sabotage_neither_existing_gate_supplies_this_boilerplate():
    # Confirms this is genuinely new context, not a duplicate of
    # something `gate_missing_own_output_write`/`gate_output_json_key`
    # already hand to the writer. Both gates only ever fire *after* a
    # bad file has already been generated; they never appear in the
    # first-attempt GENERATE_PROMPT the way these two blocks now do.
    assert codegen.gate_missing_own_output_write(LIVE_SPEC, "x = 1\n") is not None, (
        "sanity: the live spec must still trip the existing gate on a "
        "file with no write at all -- proving the gate alone did not "
        "stop this failure from recurring four times live")
    print("  sabotage: the existing gate alone (no boilerplate) is "
         "exactly the state that let the live failure recur four times")


def main():
    test_live_bug_gets_a_real_write_example()
    test_live_bug_also_gets_a_real_read_example()
    test_a_component_with_no_handoff_gets_neither_block()
    test_a_pure_consumer_gets_the_read_block_and_not_the_write_block()
    test_multiple_sibling_reads_each_get_their_own_real_line()
    test_a_spec_with_no_recoverable_key_falls_back_but_still_writes_something()
    test_sabotage_neither_existing_gate_supplies_this_boilerplate()
    print("OK")


if __name__ == "__main__":
    main()
