"""Offline check that the invariant harness passes good code and fails bad.

No models involved: the model only ever supplies the assertion body, so that
body is written by hand here and the deterministic scaffolding around it is
what gets exercised.
"""
import os
import subprocess
import sys
import tempfile

import checks

BODY = """
check("basic", mod.slugify("Hello, World!") == "hello-world")
check("collapse", mod.slugify("a   b") == "a-b")
check("trim", mod.slugify("--Hi--") == "hi")
check("empty", mod.slugify("!!!") == "")
"""

GOOD = '''
import re


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s
'''

BAD = '''
def slugify(text):
    return text.lower()
'''

# Models reflexively wrap assertions in a main guard. Dropping the block would
# silently discard real checks and leave a test that passes because it tested
# nothing, so the guard is stripped and its contents promoted.
SCAFFOLDED = """
import os
check("basic", mod.slugify("Hello, World!") == "hello-world")
if __name__ == "__main__":
    check("guarded still runs", mod.slugify("a   b") == "a-b")
"""


def run(module_source, body):
    tmp = tempfile.mkdtemp()
    mod_path = os.path.join(tmp, "m.py")
    with open(mod_path, "w") as fh:
        fh.write(module_source)
    source = (checks._HEAD % {"path": "m.py", "module": mod_path}
              + checks._strip_scaffolding(body) + checks._TAIL)
    test_path = os.path.join(tmp, "t.py")
    with open(test_path, "w") as fh:
        fh.write(source)
    p = subprocess.run([sys.executable, test_path], capture_output=True,
                       text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


code, out = run(GOOD, BODY)
print("correct impl   -> rc=%d %s" % (code, out.splitlines()[-1]))
assert code == 0, "harness rejected correct code"

code, out = run(BAD, BODY)
print("wrong impl     -> rc=%d %s" % (code, out.splitlines()[0]))
assert code == 1, "harness PASSED wrong code"

code, out = run(GOOD, SCAFFOLDED)
print("scaffolded body-> rc=%d %s" % (code, out.splitlines()[-1]))
assert code == 0 and "2 invariant" in out, "guard stripping is wrong"

code, out = run(GOOD, "\n# nothing\n")
print("empty body     -> rc=%d %s" % (code, out.splitlines()[-1]))
assert code == 1, "a test with no assertions must not pass"

print("\nALL PASS")
