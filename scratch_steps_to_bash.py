"""Validates `team.cmd_steps_to_bash`, which replaces the fragile inline
`eval "$(python3 -c '<multi-line program>')"` shape in the
generate.sh/orchestrate.sh templates with one line: `eval "$(team
steps-to-bash steps.json)"`.

Measured live: an orchestrating model reconstructing that multi-line
embedded-python shape from memory (its own comment in the file read
"abbreviated for brevity, but I'll write the full thing") wrote one extra
stray `'` right after the python string's real closing quote --
`'')"` instead of `')"` -- which opened a second, never-closed string that
swallowed the rest of the file. Bash reported it as "unexpected EOF while
looking for matching `'`" at the *next* invocation of the script, more
than 80 lines past the actual mistake. This moves the seven-line embedded
program into `team.py` itself, written once and never regenerated from
memory by anything, so the template a model has to reproduce has no
nested string delimiters left to get wrong.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team


def _run_steps_to_bash(steps_path: str) -> (int, str):
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        args = types.SimpleNamespace(steps=steps_path)
        code = team.cmd_steps_to_bash(args, None, None)
    finally:
        sys.stdout = real_stdout
    return code, captured.getvalue()


def test_emits_the_reset_line_and_one_line_per_step():
    with tempfile.TemporaryDirectory() as root:
        steps_path = os.path.join(root, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "01_create_project",
                 "request": "create a project", "api": "maven",
                 "flags": "--single-file"},
                {"dir": "02_create_task", "request": "read project_id"},
            ], fh)
        code, out = _run_steps_to_bash(steps_path)
        assert code == 0
        lines = out.strip().splitlines()
        assert lines[0] == (
            "STEP_DIRS=(); STEP_REQS=(); STEP_APIS=(); STEP_FLAGS=()")
        assert len(lines) == 3, "one reset line plus one line per step"
        assert "01_create_project" in lines[1]
        assert "02_create_task" in lines[2]
    print("steps-to-bash: emits the reset line and one line per step")


def test_the_output_is_actually_valid_bash_and_round_trips():
    """The whole point: `eval` this output and the arrays it built must
    contain exactly what was in steps.json, including punctuation that
    would break a hand-rolled quoting scheme -- an apostrophe *and* a
    double quote in the same request, which is exactly what defeated the
    live, hand-written version of this logic."""
    with tempfile.TemporaryDirectory() as root:
        steps_path = os.path.join(root, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "01_create_project",
                 "request": "create a project; it's got an apostrophe "
                            "and a \"quote\" too",
                 "api": "maven", "flags": "--single-file"},
                {"dir": "02_create_task", "request": "read project_id"},
            ], fh)
        code, out = _run_steps_to_bash(steps_path)
        assert code == 0

        script = out + (
            '\nfor i in "${!STEP_DIRS[@]}"; do\n'
            '  printf "%s|%s|%s|%s\\n" "${STEP_DIRS[$i]}" "${STEP_REQS[$i]}" '
            '"${STEP_APIS[$i]}" "${STEP_FLAGS[$i]}"\n'
            'done\n'
        )
        result = subprocess.run(["bash", "-c", script],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (
            "the emitted bash must actually parse and run clean: %r"
            % result.stderr)
        rows = [ln.split("|", 3) for ln in
               result.stdout.strip().splitlines()]
        assert rows[0][0] == "01_create_project"
        assert rows[0][1] == ("create a project; it's got an apostrophe "
                              "and a \"quote\" too")
        assert rows[0][2] == "maven"
        assert rows[0][3] == "--single-file"
        assert rows[1][0] == "02_create_task"
        assert rows[1][1] == "read project_id"
        assert rows[1][2] == ""
        assert rows[1][3] == ""
    print("steps-to-bash: the emitted bash evals clean and round-trips "
         "every field, including an apostrophe and a quote in one request")


def test_missing_file_fails_soft_with_a_message_not_a_traceback():
    args = types.SimpleNamespace(steps="/no/such/file.json")
    captured = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = captured
    try:
        code = team.cmd_steps_to_bash(args, None, None)
    finally:
        sys.stderr = real_stderr
    assert code != 0
    assert "could not read" in captured.getvalue()
    print("steps-to-bash: a missing file fails soft, not with a traceback")


def test_a_step_missing_dir_or_request_fails_soft():
    with tempfile.TemporaryDirectory() as root:
        steps_path = os.path.join(root, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([{"dir": "01_only_a_dir"}], fh)
        args = types.SimpleNamespace(steps=steps_path)
        captured = io.StringIO()
        real_stderr = sys.stderr
        sys.stderr = captured
        try:
            code = team.cmd_steps_to_bash(args, None, None)
        finally:
            sys.stderr = real_stderr
        assert code != 0
        assert "\"dir\" and \"request\"" in captured.getvalue()
    print("steps-to-bash: a malformed step fails soft, not with a traceback")


def test_sabotage_the_stray_quote_that_caused_the_live_failure():
    """Reproduces the exact live defect as a string comparison: the
    hand-written template line this replaces ended in `'')"` (one stray
    extra quote) instead of `')"`. Confirms that shape is genuinely
    broken bash and that `steps-to-bash`'s own output contains no such
    stray quote anywhere."""
    broken_tail = "'')\""
    correct_tail = "')\""
    broken_script = (
        "eval \"$(python3 -c '\nprint(1)\n%s\n"
        "echo should_never_print_this_line\n" % broken_tail
    )
    result = subprocess.run(["bash", "-n", "-c", broken_script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0, (
        "the exact live broken shape must actually fail bash -n")
    assert "unexpected EOF" in result.stderr or "unexpected end" in \
        result.stderr

    with tempfile.TemporaryDirectory() as root:
        steps_path = os.path.join(root, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([{"dir": "d", "request": "r"}], fh)
        _, out = _run_steps_to_bash(steps_path)
        assert broken_tail not in out
        assert correct_tail not in out, (
            "steps-to-bash's output never needs this closing shape at "
            "all -- there is no eval-of-python-c line left to close"
        )
    print("sabotage: the live broken closing shape is confirmed-broken "
         "bash, and steps-to-bash's own output contains no such sequence "
         "because the fragile shape does not exist in it anymore")


if __name__ == "__main__":
    test_emits_the_reset_line_and_one_line_per_step()
    test_the_output_is_actually_valid_bash_and_round_trips()
    test_missing_file_fails_soft_with_a_message_not_a_traceback()
    test_a_step_missing_dir_or_request_fails_soft()
    test_sabotage_the_stray_quote_that_caused_the_live_failure()
    print("OK")
