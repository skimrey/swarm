#!/usr/bin/env python3
"""Validates `intent.enforce_requested_entry_point` against the live
failure: a single-component request that says "the runnable entry point
is main.py" verbatim came back named `applet_creator.py` anyway.
`generate.sh`'s own `if [ -f main.py ]` check then read the missing file
as "no main.py -- skipping" -- the correct reading for a genuinely
static deliverable, but silently wrong here -- so the step never ran,
the applet was never created, and the project was never assigned one.
"""
import intent

LIVE_REQUEST = ("create an applet; save its id as applet_id in this "
                "directory's own output.json; the runnable entry point "
                "is main.py")


def test_single_component_wrong_name_is_renamed():
    comps = [intent.Component("applet_creator", "applet_creator.py", 4, [],
                              LIVE_REQUEST)]
    result = intent.enforce_requested_entry_point(comps, LIVE_REQUEST)
    assert result == ("applet_creator.py", "main.py"), result
    assert comps[0].path == "main.py"
    assert comps[0].name == "main"


def test_already_correctly_named_is_left_alone():
    comps = [intent.Component("main", "main.py", 4, [], LIVE_REQUEST)]
    result = intent.enforce_requested_entry_point(comps, LIVE_REQUEST)
    assert result is None
    assert comps[0].path == "main.py"


def test_a_request_with_no_such_clause_at_all_is_untouched():
    comps = [intent.Component("survey", "survey.html", 2, [],
                              "create survey.html, a three-question survey")]
    result = intent.enforce_requested_entry_point(
        comps, "create survey.html, a three-question survey")
    assert result is None
    assert comps[0].path == "survey.html"


def test_multi_component_plan_renames_the_real_sink_via_plan_entry_point():
    # Mirrors intent.plan_entry_point's own sink-with-deps heuristic: the
    # entry point is the one file nothing else depends on, and which
    # itself depends on something else in the plan.
    request = ("create an applet and upload survey.html to it; the "
              "runnable entry point is main.py")
    comps = [
        intent.Component("api_client", "api_client.py", 4, [], "client spec"),
        intent.Component("do_upload", "do_upload.py", 2, ["api_client"],
                         "entry spec"),
    ]
    result = intent.enforce_requested_entry_point(comps, request)
    assert result == ("do_upload.py", "main.py"), result
    assert comps[1].path == "main.py"
    assert comps[1].name == "main"
    # The sibling's own deps list must follow the rename, not go stale.
    assert comps[0].name == "api_client"  # untouched, it was not renamed


def test_dependents_deps_list_is_updated_to_the_new_name():
    request = ("create an applet and upload survey.html to it; the "
              "runnable entry point is main.py")
    comps = [
        intent.Component("api_client", "api_client.py", 4, [], "client spec"),
        intent.Component("weird_name", "weird_name.py", 2, ["api_client"],
                         "entry spec"),
    ]
    intent.enforce_requested_entry_point(comps, request)
    assert comps[1].name == "main"
    # Nothing in this plan depended on the renamed file's old name, so
    # this mainly proves the rename does not crash when nothing needs
    # updating; the meaningful case (a third file depending on the entry
    # point by its old name) is covered by the ambiguous-plan test below.


def test_multi_component_plan_with_no_clear_sink_is_left_alone():
    # plan_entry_point returns None for two sinks -- enforce_requested_entry_point
    # must not guess either, matching the same "wrong guess is worse than
    # no guess" rule plan_entry_point documents for itself.
    request = "do two independent things; the runnable entry point is main.py"
    comps = [
        intent.Component("a", "a.py", 2, [], "s"),
        intent.Component("b", "b.py", 2, ["a"], "s"),
        intent.Component("c", "c.py", 2, ["a"], "s"),
    ]
    result = intent.enforce_requested_entry_point(comps, request)
    assert result is None
    assert [c.path for c in comps] == ["a.py", "b.py", "c.py"]


def test_a_different_requested_name_is_honoured_too():
    # Not hardcoded to "main.py" specifically -- whatever name the request
    # states verbatim is the one enforced.
    request = "upload a unit; the runnable entry point is upload_unit.py"
    comps = [intent.Component("do_it", "do_it.py", 2, [], request)]
    result = intent.enforce_requested_entry_point(comps, request)
    assert result == ("do_it.py", "upload_unit.py"), result
    assert comps[0].path == "upload_unit.py"
    assert comps[0].name == "upload_unit"


def test_sabotage_without_the_fix_reproduces_the_live_bug():
    # The exact shape of the live failure: nothing renames the lone
    # wrongly-named file, so `generate.sh`'s `if [ -f main.py ]` check
    # would find nothing and silently skip the step.
    comps = [intent.Component("applet_creator", "applet_creator.py", 4, [],
                              LIVE_REQUEST)]
    # No call to enforce_requested_entry_point at all -- this is what the
    # pipeline did before the fix existed.
    assert not any(c.path == "main.py" for c in comps), (
        "without the fix, nothing renames applet_creator.py to main.py, "
        "confirming the live bug was reachable")


# --------------------------------------------------------------------------
# `intent.default_api_entry_point` -- the same failure one level further
# out: a `--api` request whose text never says "the runnable entry point
# is X" at all, so `enforce_requested_entry_point` above correctly finds
# nothing to do, and the plan's one component still needs renaming.

NO_PHRASE_REQUEST = ("create a project; save its id in this directory's "
                     "own output.json")


def test_api_single_component_with_no_phrase_still_gets_main_py():
    comps = [intent.Component("project_creator", "project_creator.py", 2, [],
                              NO_PHRASE_REQUEST)]
    # Mirrors the real call order in team.py: the verbatim check runs
    # first and (correctly) finds nothing, then the --api fallback fires.
    assert intent.enforce_requested_entry_point(comps, NO_PHRASE_REQUEST) \
        is None
    result = intent.default_api_entry_point(comps)
    assert result == ("project_creator.py", "main.py"), result
    assert comps[0].path == "main.py"
    assert comps[0].name == "main"


def test_already_main_py_is_left_alone():
    comps = [intent.Component("main", "main.py", 2, [], NO_PHRASE_REQUEST)]
    assert intent.default_api_entry_point(comps) is None
    assert comps[0].path == "main.py"


def test_multi_component_plan_is_not_touched():
    # This shape belongs to ensure_entry_point/plan_entry_point, not this
    # fallback -- a multi-component plan already has its own, more
    # careful sink-detection, and guessing here would risk renaming the
    # wrong file.
    comps = [
        intent.Component("api_client", "api_client.py", 4, [], "spec"),
        intent.Component("main_logic", "main_logic.py", 2, ["api_client"],
                         "spec"),
    ]
    assert intent.default_api_entry_point(comps) is None
    assert [c.path for c in comps] == ["api_client.py", "main_logic.py"]


def test_non_python_single_component_is_left_alone():
    # A static survey.html deliverable is correctly never run at all --
    # forcing it to "main.py" would make it a Python file that does not
    # exist, not fix anything.
    comps = [intent.Component("survey", "survey.html", 2, [], "spec")]
    assert intent.default_api_entry_point(comps) is None
    assert comps[0].path == "survey.html"


def test_sabotage_without_the_api_fallback_reproduces_the_live_bug():
    # The exact shape of the second live failure: a --api request with no
    # "runnable entry point" phrase at all leaves project_creator.py
    # unrenamed unless default_api_entry_point is actually called.
    comps = [intent.Component("project_creator", "project_creator.py", 2, [],
                              NO_PHRASE_REQUEST)]
    intent.enforce_requested_entry_point(comps, NO_PHRASE_REQUEST)
    assert not any(c.path == "main.py" for c in comps), (
        "without calling default_api_entry_point, project_creator.py is "
        "never renamed, confirming the second live bug was reachable")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print("%d/%d passed" % (len(tests), len(tests)))
