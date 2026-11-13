"""Continuity across separate `do` invocations, tested with no models at all.

record/turns/history_block/existing_files_block are pure file I/O and string
formatting -- this checks all of it directly.
"""
import json
import os
import shutil
import tempfile
import time

import session
import team


class FakeComponent:
    def __init__(self, path, spec="", floor=4, deps=(), ok=True,
                 attempts=1, error=""):
        self.path = path
        self.spec = spec
        self.floor = floor
        self.deps = list(deps)
        self.ok = ok
        self.attempts = attempts
        self.error = error


def test_empty_project_has_no_history() -> None:
    root = tempfile.mkdtemp(prefix="sess-")
    try:
        assert session.turns(root) == []
        assert session.history_block(root) == ""
        assert session.existing_files_block(root, team.public_names) == ""
        assert session.context_for(root, team.public_names) == ""
    finally:
        shutil.rmtree(root, ignore_errors=True)
        _clear(root)
    print("empty project     no history, no crash, empty context")


def test_record_and_render() -> None:
    root = tempfile.mkdtemp(prefix="sess-")
    try:
        session.record(root, "build a dancing shapes animation",
                       [FakeComponent("constants.py"), FakeComponent("main.py")],
                       ok=2, total=2, elapsed=43.2)
        history = session.turns(root)
        assert len(history) == 1
        assert history[0]["request"] == "build a dancing shapes animation"
        assert history[0]["files"] == ["constants.py", "main.py"]
        assert history[0]["ok"] == 2 and history[0]["total"] == 2

        block = session.history_block(root)
        assert "dancing shapes animation" in block
        assert "constants.py" in block and "main.py" in block
        assert "2/2 ok" in block
        assert "Build on this existing work" in block
        print("record + render   turn recorded and rendered correctly")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_two_projects_do_not_collide() -> None:
    """Different directories must not share history just because both exist."""
    root_a = tempfile.mkdtemp(prefix="sess-a-")
    root_b = tempfile.mkdtemp(prefix="sess-b-")
    try:
        session.record(root_a, "project A's request", [FakeComponent("a.py")],
                       1, 1, 1.0)
        assert session.turns(root_b) == [], \
            "project B saw project A's history -- session paths are colliding"
        assert "project A's request" in session.history_block(root_a)
        print("project isolation two projects, two separate histories")
    finally:
        shutil.rmtree(root_a, ignore_errors=True)
        shutil.rmtree(root_b, ignore_errors=True)


def test_storage_is_unbounded_but_render_window_is_capped() -> None:
    """Storage keeps everything -- a turn is a few lines of JSON, cheap to
    keep -- but the planner must only ever see a fixed tail of it, or a long
    project's history would grow the prompt (and its cost) without bound."""
    root = tempfile.mkdtemp(prefix="sess-")
    try:
        assert session.MAX_TURNS is None, \
            "storage should be unbounded by default"
        count = session.HISTORY_LIMIT + 15
        for i in range(count):
            session.record(root, "request %d" % i, [FakeComponent("f.py")],
                           1, 1, 1.0)
        history = session.turns(root)
        assert len(history) == count, \
            "storage trimmed something even though MAX_TURNS is None: %d/%d" \
            % (len(history), count)

        block = session.history_block(root)
        shown = block.count("\"%s\"" % "")  # placeholder, replaced below
        rendered = [ln for ln in block.splitlines() if ln.strip()[:2].rstrip(".").isdigit()]
        assert len(rendered) == session.HISTORY_LIMIT, \
            "render window did not cap at HISTORY_LIMIT: %d lines" % len(rendered)
        assert "request %d" % (count - 1) in block, \
            "the render window kept the wrong end -- newest should be shown"
        assert "request 0\"" not in block, \
            "the oldest turn should have fallen outside the render window"
        print("storage vs render  %d turns stored, most recent %d rendered"
              % (count, session.HISTORY_LIMIT))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_existing_files_lists_real_public_names() -> None:
    """This is what lets a follow-up request target an existing file by name
    instead of the planner inventing a duplicate -- it has to be the file's
    real public names, not guessed ones."""
    root = tempfile.mkdtemp(prefix="sess-")
    try:
        with open(os.path.join(root, "shapes.py"), "w", encoding="utf-8") as fh:
            fh.write("class Shape:\n    pass\n\ndef make_shape():\n    pass\n")
        block = session.existing_files_block(root, team.public_names)
        assert "shapes.py" in block
        assert "Shape" in block and "make_shape" in block
        assert "SAME path" in block
        print("existing files    real classes/functions listed, not guessed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_never_raises_on_a_corrupt_file() -> None:
    """A record() call must never take a run down with it."""
    root = tempfile.mkdtemp(prefix="sess-")
    try:
        os.makedirs(session.SESSION_DIR, exist_ok=True)
        with open(session._session_path(root), "w", encoding="utf-8") as fh:
            fh.write("not valid json{{{")
        assert session.turns(root) == [], "corrupt file should read as no history"
        session.record(root, "recovers anyway", [FakeComponent("x.py")], 1, 1, 1.0)
        assert len(session.turns(root)) == 1
        print("corrupt recovery  bad json treated as empty, then repaired")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(session._session_path(root))
        except OSError:
            pass


def _touch(root: str, *names: str) -> None:
    for n in names:
        with open(os.path.join(root, n), "w", encoding="utf-8") as fh:
            fh.write("def placeholder():\n    pass\n")


def test_a_files_reason_is_attached_to_that_file() -> None:
    """The planner picks a path, so the why has to be next to the path."""
    root = tempfile.mkdtemp(prefix="sess-why-")
    try:
        _touch(root, "vm_engine.py")
        session.record(root, "stop a bad jump target crashing the program",
                       [FakeComponent("vm_engine.py",
                                      "Validate every jump target against "
                                      "program length before jumping.")],
                       1, 1, 5.0, existing={"vm_engine.py"})
        block = session.existing_files_block(root, team.public_names)
        assert "vm_engine.py" in block
        assert "Validate every jump target" in block, \
            "the reason never reached the file listing:\n%s" % block
        assert "stop a bad jump target" in block, \
            "the originating request is not shown:\n%s" % block
        print("reason on file    why sits next to the path it explains")
    finally:
        _clear(root)
        shutil.rmtree(root, ignore_errors=True)


def test_a_reason_outlives_unrelated_later_turns() -> None:
    """The property that a most-recent-N-turns window would break.

    Detailing the last few turns spends the budget on whatever happened
    last, so a run of tidy-ups to one helper buries the turn that explains
    the interesting file. Keying by file instead makes age irrelevant.
    """
    root = tempfile.mkdtemp(prefix="sess-outlive-")
    try:
        _touch(root, "vm_engine.py", "util.py")
        session.record(root, "stop a bad jump target crashing",
                       [FakeComponent("vm_engine.py", "Validate jump targets.")],
                       1, 1, 5.0, existing={"vm_engine.py"})
        for i in range(30):
            session.record(root, "tidy-up %d" % i,
                           [FakeComponent("util.py", "A helper nobody remembers.")],
                           1, 1, 1.0, existing={"util.py"})

        block = session.existing_files_block(root, team.public_names)
        assert "Validate jump targets." in block, \
            "the reason aged out after 30 unrelated turns:\n%s" % block
        # And the repeated one appears once, not thirty times.
        assert block.count("A helper nobody remembers.") == 1, \
            "a repeated edit was rendered %d times" \
            % block.count("A helper nobody remembers.")
        print("reason outlives   survived 30 later turns, no duplication")
    finally:
        _clear(root)
        shutil.rmtree(root, ignore_errors=True)


def test_a_later_change_replaces_the_earlier_reason() -> None:
    """What matters is why the file is the way it is now."""
    root = tempfile.mkdtemp(prefix="sess-replace-")
    try:
        _touch(root, "engine.py")
        session.record(root, "first", [FakeComponent("engine.py", "THE OLD REASON.")],
                       1, 1, 1.0, existing=set())
        session.record(root, "second", [FakeComponent("engine.py", "THE NEW REASON.")],
                       1, 1, 1.0, existing={"engine.py"})
        block = session.existing_files_block(root, team.public_names)
        assert "THE NEW REASON." in block
        assert "THE OLD REASON." not in block, \
            "a superseded reason is still being shown:\n%s" % block
        print("reason replaced   newest change wins, stale reason dropped")
    finally:
        _clear(root)
        shutil.rmtree(root, ignore_errors=True)


def test_a_failure_is_reported_but_never_becomes_a_file_reason() -> None:
    """A file that was never written has nothing to attach a reason to."""
    root = tempfile.mkdtemp(prefix="sess-fail-")
    try:
        session.record(root, "add a disassembler",
                       [FakeComponent("disasm.py", "Turn opcodes back into text.",
                                      ok=False,
                                      error="NameError: OPCODES is not defined")],
                       0, 1, 2.0, existing=set())
        assert "disasm.py" not in session.file_reasons(root), \
            "a component that failed was recorded as the reason for a file"
        history = session.history_block(root)
        assert "FAILED" in history and "OPCODES" in history, \
            "the failure and its error are not in the history:\n%s" % history
        print("failure           error kept in history, not as a file reason")
    finally:
        _clear(root)
        shutil.rmtree(root, ignore_errors=True)


def test_turns_recorded_before_this_feature_still_render() -> None:
    """Old history has no `plan` key. It must not crash or vanish."""
    root = tempfile.mkdtemp(prefix="sess-old-")
    try:
        os.makedirs(session.SESSION_DIR, exist_ok=True)
        with open(session._session_path(root), "w", encoding="utf-8") as fh:
            json.dump([{"when": time.time(), "request": "an ancient request",
                        "files": ["old.py"], "ok": 1, "total": 1,
                        "seconds": 3.0}], fh)
        block = session.history_block(root)
        assert "an ancient request" in block, \
            "a turn without a plan disappeared:\n%s" % block
        assert session.file_reasons(root) == {}
        print("old format        pre-plan turns still render, no migration")
    finally:
        _clear(root)
        shutil.rmtree(root, ignore_errors=True)


def test_one_project_reached_two_ways_is_one_project() -> None:
    """A symlinked path and its target must not be two separate histories.

    `project_root()` hands over `os.getcwd()`, which the OS has already
    resolved; `SWARM_ROOT`, a test, or a shell alias hands over whatever was
    typed. Keying on `abspath` made those different projects -- on macOS
    `/tmp` is a symlink to `/private/tmp`, so a directory reached by its
    obvious name appeared to have forgotten everything recorded under its
    real one.
    """
    real = tempfile.mkdtemp(prefix="sess-real-")
    link = os.path.join(tempfile.mkdtemp(prefix="sess-link-"), "alias")
    try:
        os.symlink(real, link)
        session.record(real, "asked via the real path",
                       [FakeComponent("a.py")], 1, 1, 1.0)
        assert len(session.turns(link)) == 1, \
            "history recorded under the real path was invisible via the symlink"

        session.set_attachments(link, ["design.md"])
        assert session.attachments(real) == ["design.md"], \
            "an attachment set via the symlink was invisible via the real path"
        print("symlinked root    one directory, one history, either spelling")
    finally:
        session.set_attachments(real, [])
        _clear(real)
        shutil.rmtree(real, ignore_errors=True)
        shutil.rmtree(os.path.dirname(link), ignore_errors=True)


def _clear(root: str) -> None:
    try:
        os.remove(session._session_path(root))
    except OSError:
        pass


if __name__ == "__main__":
    test_empty_project_has_no_history()
    test_record_and_render()
    test_two_projects_do_not_collide()
    test_storage_is_unbounded_but_render_window_is_capped()
    test_existing_files_lists_real_public_names()
    test_never_raises_on_a_corrupt_file()
    test_one_project_reached_two_ways_is_one_project()
    test_a_files_reason_is_attached_to_that_file()
    test_a_reason_outlives_unrelated_later_turns()
    test_a_later_change_replaces_the_earlier_reason()
    test_a_failure_is_reported_but_never_becomes_a_file_reason()
    test_turns_recorded_before_this_feature_still_render()
    print("OK")
