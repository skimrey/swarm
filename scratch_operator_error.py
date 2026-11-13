"""--retry --file --error puts an already-passed component back to work.

A gate only ever proves the mock accepted a call, or typed input did not
crash the file -- it cannot see a real API rejecting a real request, or
real input the operator actually typed. When that happens the saved plan
has nothing failed to retry, and `team.apply_operator_error` is what lets
the operator hand over the log from actually running the thing instead.
"""
import intent
import team


def plan(*specs):
    """specs: (name, path, ok) tuples."""
    out = []
    for name, path, ok in specs:
        c = intent.Component(name, path, 2, [], "do the thing")
        c.ok = ok
        c.error = ""
        c.code = "the current, working content of %s\n" % path if ok else ""
        out.append(c)
    return out


def test_a_passed_component_is_flipped_and_given_the_error():
    restored = plan(("main", "main.py", True), ("client", "client.py", True))
    err, additions = team.apply_operator_error(
        restored, "main.py", "  KeyError: 'foo'  \n")
    assert err is None, err
    assert restored[0].ok is False
    assert restored[0].error == "KeyError: 'foo'", repr(restored[0].error)
    assert restored[1].ok is True, "the untouched component is left alone"
    print("  the named component is flipped; the other is untouched")


def test_its_current_content_is_returned_for_preservation():
    restored = plan(("main", "main.py", True))
    err, additions = team.apply_operator_error(restored, "main.py", "boom")
    assert err is None, err
    assert additions == {"main.py": "the current, working content of main.py\n"}, additions
    print("  the file's real content comes back for the edit-in-place path")


def test_a_name_not_in_the_plan_is_refused_not_silently_skipped():
    restored = plan(("main", "main.py", True))
    err, additions = team.apply_operator_error(restored, "ghost.py", "boom")
    assert err is not None and "ghost.py" in err, err
    assert additions == {}
    assert restored[0].ok is True, "nothing should change on a refusal"
    print("  a made-up filename is refused, not treated as already handled")


def test_a_component_with_no_code_yet_contributes_no_addition():
    """A component that never actually wrote a file (e.g. it was refused
    for size) has nothing to preserve."""
    restored = plan(("main", "main.py", True))
    restored[0].code = ""
    err, additions = team.apply_operator_error(restored, "main.py", "boom")
    assert err is None, err
    assert additions == {}, additions
    print("  no prior code means no preservation entry, not a crash")


def test_two_components_writing_the_same_path_are_both_flipped():
    """Defensive: matching is by path, and a plan should not have two
    components claim one path, but if it somehow did, both get fixed
    rather than one silently staying stale."""
    restored = plan(("a", "shared.py", True), ("b", "shared.py", True))
    err, additions = team.apply_operator_error(restored, "shared.py", "boom")
    assert err is None, err
    assert all(not c.ok for c in restored)
    print("  every component claiming the named path is flipped")


def main():
    test_a_passed_component_is_flipped_and_given_the_error()
    test_its_current_content_is_returned_for_preservation()
    test_a_name_not_in_the_plan_is_refused_not_silently_skipped()
    test_a_component_with_no_code_yet_contributes_no_addition()
    test_two_components_writing_the_same_path_are_both_flipped()
    print("OK")


if __name__ == "__main__":
    main()
