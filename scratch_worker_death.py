"""A worker that raises must not wedge the run.

Exercises the real `run_components` with generation stubbed out, so nothing
touches the network. The failure being guarded against is a hang, so the test
runs it on a thread and fails if it does not return.
"""
import threading
import types

import codegen
import intent
import swarm
import team


def comp(name, deps, floor=2):
    c = intent.Component.__new__(intent.Component)
    c.name = name
    c.path = "scratchpkg/%s.py" % name
    c.spec = "irrelevant, generation is stubbed"
    c.floor = floor
    c.deps = deps
    c.ok = False
    c.code = ""
    c.error = ""
    c.target = ""
    c.attempts = 0
    c.seconds = 0.0
    return c


def main():
    plan = [comp("a", []), comp("b", []),
            comp("joiner", ["a", "b"])]

    real = codegen.generate_file

    def exploding(spec, out_path, node, model, cfg, state, **kw):
        # "b" is the one whose worker dies, mid-run, the way an OllamaError or
        # a node dropping off the LAN would kill it.
        if out_path.endswith("b.py"):
            raise RuntimeError("node fell over")
        return {"ok": True, "path": out_path, "code": "x = 1\n",
                "target": "stub", "attempts": 1, "seconds": 0.1,
                "history": []}

    codegen.generate_file = exploding
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        result = {}

        def run():
            try:
                result["value"] = team.run_components(
                    plan, cfg, state, repair=1, answer_tokens=100,
                    check=None, root="/tmp", tests=None)
            except BaseException as exc:
                result["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=45)

        assert not t.is_alive(), (
            "HANG: run_components never returned after a worker died")
        assert "error" not in result, "raised out: %r" % result.get("error")

        passed, _secs = result["value"]
        names = {c.name: c for c in plan}
        assert names["b"].ok is False, "the dead component was reported ok"
        assert "worker died" in (names["b"].error or ""), (
            "no cause recorded: %r" % names["b"].error)
        assert passed < 3, "a failed component counted as passing"
        print("worker death: run returned, %d/3 passed, cause recorded" % passed)
        print("  b.error = %s" % names["b"].error)
        print("OK")
        state.close()
    finally:
        codegen.generate_file = real


if __name__ == "__main__":
    main()
