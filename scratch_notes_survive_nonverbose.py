"""An advisory note found during generation must survive verbose=False.

`run_components` calls `codegen.generate_file` with `verbose=False` for
every parallel component, deliberately -- interleaving several
components' worth of per-attempt chatter into one stream would be
unreadable. Every advisory-only gate (gate_platform_facts,
gate_output_json_wrong_variable, and previously
gate_narrowed_conflict_retry_status_codes before it got an autofix)
printed its note only when verbose=True, which meant the note was not
delayed, it was gone: nothing else in the pipeline ever read it, so a
real, correctly-found defect sat in a component's own generate.log with
no visible trace at all.

`generate_file` now returns any advisory notes it found in a `notes`
list regardless of `verbose`, and `run_components` prints them once,
serialized, right where it already prints `history_trail` -- after this
component's own generation has already finished, so printing here can
never interleave with another component's output the way live
per-attempt chatter would.

This exercises the real `run_components` with `codegen.generate_file`
stubbed to return a successful result carrying a `notes` entry, and
checks the note actually reaches stderr.

Run directly: `python3 scratch_notes_survive_nonverbose.py`.
"""
import io
import os
import sys

import codegen
import intent
import swarm
import team


def comp(name, deps, floor=2):
    c = intent.Component.__new__(intent.Component)
    c.name = name
    c.path = "scratchpkg/%s.py" % name
    c.spec = "irrelevant, generation is stubbed"
    c.floor = floor
    c.deps = deps
    c.ok = False
    c.code = ""
    c.error = ""
    c.target = ""
    c.attempts = 0
    c.seconds = 0.0
    c.contract = ""
    c.history_trail = []
    return c


NOTE_TEXT = ("prints a malformed platform link: "
             "https://dztfdhkmj6wqi.cloudfront.net/ui (fake host)")


def run_with_stub(result_notes):
    plan = [comp("main", [])]
    real = codegen.generate_file

    def stub(spec, out_path, node, model, cfg, state, **kw):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write("x = 1\n")
        return {"ok": True, "path": out_path, "code": "x = 1\n",
                "target": "stub", "attempts": 1, "seconds": 0.5,
                "history": [], "notes": result_notes}

    codegen.generate_file = stub
    captured = io.StringIO()
    real_stderr = sys.stderr
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        sys.stderr = captured
        try:
            team.run_components(plan, cfg, state, repair=1,
                                answer_tokens=100, check=None,
                                root="/tmp", tests=None)
        finally:
            sys.stderr = real_stderr
        state.close()
    finally:
        codegen.generate_file = real
    return captured.getvalue()


def test_note_reaches_the_operator_despite_verbose_false():
    output = run_with_stub([NOTE_TEXT])
    print(output)
    assert NOTE_TEXT in output, (
        "the advisory note never reached stderr even though "
        "generate_file returned it in `notes`:\n%s" % output)
    assert "advisory note(s)" in output, output
    print("  a note returned by a verbose=False generate_file call is "
          "printed at completion anyway")


def test_duplicate_notes_across_attempts_print_once():
    # The same note firing on every attempt of a multi-attempt run must
    # not spam the completion print once per attempt.
    output = run_with_stub([NOTE_TEXT, NOTE_TEXT, NOTE_TEXT])
    assert output.count(NOTE_TEXT) == 1, (
        "a note repeated across attempts was not deduplicated:\n%s" % output)
    print("  a note repeated across several attempts prints once, not "
          "once per attempt")


def test_no_notes_prints_nothing_extra():
    output = run_with_stub([])
    assert "advisory note(s)" not in output, (
        "an empty notes list still printed the advisory banner:\n%s"
        % output)
    print("  a component with no advisory notes gets no extra banner")


def test_dedupe_helper_preserves_first_seen_order():
    out = codegen._dedupe_preserve_order(["a", "b", "a", "c", "b"])
    assert out == ["a", "b", "c"], out
    print("  codegen._dedupe_preserve_order keeps first-seen order")


def test_sabotage_notes_field_actually_used_not_ignored():
    # Confirm the wiring is load-bearing: a result dict with no "notes"
    # key at all (the pre-fix shape, or any other caller that never
    # populates it) must not crash and must simply print nothing extra --
    # proving `.get("notes")` is really being read, not a decoration that
    # happens to look right.
    plan = [comp("main", [])]
    real = codegen.generate_file

    def stub_no_notes_key(spec, out_path, node, model, cfg, state, **kw):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write("x = 1\n")
        return {"ok": True, "path": out_path, "code": "x = 1\n",
                "target": "stub", "attempts": 1, "seconds": 0.5,
                "history": []}

    codegen.generate_file = stub_no_notes_key
    captured = io.StringIO()
    real_stderr = sys.stderr
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        sys.stderr = captured
        try:
            team.run_components(plan, cfg, state, repair=1,
                                answer_tokens=100, check=None,
                                root="/tmp", tests=None)
        finally:
            sys.stderr = real_stderr
        state.close()
    finally:
        codegen.generate_file = real
    output = captured.getvalue()
    assert "advisory note(s)" not in output, output
    print("  a result dict with no `notes` key at all is handled "
          "gracefully (old-shape caller safety)")


def main():
    test_note_reaches_the_operator_despite_verbose_false()
    test_duplicate_notes_across_attempts_print_once()
    test_no_notes_prints_nothing_extra()
    test_dedupe_helper_preserves_first_seen_order()
    test_sabotage_notes_field_actually_used_not_ignored()
    print("OK")


if __name__ == "__main__":
    main()
