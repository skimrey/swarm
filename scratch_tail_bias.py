"""A genuinely idle floor-4 worker must not defer to a slower biased box.

Live tonight: `main.py` (floor 2) depended on `api_client.py` (floor 4). Once
api_client.py finished, g26b sat idle with no more floor-4 work coming and
should have taken the one remaining file -- it is the fastest real box in the
fleet. Instead it deferred to `mac/gemma4:e4b`, which only looked faster
because `model_bias` (2.0, set to win a *different* race against
`e4b-linux`) had inflated its rate past g26b's own -- and g26b's opportunistic
floor-2 rate is *itself* discounted to 0.3x on purpose, so it can step aside
for routine work while more floor-4 work might still be coming. That
reasoning has nothing left to stand on once no floor-4 component remains at
all: deferring then buys nothing, because g26b has nothing else to wait for.

This never needs real generation or a live node: `codegen.generate_file` is
replaced with a stub that records who was asked to write each file and sleeps
long enough to force a genuine tail (light workers idle well before the
floor-4 work finishes), then asserts the fixed claim loop hands the tail to
g26b, and that a plan with more floor-4 work still pending leaves the old
deferral behaviour untouched.
"""
import json
import os
import shutil
import tempfile
import threading
import time

import codegen
import intent
import swarm
import team

# This test's whole scenario depends on there being exactly one
# capability-4 node (g26b) and two capability-2 light boxes (mac,
# e4b-linux) racing it on paper via inflated rates -- that is the live bug
# it reproduces. `gemma4:e4b`/`gemma4-e4b-fixed:latest` are capability 4
# too now (see config.json's 2026-09-20 note), which makes both of those
# boxes genuine floor-4 competitors and means "the tail must go to g26b
# specifically" is no longer a determinate claim -- any of the three could
# legitimately end up the one with nothing left. So this test loads its own
# temp copy of config.json (the same pattern `scratch_floor4_fallback.py`/
# `scratch_serial_link_routing.py` already use) and resets those two
# models back to capability 2, reconstructing the exact topology the rest
# of this file's comments and RATES table were written against, rather
# than depending on whatever the live config happens to say.
with open(os.path.join(swarm.HERE, "config.json"), encoding="utf-8") as fh:
    _real_cfg = json.load(fh)
for _name in ("gemma4:e4b", "gemma4-e4b-fixed:latest"):
    if _name in _real_cfg.get("models", {}):
        _real_cfg["models"][_name]["capability"] = 2
_tmp_dir = tempfile.mkdtemp(prefix="tail-bias-cfg-")
_tmp_cfg_path = os.path.join(_tmp_dir, "config.json")
with open(_tmp_cfg_path, "w", encoding="utf-8") as fh:
    json.dump(_real_cfg, fh)
TEST_CFG = swarm.Config.load(_tmp_cfg_path)

TIMEOUT = 15.0

# The exact shape measured live tonight (see AGENTS.md / the run this bug was
# found in): mac's model_bias (2.0) beats e4b-linux's raw rate, and g26b's
# own opportunistic-floor-2 bias (0.3) is low enough that *both* light boxes
# out-rank it on paper even though g26b is the fastest real box in the fleet.
RATES = {
    ("mac", "gemma4:e4b"): 30.6,
    ("e4b-linux", "gemma4-e4b-fixed:latest"): 38.1,
    ("e2b-2", "gemma4:e2b"): 5.0,
    ("g26b", "gemma4:12b"): 43.2,
    ("g26b", "gemma4-fixed:latest"): 39.2,
}


def _fake_measured_rate(state, node, model):
    return RATES.get((node, model), 0.0)


def run_plan(specs, sleeps, timeout=TIMEOUT):
    """Run a plan shape with generation stubbed to a controlled sleep.

    Returns {component_name: "node/model"} recording who wrote each file.
    """
    components = []
    for name, path, floor, deps in specs:
        component = intent.Component(name, path, floor, list(deps), "spec")
        component.contract = ""
        components.append(component)

    claimed = {}

    def _instant(spec, out_path, node, model, cfg, state, **kw):
        name = out_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        time.sleep(sleeps.get(name, 0.0))
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        claimed[name] = "%s/%s" % (node.name, model)
        return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                "target": "%s/%s" % (node.name, model), "code": "VALUE = 1\n"}

    real_gen = codegen.generate_file
    real_rate = team.measured_rate
    codegen.generate_file = _instant
    team.measured_rate = _fake_measured_rate
    root = tempfile.mkdtemp(prefix="plan-")
    box = {}
    try:
        thread = threading.Thread(
            target=lambda: box.update(r=team.run_components(
                components, TEST_CFG, swarm.State(), root=root)),
            daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise AssertionError("deadlocked: a component was never claimed")
        return claimed
    finally:
        codegen.generate_file = real_gen
        team.measured_rate = real_rate
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    # Tonight's exact shape: one floor-4 file, one floor-2 file that depends
    # on it. Once api_client finishes, g26b has nothing else in the plan --
    # the tail must go to it, not to whichever light box merely looks
    # faster on paper.
    specs = [("api_client", "api_client.py", 4, []),
             ("main", "main.py", 2, ["api_client"])]
    sleeps = {"api_client": 0.15}
    for trial in range(10):
        claimed = run_plan(specs, sleeps)
        assert claimed["main"].startswith("g26b/"), (
            "trial %d: tail went to %r instead of the genuinely idle g26b "
            "worker" % (trial, claimed["main"]))
    print("lone floor-4 dependency, idle tail -> g26b in 10/10 trials")

    # With more floor-4 work still pending, g26b must keep deferring to the
    # light boxes as before -- the fix only fires when there is truly
    # nothing left for the strong worker to wait for.
    specs2 = [("api_client", "api_client.py", 4, []),
              ("engine", "engine.py", 4, ["api_client"]),
              ("main", "main.py", 2, [])]
    sleeps2 = {"api_client": 0.1, "engine": 0.3}
    claimed2 = run_plan(specs2, sleeps2)
    assert not claimed2["main"].startswith("g26b/"), (
        "g26b grabbed routine work (%r) while more floor-4 work of its own "
        "was still pending -- it should have kept deferring" % claimed2)
    print("floor-4 work still pending -> g26b still defers (%s)"
          % claimed2["main"])
    print("OK")


if __name__ == "__main__":
    main()
