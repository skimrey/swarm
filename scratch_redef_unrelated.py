"""Two components defining one name is a clash whether or not they are linked.

Taken from a live failure: a types module and an unrelated maths module each
declared their own shape type, the joining component bridged them with a
throwaway class and a hardcoded field, and the program ran but did nothing.
Every per-file gate passed.
"""
import os
import tempfile

import intent


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def comp(name, path, deps=()):
    return intent.Component(name, path, 2, list(deps), "spec")


def main():
    root = tempfile.mkdtemp()
    write(root, "types_mod.py", "class ShapeData:\n    pass\n")
    # No dependency declared between these two, which is what hid the bug.
    write(root, "math_engine.py",
          "class ShapeData:\n    def __init__(self, angle):\n        self.angle = angle\n")
    write(root, "renderer.py", "def render(shapes):\n    return len(shapes)\n")

    plan = [comp("types", "types_mod.py"),
            comp("math_engine", "math_engine.py"),
            comp("renderer", "renderer.py", ["types"])]

    clashes = intent.redefined_symbols(plan, root)
    assert clashes, "the duplicate ShapeData was not detected"
    assert len(clashes) == 1, clashes
    path, symbol, owner = clashes[0]
    assert symbol == "ShapeData", clashes
    assert path == "math_engine.py", "wrong file blamed: %s" % path
    assert owner == "types_mod.py", "wrong owner: %s" % owner
    print("caught the unrelated duplicate: %s redeclares %s owned by %s"
          % (path, symbol, owner))

    # The dependency-based case must keep working and must not double-report.
    write(root, "api.py", "class ShapeData:\n    pass\n")
    plan2 = plan + [comp("api", "api.py", ["types"])]
    clashes2 = intent.redefined_symbols(plan2, root)
    pairs = [(p, s) for p, s, _ in clashes2]
    assert pairs.count(("api.py", "ShapeData")) == 1, "double reported: %s" % pairs
    assert ("math_engine.py", "ShapeData") in pairs
    print("dependency case still caught, no double reporting: %s" % pairs)

    # Every generated file carries a demo entry point, so `main` appearing in
    # all of them is normal. Flagging it produced three wrong findings on a
    # live run and burned two repair rounds rewriting files that worked.
    write(root, "c1.py", "class Alpha:\n    pass\n\ndef main():\n    pass\n")
    write(root, "c2.py", "class Beta:\n    pass\n\ndef main():\n    pass\n")
    shared = intent.redefined_symbols(
        [comp("c1", "c1.py"), comp("c2", "c2.py", ["c1"])], root)
    assert shared == [], "flagged a demo main(): %s" % shared
    print("ignores the demo main() every component defines")

    # Distinct names must stay silent, or the detector costs more than it saves.
    write(root, "a.py", "class Alpha:\n    pass\n")
    write(root, "b.py", "class Beta:\n    pass\n")
    quiet = intent.redefined_symbols([comp("a", "a.py"), comp("b", "b.py")], root)
    assert quiet == [], "fired on code with no duplicates: %s" % quiet
    print("silent on clean plans")
    print("OK")


if __name__ == "__main__":
    main()
