"""Validates `codegen._missing_sibling_error` and its wiring into
`gate_smoke`: a smoke-run crash naming a `../sibling/output.json` that
genuinely does not exist yet on real disk must not count as a failure at
all -- `gate_smoke` returns `None` (the same "nothing to report" result a
passing run gets), not a failure string with a nicer explanation. This is
a stronger claim than "explain it better": no amount of rewriting this
file can make a sibling step's real output exist before that sibling has
been run, so failing the component here blocks nothing worth blocking --
it only burns repair attempts. A crash on a path that *does* exist is a
real failure and must still block, since that is a bug in the code, not
an ordering problem.

Live cost this exists to stop paying: a correct `main.py` for
`02_create_task`, generated before `01_create_project`'s `main.py` had
actually been run, burned 6 attempts, a full-loop restart, then 6 more --
12 attempts total -- because its own crash ("RuntimeError: Failed to read
project ID from ../01_create_project/output.json") was indistinguishable
from a real bug to everything that read it before this existed.

Sabotage-validated: a version of the detector that does not check whether
the real file actually exists would misreport a real read-bug (a sibling
file that *is* there, but the code reads the wrong key out of it) as "not
a defect" too -- checked directly below.
"""
import os
import tempfile

import codegen


SOURCE = '''
import json

def main():
    with open("../01_create_project/output.json") as f:
        data = json.load(f)
    project_id = data["id"]
    print(project_id)

if __name__ == "__main__":
    main()
'''

CRASH_MISSING = (
    'Traceback (most recent call last):\n'
    '  File "main.py", line 10, in <module>\n'
    '    main()\n'
    '  File "main.py", line 5, in main\n'
    '    with open("../01_create_project/output.json") as f:\n'
    "FileNotFoundError: [Errno 2] No such file or directory: "
    "'../01_create_project/output.json'\n"
)

CRASH_REAL_BUG = (
    'Traceback (most recent call last):\n'
    '  File "main.py", line 10, in <module>\n'
    '    main()\n'
    '  File "main.py", line 6, in main\n'
    '    project_id = data["id"]\n'
    "KeyError: 'id'\n"
)


def test_missing_file_is_recognised() -> None:
    with tempfile.TemporaryDirectory() as cwd:
        # deliberately no ../01_create_project directory at all
        ref = codegen._missing_sibling_error(CRASH_MISSING, SOURCE, cwd)
        assert ref == "../01_create_project/output.json", (
            "a crash naming a sibling path that does not exist on real "
            "disk must be recognised as a missing dependency, not a "
            "code defect")
    print("_missing_sibling_error: a genuinely absent sibling file is caught")


def test_real_file_present_is_not_excused() -> None:
    with tempfile.TemporaryDirectory() as parent:
        cwd = os.path.join(parent, "02_create_task")
        os.makedirs(cwd)
        sib_dir = os.path.join(parent, "01_create_project")
        os.makedirs(sib_dir)
        with open(os.path.join(sib_dir, "output.json"), "w") as fh:
            fh.write('{"id": "real-uuid-1234"}')
        # The real file exists -- a crash here (wrong key, bad parse) is
        # this file's own fault, and the traceback does not even name the
        # path (a KeyError on a key inside it, not a missing-file error),
        # so this must return None either way.
        ref = codegen._missing_sibling_error(CRASH_REAL_BUG, SOURCE, cwd)
        assert ref is None, (
            "a real bug against a real, present sibling file must not be "
            "excused as 'the dependency has not been run yet'")
    print("_missing_sibling_error: a present sibling file is never excused")


def test_sabotage_ignoring_real_disk_check() -> None:
    """A detector that only pattern-matches the crash text, without ever
    checking whether the referenced file is actually on disk, would wrongly
    excuse the real-bug case above too -- this is the exact bug this test
    would have caught."""
    def naive(out: str, source: str, _cwd: str):
        for ref in codegen.referenced_parent_paths(source):
            if ref in (out or ""):
                return ref
        return None

    with tempfile.TemporaryDirectory() as parent:
        cwd = os.path.join(parent, "02_create_task")
        os.makedirs(cwd)
        sib_dir = os.path.join(parent, "01_create_project")
        os.makedirs(sib_dir)
        with open(os.path.join(sib_dir, "output.json"), "w") as fh:
            fh.write('{"id": "real-uuid-1234"}')
        # The naive version matches on the *open()* call site's path
        # literal appearing anywhere in the source, even though the actual
        # crash text (CRASH_REAL_BUG) never names the path at all -- so
        # sabotage it a different way: feed it a crash that does mention
        # the path incidentally (e.g. a repr of the whole open() call in a
        # wrapping error) to show the naive version does not gate on
        # real-disk presence.
        misleading = CRASH_REAL_BUG + "\n(while reading ../01_create_project/output.json)"
        naive_ref = naive(misleading, SOURCE, cwd)
        assert naive_ref == "../01_create_project/output.json", (
            "the naive version should have matched here (that is the bug)")
        real_ref = codegen._missing_sibling_error(misleading, SOURCE, cwd)
        assert real_ref is None, (
            "the real detector must refuse to excuse this because the "
            "file genuinely exists on disk -- unlike the naive version")
    print("_missing_sibling_error: sabotage (skip the real-disk check) "
         "reproduces the false excuse; the real check does not")


MAIN_SOURCE = '''
import json

def main():
    with open("../01_create_project/output.json") as f:
        data = json.load(f)
    project_id = data["id"]
    print(project_id)

if __name__ == "__main__":
    main()
'''


def test_gate_smoke_does_not_fail_on_missing_sibling() -> None:
    """The actual behavioural claim: `gate_smoke` itself, run end to end
    against a real file, must return `None` (pass) when the only thing
    wrong is a sibling step's output not existing yet -- not merely a
    nicer failure message. A `None` result is indistinguishable from a
    clean run to every caller of this gate, which is the whole point:
    this class of failure must not block anything downstream."""
    with tempfile.TemporaryDirectory() as parent:
        cwd = os.path.join(parent, "02_create_task")
        os.makedirs(cwd)
        # deliberately no ../01_create_project anywhere
        main_path = os.path.join(cwd, "main.py")
        with open(main_path, "w") as fh:
            fh.write(MAIN_SOURCE)
        result = codegen.gate_smoke(main_path, cwd)
        assert result is None, (
            "gate_smoke must treat a missing sibling as a passed "
            "(skipped) check, not a failure -- got: %r" % (result,))
    print("gate_smoke: a missing sibling step's output does not fail the gate")


def test_gate_smoke_still_fails_on_a_real_bug() -> None:
    """The other half of the same claim: this must not become a blanket
    excuse for every crash near a `../` path. A real bug against a
    sibling file that genuinely exists -- reading the wrong key -- must
    still fail the gate, exactly as it always has."""
    with tempfile.TemporaryDirectory() as parent:
        cwd = os.path.join(parent, "02_create_task")
        os.makedirs(cwd)
        sib_dir = os.path.join(parent, "01_create_project")
        os.makedirs(sib_dir)
        with open(os.path.join(sib_dir, "output.json"), "w") as fh:
            fh.write('{"project_id": "real-uuid-1234"}')  # wrong key: "id" expected
        main_path = os.path.join(cwd, "main.py")
        with open(main_path, "w") as fh:
            fh.write(MAIN_SOURCE)
        result = codegen.gate_smoke(main_path, cwd)
        assert result is not None, (
            "a real bug (wrong key) against a sibling file that genuinely "
            "exists must still fail the gate")
        assert "KeyError" in result or "'id'" in result, (
            "the failure must still name the real defect: %r" % (result,))
    print("gate_smoke: a real bug against a present sibling file still fails")


if __name__ == "__main__":
    test_missing_file_is_recognised()
    test_real_file_present_is_not_excused()
    test_sabotage_ignoring_real_disk_check()
    test_gate_smoke_does_not_fail_on_missing_sibling()
    test_gate_smoke_still_fails_on_a_real_bug()
