"""Validates `team.pick_writer` now prefers an idle node over a busy one.

Measured live in `wednesdaysept23-6/bulk_generation_test/run_005`:
`interface_gate` repaired the same file twice, both rounds landing on
`e2b-2` -- this fleet's slowest, most-often-asleep box -- because
`pick_writer` asked `pick_solvers` for exactly one candidate (`count=1`)
and returned whatever that single top pick was, with no notion of
whether some other equally-eligible node was actually free right now.
The repair call also has none of `run_components`' own idle-race
protection (`pick_idle_race_candidates`/`race_generate_file`), so a slow
node here has no fallback at all -- the whole run visibly stalled for
almost half an hour on one box while others sat idle.

This test constructs a config where the faster eligible model (by
measured decode/prefill rate -- what `pick_solvers` actually ranks by)
lives on a node this test marks busy (via a real lease, the same
mechanism `_node_load` reads in production), fast enough that
`pick_solvers`' own soft, additive busy-penalty still is not enough to
outweigh it, and checks that `pick_writer`'s hard idle-first tie-break
returns the idle node instead.

Run directly: `python3 scratch_pick_writer_idle.py`.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swarm
import team


def make_state():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return swarm.State(tmp.name)


def make_cfg():
    data = {
        "nodes": [
            {
                "name": "fast_but_busy",
                "url": "http://127.0.0.1:1",
                "models": ["strong"],
                "enabled": True,
                "max_parallel": 1,
                "model_bias": {},
            },
            {
                "name": "slow_but_idle",
                "url": "http://127.0.0.1:2",
                "models": ["weak"],
                "enabled": True,
                "max_parallel": 1,
                "model_bias": {},
            },
        ],
        "models": {
            "strong": {"capability": 2, "quality": 0.9, "family": "gemma"},
            "weak": {"capability": 2, "quality": 0.5, "family": "gemma"},
        },
        "team": {},
    }
    return swarm.Config(data, "/tmp/scratch_pick_writer_idle_cfg.json")


def _record_contrasting_perf(state):
    # pick_solvers ranks by measured decode/prefill rate, not the
    # `quality` field -- that only matters to `pick_stronger_writer`. A
    # real perf sample makes fast_but_busy score far better on speed
    # alone, which is what actually lets its soft, additive busy-penalty
    # (`_node_load`'s `queue_s`) get outweighed: `pick_solvers` adds a
    # flat estimated wait on top of a real cost difference, so a node
    # fast enough can still win the score even while genuinely busy.
    state.record_perf("fast_but_busy", "strong",
                      {"prompt_tokens": 300, "output_tokens": 200,
                       "prefill_tps": 500, "decode_tps": 100,
                       "load_s": 1, "total_s": 2})
    state.record_perf("slow_but_idle", "weak",
                      {"prompt_tokens": 300, "output_tokens": 200,
                       "prefill_tps": 10, "decode_tps": 2,
                       "load_s": 30, "total_s": 100})


def test_idle_node_wins_over_faster_busy_node():
    cfg = make_cfg()
    state = make_state()
    _record_contrasting_perf(state)
    fast_node = cfg.node("fast_but_busy")
    # A real lease, the exact mechanism `_node_load` reads in production
    # (not a stub) -- marks fast_but_busy genuinely occupied.
    state.acquire_lease(fast_node.name, "strong", est_seconds=120)

    # pick_solvers' own soft scoring still picks the busy-but-much-faster
    # node here -- confirmed below -- which is exactly why a hard
    # idle-first rule is needed rather than trusting that score alone.
    old_shape = team.pick_solvers(cfg, state, 2, count=1, max_over=0)
    assert old_shape and old_shape[0][0].name == "fast_but_busy", (
        "fixture invalid: pick_solvers' own score did not favour the "
        "busy node, so this case does not test anything")

    found = team.pick_writer(cfg, state, floor=2)
    assert found is not None
    node, model = found
    assert node.name == "slow_but_idle", (
        "pick_writer chose %r while it was busy and slow_but_idle sat "
        "idle -- this is exactly the live bug" % (node.name,))
    print("  an idle, much-slower node is chosen over a busy, faster "
         "one -- idle beats busy even when pick_solvers' own score "
         "alone would not have picked it")


def test_no_busy_node_keeps_the_original_speed_ranking():
    cfg = make_cfg()
    state = make_state()
    _record_contrasting_perf(state)
    found = team.pick_writer(cfg, state, floor=2)
    assert found is not None
    node, model = found
    assert node.name == "fast_but_busy", (
        "when nobody is busy, pick_writer must still prefer the "
        "faster node -- got %r" % (node.name,))
    print("  with nothing busy, the faster node still wins "
         "(idle-awareness must not override the ranking when it "
         "doesn't need to)")


def test_only_one_eligible_node_still_returns_it_even_if_busy():
    cfg = make_cfg()
    state = make_state()
    _record_contrasting_perf(state)
    fast_node = cfg.node("fast_but_busy")
    slow_node = cfg.node("slow_but_idle")
    state.acquire_lease(fast_node.name, "strong", est_seconds=120)
    state.acquire_lease(slow_node.name, "weak", est_seconds=120)

    found = team.pick_writer(cfg, state, floor=2)
    assert found is not None, "every node busy is not the same as no writer"
    print("  every candidate busy still returns the least-bad option, "
         "never None")


def test_sabotage_count_1_reproduces_the_live_bug():
    # Confirms this is genuinely new coverage: the old `count=1` call
    # shape (pick_solvers' single top pick, no idle re-ranking) returns
    # the busy-but-faster node exactly the way the live run's repair did,
    # while the patched `pick_writer` (tested above) does not.
    cfg = make_cfg()
    state = make_state()
    _record_contrasting_perf(state)
    fast_node = cfg.node("fast_but_busy")
    state.acquire_lease(fast_node.name, "strong", est_seconds=120)

    old_shape = team.pick_solvers(cfg, state, 2, count=1, max_over=0)
    assert old_shape and old_shape[0][0].name == "fast_but_busy", (
        "sabotage fixture invalid: the pre-fix call shape did not even "
        "reproduce the live bug")
    print("  sabotage: the old count=1, no-idle-check call shape "
         "reproduces the live bug exactly")


def main():
    test_idle_node_wins_over_faster_busy_node()
    test_no_busy_node_keeps_the_original_speed_ranking()
    test_only_one_eligible_node_still_returns_it_even_if_busy()
    test_sabotage_count_1_reproduces_the_live_bug()
    print("OK")


if __name__ == "__main__":
    main()
