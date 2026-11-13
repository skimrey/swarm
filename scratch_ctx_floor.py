"""`plan_context` never shrinks `num_ctx` below what a node/model already
paid to load, across separate `do`/`code` invocations.

Ollama reloads the weights whenever `num_ctx` changes, in either direction.
`plan_context` already fixed this *within* one component's own repair loop
by sizing once for the whole thing, but each `do`/`code` invocation is a
separate process with no memory of the last one -- and unlike `split`/`run`,
which pins one context per node+model for a whole run via `plan_contexts`,
`do`/`code` claims components onto workers live with no run-wide plan to
size against up front.

Measured live: an `--api`-heavy run left `e4b-linux` loaded at a wide
window, and the next, unrelated, much smaller request needed far less --
so it reloaded to shrink, and would have reloaded a second time the next
time an API-sized request landed there. `state.resident_ctx` persists the
high-water mark in the sqlite state db so it survives across processes,
and `plan_context` takes `max(freshly computed, that floor)` rather than
always using the fresh estimate.
"""
import os
import tempfile

import codegen
import swarm


def _state():
    tmp = tempfile.mkdtemp()
    return swarm.State(os.path.join(tmp, "state.db"))


def test_a_small_request_after_a_big_one_does_not_shrink():
    state = _state()
    cfg = swarm.Config.load()
    node = cfg.node("e4b-linux")
    model = "gemma4-e4b-fixed:latest"

    small = "x" * 200
    first = codegen.plan_context(small, cfg, node, model, 500, 300,
                                 state=state)

    big = "x" * 80000
    second = codegen.plan_context(big, cfg, node, model, 500, 300,
                                  state=state)
    assert second > first, "the big prompt should have needed more room"

    third = codegen.plan_context(small, cfg, node, model, 500, 300,
                                 state=state)
    assert third == second, (
        "ctx shrank back down (%d -> %d) -- this is the exact reload the "
        "floor exists to avoid" % (second, third))
    state.close()


def test_the_floor_persists_across_separate_state_objects():
    # Two `swarm.State(path)` opens of the same db is what two separate
    # `do` processes actually look like -- not two calls on one open
    # connection, which would prove nothing about persistence.
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "state.db")
    cfg = swarm.Config.load()
    node = cfg.node("e4b-linux")
    model = "gemma4-e4b-fixed:latest"

    state1 = swarm.State(db_path)
    big = codegen.plan_context("x" * 80000, cfg, node, model, 500, 300,
                               state=state1)
    state1.close()

    state2 = swarm.State(db_path)
    small = codegen.plan_context("x" * 200, cfg, node, model, 500, 300,
                                 state=state2)
    assert small == big, (
        "a fresh process reading the same state db still shrank (%d -> %d)"
        % (big, small))
    state2.close()


def test_no_state_is_the_old_behaviour_not_a_crash():
    # `state` is optional. Every other caller of `plan_context` (there is
    # exactly one other, in team.py's --batch warmer) still works with no
    # floor at all -- it just sizes fresh every time, as it always did.
    cfg = swarm.Config.load()
    node = cfg.node("e4b-linux")
    model = "gemma4-e4b-fixed:latest"
    ctx = codegen.plan_context("x" * 200, cfg, node, model, 500, 300)
    assert ctx > 0


def test_the_floor_is_clamped_to_the_node_ceiling():
    # A floor recorded before a config change lowered a model's max_ctx
    # must not be replayed past the new ceiling -- `pick_num_ctx` already
    # clamps the fresh estimate, and the floor has to go through the same
    # clamp or a stale floor could win the max() and exceed what the node
    # can actually hold now.
    state = _state()
    cfg = swarm.Config.load()
    node = cfg.node("e4b-linux")
    model = "gemma4-e4b-fixed:latest"
    ceiling = node.max_ctx(model)
    state.mark_resident_ctx(node.name, model, ceiling * 10)
    ctx = codegen.plan_context("x" * 200, cfg, node, model, 500, 300,
                              state=state)
    assert ctx <= ceiling, "floor was replayed past the node's own ceiling"
    state.close()


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok   %s" % name)
            except AssertionError as exc:
                print("  FAIL %s -- %s" % (name, exc))
                failures.append(name)
    if failures:
        raise SystemExit("%d failure(s): %s" % (len(failures), failures))
    print("all ctx-floor checks passed")
