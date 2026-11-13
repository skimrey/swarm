"""A component's result has to be saved the instant it comes back, not only
once every component in the run has.

Measured live: `client.py` and `survey.html` both passed, `main.py` burned
six attempts and started a clean-slate restart, and the operator killed the
run there. Nothing had ever been written to `.swarm/plans/` -- `save_plan`
was only ever called once, in `cmd_do`, after `run_components` had already
returned everything -- so a `--retry` afterwards had no error to work from
and no record that the two passing files had passed at all. The honest
answer to "why did this fail" was "nothing was recorded", which is a worse
answer than any wrong one.

Exercises the real `run_components` with generation stubbed out (so nothing
touches the network) and one component deliberately held open on a
`threading.Event`, so the assertion is that the plan on disk reflects a
finished component *while the run is still going*, not merely after it
returns.
"""
import os
import shutil
import tempfile
import threading
import time

import codegen
import intent
import swarm
import team


def comp(name, deps, floor=2):
    c = intent.Component.__new__(intent.Component)
    c.name = name
    c.path = "%s.py" % name
    c.spec = "irrelevant, generation is stubbed"
    c.floor = floor
    c.deps = deps
    c.ok = False
    c.code = ""
    c.error = ""
    c.target = ""
    c.attempts = 0
    c.seconds = 0.0
    c.contract = ""
    return c


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_finished_component_is_on_disk_before_the_run_ends():
    plan = [comp("fast", []), comp("slow", [])]
    release = threading.Event()
    real = codegen.generate_file

    def stub(spec, out_path, node, model, cfg, state, **kw):
        if out_path.endswith("slow.py"):
            release.wait(timeout=20)
            # Shaped as `gave_up_early` (carries NO_MOCK_MARKER) so this
            # stays off the whole-loop-restart and replan_component paths
            # -- both make a real network call, which is a different
            # mechanism than the one under test here.
            return {"ok": False, "path": out_path, "code": "",
                    "target": "stub", "attempts": 1, "seconds": 0.1,
                    "history": [{"stage": "smoke",
                                "error": "boom -- " + codegen.NO_MOCK_MARKER}]}
        return {"ok": True, "path": out_path, "code": "x = 1\n",
                "target": "stub", "attempts": 1, "seconds": 0.1,
                "history": []}

    codegen.generate_file = stub
    root = tempfile.mkdtemp(prefix="checkpoint-")
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        result = {}

        def run():
            result["value"] = team.run_components(
                plan, cfg, state, repair=1, answer_tokens=100, check=None,
                root=root, tests=None,
                request="write fast.py and slow.py")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        try:
            seen = wait_for(lambda: (team.load_plan(root) or {}).get(
                "components") and any(
                    c["name"] == "fast" and c["ok"]
                    for c in team.load_plan(root)["components"]))
            assert seen, (
                "the fast component finished but was never written to "
                "disk while the run was still going -- an interruption "
                "at this point would lose it, which is the exact failure "
                "this exists to prevent")
            # And the still-running component must not be reported as a
            # false pass just because it is present in the snapshot.
            saved = team.load_plan(root)
            slow_entry = next(c for c in saved["components"]
                              if c["name"] == "slow")
            assert slow_entry["ok"] is False, (
                "a component still in flight was checkpointed as passing")
            print("  a finished component is on disk while its sibling is "
                  "still running")
        finally:
            release.set()
            t.join(timeout=20)
            assert not t.is_alive(), "HANG: run_components never returned"

        final = team.load_plan(root)
        by_name = {c["name"]: c for c in final["components"]}
        assert by_name["fast"]["ok"] is True
        assert by_name["slow"]["ok"] is False
        assert "boom" in by_name["slow"]["error"] or by_name["slow"]["error"]
        print("  the final saved plan matches both outcomes")
        state.close()
    finally:
        codegen.generate_file = real
        shutil.rmtree(root, ignore_errors=True)


def test_a_refused_oversized_component_is_also_checkpointed():
    # The "too large to rewrite whole" refusal path sets `component.ok =
    # False` and returns before ever calling `generate_file` at all -- a
    # second, independent completion site from the main one, and one that
    # `_checkpoint` has to cover too, not just the common case.
    existing_path = None
    c = comp("big", [])

    def stub(spec, out_path, node, model, cfg, state, **kw):
        raise AssertionError("should never be called for a refused component")

    real = codegen.generate_file
    codegen.generate_file = stub
    root = tempfile.mkdtemp(prefix="checkpoint-refuse-")
    try:
        existing_path = os.path.join(root, "big.py")
        # A large-enough prior file to trip the refusal, regardless of the
        # exact threshold any single node happens to be configured with.
        with open(existing_path, "w", encoding="utf-8") as fh:
            fh.write("x = 1\n" * 200000)
        cfg = swarm.Config.load()
        state = swarm.State()
        team.run_components([c], cfg, state, repair=1, answer_tokens=100,
                            check=None, root=root, tests=None,
                            existing={"big.py": "x = 1\n" * 200000},
                            request="rewrite big.py")
        saved = team.load_plan(root)
        assert saved is not None, "a refused component was never saved"
        entry = next(x for x in saved["components"] if x["name"] == "big")
        assert entry["ok"] is False
        assert "too large" in entry["error"]
        print("  a component refused for being too large is checkpointed "
              "too")
        state.close()
    finally:
        codegen.generate_file = real
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_a_finished_component_is_on_disk_before_the_run_ends()
    test_a_refused_oversized_component_is_also_checkpointed()
    print("OK")


if __name__ == "__main__":
    main()
