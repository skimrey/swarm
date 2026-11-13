"""Validates `intent.implied_nonempty_counts` and
`mockapi.Mock.zero_problems_for`, added for the live failure where "upload
units to the task" (no number given) produced a real file that posted
`{"units": []}` to the real import endpoint. The call succeeded, every gate
passed, and the task was left with zero units -- `required_counts` had
nothing to check because the request never named a number.
"""
import sys

import intent
import mockapi


def test_implied_nonempty_finds_bare_plural_after_bulk_verb():
    # The exact live request text.
    text = "read task_id from ../02_create_task/output.json; upload units to the task"
    found = intent.implied_nonempty_counts(text)
    assert "units" in found, found


def test_implied_nonempty_excludes_when_a_number_is_given():
    # "upload 3 units" is required_counts' job, not this one's -- if a
    # number is right there, this must not also claim the resource.
    text = "upload 3 units to the task"
    found = intent.implied_nonempty_counts(text)
    assert "units" not in found, found
    # required_counts should be the one that catches it instead.
    assert intent.required_counts(text) == {"units": 3}


def test_implied_nonempty_ignores_unrelated_plurals():
    text = "create a project and save its id in a new output.json"
    found = intent.implied_nonempty_counts(text)
    assert not found, found


def test_implied_nonempty_matches_several_bulk_verbs():
    for verb, plural in [("upload", "units"), ("import", "records"),
                         ("add", "items"), ("submit", "results"),
                         ("attach", "files"), ("push", "changes")]:
        text = "please %s the %s now" % (verb, plural)
        found = intent.implied_nonempty_counts(text)
        assert plural in found, (verb, plural, found)


def _mock_with_calls(calls):
    m = mockapi.Mock.__new__(mockapi.Mock)
    m.calls = calls
    m.lock = __import__("threading").Lock()
    return m


def test_zero_problems_fires_on_empty_payload():
    m = _mock_with_calls([
        {"component": "main.py", "method": "POST",
         "path": "/api/tasks/abc/units/import", "ok": True,
         "body": {"units": []}},
    ])
    problems = m.zero_problems_for("main.py", 0, {"units"})
    assert problems, "expected a problem for an empty units payload"
    assert "units" in problems[0] and "zero" in problems[0], problems


def test_zero_problems_silent_on_real_payload():
    m = _mock_with_calls([
        {"component": "main.py", "method": "POST",
         "path": "/api/tasks/abc/units/import", "ok": True,
         "body": {"units": [{"content": "hi"}]}},
    ])
    problems = m.zero_problems_for("main.py", 0, {"units"})
    assert problems == [], problems


def test_zero_problems_silent_when_resource_never_touched():
    m = _mock_with_calls([
        {"component": "main.py", "method": "POST",
         "path": "/api/projects", "ok": True, "body": {"name": "x"}},
    ])
    problems = m.zero_problems_for("main.py", 0, {"units"})
    assert problems == [], problems


def test_zero_problems_scoped_to_component():
    m = _mock_with_calls([
        {"component": "other.py", "method": "POST",
         "path": "/api/tasks/abc/units/import", "ok": True,
         "body": {"units": []}},
    ])
    problems = m.zero_problems_for("main.py", 0, {"units"})
    assert problems == [], problems


def test_zero_problems_empty_resources_is_a_noop():
    m = _mock_with_calls([
        {"component": "main.py", "method": "POST",
         "path": "/api/tasks/abc/units/import", "ok": True,
         "body": {"units": []}},
    ])
    assert m.zero_problems_for("main.py", 0, set()) == []
    assert m.zero_problems_for("main.py", 0, []) == []


def test_sabotage_without_the_fix_the_live_shape_goes_undetected():
    # Reproduces the pre-fix state: `required_counts` alone sees nothing
    # because the request names no number, so nothing would have caught
    # the empty payload before `implied_nonempty_counts` existed.
    text = "read task_id from ../02_create_task/output.json; upload units to the task"
    assert intent.required_counts(text) == {}, (
        "sanity check: required_counts must find nothing here, or this "
        "sabotage isn't exercising the real gap")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok  ", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "--", e)
    if failed:
        print("%d/%d failed" % (failed, len(tests)))
        sys.exit(1)
    print("%d/%d passed" % (len(tests), len(tests)))


if __name__ == "__main__":
    main()
