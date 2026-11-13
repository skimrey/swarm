"""The hedge mechanism (`team._write_component_hedged`, wired into every
initial component write via `run_components`) used to check for idle
capacity exactly once, at the `HEDGE_AFTER_SECONDS` (180s) mark. If
nothing happened to be idle at that exact instant, it fell back to an
untimed `queue.Queue.get()` for the rest of the component's run and never
looked again -- and printed nothing saying so either way.

Measured live: an operator watching two components stuck on `gemma4:e4b`
boxes for well past 180s saw no hedge/race message at all, with no way to
tell whether the mechanism was silently broken or correctly declining
because nothing was idle. Both were possible from the code as it stood,
and a box that freed up a minute later (a short unrelated component
finishing elsewhere) would never have been noticed either, because the
idle check never ran a second time.

This validates two things against `team.run_components`, real config
(`swarm.Config.load()`), and a stubbed `codegen.generate_file` (never the
real network), with `HEDGE_AFTER_SECONDS`/`HEDGE_POLL_SECONDS` patched
down so the test runs in well under a second:

1. When nothing is ever idle, the operator sees exactly one "no other box
   is idle right now; will keep checking every Ns" line -- not silence,
   and not a spammed line on every poll -- and the component still
   finishes normally once the primary completes.
2. When nothing is idle for the first two polls and then a box frees up,
   the re-poll actually notices it and races -- proving this is a live
   retry, not merely a friendlier one-shot failure message.
"""
import io
import sys
import time

import codegen
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


def _comp(name):
    c = intent.Component.__new__(intent.Component)
    c.name = name
    c.path = "scratchhedge/%s.py" % name
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


def _run_with_stubs(generate_stub, idle_stub):
    old_generate = codegen.generate_file
    old_idle = team.pick_idle_race_candidates
    old_hedge_after = team.HEDGE_AFTER_SECONDS
    old_hedge_poll = team.HEDGE_POLL_SECONDS
    codegen.generate_file = generate_stub
    team.pick_idle_race_candidates = idle_stub
    team.HEDGE_AFTER_SECONDS = 0.1
    team.HEDGE_POLL_SECONDS = 0.1
    captured = io.StringIO()
    real_stderr = sys.stderr
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        sys.stderr = captured
        try:
            team.run_components([_comp("thing")], cfg, state, repair=1,
                                answer_tokens=100, check=None,
                                root="/tmp", tests=None)
        finally:
            sys.stderr = real_stderr
        state.close()
    finally:
        codegen.generate_file = old_generate
        team.pick_idle_race_candidates = old_idle
        team.HEDGE_AFTER_SECONDS = old_hedge_after
        team.HEDGE_POLL_SECONDS = old_hedge_poll
    return captured.getvalue()


def test_never_idle_still_announces_and_still_finishes():
    def slow_ok(spec, out_path, node, model, cfg, state, **kw):
        time.sleep(0.35)
        return {"ok": True, "path": out_path, "code": "x = 1\n",
                "target": "stub", "attempts": 1, "seconds": 0.35,
                "history": []}

    def never_idle(cfg, state, exclude_node, floor, family=None):
        return []

    out = _run_with_stubs(slow_ok, never_idle)

    announce_count = out.count("will keep checking every")
    check("the operator sees exactly one 'will keep checking' line, not "
          "silence and not a spammed one per poll",
          announce_count == 1, out)
    check("no false 'racing them too' claim when nothing was ever idle",
          "racing them too" not in out, out)


def test_a_later_idle_box_is_noticed_on_a_later_poll():
    calls = {"n": 0}

    def first_call_slow_rest_fast(spec, out_path, node, model, cfg, state,
                                  **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # The primary attempt: deliberately outlasts every poll below
            # so the only way this test's assertions can pass is if the
            # hedge candidate (call #2+) actually wins.
            time.sleep(1.2)
        else:
            time.sleep(0.03)
        return {"ok": True, "path": out_path, "code": "x = 1\n",
                "target": "stub", "attempts": 1, "seconds": 0.03,
                "history": []}

    idle_calls = {"n": 0}

    def idle_after_two_misses(cfg, state, exclude_node, floor, family=None):
        idle_calls["n"] += 1
        if idle_calls["n"] < 3:
            return []
        real_cfg = swarm.Config.load()
        candidates = [n for n in real_cfg.enabled_nodes()
                     if n.name != exclude_node]
        if not candidates:
            return []
        node = candidates[0]
        model = node.models[0] if node.models else "stub_model"
        return [(node, model)]

    out = _run_with_stubs(first_call_slow_rest_fast, idle_after_two_misses)

    check("at least one 'will keep checking' line was printed before the "
          "box freed up", "will keep checking every" in out, out)
    check("the re-poll noticed the later idle box and raced it",
          "racing them too" in out, out)
    check("the hedge candidate (not the still-sleeping primary) won",
          "won the hedge race" in out, out)


test_never_idle_still_announces_and_still_finishes()
test_a_later_idle_box_is_noticed_on_a_later_poll()

print("\n%d failed" % len(FAILURES))
sys.exit(1 if FAILURES else 0)
