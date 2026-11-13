"""Validates the `--retry` fix for a run that never decomposed at all.

Live bug: `01_create_project` failed with "could not decompose; falling
back to a single file" and no `--out`, so `cmd_do` returned 1 *before*
`save_plan` was ever called anywhere in that code path. `team do --retry`
in the same directory then printed "no previous run recorded" -- there
was nothing to retry, forcing the operator to retype the original request
from scratch instead of just running `--retry`.

The fix: that failure branch now calls `save_plan(root, request, [],
..., decompose_failed=True)`, and `--retry`'s consumption side treats an
empty `components` list differently depending on that flag -- re-decompose
from the saved request if `decompose_failed` is set, versus the pre-existing
"nothing failed last time" meaning if it is not.

This test exercises `save_plan`/`load_plan` directly (the real functions,
not stubs) and replicates the exact boolean `team.py` uses to distinguish
the two cases, sabotage-checked against the old (missing) flag.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team
import intent


def total_decompose_failure(saved):
    # Mirrors the exact expression added in team.cmd_do.
    return bool(saved and saved.get("decompose_failed") and not saved.get("components"))


def test_round_trip_records_decompose_failure():
    with tempfile.TemporaryDirectory() as root:
        team.save_plan(root, "create a survey.html and push it", [],
                        check=None, answer_tokens=900, api="maven",
                        decompose_failed=True)
        saved = team.load_plan(root)
        assert saved is not None, "save_plan/load_plan round trip lost the plan"
        assert saved["decompose_failed"] is True
        assert saved["components"] == []
        assert saved["request"] == "create a survey.html and push it"
        assert saved["api"] == "maven"
        assert total_decompose_failure(saved) is True


def test_ordinary_all_succeeded_is_not_a_decompose_failure():
    # The pre-existing, legitimate meaning of an empty failed-components
    # list: every component in the last run already passed. Must NOT be
    # confused with "nothing was ever attempted".
    with tempfile.TemporaryDirectory() as root:
        comp = intent.Component("main", "main.py", 4, [], "do the thing")
        comp.ok = True
        comp.error = ""
        team.save_plan(root, "do the thing", [comp], check=None,
                        answer_tokens=900, api=None)
        saved = team.load_plan(root)
        assert saved["decompose_failed"] is False
        assert len(saved["components"]) == 1
        assert total_decompose_failure(saved) is False


def test_a_real_failed_component_is_not_a_decompose_failure():
    with tempfile.TemporaryDirectory() as root:
        comp = intent.Component("main", "main.py", 4, [], "do the thing")
        comp.ok = False
        comp.error = "boom"
        team.save_plan(root, "do the thing", [comp], check=None,
                        answer_tokens=900, api=None)
        saved = team.load_plan(root)
        assert total_decompose_failure(saved) is False


def test_no_saved_plan_at_all_is_not_a_decompose_failure():
    assert total_decompose_failure(None) is False
    assert total_decompose_failure({}) is False


def test_sabotage_missing_flag_reads_as_no_previous_failure():
    # Reproduces the exact pre-fix payload shape (no `decompose_failed`
    # key at all, since old code never wrote one) to prove the new check
    # is what tells the two cases apart, not something incidental. An
    # old-format save with zero components -- which could only mean the
    # legitimate "all succeeded" case pre-fix, since a real total-failure
    # save never existed at all before this fix -- must not spuriously
    # trigger the new decompose-retry path.
    with tempfile.TemporaryDirectory() as root:
        payload = {
            "root": os.path.abspath(root),
            "request": "do the thing",
            "check": None,
            "answer_tokens": 900,
            "api": None,
            "when": 0,
            "contract": "",
            "components": [],
        }
        os.makedirs(team.PLAN_DIR, exist_ok=True)
        with open(team._plan_path(root), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        saved = team.load_plan(root)
        assert total_decompose_failure(saved) is False, (
            "an old-format saved plan with no decompose_failed key must "
            "not be misread as a total decompose failure")


def run():
    test_round_trip_records_decompose_failure()
    test_ordinary_all_succeeded_is_not_a_decompose_failure()
    test_a_real_failed_component_is_not_a_decompose_failure()
    test_no_saved_plan_at_all_is_not_a_decompose_failure()
    test_sabotage_missing_flag_reads_as_no_previous_failure()
    print("scratch_retry_decompose_failure: all checks passed")


if __name__ == "__main__":
    run()
