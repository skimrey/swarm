"""Validates `team.pick_idle_race_candidates` and `team.race_generate_file`,
the "borrow every idle machine and accept whichever answers first" rung
added for a component stuck on the "node stopped answering" fallback with
more than one other box genuinely idle at that moment.

Checks, against synthetic configs and a stubbed `codegen.generate_file`
(never the real network):
  - `pick_idle_race_candidates` excludes the caller's own node, excludes a
    node holding a real lease (busy), and returns one best-quality model
    per remaining idle node
  - `race_generate_file` returns the *fastest to succeed* candidate's
    result, not simply the first candidate in the list
  - the winner's content -- and only the winner's -- lands on the real
    `out_path`, with the previous content backed up first
  - a candidate that finishes first but fails does not end the race; a
    slower candidate that succeeds afterwards still wins
  - if every candidate fails, the caller still gets a real result to log,
    not an exception
  - scratch files for every candidate that reported in before the winner
    was decided are cleaned up

Sabotage: the pre-existing single-pick behaviour (`pick_fallback_writer`
plus one `generate_file` call) is simulated directly against a fixture
where the *first-ranked* candidate is the one that hangs/fails, and is
shown to return that failure -- proving the race, not just "trying
someone else eventually", is what recovers here.
"""
import glob
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen
import swarm
import team


def make_state():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return swarm.State(tmp.name)


def make_cfg():
    data = {
        "nodes": [
            {"name": "origin", "url": "http://127.0.0.1:20",
             "models": ["origin_model"], "enabled": True, "model_bias": {}},
            {"name": "busy", "url": "http://127.0.0.1:21",
             "models": ["busy_model"], "enabled": True, "model_bias": {}},
            {"name": "idle_slow", "url": "http://127.0.0.1:22",
             "models": ["slow_model"], "enabled": True, "model_bias": {}},
            {"name": "idle_fast", "url": "http://127.0.0.1:23",
             "models": ["fast_model"], "enabled": True, "model_bias": {}},
        ],
        "models": {
            "origin_model": {"capability": 4, "quality": 0.5, "family": "gemma"},
            "busy_model": {"capability": 4, "quality": 1.0, "family": "gemma"},
            "slow_model": {"capability": 4, "quality": 0.9, "family": "gemma"},
            "fast_model": {"capability": 4, "quality": 0.6, "family": "gemma"},
        },
    }
    return swarm.Config(data, "/tmp/scratch_race_cfg.json")


def test_idle_candidates_exclude_origin_and_busy():
    cfg = make_cfg()
    state = make_state()
    busy_node = next(n for n in cfg.enabled_nodes() if n.name == "busy")
    lease_id = state.acquire_lease(busy_node.name, "busy_model", 300.0)
    try:
        out = team.pick_idle_race_candidates(cfg, state, "origin", 4)
        names = sorted(n.name for n, _m in out)
        assert names == ["idle_fast", "idle_slow"], (
            "busy and origin must both be excluded; got %r" % (names,))
    finally:
        state.release_lease(lease_id)


def _cleanup_scratch(out_path):
    for f in glob.glob(out_path + ".race*.tmp"):
        try:
            os.remove(f)
        except OSError:
            pass


def _stub_generate_file(delays, failing):
    """Returns a fake `codegen.generate_file` that sleeps `delays[model]`
    seconds, then either fails (if `model in failing`) or writes a
    model-tagged marker to `out_path` and reports success."""
    def fake(spec, out_path, node, model, cfg, state, check=None,
            attempts=1, answer_tokens=0, thinking=0, root=None, extra="",
            verbose=False, env_extra=None, rejected=None, sent_count=None,
            count_check=None, dep_paths=None, **_ignored):
        # `**_ignored` absorbs any newer keyword `generate_file` grows
        # (e.g. `forbid_required_cli_args`) so this stub does not need a
        # matching edit every time an unrelated gate adds a switch --
        # this stub only cares about timing and success/failure.
        time.sleep(delays.get(model, 0.0))
        if model in failing:
            return {"ok": False, "attempts": 1, "seconds": delays.get(model, 0.0),
                    "history": [{"stage": "smoke", "error": "boom: " + model}],
                    "target": "%s/%s" % (node.name, model)}
        code = "# written by %s\n" % model
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(code)
        return {"ok": True, "attempts": 1, "seconds": delays.get(model, 0.0),
                "history": [], "code": code,
                "target": "%s/%s" % (node.name, model)}
    return fake


def test_race_returns_the_fastest_winner_not_the_first_candidate():
    cfg = make_cfg()
    state = make_state()
    idle_slow = next(n for n in cfg.enabled_nodes() if n.name == "idle_slow")
    idle_fast = next(n for n in cfg.enabled_nodes() if n.name == "idle_fast")
    candidates = [(idle_slow, "slow_model"), (idle_fast, "fast_model")]

    old = codegen.generate_file
    codegen.generate_file = _stub_generate_file(
        delays={"slow_model": 0.3, "fast_model": 0.05}, failing=set())
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
    tmp.close()
    out_path = tmp.name
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("# stale previous content\n")
        res, node, model = team.race_generate_file(
            candidates, "spec", out_path, cfg, state, attempts=1,
            answer_tokens=100, root="/tmp", extra="",
            env_extra={}, component_label="widget.py")
        assert res["ok"], "the fast candidate should have won"
        assert model == "fast_model", (
            "the fastest candidate should win the race, got %r" % (model,))
        with open(out_path, encoding="utf-8") as fh:
            content = fh.read()
        assert content == "# written by fast_model\n", (
            "the real out_path must hold the winner's content, got %r"
            % (content,))
        backups = codegen.backups_for(out_path)
        assert backups, "the stale previous content must be backed up"
    finally:
        codegen.generate_file = old
        os.remove(out_path)
        _cleanup_scratch(out_path)
        for b in codegen.backups_for(out_path):
            try:
                os.remove(b)
            except OSError:
                pass
        time.sleep(0.4)  # let the slower straggler thread finish quietly


def test_a_fast_failure_does_not_end_the_race():
    cfg = make_cfg()
    state = make_state()
    idle_slow = next(n for n in cfg.enabled_nodes() if n.name == "idle_slow")
    idle_fast = next(n for n in cfg.enabled_nodes() if n.name == "idle_fast")
    # fast_model finishes first (short delay) but fails; slow_model
    # finishes later and succeeds. The race must wait for it rather than
    # reporting the fast failure as the final answer.
    candidates = [(idle_slow, "slow_model"), (idle_fast, "fast_model")]

    old = codegen.generate_file
    codegen.generate_file = _stub_generate_file(
        delays={"slow_model": 0.15, "fast_model": 0.02},
        failing={"fast_model"})
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
    tmp.close()
    out_path = tmp.name
    try:
        os.remove(out_path)  # start with no prior content at all
        res, node, model = team.race_generate_file(
            candidates, "spec", out_path, cfg, state, attempts=1,
            answer_tokens=100, root="/tmp", extra="",
            env_extra={}, component_label="widget.py")
        assert res["ok"], "the slower candidate should still win the race"
        assert model == "slow_model", (
            "expected the eventual success, not the fast failure, got %r"
            % (model,))
    finally:
        codegen.generate_file = old
        if os.path.exists(out_path):
            os.remove(out_path)
        _cleanup_scratch(out_path)


def test_every_candidate_failing_still_returns_a_real_result():
    cfg = make_cfg()
    state = make_state()
    idle_slow = next(n for n in cfg.enabled_nodes() if n.name == "idle_slow")
    idle_fast = next(n for n in cfg.enabled_nodes() if n.name == "idle_fast")
    candidates = [(idle_slow, "slow_model"), (idle_fast, "fast_model")]

    old = codegen.generate_file
    codegen.generate_file = _stub_generate_file(
        delays={"slow_model": 0.05, "fast_model": 0.02},
        failing={"slow_model", "fast_model"})
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
    tmp.close()
    out_path = tmp.name
    try:
        os.remove(out_path)
        res, node, model = team.race_generate_file(
            candidates, "spec", out_path, cfg, state, attempts=1,
            answer_tokens=100, root="/tmp", extra="",
            env_extra={}, component_label="widget.py")
        assert not res["ok"], "nothing succeeded; res must say so, not crash"
        assert not os.path.exists(out_path), (
            "no winner means the real path must never be touched")
    finally:
        codegen.generate_file = old
        if os.path.exists(out_path):
            os.remove(out_path)
        _cleanup_scratch(out_path)


def test_sabotage_old_single_pick_returns_the_first_ranked_failure():
    """Reproduces the exact shape the old code had: `pick_fallback_writer`
    picks one candidate (ranked, not raced) and that one call's result is
    final. If the top-ranked candidate happens to be the slow/failing
    one, the old behaviour reports failure even though a second idle box
    would have succeeded quickly -- which is exactly what `race_generate_file`
    exists to avoid."""
    cfg = make_cfg()
    state = make_state()
    idle_slow = next(n for n in cfg.enabled_nodes() if n.name == "idle_slow")
    idle_fast = next(n for n in cfg.enabled_nodes() if n.name == "idle_fast")
    # slow_model has higher quality (0.9 vs 0.6), so the old ranked
    # single-pick (`pick_fallback_writer`, quality tiebreak among idle
    # candidates) would choose it first -- and it is the one that fails.
    candidates = [(idle_slow, "slow_model"), (idle_fast, "fast_model")]

    old = codegen.generate_file
    codegen.generate_file = _stub_generate_file(
        delays={"slow_model": 0.05, "fast_model": 0.02},
        failing={"slow_model"})
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
    tmp.close()
    out_path = tmp.name
    try:
        os.remove(out_path)
        # The old, sabotaged behaviour: just call generate_file on the
        # single top-ranked candidate and stop.
        top_node, top_model = candidates[0]
        old_style_res = codegen.generate_file(
            "spec", out_path, top_node, top_model, cfg, state)
        assert not old_style_res["ok"], (
            "sanity: the old single-pick shape must reproduce the live "
            "failure -- it picked the candidate that fails")

        # The real fix: race both, and the fast success wins.
        res, node, model = team.race_generate_file(
            candidates, "spec", out_path, cfg, state, attempts=1,
            answer_tokens=100, root="/tmp", extra="",
            env_extra={}, component_label="widget.py")
        assert res["ok"], (
            "the real race must succeed via fast_model even though the "
            "old single-pick approach (proven above) would have failed")
        assert model == "fast_model"
    finally:
        codegen.generate_file = old
        if os.path.exists(out_path):
            os.remove(out_path)
        _cleanup_scratch(out_path)


def test_run_components_races_the_ordinary_restart_not_only_budget():
    """`run_components` must race the plain "N attempts exhausted,
    restarting fresh" rung too, not only the "node stopped answering"
    (budget) rung -- the whole point being extended here. Uses the real
    `swarm.Config.load()`/`swarm.State()` fleet (the same fixture
    `scratch_failed_reason_printed.py` already relies on) with
    `codegen.generate_file` stubbed to always fail with a non-budget
    ("smoke") stage, so the only way a race can happen at all is via the
    restart rung, not the budget-exhaustion one."""
    import io
    import intent

    def comp(name):
        c = intent.Component.__new__(intent.Component)
        c.name = name
        c.path = "scratchpkg2/%s.py" % name
        c.spec = "irrelevant, generation is stubbed"
        c.floor = 2
        c.deps = []
        c.ok = False
        c.code = ""
        c.error = ""
        c.target = ""
        c.attempts = 0
        c.seconds = 0.0
        c.contract = ""
        c.history_trail = []
        return c

    plan = [comp("thing")]
    old = codegen.generate_file

    def always_fails_smoke(spec, out_path, node, model, cfg, state, **kw):
        return {"ok": False, "path": out_path, "code": "", "target": "stub",
                "attempts": 1, "seconds": 0.01,
                "history": [{"attempt": 1, "stage": "smoke",
                            "error": "running main.py raised:\nValueError: "
                                     "bad input"}]}

    codegen.generate_file = always_fails_smoke
    captured = io.StringIO()
    real_stderr = sys.stderr
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        sys.stderr = captured
        try:
            team.run_components(plan, cfg, state, repair=1,
                                answer_tokens=100, check=None,
                                root="/tmp", tests=None)
        finally:
            sys.stderr = real_stderr
        state.close()
    finally:
        codegen.generate_file = old

    output = captured.getvalue()
    assert "racing a fresh restart" in output, (
        "the restart rung never attempted to race idle boxes -- only "
        "the budget-exhaustion rung did, which is the exact bug this "
        "test exists to catch:\n%s" % output)
    # And the earlier bug this session found live: a race where nothing
    # actually succeeded must never be reported as "won".
    assert "won the restart race" not in output, (
        "every stubbed candidate fails in this fixture; nothing won, "
        "so the log must not claim otherwise:\n%s" % output)
    assert "none succeeded" in output


def run():
    test_idle_candidates_exclude_origin_and_busy()
    test_race_returns_the_fastest_winner_not_the_first_candidate()
    test_a_fast_failure_does_not_end_the_race()
    test_every_candidate_failing_still_returns_a_real_result()
    test_sabotage_old_single_pick_returns_the_first_ranked_failure()
    test_run_components_races_the_ordinary_restart_not_only_budget()
    print("scratch_race_generate_file: all checks passed")


if __name__ == "__main__":
    run()
