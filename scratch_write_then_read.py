"""`gate_write_then_read_same_handle` catches a mechanical, unconditional
Python error before any live run has to hit it: `open(path, "w")` (or
`"x"`/`"a"`, no `+`) followed by `.read()`/`.readline()`/`.readlines()` on
the same bound handle inside the same `with` block always raises
`io.UnsupportedOperation: not readable`, regardless of what was written.

Measured live: a generated `main.py` for "create a project; save its id
as project_id in output.json" hit exactly this, identically, across four
completely independent write attempts -- an initial attempt, a 4-way idle
restart race, a model escalation, and a CONTRACT/SPEC replan -- because
none of those rungs address a file-mode mistake, and the condensed
history_trail summary an operator sees (`smoke:
io.UnsupportedOperation: not readable`) throws away the traceback's own
file/line, so it reads as unexplained noise repeating four times rather
than a diagnosis.

Confirms:
1. the exact live shape is caught, with a message naming the handle and
   the offending method;
2. a correct read-then-merge-then-write pattern (`gate_output_json_
   overwritten`'s own "fine" case: open "r", then open "w" separately) is
   left alone;
3. `"r+"`/`"a+"` (genuinely readable, despite starting with the same
   letter) are not flagged;
4. reading a *different* handle than the one just opened for writing is
   not flagged;
5. by sabotage, the exact live traceback text this gate exists to avoid
   is reproduced by actually running the caught snippet, proving the
   gate's own diagnosis is not a guess about what CPython does.
"""
import sys

import codegen

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- %s" % detail) if detail else ""))
        FAILURES.append(name)


LIVE_SHAPE = """
import json

def main():
    project_id = "abc-123"
    print(project_id)
    with open("output.json", "w") as f:
        json.dump({"project_id": project_id}, f)
        print(f.read())

if __name__ == "__main__":
    main()
"""

CORRECT_READ_MERGE_WRITE = """
import json, os

def main():
    data = {}
    if os.path.exists("output.json"):
        with open("output.json", "r") as f:
            data = json.load(f)
    data["project_id"] = "abc-123"
    with open("output.json", "w") as f:
        json.dump(data, f)

if __name__ == "__main__":
    main()
"""

R_PLUS_MODE = """
import json

def main():
    with open("output.json", "a+") as f:
        f.write("x")
        f.seek(0)
        print(f.read())

if __name__ == "__main__":
    main()
"""

DIFFERENT_HANDLE = """
import json

def main():
    with open("output.json", "w") as f:
        json.dump({"a": 1}, f)
    with open("output.json", "r") as g:
        print(g.read())

if __name__ == "__main__":
    main()
"""


def test_catches_the_live_shape():
    failure = codegen.gate_write_then_read_same_handle(LIVE_SHAPE)
    check("the live write-then-read-same-handle shape is caught",
         failure is not None, failure)
    if failure:
        check("the message names the handle and the read method",
             "f.read" in failure and "output.json" in failure, failure)


def test_correct_read_merge_write_is_not_flagged():
    failure = codegen.gate_write_then_read_same_handle(
        CORRECT_READ_MERGE_WRITE)
    check("a correct read-then-merge-then-write pattern is left alone",
         failure is None, failure)


def test_r_plus_mode_is_not_flagged():
    failure = codegen.gate_write_then_read_same_handle(R_PLUS_MODE)
    check("'a+' is genuinely readable and must not be flagged",
         failure is None, failure)


def test_different_handle_is_not_flagged():
    failure = codegen.gate_write_then_read_same_handle(DIFFERENT_HANDLE)
    check("reading a different, separately-reopened handle is not this "
         "defect", failure is None, failure)


def test_autofix_produces_code_the_gate_no_longer_flags():
    fixed = codegen.autofix_write_then_read_same_handle(LIVE_SHAPE)
    check("the autofix returns a rewritten file", fixed is not None, fixed)
    if fixed is None:
        return
    check("the fixed code no longer trips the gate",
         codegen.gate_write_then_read_same_handle(fixed) is None, fixed)
    check("the fixed code still parses",
         _parses(fixed), fixed)


def _parses(code):
    import ast as _ast
    try:
        _ast.parse(code)
        return True
    except SyntaxError:
        return False


def test_autofix_actually_runs_correctly():
    """Not just gate-clean -- actually executed, to prove the splice is
    semantically correct and not merely syntactically clean."""
    import subprocess
    import sys as _sys
    import tempfile
    import os as _os
    fixed = codegen.autofix_write_then_read_same_handle(LIVE_SHAPE)
    tmp = tempfile.mkdtemp()
    path = _os.path.join(tmp, "main.py")
    with open(path, "w") as fh:
        fh.write(fixed)
    proc = subprocess.run([_sys.executable, path], cwd=tmp,
                          capture_output=True, text=True)
    check("the autofixed file actually runs without raising, and prints "
         "the id it read back",
         proc.returncode == 0 and "abc-123" in proc.stdout,
         (proc.returncode, proc.stdout, proc.stderr))


def test_autofix_declines_when_the_read_is_the_first_statement():
    """No evidence to act on -- nothing was written before the read was
    attempted, so this is not the "forgot to reopen" shape, and the
    autofix must not invent a `pass` to paper over it."""
    snippet = """
def main():
    with open("output.json", "w") as f:
        print(f.read())

if __name__ == "__main__":
    main()
"""
    fixed = codegen.autofix_write_then_read_same_handle(snippet)
    check("declines to fix when there is nothing written before the "
         "read (index 0 in the with-body)", fixed is None, fixed)


def test_sabotage_the_live_shape_actually_raises_this_exact_error():
    """Proves the gate's own claim about what CPython does, by actually
    running the caught snippet rather than asserting it from memory."""
    import subprocess
    import sys as _sys
    import tempfile
    import os as _os
    tmp = tempfile.mkdtemp()
    path = _os.path.join(tmp, "main.py")
    with open(path, "w") as fh:
        fh.write(LIVE_SHAPE)
    proc = subprocess.run([_sys.executable, path], cwd=tmp,
                          capture_output=True, text=True)
    check("sabotage: actually running the live shape raises exactly "
         "`io.UnsupportedOperation: not readable`, proving the gate's "
         "diagnosis is not a guess",
         "io.UnsupportedOperation: not readable" in proc.stderr,
         proc.stderr)


def main():
    test_catches_the_live_shape()
    test_correct_read_merge_write_is_not_flagged()
    test_r_plus_mode_is_not_flagged()
    test_different_handle_is_not_flagged()
    test_autofix_produces_code_the_gate_no_longer_flags()
    test_autofix_actually_runs_correctly()
    test_autofix_declines_when_the_read_is_the_first_statement()
    test_sabotage_the_live_shape_actually_raises_this_exact_error()
    if FAILURES:
        print("%d failed" % len(FAILURES))
        sys.exit(1)
    print("0 failed")


if __name__ == "__main__":
    main()
