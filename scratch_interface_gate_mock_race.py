"""`interface_gate`'s repair call had two gaps, both found live in
`wednesdaysept23-6/bulk_generation_test/run_005`:

1. It never passed `env_extra`/`mock` to `codegen.generate_file`, so a
   repair for an `--api` component's file was never actually smoke-run
   against the mock -- `local_urls` rewrites any non-loopback URL to the
   dead `127.0.0.1:1` sink without an explicit mock override, so
   `gate_smoke` always took the "no mock configured" free pass instead
   of really exercising the file.
2. It had none of `run_components`' own idle-race protection: a single
   `pick_writer` pick, committed to unconditionally. Measured live: two
   successive repair rounds on the same file both landed on this
   fleet's slowest, most-often-asleep box, with no other node ever
   tried even though idle capacity existed.

This validates both fixes against the exact live shape, by monkeypatching
`codegen.generate_file`, `crossref.scan`, `team.pick_writer`,
`team.pick_idle_race_candidates`, and `team.race_generate_file` --
never touching the real network.

Run directly: `python3 scratch_interface_gate_mock_race.py`.
"""
import os
import sys
import tempfile

import codegen
import crossref
import intent
import swarm
import team


FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- %s" % detail) if detail else ""))
        FAILURES.append(name)


def _state():
    tmp = tempfile.mkdtemp()
    return swarm.State(os.path.join(tmp, "state.db"))


def _component(root):
    path = "main.py"
    full = os.path.join(root, path)
    with open(full, "w") as fh:
        fh.write("import requests\ndef main():\n    pass\n")
    c = intent.Component("main", path, 4, [], "upload three units")
    c.ok = True
    c.code = open(full).read()
    return c


def _one_shot_finding(component):
    def fake_scan(paths, root_, deps, paths_by_component):
        if fake_scan.calls == 0:
            fake_scan.calls += 1
            return [crossref.Finding(component.path, 1,
                                     "calls requests.HTTPError, but "
                                     "MockClient in main.py has no such "
                                     "attribute", "")]
        return []
    fake_scan.calls = 0
    return fake_scan


class _FakeNode:
    def __init__(self, name):
        self.name = name


def test_mock_and_env_extra_reach_the_repair_call():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)
    sentinel_mock = object()
    sentinel_env = {"API_BASE_URL": "http://127.0.0.1:54321"}

    captured = []

    def fake_generate_file(spec, out_path, node, model, cfg, state, **kw):
        captured.append(kw)
        return {"ok": False, "code": None}

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    old_race_candidates = team.pick_idle_race_candidates
    codegen.generate_file = fake_generate_file
    crossref.scan = _one_shot_finding(component)
    team.pick_idle_race_candidates = lambda *a, **k: []  # nobody idle
    try:
        team.interface_gate([component], cfg, state, 3000, root,
                            mock=sentinel_mock, env_extra=sentinel_env)
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan
        team.pick_idle_race_candidates = old_race_candidates

    check("the repair call received env_extra",
          any(kw.get("env_extra") == sentinel_env for kw in captured),
          captured)
    check("the repair call received the mock object",
          any(kw.get("mock") is sentinel_mock for kw in captured), captured)


def test_sabotage_no_mock_argument_loses_both():
    """Same fixture, called the pre-fix way (no mock/env_extra kwargs) --
    proves the fixture actually exercises the gap the fix closes."""
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    captured = []

    def fake_generate_file(spec, out_path, node, model, cfg, state, **kw):
        captured.append(kw)
        return {"ok": False, "code": None}

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    old_race_candidates = team.pick_idle_race_candidates
    codegen.generate_file = fake_generate_file
    crossref.scan = _one_shot_finding(component)
    team.pick_idle_race_candidates = lambda *a, **k: []
    try:
        team.interface_gate([component], cfg, state, 3000, root)
        # no mock, no env_extra -- the pre-fix call shape
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan
        team.pick_idle_race_candidates = old_race_candidates

    check("sabotage: with no mock/env_extra arguments (the pre-fix call "
         "shape), the repair call gets env_extra=None -- reproducing "
         "the live gap where a repair never actually reached the mock",
          all(kw.get("env_extra") is None for kw in captured), captured)


def test_idle_candidates_trigger_a_race_not_a_single_call():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    idle_node = _FakeNode("idle_fast_node")
    race_calls = []

    def fake_race(candidates, spec, out_path, cfg, state, **kw):
        race_calls.append((candidates, kw))
        return {"ok": True, "code": "import requests\n"}, idle_node, "model"

    single_calls = []

    def fake_generate_file(spec, out_path, node, model, cfg, state, **kw):
        single_calls.append((node, model, kw))
        return {"ok": False, "code": None}

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    old_race_candidates = team.pick_idle_race_candidates
    old_race = team.race_generate_file
    codegen.generate_file = fake_generate_file
    crossref.scan = _one_shot_finding(component)
    team.pick_idle_race_candidates = lambda *a, **k: [(idle_node, "model")]
    team.race_generate_file = fake_race
    try:
        team.interface_gate([component], cfg, state, 3000, root)
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan
        team.pick_idle_race_candidates = old_race_candidates
        team.race_generate_file = old_race

    check("when idle capacity exists, the repair races instead of "
         "committing to one node's single attempt",
          len(race_calls) == 1, race_calls)
    check("the losing/committed single-node generate_file path is not "
         "also called when a race happens",
          len(single_calls) == 0, single_calls)


def test_no_idle_candidates_falls_through_to_the_single_call_unchanged():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    race_calls = []

    def fake_race(*a, **k):
        race_calls.append((a, k))
        return {"ok": True, "code": "x"}, _FakeNode("x"), "m"

    single_calls = []

    def fake_generate_file(spec, out_path, node, model, cfg, state, **kw):
        single_calls.append((node, model))
        return {"ok": True, "code": "import requests\n"}

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    old_race_candidates = team.pick_idle_race_candidates
    old_race = team.race_generate_file
    codegen.generate_file = fake_generate_file
    crossref.scan = _one_shot_finding(component)
    team.pick_idle_race_candidates = lambda *a, **k: []  # nobody idle
    team.race_generate_file = fake_race
    try:
        team.interface_gate([component], cfg, state, 3000, root)
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan
        team.pick_idle_race_candidates = old_race_candidates
        team.race_generate_file = old_race

    check("with nobody idle, the ordinary single-node call still runs "
         "(no regression for the common case)",
          len(single_calls) == 1, single_calls)
    check("race_generate_file is never called when there is nothing to "
         "race against",
          len(race_calls) == 0, race_calls)


test_mock_and_env_extra_reach_the_repair_call()
test_sabotage_no_mock_argument_loses_both()
test_idle_candidates_trigger_a_race_not_a_single_call()
test_no_idle_candidates_falls_through_to_the_single_call_unchanged()

print("\n%d failed" % len(FAILURES))
sys.exit(1 if FAILURES else 0)
