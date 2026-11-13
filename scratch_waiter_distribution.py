"""Validates the fix for: several independent `team do` processes each
picking the same node as their writer, piling queued work onto it while
genuinely idle nodes sit untouched.

Root cause: `team._node_load`'s busy signal was a single bit (`load_for`'s
real in-flight leases, or a flat `_CLAIM_BUSY_ESTIMATE_S` if `node_claims`
showed any live holder at all) -- it never distinguished "one sibling
process is ahead of me" from "five are". Every independent process reads
the same flat signal and reaches the same "this node is only ~60s behind,
still my best pick" conclusion, so the queue concentrates on whichever
node looked fastest instead of spreading across the fleet.

The fix: `swarm.State.register_waiter`/`unregister_waiter`/`waiter_count`
track, across all processes, how many currently want a given node --
covering the whole hold-or-wait span of `swarm.node_claim`, not just the
polling part. `_node_load` now scores an extra `_CLAIM_BUSY_ESTIMATE_S`
per waiter beyond the first, so a heavily-contended node's estimated
queue keeps growing as more callers pile onto it, and a later caller's
`pick_solvers`/`pick_role_candidates` ranking naturally prefers a node
with fewer waiters instead of agreeing with everyone before it.
"""
import os
import tempfile

import swarm
import team


def _fresh_state():
    path = tempfile.mktemp(suffix=".db")
    return swarm.State(path), path


class _Node:
    def __init__(self, name):
        self.name = name
        self.max_parallel = 1


def test_no_waiters_is_not_busy():
    state, path = _fresh_state()
    try:
        busy, q = team._node_load(state, _Node("solo"))
        assert busy is False
        assert q == 0.0
    finally:
        state.close()
        os.remove(path)


def test_one_waiter_alone_does_not_inflate_beyond_the_existing_estimate():
    # A single holder/waiter is exactly what the pre-existing
    # `active_claim` check already covers (flat `_CLAIM_BUSY_ESTIMATE_S`)
    # -- the new mechanism must not double-count that first one.
    state, path = _fresh_state()
    try:
        wid = state.register_waiter("nodeA")
        busy, q = team._node_load(state, _Node("nodeA"))
        assert busy is True
        assert q == team._CLAIM_BUSY_ESTIMATE_S, q
        state.unregister_waiter(wid)
    finally:
        state.close()
        os.remove(path)


def test_more_waiters_scores_progressively_busier():
    state, path = _fresh_state()
    try:
        ids = [state.register_waiter("hot") for _ in range(4)]
        busy, q = team._node_load(state, _Node("hot"))
        assert busy is True
        assert q == 4 * team._CLAIM_BUSY_ESTIMATE_S, q
        for i in ids:
            state.unregister_waiter(i)
        busy, q = team._node_load(state, _Node("hot"))
        assert busy is False
        assert q == 0.0
    finally:
        state.close()
        os.remove(path)


def test_a_contended_node_ranks_worse_than_an_idle_one():
    # The actual point: given the choice, a new caller must see the
    # contended node as costing more than an untouched one, which is
    # what makes `pick_solvers`'s sort spread work out instead of
    # concentrating it.
    state, path = _fresh_state()
    try:
        for _ in range(5):
            state.register_waiter("crowded")
        _busy_c, q_crowded = team._node_load(state, _Node("crowded"))
        _busy_i, q_idle = team._node_load(state, _Node("idle"))
        assert q_crowded > q_idle
    finally:
        state.close()
        os.remove(path)


def test_dead_process_waiter_rows_do_not_permanently_inflate_the_count():
    state, path = _fresh_state()
    try:
        with state.lock:
            state.db.execute(
                "INSERT INTO node_waiters (node, pid, started) VALUES (?,?,?)",
                ("nodeA", 999999999, swarm.now()))
            state.db.commit()
        assert state.waiter_count("nodeA") == 0
    finally:
        state.close()
        os.remove(path)


def test_node_claim_registers_and_releases_a_waiter():
    state, path = _fresh_state()
    try:
        node = _Node("nodeA")
        with swarm.node_claim(state, node, "modelX") as held:
            assert held is True
            assert state.waiter_count("nodeA") == 1
        assert state.waiter_count("nodeA") == 0
    finally:
        state.close()
        os.remove(path)


def test_two_node_claims_at_once_both_count_as_waiters():
    # A hold spanning several minutes (the real `codegen.generate_file`
    # attempt loop) is exactly as much a reason for a sibling to look
    # elsewhere as someone still polling -- both must count.
    state, path = _fresh_state()
    try:
        node = _Node("nodeA")
        with swarm.node_claim(state, node, "modelX") as held1:
            assert held1 is True
            # A second process wanting a *different* model queues rather
            # than being granted immediately (max_parallel=1), and must
            # still be visible as a waiter the instant it enters.
            wid2 = state.register_waiter("nodeA")
            try:
                assert state.waiter_count("nodeA") == 2
            finally:
                state.unregister_waiter(wid2)
        assert state.waiter_count("nodeA") == 0
    finally:
        state.close()
        os.remove(path)


def test_sabotage_flat_busy_bit_would_miss_the_pileup():
    # Reproduces the pre-fix shape directly: a busy/not-busy bit plus a
    # flat estimate cannot tell one waiter apart from five, so a node
    # with a real pileup would score identically to one with a single
    # ordinary holder -- proving the waiter count, not merely "is
    # someone home", is what the fix actually adds.
    state, path = _fresh_state()
    try:
        depth, queue_s = state.load_for("crowded")
        claim = state.active_claim("crowded")
        old_style_busy = depth >= 1 or claim is not None
        old_style_queue = queue_s
        if claim is not None:
            old_style_queue = max(queue_s, team._CLAIM_BUSY_ESTIMATE_S)
        # No claim and no lease at all -- the old signals see this node
        # as completely free even with five processes already waiting
        # for it via the new mechanism, which is exactly the miss this
        # fix closes.
        assert old_style_busy is False
        for _ in range(5):
            state.register_waiter("crowded")
        busy, queue_s = team._node_load(state, _Node("crowded"))
        assert busy is True
        assert queue_s > old_style_queue
    finally:
        state.close()
        os.remove(path)


if __name__ == "__main__":
    test_no_waiters_is_not_busy()
    test_one_waiter_alone_does_not_inflate_beyond_the_existing_estimate()
    test_more_waiters_scores_progressively_busier()
    test_a_contended_node_ranks_worse_than_an_idle_one()
    test_dead_process_waiter_rows_do_not_permanently_inflate_the_count()
    test_node_claim_registers_and_releases_a_waiter()
    test_two_node_claims_at_once_both_count_as_waiters()
    test_sabotage_flat_busy_bit_would_miss_the_pileup()
    print("ok")
