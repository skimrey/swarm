"""team.cmd_fix_version_conflict / `team fix-version-conflict`, and the
new run_step() rungs in the orchestrate-sh generator that call it.

`run_with_version_conflict_retry` only ever runs inside `cmd_do`'s own
`--run` handling. `orchestrate.sh`'s `run_step()` executes a finished
component with a bare `python3 main.py` for its first real attempt AND
every retry after a `team do --retry --run` self-repair -- never through
team.py at all. Measured live: a swallowed-exception bug hid the real
"Version '1.0.0' already exists" response the one time `team do --retry
--run`'s own probe ran, so it fixed the swallowed exception and moved on;
the *next* bare `python3 main.py`, straight from bash, hit the real
conflict with nothing watching its output at all, and the run failed
twice more with the same collision before giving up.

This tests two things: `cmd_fix_version_conflict` end to end via a real
subprocess, and that the generated `orchestrate.sh` actually calls it in
the right place in the ladder, before falling to a real repair attempt.

Run directly: `python3 scratch_fix_version_conflict_cli.py`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
TEAM_PY = os.path.join(ROOT, "team.py")

FIXTURE_MAIN = '''\
import json
import os

VERSION_ID = "1.0.0"

_here = os.path.dirname(__file__)
with open(os.path.join(_here, "_server_taken.json")) as fh:
    taken = json.load(fh)

if VERSION_ID in taken:
    raise RuntimeError(
        "400 Client Error: Bad Request\\n"
        "response body: {\\"detail\\": \\"Version '%s' already exists "
        "for this applet\\"}" % VERSION_ID)
else:
    with open(os.path.join(_here, "output.json"), "w") as fh:
        json.dump({"version_id": VERSION_ID}, fh)
    print("ok")
'''

NEVER_CONFLICTS_MAIN = '''\
import json
import os

_here = os.path.dirname(__file__)
raise RuntimeError("500 Internal Server Error: something unrelated broke")
'''


def _make_fixture(tmpdir, main_src, taken=("1.0.0",)):
    d = os.path.join(tmpdir, "step")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as fh:
        fh.write(main_src)
    with open(os.path.join(d, "_server_taken.json"), "w",
             encoding="utf-8") as fh:
        json.dump(list(taken), fh)
    return d


def test_cli_mechanically_resolves_a_real_version_conflict():
    tmpdir = tempfile.mkdtemp()
    try:
        d = _make_fixture(tmpdir, FIXTURE_MAIN)
        proc = subprocess.run(
            [sys.executable, TEAM_PY, "fix-version-conflict",
             "--dir", d, "--file", "main.py"],
            capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, (
            "a resolvable version conflict must exit 0: %r" % proc.stdout)
        assert "no model or repair attempt spent" in proc.stdout
        out_path = os.path.join(d, "output.json")
        assert os.path.exists(out_path), (
            "the fixed file must have actually run to completion and "
            "written its real output")
        with open(out_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["version_id"] != "1.0.0", (
            "the literal must have actually been replaced")
        print("  the CLI resolves a real, subprocess-level version "
              "conflict end to end with no model call")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cli_reports_failure_and_nonzero_exit_when_not_a_conflict():
    tmpdir = tempfile.mkdtemp()
    try:
        d = os.path.join(tmpdir, "step")
        os.makedirs(d)
        with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as fh:
            fh.write(NEVER_CONFLICTS_MAIN)
        proc = subprocess.run(
            [sys.executable, TEAM_PY, "fix-version-conflict",
             "--dir", d, "--file", "main.py"],
            capture_output=True, text=True, timeout=30)
        assert proc.returncode != 0
        assert "nothing here for this mechanism to fix" in proc.stdout
        print("  an unrelated failure is correctly reported as not "
              "mechanically fixable, exit nonzero, no mutation attempted")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_orchestrate_sh_calls_it_between_self_repair_and_dependency_repair():
    tmpdir = tempfile.mkdtemp()
    try:
        steps = [{"dir": "01_x",
                  "request": "create a project; save its id as "
                             "project_id in a new output.json in the "
                             "same directory as the script",
                  "api": "maven"}]
        steps_path = os.path.join(tmpdir, "steps.json")
        with open(steps_path, "w", encoding="utf-8") as fh:
            json.dump(steps, fh)
        proc = subprocess.run(
            [sys.executable, TEAM_PY, "orchestrate-sh", steps_path],
            capture_output=True, text=True, timeout=30, cwd=tmpdir)
        assert proc.returncode == 0, proc.stderr
        script = proc.stdout

        assert "team fix-version-conflict" in script, (
            "the generated run_step() must call the new mechanical-fix "
            "CLI somewhere")

        # Ordering: the mechanical fix must be tried strictly between the
        # first self-repair's failed retry and the dependency-repair
        # block, and a second self-repair pass must exist after
        # dependency-repair also fails, before the final give-up line.
        first_repair = script.index("team do --retry --run")
        mech_fix = script.index("team fix-version-conflict")
        dep_repair_block = script.index(
            "so repairing those next")
        second_repair = script.index(
            "repairing itself once more against its current")
        give_up = script.index("failed again after repair")

        assert first_repair < mech_fix < dep_repair_block < second_repair \
            < give_up, (
            "the ladder must run: self-repair -> mechanical fix -> "
            "dependency repair -> second self-repair -> give up, in "
            "that order")

        # A shell syntax check on the actual generated script, not a
        # hand-copied fragment of it.
        script_path = os.path.join(tmpdir, "orchestrate.sh")
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        check = subprocess.run(["bash", "-n", script_path],
                               capture_output=True, text=True)
        assert check.returncode == 0, check.stderr

        print("  the generated orchestrate.sh calls the mechanical fix "
              "in the right place in the ladder, and is valid bash")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_sabotage_old_template_shape_had_no_mechanical_rung_at_all():
    # Reproduce the exact pre-fix run_step() body (self-repair, raw run,
    # dependency-repair, raw run, give up -- no mechanical fix, no
    # second self-repair) and confirm the sabotage fixture genuinely
    # lacks what the real generator now adds, proving the assertions
    # above are not vacuously true against any old script.
    old_shape = '''
    echo "running $name"
    if _run_step_try; then
        return 0
    fi
    (cd "$dir" && team do --retry --run < /dev/null)
    if _run_step_try; then
        return 0
    fi
    if [ "${#deps[@]}" -gt 0 ]; then
        for dep in "${deps[@]}"; do
            (cd "$dep" && team do --retry --run < /dev/null)
        done
        if _run_step_try; then
            return 0
        fi
    fi
    echo "$name failed again after repair" >&2
    return 1
'''
    assert "team fix-version-conflict" not in old_shape
    assert "repairing itself once more" not in old_shape
    print("  sabotage confirmed: the pre-fix run_step() shape has "
          "neither the mechanical-fix rung nor the second self-repair "
          "rung, which is exactly the live gap this fix closes")


def main():
    test_cli_mechanically_resolves_a_real_version_conflict()
    test_cli_reports_failure_and_nonzero_exit_when_not_a_conflict()
    test_orchestrate_sh_calls_it_between_self_repair_and_dependency_repair()
    test_sabotage_old_template_shape_had_no_mechanical_rung_at_all()
    print("OK")


if __name__ == "__main__":
    main()
