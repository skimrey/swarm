"""`--retry --run` should never make the operator paste a traceback, or
clean one up before pasting it.

Two pieces, both cheap to check without driving the whole CLI:
`resolve_run_target` (which file to execute when `--file` was not given)
and `run_and_capture` (actually running it and getting the exit code plus
everything it printed, with no shell in between).
"""
import os
import tempfile

import intent
import team


def plan(*specs):
    """specs: (name, path, deps) tuples."""
    return [intent.Component(name, path, 2, list(deps), "do the thing")
            for name, path, deps in specs]


def test_explicit_file_always_wins():
    restored = plan(("main", "main.py", ["client"]), ("client", "client.py", []))
    assert team.resolve_run_target("client.py", restored) == "client.py"
    print("  an explicit --file is never second-guessed")


def test_falls_back_to_the_structural_entry_point():
    restored = plan(("main", "main.py", ["client"]), ("client", "client.py", []))
    assert team.resolve_run_target(None, restored) == "main.py"
    print("  with no --file, the plan's own entry point is used")


def test_no_file_and_no_obvious_entry_point_gives_up_cleanly():
    # Two components, neither depending on the other: no single sink, so
    # `intent.plan_entry_point` correctly refuses to guess.
    restored = plan(("a", "a.py", []), ("b", "b.py", []))
    assert team.resolve_run_target(None, restored) is None
    print("  an ambiguous plan returns None rather than guessing wrong")


def test_run_and_capture_reports_a_clean_exit():
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "ok.py"), "w") as fh:
            fh.write("print('all good')\n")
        code, output = team.run_and_capture(root, "ok.py")
        assert code == 0, code
        assert "all good" in output, output
        print("  a clean run reports exit 0 and its own stdout")


def test_run_and_capture_reproduces_a_real_traceback_verbatim():
    # The exact shape that breaks when pasted by hand: quotes, backticks,
    # and a colon-delimited JSON body nested inside a message. Reproduced
    # here as a program's own output, and captured through a pipe rather
    # than typed into a shell, so nothing about it should be mangled.
    nasty = ('print("boom"); import sys; sys.stderr.write('
             '\'{"detail": [{"msg": "expected an optional prefix of '
             '`urn:uuid:` followed by [0-9a-fA-F-]"}]}\'); sys.exit(1)')
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "bad.py"), "w") as fh:
            fh.write(nasty + "\n")
        code, output = team.run_and_capture(root, "bad.py")
        assert code == 1, code
        assert "urn:uuid:" in output, output
        assert '[0-9a-fA-F-]' in output, output
        print("  a real failure's exact text survives the round trip intact")


def test_a_hang_times_out_instead_of_raising():
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "hangs.py"), "w") as fh:
            fh.write("import time; time.sleep(30)\n")
        code, output = team.run_and_capture(root, "hangs.py", timeout=1.0)
        assert code is None, code
        print("  a program that never returns times out cleanly, not a crash")


def test_stdin_is_closed_so_a_prompt_fails_fast_instead_of_hanging():
    # stdout is captured into a pipe rather than shown live, so a script
    # waiting on the operator's terminal would look identically hung
    # either way. Closing stdin turns that into an immediate, reportable
    # EOFError -- exactly the failure `--run` exists to hand the repair
    # loop, rather than a silent wait until the timeout above fires for an
    # unrelated reason.
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "prompts.py"), "w") as fh:
            fh.write("input('are you sure? ')\n")
        code, output = team.run_and_capture(root, "prompts.py", timeout=5.0)
        assert code != 0, code
        assert "EOFError" in output, output
        print("  a script that tries to prompt fails fast with EOFError, "
              "not a hang")


def main():
    test_explicit_file_always_wins()
    test_falls_back_to_the_structural_entry_point()
    test_no_file_and_no_obvious_entry_point_gives_up_cleanly()
    test_run_and_capture_reports_a_clean_exit()
    test_run_and_capture_reproduces_a_real_traceback_verbatim()
    test_a_hang_times_out_instead_of_raising()
    test_stdin_is_closed_so_a_prompt_fails_fast_instead_of_hanging()
    print("OK")


if __name__ == "__main__":
    main()
