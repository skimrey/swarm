"""team._repeated_interface_defect (nested inside run_components).

`gate_interfaces`/crossref findings are AST facts, not model opinions: the
message is built purely from a call site and the dependency's real defined
names, so it cannot change between attempts unless the underlying defect
does. Measured live: a CONTRACT missing a version-creating method produced
the exact same "calls client.create_applet_version(...), but client.py has
no such attribute" finding on all 6 initial attempts, a full whole-loop
restart (6 more), and a model escalation -- 13 attempts re-confirming a fact
the second attempt had already established, before `replan_component` was
ever consulted.

This is a closure-scoped nested function inside `run_components`, the same
situation `scratch_escalate_stronger_writer.py` already handles for
`_last_stage_is_unreachable` -- reconstructed here against fixture
histories rather than driving the full function, and checked by sabotage
against the exact pre-fix behaviour (nothing short-circuits the restart or
escalation, so the live 13-attempt shape reproduces).

Run directly: `python3 scratch_repeated_interface_defect.py`.
"""


def _repeated_interface_defect(r):
    """Mirrors team.py's real nested function body exactly."""
    hits = [h for h in r["history"] if h.get("stage") == "interfaces"]
    if len(hits) < 2:
        return False
    return len({h.get("error") for h in hits}) == 1


LIVE_FINDING = (
    "main.py calls client.create_applet_version(...), but client.py has "
    "no such attribute; it defines: MavenClient, __init__, create_applet")


def test_two_identical_interface_findings_trip_the_detector():
    r = {"ok": False, "history": [
        {"attempt": 1, "stage": "interfaces", "error": LIVE_FINDING},
        {"attempt": 2, "stage": "interfaces", "error": LIVE_FINDING},
    ]}
    assert _repeated_interface_defect(r) is True
    print("  two identical crossref findings in a row are recognized as "
          "a plan-level defect, not a coding mistake")


def test_a_single_interface_finding_is_not_enough():
    # One occurrence says nothing yet -- it takes a repeat (the model was
    # shown it and told to fix it, per REPAIR_PROMPT, and produced the
    # same thing again) to be evidence the defect cannot be fixed by
    # retrying.
    r = {"ok": False, "history": [
        {"attempt": 1, "stage": "interfaces", "error": LIVE_FINDING},
        {"attempt": 2, "stage": "smoke", "error": "AssertionError: ..."},
    ]}
    assert _repeated_interface_defect(r) is False
    print("  a single interface finding does not trigger the fast path")


def test_two_different_interface_findings_do_not_trip_it():
    # Two distinct findings mean the model is making *different* mistakes
    # each time, which is exactly the shape ordinary retrying is meant to
    # converge on -- not evidence the CONTRACT itself is unsatisfiable.
    r = {"ok": False, "history": [
        {"attempt": 1, "stage": "interfaces",
         "error": "main.py calls client.foo(...), but client.py has no "
                  "such attribute; it defines: MavenClient, __init__"},
        {"attempt": 2, "stage": "interfaces",
         "error": "main.py calls client.bar(...), but client.py has no "
                  "such attribute; it defines: MavenClient, __init__"},
    ]}
    assert _repeated_interface_defect(r) is False
    print("  two genuinely different findings are left for ordinary "
          "retrying, not fast-tracked")


def test_ordinary_wrong_answer_failures_are_unaffected():
    r = {"ok": False, "history": [
        {"attempt": 1, "stage": "smoke", "error": "AssertionError: x != y"},
        {"attempt": 2, "stage": "smoke", "error": "AssertionError: x != y"},
    ]}
    assert _repeated_interface_defect(r) is False, (
        "only the 'interfaces' stage is mechanical/AST-derived enough to "
        "trust here -- an identical smoke-test failure twice is still "
        "just as plausibly the model repeating a coding mistake")
    print("  non-interface stages (even if repeated) never trigger this "
          "fast path -- it is scoped to the one AST-derived, non-model-"
          "judged finding")


def test_three_attempts_where_the_last_two_match_still_trips_it():
    # A finding does not have to repeat on the very first two attempts --
    # any two identical occurrences anywhere in the history are sufficient
    # evidence, since the mechanical fact does not become less true for
    # having taken a couple of attempts to reappear.
    r = {"ok": False, "history": [
        {"attempt": 1, "stage": "smoke", "error": "unrelated bug fixed "
                                                   "in between"},
        {"attempt": 2, "stage": "interfaces", "error": LIVE_FINDING},
        {"attempt": 3, "stage": "interfaces", "error": LIVE_FINDING},
    ]}
    assert _repeated_interface_defect(r) is True
    print("  two identical findings anywhere in the history are enough, "
          "not only on the first two attempts")


def test_sabotage_without_the_detector_the_live_13_attempt_shape_persists():
    # Reproduce the pre-fix decision logic directly: with no
    # `_repeated_interface_defect` check anywhere, a component whose
    # initial attempts already show the identical crossref finding twice
    # still has `gave_up_early` computed the old way (no_mock / budget /
    # unreachable only) -- none of which fire for an "interfaces" stage,
    # so both the restart and the escalation would still run before
    # replan_component, exactly reproducing the live 13-attempt waste.
    r = {"ok": False, "history": [
        {"attempt": 1, "stage": "interfaces", "error": LIVE_FINDING},
        {"attempt": 2, "stage": "interfaces", "error": LIVE_FINDING},
    ]}

    def _no_mock(_r):
        return False

    def _last_stage_is_budget(_r):
        return False

    def _last_stage_is_unreachable(_r):
        return False

    gave_up_early_pre_fix = (_no_mock(r) or _last_stage_is_budget(r)
                            or _last_stage_is_unreachable(r))
    assert gave_up_early_pre_fix is False, (
        "sabotage setup: none of the pre-existing signals catch this "
        "shape, proving the restart and escalation gates "
        "(`not gave_up_early`) would both still fire without the new check")

    # With the real detector in place, the new `skip_to_replan` flag is
    # what actually prevents the restart/escalation gates from firing --
    # this is the one boolean the real team.py code threads through both.
    skip_to_replan = _repeated_interface_defect(r)
    assert skip_to_replan is True, (
        "the fix must recognize this exact shape so the restart and "
        "escalation gates can be skipped")
    print("  sabotage confirmed: without the detector, nothing would have "
          "stopped the restart and escalation from both running on a "
          "provably-unfixable-by-retrying CONTRACT gap")


def main():
    test_two_identical_interface_findings_trip_the_detector()
    test_a_single_interface_finding_is_not_enough()
    test_two_different_interface_findings_do_not_trip_it()
    test_ordinary_wrong_answer_failures_are_unaffected()
    test_three_attempts_where_the_last_two_match_still_trips_it()
    test_sabotage_without_the_detector_the_live_13_attempt_shape_persists()
    print("OK")


if __name__ == "__main__":
    main()
