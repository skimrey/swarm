"""An explicit print format, demonstrated by example in the request, has to
actually show up in what the generated file prints -- not just be asked
for in English and then quietly dropped.

Measured live: "make sure it prints the task id like Project Link: Task
ID: Project ID and saves them a json file" produced a file that printed
four bare UUIDs, twice, with no labels anywhere. Every other gate passed,
because nothing crashed.

Three pieces: `codegen.output_labels` (extracting the labels from prose),
`codegen.gate_smoke` (refusing a file whose actual output does not carry
them), and `team`'s own fixup (making sure the literal words reach the
entry point's SPEC even if the planner's paraphrase dropped them).
"""
import os
import shutil
import tempfile

import codegen
import intent
import team


def run_smoke(source, required_labels=()):
    root = tempfile.mkdtemp(prefix="labelcase-")
    path = os.path.join(root, "main.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    try:
        return codegen.gate_smoke(path, root, required_labels=required_labels)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def test_the_live_request_yields_all_three_labels_despite_the_dropped_colon():
    text = ("make sure it prints the task id like Project Link: Task ID: "
            "Project ID and saves them a json file.")
    assert codegen.output_labels(text) == [
        "Project Link:", "Task ID:", "Project ID:"]


def test_a_fully_punctuated_chain_needs_no_recovery():
    text = "print it like Project Link: Task ID: Project ID: Applet ID:."
    assert codegen.output_labels(text) == [
        "Project Link:", "Task ID:", "Project ID:", "Applet ID:"]


def test_an_unrelated_colon_in_prose_is_not_mistaken_for_a_format_example():
    text = "Note: this needs a key, and the applet should be html"
    assert codegen.output_labels(text) == []


def test_no_like_or_as_trigger_means_nothing_is_extracted():
    text = "the applet should be html and print the survey results"
    assert codegen.output_labels(text) == []


def test_a_lone_titlecase_phrase_with_no_chain_is_not_recovered():
    # There is nothing here to disambiguate a proper noun from a dropped
    # label -- recovery only applies to the tail of an actual chain.
    text = "print it like Project Name"
    assert codegen.output_labels(text) == []


def test_a_label_repeated_within_one_clause_is_not_duplicated():
    text = "prints it like Project Link: Project Link: Task ID: id"
    assert codegen.output_labels(text) == ["Project Link:", "Task ID:"]


# --------------------------------------------------------------------------
# the gate itself
# --------------------------------------------------------------------------

UNLABELLED = '''
def main():
    print("75b00a91-b889-4122-82e7-5e376d7bd140")
    print("https://d30ca7kumywvjf.cloudfront.net")


if __name__ == "__main__":
    main()
'''

LABELLED = '''
def main():
    print("Project Link: https://d30ca7kumywvjf.cloudfront.net")
    print("Task ID: 1d1dad26-ce26-4129-8973-9a0bb266ac46")


if __name__ == "__main__":
    main()
'''


def test_bare_ids_with_no_labels_fail_the_gate_when_labels_are_required():
    failure = run_smoke(UNLABELLED, required_labels=["Project Link:",
                                                     "Task ID:"])
    assert failure is not None, "missing labels were not caught"
    assert "Project Link:" in failure and "Task ID:" in failure


def test_the_same_bare_output_is_accepted_when_nothing_was_asked_for():
    # The gate must not invent a requirement no request ever stated.
    assert run_smoke(UNLABELLED, required_labels=()) is None


def test_correctly_labelled_output_passes():
    assert run_smoke(LABELLED, required_labels=["Project Link:",
                                                "Task ID:"]) is None


def test_partial_compliance_still_names_only_what_is_missing():
    failure = run_smoke(LABELLED, required_labels=["Project Link:",
                                                    "Applet ID:"])
    assert failure is not None
    assert "Applet ID:" in failure
    assert "'Project Link:'" not in failure  # this one was not missing


# --------------------------------------------------------------------------
# the fixup that gets the literal words into the entry point's SPEC
# --------------------------------------------------------------------------

def test_a_paraphrased_spec_is_reinforced_with_the_literal_labels():
    request = ("write a script that creates a project and prints the "
               "result like Project Link: Task ID: Project ID and saves "
               "it to a json file")
    components = [intent.Component(
        "main.py", "main.py", 2, [],
        # The planner's own paraphrase -- correct in spirit, but it never
        # repeats the operator's exact words, which is exactly what
        # happened live.
        "Creates the project via the client and prints a short summary "
        "of what was created, then writes the results to disk.")]
    before = components[0].spec
    labels = codegen.output_labels(request)
    assert not all(lbl in before for lbl in labels)

    fixed = team.reinforce_output_labels(components, request)
    assert fixed == "main.py"
    entry = components[0]
    assert "Project Link:" in entry.spec
    assert "Task ID:" in entry.spec
    assert "Project ID:" in entry.spec
    # Not just present as a substring -- has to round-trip through the same
    # extractor `gate_smoke` will use, or the fixup only ever looked like
    # protection. An earlier wording of the appended sentence failed
    # exactly this assertion while still passing the substring checks
    # above.
    assert codegen.output_labels(entry.spec) == labels
    print("  a paraphrased SPEC is reinforced with the operator's own "
          "words, in a form the gate can actually read back")


def test_a_spec_that_already_repeats_the_labels_is_left_alone():
    request = "print it like Task ID: Project ID"
    components = [intent.Component(
        "main.py", "main.py", 2, [],
        "prints the result as Task ID: <id> Project ID: <id>")]
    original = components[0].spec
    assert team.reinforce_output_labels(components, request) is None
    assert components[0].spec == original, (
        "a spec that already has the labels was touched")


def test_a_request_with_no_demonstrated_format_changes_nothing():
    components = [intent.Component(
        "main.py", "main.py", 2, [], "creates a project and prints it")]
    original = components[0].spec
    assert team.reinforce_output_labels(
        components, "make a project and print it") is None
    assert components[0].spec == original


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print("  ok   %s" % t.__name__)
        except AssertionError as exc:
            print("  FAIL %s -- %s" % (t.__name__, exc))
            failures.append(t.__name__)
    if failures:
        raise SystemExit("%d failure(s): %s" % (len(failures), failures))
    print("OK")


if __name__ == "__main__":
    main()
