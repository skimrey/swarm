"""Validates team.cmd_orchestrate_sh: emits ONE syntactically valid bash
script containing both the write phase (team-do fan-out, touching
nothing real) and the run phase (the full retry ladder), with
dependencies computed mechanically from each step's own request text --
and, by sabotage, that a version split across two files (generate.sh +
orchestrate.sh) reproduces the exact live failure this design replaces:
a script whose first real action is 'No such file or directory'."""
import json
import subprocess
import sys
import tempfile
import os
import io
from contextlib import redirect_stdout

import team

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + name)
    else:
        FAIL += 1
        print("FAIL " + name + (": " + str(detail) if detail else ""))


STEPS = [
    {"dir": "01_create_project",
     "request": "create a project; save its id as project_id in a new "
                "output.json in the same directory as the script"},
    {"dir": "02_create_task",
     "request": "read project_id from ../01_create_project/output.json; "
                "create a task; save its id as task_id in a new "
                "output.json in the same directory as the script"},
    {"dir": "03_upload_units",
     "request": "read task_id from ../02_create_task/output.json; "
                "upload 3 units"},
    # A request naming a directory that is not actually one of this
    # chain's own steps -- must not be added as a dependency.
    {"dir": "04_unrelated",
     "request": "read something from ../not_a_real_step/output.json"},
]


def render(steps):
    with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(steps, fh)
        path = fh.name
    try:
        class Args:
            pass
        args = Args()
        args.steps = path
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = team.cmd_orchestrate_sh(args, None, None)
        return rc, buf.getvalue()
    finally:
        os.remove(path)


rc, script = render(STEPS)
check("exits 0", rc == 0, rc)

print("\nthe emitted script is syntactically valid bash")
with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
    fh.write(script)
    sh_path = fh.name
try:
    proc = subprocess.run(["bash", "-n", sh_path], capture_output=True, text=True)
    check("bash -n accepts it", proc.returncode == 0, proc.stderr)
finally:
    os.remove(sh_path)

print("\nit is ONE file containing both phases -- no executable "
      "reference to a second generated script anywhere (the header "
      "comment mentions the old split design by name, as history)")
non_comment_lines = [ln for ln in script.splitlines()
                     if not ln.strip().startswith("#")]
check("no 'generate.sh' file reference in any executable line",
      not any("generate.sh" in ln for ln in non_comment_lines),
      non_comment_lines)
check("no 'nohup bash' launching a second script in any executable line",
      not any("nohup bash" in ln for ln in non_comment_lines),
      non_comment_lines)
check("the write-phase team-do fan-out is present in executable code",
      any("team do" in ln for ln in non_comment_lines), non_comment_lines)
check("the run-phase python3 main.py call is present in executable code",
      any("python3 main.py" in ln for ln in non_comment_lines),
      non_comment_lines)

print("\nsteps.json is embedded whole, so the script is self-contained")
check("both real requests' text appear in the embedded here-doc",
      all(s["request"] in script for s in STEPS[:3]), script[:200])

print("\ndependencies are computed mechanically from each step's own "
      "request text, never typed by the caller")
check("02_create_task depends on 01_create_project",
      'run_step 02_create_task 02_create_task 01_create_project || exit 1'
      in script, script)
check("03_upload_units depends on 02_create_task",
      'run_step 03_upload_units 03_upload_units 02_create_task || exit 1'
      in script, script)
check("01_create_project has no dependency (nothing to add)",
      'run_step 01_create_project 01_create_project || exit 1' in script,
      script)
check("a sibling reference to a directory outside this chain adds no "
      "dependency",
      'run_step 04_unrelated 04_unrelated || exit 1' in script, script)

print("\nthe retry ladder itself is present verbatim, not summarised or "
      "half-copied")
for must_have in (
        'echo "$name failed -- asking team do --retry --run to repair '
        'it against the real failure it just produced" >&2',
        '(cd "$dir" && team do --retry --run < /dev/null)',
        'echo "$name still fails after repairing itself -- it depends '
        'on ${deps[*]}, so repairing those next" >&2',
        'if [ -f "$dir/.orchestrate_done" ]; then',
        'echo "$name failed again after repair" >&2',
):
    check("retry ladder contains: %r" % must_have[:50], must_have in script,
          "missing: " + must_have)

print("\nthe write phase stops the whole script before any real "
      "run_step call if any component failed to generate")
check("a generation failure exits 1 before the run phase's own header",
      'echo "orchestrate.sh: generation failed for at least one step' in script
      and script.index('GEN_FAILED') < script.index('run_step()'),
      "ordering check failed")

print("\nthe .env preamble is present and setdefault-only (never "
      "overwrites a real export)")
check(".env preamble present",
      "_SWARM_ENV=" in script and
      '[ -z "${!_swarm_k:-}" ] && export "$_swarm_k=$_swarm_v"' in script,
      script[:400])

print("\nthe .env path embedded in the preamble is the real one beside "
      "team.py's own symlink target, not the directory of whichever "
      "symlink happened to invoke it")
# Live bug: `_SWARM_ENV_PATH` used to be computed with a *second*,
# independent `os.path.dirname(os.path.abspath(__file__))` instead of
# reusing `swarm.HERE` (which already gets this right via `realpath`).
# `abspath` leaves a symlink's own path untouched, so invoking through
# `~/.local/bin/team -> .../team.py` produced `~/.local/bin/.env` --
# a directory with no `.env` in it -- and every credential export the
# preamble exists to guarantee silently did nothing. This asserts the
# emitted script actually names the real, `swarm.HERE`-relative path.
import shlex
import swarm as _swarm_mod
_real_env_line = '_SWARM_ENV=%s' % shlex.quote(
    os.path.join(_swarm_mod.HERE, ".env"))
check("emitted _SWARM_ENV= line uses the real, realpath-resolved directory",
      _real_env_line in script,
      "expected %r somewhere in the script; got preamble: %s"
      % (_real_env_line, script[:400]))
check("team._SWARM_ENV_PATH itself matches swarm.HERE/.env",
      team._SWARM_ENV_PATH == os.path.join(_swarm_mod.HERE, ".env"),
      team._SWARM_ENV_PATH)

print("\nsabotage: reproducing the pre-fix abspath(__file__) computation "
      "shows a symlink-invoked path diverging from the real one")
# Simulates exactly what `os.path.dirname(os.path.abspath(__file__))`
# computed when `team.py` was invoked through a symlink: `__file__` (and
# therefore `abspath`) is left as the symlink's own path, never resolved
# to the real target the way `realpath` does. This proves the fixture
# actually distinguishes the two, rather than merely asserting a string
# that happened to already be correct.
_tmp_symlink_dir = tempfile.mkdtemp()
_fake_symlink = os.path.join(_tmp_symlink_dir, "team")
try:
    os.symlink(os.path.realpath(team.__file__), _fake_symlink)
    _buggy_path = os.path.dirname(os.path.abspath(_fake_symlink))
    _fixed_path = os.path.dirname(os.path.realpath(_fake_symlink))
    check("sabotage: abspath(symlink) points at the symlink's own "
          "directory, not team.py's real one",
          _buggy_path == _tmp_symlink_dir and _buggy_path != _fixed_path,
          (_buggy_path, _fixed_path))
    check("realpath(symlink) recovers team.py's real directory instead",
          _fixed_path == os.path.dirname(os.path.realpath(team.__file__)),
          _fixed_path)
finally:
    os.remove(_fake_symlink)
    os.rmdir(_tmp_symlink_dir)

print("\nmalformed steps.json fails loudly rather than emitting a "
      "half-finished script")
rc_bad, _ = render([{"dir": "only_a_dir_no_request"}])
check("a step missing \"request\" exits non-zero", rc_bad != 0, rc_bad)

print("\nsabotage: the split-into-two-files design this replaces "
      "reproduces the exact live failure the operator hit -- an "
      "orchestrator whose first real action fails because the other "
      "generated file was never created")
SPLIT_ORCHESTRATE_SH = '''#!/bin/bash
nohup bash generate.sh > generate.log 2>&1 &
GENERATE_PID=$!
wait "$GENERATE_PID"
if [ $? -ne 0 ]; then
    echo "generate.sh failed -- see generate.log" >&2
    exit 1
fi
echo "Generation phase succeeded."
'''
with tempfile.TemporaryDirectory() as d:
    orch_path = os.path.join(d, "orchestrate.sh")
    with open(orch_path, "w") as fh:
        fh.write(SPLIT_ORCHESTRATE_SH)
    proc = subprocess.run(["bash", "orchestrate.sh"], cwd=d,
                           capture_output=True, text=True)
    check("sabotage: the two-file split design fails immediately with "
          "'No such file or directory' when only orchestrate.sh was "
          "generated -- proving the merge into one file (asserted "
          "above) is what actually removes this failure, not merely a "
          "reworded comment",
          proc.returncode != 0 and "generate.sh" in (proc.stdout + proc.stderr),
          (proc.returncode, proc.stdout, proc.stderr))
check("sabotage cross-check: the real, current cmd_orchestrate_sh "
      "output has no such gap to reproduce -- confirmed above by "
      "'no generate.sh reference in any executable line'",
      not any("generate.sh" in ln for ln in non_comment_lines),
      "regression: real output now executes a reference to generate.sh")

print("\nself-logging: the emitted script writes its own orchestrate.log "
      "regardless of whether the caller redirected to it")
check("a self-logging guard sits before anything else executes",
      script.index('exec > orchestrate.log 2>&1')
      < script.index('_SWARM_ENV='),
      "the self-log redirect must run before the .env preamble, or "
      "everything before it would still be lost to whatever the caller "
      "did or did not redirect")
check("the guard variable prevents re-truncating the log on a second "
      "sourcing of the same script",
      '_ORCHESTRATE_SELF_LOGGED' in script, script[:600])


def _fake_team_bin(tmp):
    """A minimal stand-in for the real `team` binary, used only to
    exercise orchestrate.sh's own control flow (the self-log guard, the
    write/run phase boundary) without making a real network call. Prints
    just enough for the script's own downstream logic (an empty
    STEP_DIRS array) to fall through cleanly with nothing left to run."""
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    fake = os.path.join(bindir, "team")
    with open(fake, "w") as fh:
        fh.write(
            "#!/bin/bash\n"
            "case \"$1\" in\n"
            "  lint-chain) echo 'chain is consistent'; exit 0 ;;\n"
            "  steps-to-bash) echo 'STEP_DIRS=(); STEP_REQS=(); "
            "STEP_APIS=(); STEP_FLAGS=()'; exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n")
    os.chmod(fake, 0o755)
    return bindir


print("\nlive shape: `bash orchestrate.sh` invoked with NO redirect at "
      "all (the exact hand-written batch driver this exists to survive) "
      "still produces a complete, non-empty orchestrate.log")
_, no_dep_script = render([{"dir": "01_only", "request": "do a thing"}])
with tempfile.TemporaryDirectory() as run_dir:
    orch_path = os.path.join(run_dir, "orchestrate.sh")
    with open(orch_path, "w") as fh:
        fh.write(no_dep_script)
    bindir = _fake_team_bin(run_dir)
    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    # No stdout/stderr redirect on this call at all -- exactly the live
    # bug's own invocation shape (a hand-written driver that forgot
    # `> orchestrate.log 2>&1`).
    proc = subprocess.run(["bash", "orchestrate.sh"], cwd=run_dir, env=env,
                           capture_output=True, text=True)
    log_path = os.path.join(run_dir, "orchestrate.log")
    log_exists = os.path.isfile(log_path)
    log_content = open(log_path).read() if log_exists else ""
    check("orchestrate.log exists even with no caller redirect",
          log_exists, "no orchestrate.log written at all")
    check("orchestrate.log actually has real content in it",
          "chain is consistent" in log_content, log_content)
    check("nothing leaked to the caller's own unredirected stdout "
          "instead (it all went to the self-written log)",
          "chain is consistent" not in proc.stdout, proc.stdout)

print("\nlive shape: the correctly-redirected caller (this project's own "
      "`batch-sh` convention, `bash orchestrate.sh > orchestrate.log "
      "2>&1`) is not corrupted or duplicated by the self-log guard")
with tempfile.TemporaryDirectory() as run_dir:
    orch_path = os.path.join(run_dir, "orchestrate.sh")
    with open(orch_path, "w") as fh:
        fh.write(no_dep_script)
    bindir = _fake_team_bin(run_dir)
    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    outer_log = os.path.join(run_dir, "orchestrate.log")
    with open(outer_log, "w") as out_fh:
        proc = subprocess.run(["bash", "orchestrate.sh"], cwd=run_dir,
                               env=env, stdout=out_fh, stderr=subprocess.STDOUT)
    content = open(outer_log).read()
    check("the log contains each line exactly once, not duplicated",
          content.count("chain is consistent") == 1, content)

print("\nsabotage: without the self-log guard, the exact live failure "
      "reproduces -- no redirect from the caller means no "
      "orchestrate.log at all")
_sabotaged_script = no_dep_script.replace(
    'if [ -z "${_ORCHESTRATE_SELF_LOGGED:-}" ]; then\n'
    '    export _ORCHESTRATE_SELF_LOGGED=1\n'
    '    exec > orchestrate.log 2>&1\n'
    'fi\n', '')
check("sabotage fixture actually removed the guard (script differs)",
      _sabotaged_script != no_dep_script, "replace() found nothing to remove")
with tempfile.TemporaryDirectory() as run_dir:
    orch_path = os.path.join(run_dir, "orchestrate.sh")
    with open(orch_path, "w") as fh:
        fh.write(_sabotaged_script)
    bindir = _fake_team_bin(run_dir)
    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(["bash", "orchestrate.sh"], cwd=run_dir, env=env,
                           capture_output=True, text=True)
    log_path = os.path.join(run_dir, "orchestrate.log")
    check("sabotage: with the guard removed, the exact live bug "
          "reproduces -- no orchestrate.log is ever created",
          not os.path.isfile(log_path), "guard removal should have "
          "reproduced the live failure but orchestrate.log exists anyway")
    check("sabotage: the real output went to the caller's own "
          "unredirected stdout instead, exactly like the live failure",
          "chain is consistent" in proc.stdout, proc.stdout)

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    sys.exit(1)
