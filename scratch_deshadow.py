"""A generated top-level file must never take a standard library module name.

The failure it prevents is badly disguised: one bad filename makes every other
component fail to import, so the run looks like four broken files instead of
one naming mistake.
"""
import os
import subprocess
import sys
import tempfile

import intent


def comp(name, path, deps=()):
    return intent.Component(name, path, 2, list(deps), "spec")


def main():
    # The real case, taken from a live run.
    plan = [comp("types", "types.py"),
            comp("math_engine", "math_engine.py"),
            comp("renderer", "renderer.py", ["types"])]
    renamed = intent.deshadow(plan)
    assert renamed == [("types.py", "types_mod.py")], renamed
    assert plan[0].path == "types_mod.py"
    assert plan[1].path == "math_engine.py", "an innocent name was renamed"
    print("renames only the shadowing file: %s" % renamed)

    # Inside a package nothing is shadowed, so nothing should move.
    pkg = [comp("types", "shapes/types.py"), comp("api", "shapes/__init__.py")]
    assert intent.deshadow(pkg) == [], "renamed a file that shadows nothing"
    assert pkg[0].path == "shapes/types.py"
    print("leaves package-internal names alone: shapes/types.py")

    # Other names a planner reaches for.
    for risky in ("string.py", "random.py", "math.py", "json.py", "time.py"):
        one = [comp("x", risky)]
        assert intent.deshadow(one), "%s was not caught" % risky
    print("catches string/random/math/json/time as well")

    # Prove the hazard is real rather than theoretical: a top-level types.py
    # breaks a plain interpreter start in a way that has nothing to do with
    # the file's contents.
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "types.py"), "w") as fh:
        fh.write("from typing import List\n")
    r = subprocess.run([sys.executable, "-c", "import argparse"], cwd=d,
                       capture_output=True, text=True)
    assert r.returncode != 0, "expected the shadow to break the interpreter"
    assert "types" in r.stderr
    print("confirmed hazard: `import argparse` fails next to a types.py")
    print("   %s" % r.stderr.strip().splitlines()[-1][:96])
    print("OK")


if __name__ == "__main__":
    main()
