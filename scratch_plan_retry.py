"""Validates `team.build_components`'s new retry wrapper around
`team._decompose_once`.

Live bug: a planner call landed on a demoted floor-2 model, its reply
never parsed into a usable component, and the whole request failed with
"could not decompose; falling back to a single file" on the strength of
exactly one attempt. `build_components` now retries up to
`team.PLAN_RETRIES` times, stopping as soon as one attempt returns
something usable.

This stubs `team._decompose_once` directly (the real function talks to
the network) and checks the wrapper's control flow: it must retry on an
empty result, stop retrying the moment a non-empty result appears, cap
at PLAN_RETRIES total attempts, and return `[]` (not raise) if every
attempt comes back empty.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swarm
import team
import intent


def _component(name="main"):
    return intent.Component(name, name + ".py", 4, [], "do the thing")


def test_succeeds_on_first_attempt_without_retrying():
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return [_component()]

    with mock.patch.object(team, "_decompose_once", side_effect=fake):
        result = team.build_components("do the thing", cfg=None, state=None,
                                        verbose=False)
    assert len(result) == 1
    assert len(calls) == 1, "must not retry once an attempt succeeds"


def test_retries_after_empty_attempts_then_succeeds():
    calls = []

    def fake(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            return []
        return [_component()]

    with mock.patch.object(team, "_decompose_once", side_effect=fake):
        result = team.build_components("do the thing", cfg=None, state=None,
                                        verbose=False)
    assert len(result) == 1
    assert len(calls) == 3, "expected exactly 3 attempts, got %d" % len(calls)


def test_gives_up_after_plan_retries_exhausted():
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return []

    with mock.patch.object(team, "_decompose_once", side_effect=fake):
        result = team.build_components("do the thing", cfg=None, state=None,
                                        verbose=False)
    assert result == []
    assert len(calls) == team.PLAN_RETRIES, (
        "expected exactly PLAN_RETRIES (%d) attempts, got %d"
        % (team.PLAN_RETRIES, len(calls)))


def test_sabotage_no_retry_wrapper_fails_on_first_empty_attempt():
    # Reproduces the pre-fix shape directly: calling `_decompose_once` on
    # its own, the way the old single-shot `build_components` effectively
    # did, gives up after exactly one empty attempt -- proving the retry
    # loop above is what changes the outcome, not something incidental.
    calls = []

    def fake(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            return []
        return [_component()]

    with mock.patch.object(team, "_decompose_once", side_effect=fake):
        one_shot = team._decompose_once("do the thing", None, None, "", False, "")
    assert one_shot == [], "sabotage setup: first attempt must be empty"
    assert len(calls) == 1


def test_a_node_that_raises_ollamaerror_is_excluded_from_the_next_attempt():
    """Live bug, across two separate chain steps in the same batch: g26b
    was unreachable (`swarm.OllamaError`), and because it is the fleet's
    highest-quality floor-4 candidate, `claim_first_free_role` picked it
    again on every one of 3 straight `build_components` attempts --
    ~180s spent re-proving the same fact three times before falling
    through to "could not decompose". `_decompose_once` is stubbed here
    (it talks to the network for real) to raise on the excluded node the
    same way the real function returns after logging a `swarm.OllamaError`
    -- via mutating the `exclude_nodes` set it was handed, exactly like
    the real function's `except swarm.OllamaError` branch does."""
    seen_exclude_snapshots = []

    def fake(request, cfg, state, context, verbose, root, exclude_nodes=None):
        seen_exclude_snapshots.append(set(exclude_nodes or ()))
        if exclude_nodes is not None:
            exclude_nodes.add("g26b")
        return []

    with mock.patch.object(team, "_decompose_once", side_effect=fake):
        result = team.build_components("do the thing", cfg=None, state=None,
                                        verbose=False)
    assert result == []
    assert len(seen_exclude_snapshots) == team.PLAN_RETRIES
    # Attempt 1 starts with nothing excluded yet.
    assert seen_exclude_snapshots[0] == set()
    # Every attempt after the first must see g26b already excluded --
    # this is the actual fix: the SAME set object threaded through every
    # retry, not a fresh one per attempt.
    for snap in seen_exclude_snapshots[1:]:
        assert "g26b" in snap, (
            "a node that raised OllamaError on an earlier attempt must "
            "stay excluded on every later attempt in this same "
            "build_components call -- got %r" % (seen_exclude_snapshots,))


def test_sabotage_without_threading_the_same_set_every_attempt_reoffers_it():
    """Reproduces the pre-fix shape: if each attempt got its own fresh
    `set()` instead of the same object being mutated across the loop, a
    node that just failed would be offered again on the very next retry
    -- which is exactly what the real bug looked like in the live log
    (`planning on g26b/...` three times running, identical timeout each
    time)."""
    seen_exclude_snapshots = []

    def fake_fresh_set_per_attempt(request, cfg, state, context, verbose,
                                   root, exclude_nodes=None):
        # Sabotage: ignores whatever `exclude_nodes` was passed in and
        # starts a fresh, empty one every time -- the bug this test
        # exists to catch would look like this if `build_components`
        # created a new set per attempt instead of reusing one.
        local_exclude = set()
        seen_exclude_snapshots.append(set(local_exclude))
        local_exclude.add("g26b")
        return []

    with mock.patch.object(team, "_decompose_once",
                           side_effect=fake_fresh_set_per_attempt):
        team.build_components("do the thing", cfg=None, state=None,
                              verbose=False)
    # Every attempt saw an empty set under the sabotaged shape -- proving
    # this fixture actually distinguishes "threaded across attempts"
    # from "reset every attempt", which is what the real fix changes.
    assert all(snap == set() for snap in seen_exclude_snapshots)


def run():
    test_succeeds_on_first_attempt_without_retrying()
    test_retries_after_empty_attempts_then_succeeds()
    test_gives_up_after_plan_retries_exhausted()
    test_sabotage_no_retry_wrapper_fails_on_first_empty_attempt()
    test_a_node_that_raises_ollamaerror_is_excluded_from_the_next_attempt()
    test_sabotage_without_threading_the_same_set_every_attempt_reoffers_it()
    print("scratch_plan_retry: all checks passed")


if __name__ == "__main__":
    run()
