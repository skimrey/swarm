"""A 200 from a bulk-import endpoint says the request was accepted. It says
nothing about how many rows were inside it.

Measured live: "write me a script that ... uploads 3 units to it" produced
a task holding 2, and the run reported 3/3 components passing every gate,
because nothing between the writer and the report was reading the body of
a call that had already succeeded.

Three pieces: `intent.required_counts` (pulling a number the request pinned
to a plural resource out of prose), `mockapi.Mock.count_problems_for`
(comparing what was actually sent against it), and the fact that a mismatch
there becomes a real gate failure inside `codegen.generate_file`.
"""
import intent
import mockapi


# --------------------------------------------------------------------------
# extracting a count from the request
# --------------------------------------------------------------------------

def test_the_live_request_yields_a_units_count():
    text = ("write me a script that creates an applet, creates a project, "
            "assigns the applet to the project, creates a test task, and "
            "uploads 3 units to it.")
    assert intent.required_counts(text) == {"units": 3}


def test_a_singular_noun_next_to_a_number_is_not_a_resource_count():
    # "an 3 question survey" -- singular "question", not a plural resource.
    # There is no API resource called "question" for this to ever match
    # against anyway, which is exactly why a false extraction here is
    # harmless even before it reaches the mock.
    text = "lets make it an 3 question survey asking for age"
    assert intent.required_counts(text) == {}


def test_an_unrelated_plural_count_extracts_but_matches_nothing_later():
    # Extracted, because the rule really is just "digit + plural noun" --
    # but this is exactly the case `count_problems_for` below has to leave
    # alone, since no call this run makes will ever mention "minutes".
    text = "wait 3 minutes and then upload 3 units"
    assert intent.required_counts(text) == {"minutes": 3, "units": 3}


def test_no_numbers_means_no_counts():
    assert intent.required_counts("upload some units to the task") == {}


# --------------------------------------------------------------------------
# reading a count back out of a request body
# --------------------------------------------------------------------------

def test_a_bare_list_body_counts_its_own_length():
    assert mockapi._count_in_body([1, 2, 3]) == 3


def test_a_single_list_valued_field_is_found():
    assert mockapi._count_in_body({"units": [{"a": 1}, {"a": 2}]}) == 2


def test_two_list_fields_is_ambiguous_and_reports_nothing():
    # Guessing which of two arrays is "the items" risks a wrong count that
    # is worse than no count at all -- so this stays silent instead.
    body = {"units": [1, 2], "tags": ["x", "y", "z"]}
    assert mockapi._count_in_body(body) is None


def test_a_body_with_no_list_at_all_reports_nothing():
    assert mockapi._count_in_body({"name": "task"}) is None


# --------------------------------------------------------------------------
# the mock-backed check itself
# --------------------------------------------------------------------------

def make_mock():
    # A minimal spec is enough -- Mock.__init__ builds routes from it, but
    # count_problems_for never consults self.spec at all, only self.calls.
    return mockapi.Mock({"paths": {}})


def test_sending_fewer_items_than_asked_is_caught():
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py",
               body={"units": [{"data": {}}, {"data": {}}]})
    problems = mock.count_problems_for("main.py", 0, {"units": 3})
    assert problems, "sending 2 of 3 units was not caught"
    assert "3" in problems[0] and "2" in problems[0]


def test_sending_exactly_the_right_number_passes_clean():
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py",
               body={"units": [{}, {}, {}]})
    assert mock.count_problems_for("main.py", 0, {"units": 3}) == []


def test_counts_across_two_calls_to_the_same_endpoint_are_summed():
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py", body={"units": [{}]})
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py",
               body={"units": [{}, {}]})
    assert mock.count_problems_for("main.py", 0, {"units": 3}) == []


def test_a_resource_never_mentioned_by_any_call_is_left_alone():
    # No call in this log has anything to do with "minutes" -- nothing
    # should be invented about a resource that never had a matching call,
    # even though the caller asked to check for one.
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py", body={"units": [{}]})
    assert mock.count_problems_for(
        "main.py", 0, {"units": 1, "minutes": 3}) == []


def test_a_rejected_call_is_not_counted_towards_the_total():
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=422, ok=False, component="main.py",
               body={"units": [{}, {}, {}]})
    problems = mock.count_problems_for("main.py", 0, {"units": 3})
    assert problems, "a call that never succeeded should not satisfy the count"


def test_another_components_calls_do_not_count_towards_this_one_either_way():
    # Scoped the same way `rejected_for` already is: this cannot blame
    # main.py for a shortfall in traffic it never sent, attributed to a
    # file whose own isolated smoke run has no reason to call an import
    # endpoint at all. Silence here means "not this component's business",
    # not "satisfied".
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="client.py",
               body={"units": [{}]})
    assert mock.count_problems_for("main.py", 0, {"units": 3}) == []


def test_calls_before_the_since_index_are_not_recounted_on_a_later_attempt():
    mock = make_mock()
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py",
               body={"units": [{}, {}, {}]})
    since = len(mock.calls)
    mock.record(method="POST", path="/api/tasks/{task_id}/units/import",
               status=200, ok=True, component="main.py", body={"units": [{}]})
    # Only the second attempt's own call (1 unit) should be judged.
    problems = mock.count_problems_for("main.py", since, {"units": 3})
    assert problems and "1" in problems[0]


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
