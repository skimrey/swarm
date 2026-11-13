"""Every component must be reachable by some worker, or the run never ends.

Two ways the claim loop could strand a component, both seen live tonight, both
presenting identically and in the worst possible way: 0% CPU, no open network
connections, no error, no log line. Everything about the symptom points at a
wedged node, and the process was never talking to one.

1. Workers poll one floor each, 4 and 2, and the claim matches exactly, so a
   component the planner labels 3 belongs to nobody.

2. A worker steps aside when a faster worker is idle, which is right at the
   tail of a plan and fatal when the faster worker cannot take the work: the
   only floor-4 worker waits for a light box, the light box polls floor 2 and
   never sees the component, and both sleep until killed. This arrived with no
   code change at all, when e4b-linux measured 44.5 tok/s against the 12b's
   42.4 and the ordering flipped.
"""
import shutil
import tempfile
import threading

import codegen
import intent
import swarm
import team

TIMEOUT = 30.0


def _instant(spec, out_path, node, model, cfg, state, **kw):
    """Stand in for a model: write something valid, immediately."""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("VALUE = 1\n")
    return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
            "target": "%s/%s" % (node.name, model), "code": "VALUE = 1\n"}


def run_plan(specs, timeout=TIMEOUT):
    """Run a plan shape with generation stubbed out. Raises if it hangs."""
    components = []
    for name, path, floor, deps in specs:
        component = intent.Component(name, path, floor, list(deps), "spec")
        component.contract = ""
        components.append(component)

    real = codegen.generate_file
    codegen.generate_file = _instant
    root = tempfile.mkdtemp(prefix="plan-")
    box = {}
    try:
        thread = threading.Thread(
            target=lambda: box.update(r=team.run_components(
                components, swarm.Config.load(), swarm.State(), root=root)),
            daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise AssertionError(
                "deadlocked on %s: a component was never claimed and the run "
                "never ended" % [s[0] for s in specs])
        return box["r"]
    finally:
        codegen.generate_file = real
        shutil.rmtree(root, ignore_errors=True)


FLOORS = ([2, 4], [3], [1], [1, 2, 3, 4], [5], [0])

SHAPES = {
    # The one that hung live: a lone FLOOR 4 at the tail with faster light
    # workers idle and unable to claim it.
    "lone floor-4 tail": [("constants", "constants.py", 2, []),
                          ("shapes", "shapes.py", 2, ["constants"]),
                          ("main", "main.py", 4, ["shapes", "constants"])],
    "floor-4 in middle": [("constants", "constants.py", 2, []),
                          ("shapes", "shapes.py", 2, ["constants"]),
                          ("engine", "engine.py", 4, ["shapes"]),
                          ("main", "main.py", 2, ["engine", "shapes"])],
    "all floor 4": [("a", "a.py", 4, []), ("b", "b.py", 4, ["a"])],
    "single floor 4": [("only", "only.py", 4, [])],
    "wide, no deps": [("a", "a.py", 2, []), ("b", "b.py", 2, []),
                      ("c", "c.py", 4, []), ("d", "d.py", 2, [])],
}


def main() -> None:
    for floors in FLOORS:
        specs = [("c%d" % i, "c%d.py" % i, f, []) for i, f in enumerate(floors)]
        ok, seconds = run_plan(specs)
        assert ok == len(floors), "%s -> only %d of %d written" % (
            floors, ok, len(floors))
        print("floors %-14s all %d written in %.1fs"
              % (str(floors), ok, seconds))

    for label, specs in SHAPES.items():
        ok, seconds = run_plan(specs)
        assert ok == len(specs), "%s -> only %d of %d" % (label, ok, len(specs))
        print("%-20s all %d written in %.1fs" % (label, ok, seconds))
    print("OK")


if __name__ == "__main__":
    main()
