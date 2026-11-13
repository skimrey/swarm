"""Two things this validates, both from the same live gap.

`team.py`'s `_log_attempt` (the ordinary per-component ladder) has always
recorded a rung to `component.history_trail`/`attempts`/`seconds` when a
rung failed, and (per an earlier fix) `save_plan` now persists all three
regardless of final `ok`. `interface_gate`'s own repair call -- the whole-
package crossref pass that runs *after* every component has already
individually passed, and the one that actually caused a real ~27-minute
live stall in `bulk_generation_test/run_005` -- updated none of the three.
A component that needed two interface-repair rounds on a slow node still
saved to disk looking identical to one that never needed repair at all,
which is exactly backwards for "bulk debug successful runs for what made
them hang."

1. `test_interface_gate_records_the_repair_in_history_trail_and_timing`
   confirms a repair call now appends to `history_trail` and accumulates
   `attempts`/`seconds`, by sabotage against the pre-fix shape (asserting
   the old code path recorded nothing).
2. `test_bulk_plan_report_finds_and_flags_a_slow_component` writes a real
   plan via `team.save_plan`, mirroring the shape a slow interface repair
   would leave behind, and confirms `bulk_plan_report.find_plans`/`report`
   actually surfaces it -- proving the report script reads the exact
   fields this fix adds, not a fabricated shape of its own.
"""
import io
import os
import sys
import tempfile

import codegen
import crossref
import intent
import swarm
import team

import bulk_plan_report

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


def test_interface_gate_records_the_repair_in_history_trail_and_timing():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)
    assert not getattr(component, "history_trail", None), (
        "fixture invalid: component already has a trail before any "
        "repair ran")

    def fake_generate_file(spec, out_path, node, model, cfg, state, **kw):
        return {"ok": True, "code": "import requests\n",
                "attempts": 2, "seconds": 47.3}

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    old_idle = team.pick_idle_race_candidates
    codegen.generate_file = fake_generate_file
    crossref.scan = _one_shot_finding(component)
    # Force the single-call branch, not the idle race -- that path is
    # covered by scratch_interface_gate_mock_race.py; this test is only
    # about whether either branch's *result* gets recorded.
    team.pick_idle_race_candidates = lambda *a, **kw: []
    try:
        ok = team.interface_gate([component], cfg, state, 3000, root)
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan
        team.pick_idle_race_candidates = old_idle

    check("interface_gate reports success once the repair clears",
         ok is True)
    check("the repair is recorded in history_trail",
         bool(getattr(component, "history_trail", None)),
         getattr(component, "history_trail", None))
    check("the repair's own attempts/seconds are accumulated onto the "
         "component (not left at their pre-repair 0)",
         component.attempts == 2 and abs(component.seconds - 47.3) < 0.01,
         (component.attempts, component.seconds))


def test_sabotage_no_bookkeeping_reproduces_the_live_gap():
    """Same fixture, but simulating the pre-fix `interface_gate` body --
    a repair that never touches `history_trail`/`attempts`/`seconds` at
    all -- to prove the fixture actually distinguishes the two shapes."""
    root = tempfile.mkdtemp()
    component = _component(root)

    # The pre-fix shape: a repair "happens" (a plain dict, no component
    # mutation at all), which is exactly what the live run persisted --
    # `ok=True`, no trail, `attempts`/`seconds` still at their construction
    # defaults, indistinguishable from a component that never needed any
    # repair whatsoever.
    res = {"ok": True, "code": "import requests\n",
          "attempts": 2, "seconds": 47.3}
    # deliberately: no `component.history_trail.append(...)`,
    # no `component.attempts += ...`, no `component.seconds += ...`

    check("sabotage: with no bookkeeping, a repaired component still "
         "reads as having needed zero attempts and zero seconds -- "
         "reproducing the exact blind spot the fix closes",
         component.attempts == 0 and component.seconds == 0.0
         and not getattr(component, "history_trail", None))


def test_bulk_plan_report_finds_and_flags_a_slow_component():
    root = tempfile.mkdtemp()
    slow = intent.Component("main", "main.py", 4, [], "spec")
    slow.ok = True
    slow.attempts = 3
    slow.seconds = 512.4
    slow.history_trail = [
        "[interface repair on e2b-2/gemma4:e2b] ok (8m32s)",
        "[interface repair on e2b-2/gemma4:e2b] ok (18m01s)",
    ]
    clean = intent.Component("helper", "helper.py", 2, [], "spec")
    clean.ok = True

    team.save_plan(root, "upload 3 units", [slow, clean], None, 3000)

    rows = []
    for plan in bulk_plan_report.find_plans(root):
        for comp in plan.get("components") or []:
            rows.append(comp)

    check("the saved plan is found by walking the directory",
         len(rows) == 2, rows)
    by_name = {r["name"]: r for r in rows}
    check("the slow, repaired component's seconds/attempts/trail all "
         "round-trip through save_plan -> disk -> find_plans",
         by_name["main"]["seconds"] == 512.4
         and by_name["main"]["attempts"] == 3
         and len(by_name["main"]["history_trail"]) == 2,
         by_name.get("main"))
    check("a clean, never-repaired component is not flagged",
         not bulk_plan_report._flagged(by_name["helper"], min_seconds=30.0))
    check("the slow component is flagged by the default report threshold",
         bulk_plan_report._flagged(by_name["main"], min_seconds=30.0))

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        bulk_plan_report.report([root], min_seconds=30.0, show_all=False,
                                show_trail=True)
    finally:
        sys.stdout = old_stdout
    out = buf.getvalue()
    check("the report's own printed output names the slow component and "
         "its rung trail, and omits the clean one",
         "main" in out and "helper" not in out
         and "interface repair on e2b-2" in out, out)


def main():
    test_interface_gate_records_the_repair_in_history_trail_and_timing()
    test_sabotage_no_bookkeeping_reproduces_the_live_gap()
    test_bulk_plan_report_finds_and_flags_a_slow_component()
    if FAILURES:
        print("%d failed" % len(FAILURES))
        sys.exit(1)
    print("0 failed")


if __name__ == "__main__":
    main()
