"""Validates `intent.chain_output_json_problems`, the mechanical,
model-free check that a whole chain of `team do` requests agrees with
itself about which steps produce an `output.json` another step depends
on.

This is deliberately not a prose reminder. It exists because a live
6-step orchestration chain had every single step's request skip the
"save/read output.json" convention `LOCAL.md`'s own `run_step` template
assumes is already in place -- no code anywhere in that chain ever wrote
one -- and every downstream step silently substituted a placeholder or a
missing-env-var default instead of failing loudly. The fix is not
"remember to phrase it right"; it is "fix the vocabulary once, then
check every chain against it before any of it runs."
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent


def test_consistent_chain_reports_nothing():
    steps = [
        ("01_create_project",
         "create a project; save the project id to output.json as "
         "{\"project_id\": ...}"),
        ("02_create_task",
         "read project_id from ../01_create_project/output.json; create a "
         "task; save the task id to output.json"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert problems == [], problems


def test_consumer_with_no_matching_producer_promise_is_flagged():
    """The exact live shape: `01_create_project` prints the id to stdout
    and never saves it, so `05_update_applet` reading it from
    `../01_create_project/output.json` can never get a real value no
    matter how many times the chain is retried."""
    steps = [
        ("01_create_project", "create a project and print its id"),
        ("05_update_applet",
         "read project_id from ../01_create_project/output.json and "
         "push a new applet version"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert len(problems) == 1
    assert "05_update_applet" in problems[0]
    assert "01_create_project" in problems[0]
    assert "never promises to write" in problems[0]


def test_reference_to_a_step_not_in_the_chain_is_flagged_distinctly():
    steps = [
        ("06_update_project",
         "read applet_version_id from ../05_update_applet/output.json"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert len(problems) == 1
    assert "no step named 05_update_applet" in problems[0]


def test_producer_promise_order_does_not_matter():
    """A producer's own promise is a fact about its own request,
    independent of where it sits in run order relative to the check --
    `chain_output_json_problems` builds the whole write-promise map
    before checking any reads, so a step listed before its producer in
    the input list (which should never happen in a real chain, but the
    check should not depend on ordering to be correct) still resolves."""
    steps = [
        ("05_update_applet",
         "read project_id from ../01_create_project/output.json"),
        ("01_create_project",
         "create a project; save the id to output.json"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert problems == [], problems


def test_qualified_reference_is_never_mistaken_for_a_write_promise():
    """A step whose own request only ever mentions output.json in the
    qualified `../other/output.json` form -- i.e. it only *reads* a
    sibling's file and never promises one of its own -- must not read as
    having promised to write one just because the literal substring
    "output.json" appears somewhere in its text."""
    steps = [
        ("01_create_project",
         "create a project; save the id to output.json"),
        ("02_create_task",
         "read project_id from ../01_create_project/output.json; create a "
         "task and print its id"),
        ("03_upload_units",
         "read task_id from ../02_create_task/output.json; upload units"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert len(problems) == 1, problems
    assert "02_create_task" in problems[0]


def test_self_qualified_write_promise_is_recognised():
    """The exact live failure: a step's own promise to write its output
    is phrased as `save X in ../ITS-OWN-DIR/output.json` (self-qualified)
    rather than the bare `save X to output.json` form. This is a real
    promise to write its own file -- it must not be misread as a mere
    reference to a sibling and stripped away, leaving the check believing
    no promise was ever made."""
    steps = [
        ("02_create_task",
         "create a task; save its id as task_id in "
         "../02_create_task/output.json"),
        ("05_create_applet",
         "read task_id from ../02_create_task/output.json; create an "
         "applet; save its id as applet_id in "
         "../05_create_applet/output.json"),
        ("06_update_applet",
         "read applet_id from ../05_create_applet/output.json; update "
         "the applet; save its new version id as applet_version_id in "
         "../06_update_applet/output.json"),
        ("07_update_project",
         "read applet_version_id from ../06_update_applet/output.json; "
         "update the project"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert problems == [], problems
    # And the autofixer must not try to rewrite text that already says
    # this in substance -- appending a second, redundant clause would be
    # a real, if harmless-looking, corruption of a correct chain.
    fixed, remaining = intent.autofix_chain_output_json(steps)
    assert remaining == []
    assert fixed == steps, fixed


def test_sabotage_reproduces_the_live_self_qualified_false_positive():
    """Proves the fix above is load-bearing: the pre-fix logic (strip
    every `../dir/output.json` occurrence, including self-references,
    before checking for a bare promise) reports the exact live false
    positive on the exact live chain shape."""
    steps = [
        ("05_create_applet",
         "create an applet; save its id as applet_id in "
         "../05_create_applet/output.json"),
        ("06_update_applet",
         "read applet_id from ../05_create_applet/output.json; update "
         "the applet; save its new version id as applet_version_id in "
         "../06_update_applet/output.json"),
        ("07_update_project",
         "read applet_version_id from ../06_update_applet/output.json; "
         "update the project"),
    ]

    def sabotaged(steps_):
        write_promises = {}
        for dirname, request in steps_:
            stripped = intent._OUTPUT_JSON_READ_RE.sub("", request or "")
            write_promises[dirname] = bool(
                intent._OUTPUT_JSON_WRITE_RE.search(stripped))
        problems = []
        for dirname, request in steps_:
            for match in intent._OUTPUT_JSON_READ_RE.finditer(request or ""):
                producer = match.group(1)
                if not write_promises.get(producer):
                    problems.append("%s reads ../%s/output.json" %
                                     (dirname, producer))
        return problems

    sabotaged_result = sabotaged(steps)
    assert "06_update_applet reads ../06_update_applet/output.json" in (
        sabotaged_result), (
        "sabotage fixture should reproduce the live false positive on "
        "the self-qualified reference, got %r" % (sabotaged_result,))
    real_result = intent.chain_output_json_problems(steps)
    assert real_result == [], (
        "the real, fixed check must recognise the self-qualified "
        "promise -- got %r" % (real_result,))


def test_static_step_with_no_output_json_involvement_is_silent():
    """A step that neither reads nor writes output.json (a purely static
    asset, or a step with no cross-step dependency) must never be
    flagged -- this check only ever fires on an actual `../dir/
    output.json` reference with nothing answering it."""
    steps = [
        ("04_create_survey", "create a three-question survey.html"),
    ]
    problems = intent.chain_output_json_problems(steps)
    assert problems == [], problems


def test_cli_wrapper_exits_nonzero_on_an_unfixable_broken_chain():
    """A reference to a step that is not in the chain at all cannot be
    fixed by any default behaviour -- there is no producer request to
    append a clause to -- so the plain, no-flags invocation must still
    exit 1 for it."""
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "05_update_applet",
                 "request": "read project_id from "
                            "../01_create_project/output.json"},
            ], fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "01_create_project" in result.stderr


def test_cli_wrapper_self_repairs_by_default_with_no_flags():
    """The exact complaint this exists to fix: an orchestrator building
    `generate.sh` from a template is not going to remember to add a
    `--fix` flag, so fixing has to be the plain, no-flags default. A
    fixable gap (the consumer already named the key) must be repaired
    and the run must exit 0 -- with no flag on the command line at
    all."""
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "01_create_project",
                 "request": "create a project and print its id"},
                {"dir": "05_update_applet",
                 "request": "read project_id from "
                            "../01_create_project/output.json"},
            ], fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        with open(steps_path) as fh:
            rewritten = json.load(fh)
        producer = next(s for s in rewritten
                         if s["dir"] == "01_create_project")
        assert "project_id" in producer["request"]
        assert "output.json" in producer["request"]


def test_cli_check_only_reports_without_touching_the_file():
    """`--check-only` is the opt-out for the rarer case of wanting a
    report with no mutation -- the exact behaviour the old, opt-in
    `--fix` design used to be the default of."""
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        original = [
            {"dir": "01_create_project",
             "request": "create a project and print its id"},
            {"dir": "05_update_applet",
             "request": "read project_id from "
                        "../01_create_project/output.json"},
        ]
        with open(steps_path, "w") as fh:
            json.dump(original, fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path, "--check-only"],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "01_create_project" in result.stderr
        with open(steps_path) as fh:
            unchanged = json.load(fh)
        assert unchanged == original, "must not touch the file"


def test_cli_wrapper_exits_zero_on_a_clean_chain():
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "01_create_project",
                 "request": "create a project; save the id to output.json"},
                {"dir": "02_create_task",
                 "request": "read project_id from "
                            "../01_create_project/output.json; create a "
                            "task; save the id to output.json"},
            ], fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr


def test_sabotage_dropping_the_write_promise_check_misses_everything():
    """Proves the write-promise half is load-bearing: a version of the
    check that flags every `../dir/output.json` reference regardless of
    whether the producer promised one would also flag the consistent
    chain, which is the opposite failure (a detector firing on correct
    input) this project has repeatedly decided costs more than it saves."""
    steps = [
        ("01_create_project",
         "create a project; save the project id to output.json"),
        ("02_create_task",
         "read project_id from ../01_create_project/output.json; create a "
         "task"),
    ]

    def sabotaged(steps_):
        problems = []
        for dirname, request in steps_:
            for match in intent._OUTPUT_JSON_READ_RE.finditer(request or ""):
                problems.append("%s reads ../%s/output.json" %
                                 (dirname, match.group(1)))
        return problems

    sabotaged_result = sabotaged(steps)
    assert len(sabotaged_result) == 1, (
        "sabotage fixture should flag the reference regardless of the "
        "real producer promise, got %r" % (sabotaged_result,))
    real_result = intent.chain_output_json_problems(steps)
    assert real_result == [], (
        "the real check must recognise the producer's promise and stay "
        "silent -- got %r" % (real_result,))


def test_autofix_appends_the_exact_key_the_consumer_already_named():
    """The live failure this exists to close: `01_create_project` prints
    the id and never saves it, `06_update_project` reads
    `project_id from ../01_create_project/output.json`. The consumer
    already committed to a key name -- the fix is copying that fact into
    the producer's own request, not guessing at one."""
    steps = [
        ("01_create_project", "create a project and print its id"),
        ("06_update_project",
         "read project_id from ../01_create_project/output.json; patch "
         "the project"),
    ]
    fixed, problems = intent.autofix_chain_output_json(steps)
    assert problems == [], problems
    fixed_map = dict(fixed)
    assert "save project_id to output.json" in fixed_map["01_create_project"]
    assert "\"project_id\": ..." in fixed_map["01_create_project"]
    # The consumer's own request is never rewritten -- only the producer
    # missing the promise is touched.
    assert fixed_map["06_update_project"] == steps[1][1]


def test_autofix_folds_two_consumers_of_the_same_key_into_one_clause():
    """Two different downstream steps reading the *same* key from the
    same producer must not produce two appended clauses -- one fix,
    used by both."""
    steps = [
        ("01_create_project", "create a project"),
        ("02_create_task",
         "read project_id from ../01_create_project/output.json; create "
         "a task"),
        ("03_create_survey",
         "read project_id from ../01_create_project/output.json; create "
         "a survey"),
    ]
    fixed, problems = intent.autofix_chain_output_json(steps)
    assert problems == [], problems
    fixed_map = dict(fixed)
    producer_text = fixed_map["01_create_project"]
    # One "save ... to output.json" clause, not one per consumer -- the
    # key name itself legitimately appears twice within that one clause
    # (the plain mention and the JSON field), so what must not duplicate
    # is the clause-introducing phrase itself.
    assert producer_text.count("save project_id to output.json") == 1, (
        producer_text)


def test_autofix_leaves_an_already_consistent_chain_byte_identical():
    steps = [
        ("01_create_project",
         "create a project; save the id to output.json"),
        ("02_create_task",
         "read project_id from ../01_create_project/output.json; create "
         "a task"),
    ]
    fixed, problems = intent.autofix_chain_output_json(steps)
    assert problems == []
    assert fixed == steps


def test_autofix_cannot_invent_a_step_that_does_not_exist():
    """A reference to a directory nowhere in the chain has nothing to
    fix -- there is no producer request to append a clause to -- so it
    must surface as a real, unfixed problem rather than being silently
    dropped."""
    steps = [
        ("06_update_project",
         "read project_id from ../01_create_project/output.json"),
    ]
    fixed, problems = intent.autofix_chain_output_json(steps)
    assert len(problems) == 1
    assert "no step named 01_create_project" in problems[0]
    # Nothing to fix means nothing is changed.
    assert fixed == steps


def test_autofix_cannot_recover_a_key_from_vague_phrasing():
    """"the project's id" is not the single snake_case identifier the
    key-capturing regex requires -- this is deliberate: guessing a key
    name from prose risks writing a producer promise using the wrong
    key, which is worse than leaving the gap reported. The plain
    (non-autofix) check still reports it."""
    steps = [
        ("01_create_project", "create a project and print its id"),
        ("06_update_project",
         "read the project's id from ../01_create_project/output.json"),
    ]
    fixed, problems = intent.autofix_chain_output_json(steps)
    assert len(problems) == 1, problems
    assert "01_create_project" in problems[0]
    fixed_map = dict(fixed)
    assert fixed_map["01_create_project"] == steps[0][1]


def test_cli_default_rewrites_the_steps_file_in_place_no_flags():
    """No `--fix`, no flag of any kind -- this is the shape an
    orchestrator's generated `generate.sh` actually calls it with, and
    fixing has to happen anyway."""
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "01_create_project",
                 "request": "create a project and print its id"},
                {"dir": "06_update_project",
                 "request": "read project_id from "
                            "../01_create_project/output.json; patch the "
                            "project"},
            ], fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        with open(steps_path) as fh:
            rewritten = json.load(fh)
        producer = next(s for s in rewritten
                         if s["dir"] == "01_create_project")
        assert "output.json" in producer["request"]
        assert "project_id" in producer["request"]
        # And running it again against the already-fixed file is a
        # no-op that still reports success.
        result2 = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result2.returncode == 0, result2.stdout + result2.stderr


def test_cli_default_preserves_extra_per_step_fields():
    """A step object may carry fields beyond dir/request -- generate.sh's
    own template reads back an "api" and a "flags" field to build the
    real `team do` call. Fixing must only ever touch "request"; an
    earlier version rewrote the file as bare {"dir", "request"} objects
    and silently dropped every other field, which would make a fixed
    chain launch its steps with no --api even though the original file
    asked for one."""
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "01_create_project",
                 "request": "create a project and print its id",
                 "api": "maven"},
                {"dir": "02_create_task",
                 "request": "read project_id from "
                            "../01_create_project/output.json; create a "
                            "task",
                 "api": "maven", "flags": "--single-file"},
            ], fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        with open(steps_path) as fh:
            rewritten = json.load(fh)
        by_dir = {s["dir"]: s for s in rewritten}
        assert by_dir["01_create_project"]["api"] == "maven"
        assert by_dir["02_create_task"]["api"] == "maven"
        assert by_dir["02_create_task"]["flags"] == "--single-file"


def test_cli_default_still_exits_nonzero_when_unfixable():
    with tempfile.TemporaryDirectory() as tmp:
        steps_path = os.path.join(tmp, "steps.json")
        with open(steps_path, "w") as fh:
            json.dump([
                {"dir": "06_update_project",
                 "request": "read project_id from "
                            "../01_create_project/output.json"},
            ], fh)
        repo = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(repo, "team.py"),
             "lint-chain", steps_path],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "01_create_project" in result.stderr


def run():
    test_consistent_chain_reports_nothing()
    test_consumer_with_no_matching_producer_promise_is_flagged()
    test_reference_to_a_step_not_in_the_chain_is_flagged_distinctly()
    test_producer_promise_order_does_not_matter()
    test_qualified_reference_is_never_mistaken_for_a_write_promise()
    test_self_qualified_write_promise_is_recognised()
    test_sabotage_reproduces_the_live_self_qualified_false_positive()
    test_static_step_with_no_output_json_involvement_is_silent()
    test_cli_wrapper_exits_nonzero_on_an_unfixable_broken_chain()
    test_cli_wrapper_self_repairs_by_default_with_no_flags()
    test_cli_check_only_reports_without_touching_the_file()
    test_cli_wrapper_exits_zero_on_a_clean_chain()
    test_sabotage_dropping_the_write_promise_check_misses_everything()
    test_autofix_appends_the_exact_key_the_consumer_already_named()
    test_autofix_folds_two_consumers_of_the_same_key_into_one_clause()
    test_autofix_leaves_an_already_consistent_chain_byte_identical()
    test_autofix_cannot_invent_a_step_that_does_not_exist()
    test_autofix_cannot_recover_a_key_from_vague_phrasing()
    test_cli_default_rewrites_the_steps_file_in_place_no_flags()
    test_cli_default_preserves_extra_per_step_fields()
    test_cli_default_still_exits_nonzero_when_unfixable()
    print("scratch_chain_output_json: all checks passed")


if __name__ == "__main__":
    run()
