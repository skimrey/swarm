"""A blocked entry point is renamed to what it does, not to a numbered twin.

Live: "write a script that uploads a dummy unit" -- unrelated to the
applet/project/task flow main.py already ran -- was still planned as an
edit to main.py, because the existing-files prose is advisory and lost to
the rest of the planner's prompt. `ground.entry_point_conflicts` decides
SAME/DIFFERENT once; `intent.rescue_entry_points` is the enforcement, and
it must not depend on the model agreeing a second time.

The first version of the enforcement renamed to `main_2.py`, which is a
worse name than the problem it solves: it reads as "a second copy of
main", tells the operator nothing about what the file actually does, and
gives no reason to expect the two files to cooperate -- because they were
never meant to. The name now comes from the request itself.
"""
import os
import tempfile

import intent


def plan(*paths):
    return [intent.Component(p.split(".")[0], p, 2, [], "spec")
            for p in paths]


def test_a_blocked_path_is_renamed_after_what_it_does():
    comps = plan("main.py", "client.py")
    renamed = intent.rescue_entry_points(
        comps, ["main.py"], tempfile.mkdtemp(),
        "write a script that uploads a dummy unit")
    assert renamed == [("main.py", "uploads_dummy_unit.py")], renamed
    assert comps[0].path == "uploads_dummy_unit.py", comps[0].path
    assert comps[1].path == "client.py", comps[1].path
    print("  a blocked path is renamed after the request, an unblocked one is not")


def test_nothing_blocked_means_nothing_renamed():
    comps = plan("main.py")
    renamed = intent.rescue_entry_points(comps, [], tempfile.mkdtemp(), "x")
    assert renamed == []
    assert comps[0].path == "main.py"
    print("  an empty block list changes nothing")


def test_an_empty_request_still_produces_a_usable_name():
    comps = plan("main.py")
    renamed = intent.rescue_entry_points(comps, ["main.py"], tempfile.mkdtemp(), "")
    assert renamed == [("main.py", "task.py")], renamed
    print("  no words to draw on falls back to a plain, still-valid name")


def test_the_rename_avoids_a_file_already_on_disk():
    root = tempfile.mkdtemp()
    open(os.path.join(root, "uploads_dummy_unit.py"), "w").write("# taken")
    comps = plan("main.py")
    renamed = intent.rescue_entry_points(
        comps, ["main.py"], root, "write a script that uploads a dummy unit")
    assert renamed == [("main.py", "uploads_dummy_unit_2.py")], renamed
    print("  a name already on disk is skipped, not collided with")


def test_the_rename_avoids_another_component_in_the_same_plan():
    """The disk is not the only thing a fresh name can collide with -- a
    plan can already claim the derived name for something else."""
    comps = plan("main.py", "uploads_dummy_unit.py")
    renamed = intent.rescue_entry_points(
        comps, ["main.py"], tempfile.mkdtemp(),
        "write a script that uploads a dummy unit")
    assert renamed == [("main.py", "uploads_dummy_unit_2.py")], renamed
    print("  a name already claimed by this plan is skipped too")


def test_two_blocks_in_one_call_do_not_collide_with_each_other():
    """Both rescues in a single `do` call answer to the same request, so
    they would derive the same slug -- the second must still land on a
    free name rather than silently overwriting the first's."""
    comps = plan("main.py", "runner.py")
    renamed = intent.rescue_entry_points(
        comps, ["main.py", "runner.py"], tempfile.mkdtemp(),
        "consolidate the parts")
    assert renamed == [("main.py", "consolidate_parts.py"),
                        ("runner.py", "consolidate_parts_2.py")], renamed
    print("  two rescues from one request land on two different names")


def test_the_derived_name_stays_a_short_valid_filename():
    comps = plan("main.py")
    renamed = intent.rescue_entry_points(
        comps, ["main.py"], tempfile.mkdtemp(),
        "please can you just build me a brand new little helper that will "
        "go and fetch several rows from the big shared database table")
    old, new = renamed[0]
    stem = new[:-3]
    assert 0 < len(stem.split("_")) <= 4, new
    assert stem.replace("_", "").isalpha(), new
    print("  a long request still yields a short, plain filename: %s" % new)


def main():
    test_a_blocked_path_is_renamed_after_what_it_does()
    test_nothing_blocked_means_nothing_renamed()
    test_an_empty_request_still_produces_a_usable_name()
    test_the_rename_avoids_a_file_already_on_disk()
    test_the_rename_avoids_another_component_in_the_same_plan()
    test_two_blocks_in_one_call_do_not_collide_with_each_other()
    test_the_derived_name_stays_a_short_valid_filename()
    print("OK")


if __name__ == "__main__":
    main()
