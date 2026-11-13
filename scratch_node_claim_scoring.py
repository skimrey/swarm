"""Validates swarm.State.active_claim and team._node_load: a node held
by another live process's node_claim (the whole-attempt-loop lock) must
score as busy even during the gap between that process's own individual
`leases` rows -- the exact live symptom reported: several independent
`team do` processes all picking the same fast, always-warm node and
queueing behind each other while genuinely idle boxes sat untouched.
Includes sabotage: reverting to `leases`-only scoring reproduces the
false "idle" reading on the same fixture."""
import os
import sys
import tempfile

import swarm
import team

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + name)
    else:
        FAIL += 1
        print("FAIL " + name + (": " + str(detail) if detail else ""))


class FakeNode:
    def __init__(self, name, max_parallel=1):
        self.name = name
        self.max_parallel = max_parallel


with tempfile.TemporaryDirectory() as d:
    db_path = os.path.join(d, "state.db")
    state = swarm.State(db_path)
    node = FakeNode("e4b-linux")

    print("no claim, no lease -- genuinely idle")
    busy, queue_s = team._node_load(state, node)
    check("idle node scores not-busy", busy is False, (busy, queue_s))

    print("\nanother live process holds node_claim for this node, but "
          "between its own individual chat requests -- no `leases` row "
          "exists at this exact instant (the live gap)")
    ok = state.try_claim_node(node.name, "gemma4-e4b-fixed:latest",
                              max_parallel=node.max_parallel)
    check("the other process's claim succeeds", ok)
    depth, _ = state.load_for(node.name)
    check("and, confirming the gap is real, `leases` alone reports zero "
          "in-flight requests at this instant even though the node is "
          "genuinely locked", depth == 0, depth)

    busy2, queue_s2 = team._node_load(state, node)
    check("`_node_load` now scores this node busy via `active_claim`, "
          "not only via `leases`", busy2 is True, (busy2, queue_s2))
    check("and reports a nonzero estimated wait rather than 0.0",
          queue_s2 > 0.0, queue_s2)

    print("\nonce the other process releases the node, it is idle again")
    state.release_node(node.name)
    busy3, _ = team._node_load(state, node)
    check("idle again after release", busy3 is False, busy3)

    print("\na stale claim (old `updated` timestamp, as if the holding "
          "process died without releasing) must not read as busy forever")
    state.try_claim_node(node.name, "gemma4-e4b-fixed:latest",
                        max_parallel=node.max_parallel)
    with state.lock:
        state.db.execute(
            "UPDATE node_claims SET updated = ? WHERE node = ?",
            (swarm.now() - 7200.0, node.name))
        state.db.commit()
    claim = state.active_claim(node.name)
    check("a stale claim (older than stale_after) reads as no claim",
          claim is None, claim)
    busy4, _ = team._node_load(state, node)
    check("and does not fool `_node_load` into reporting busy either",
          busy4 is False, busy4)
    state.release_node(node.name)

    print("\na claim held by a pid that is no longer alive must not read "
          "as busy either (the same rule `try_claim_node` itself applies)")
    with state.lock:
        state.db.execute(
            "INSERT INTO node_claims (node, model, holders, updated, pid) "
            "VALUES (?,?,?,?,?)",
            (node.name, "gemma4-e4b-fixed:latest", 1, swarm.now(), 999999999))
        state.db.commit()
    claim_dead = state.active_claim(node.name)
    check("a claim from a dead pid reads as no claim",
          claim_dead is None, claim_dead)
    busy5, _ = team._node_load(state, node)
    check("and `_node_load` correctly reports idle for it",
          busy5 is False, busy5)
    with state.lock:
        state.db.execute("DELETE FROM node_claims WHERE node = ?", (node.name,))
        state.db.commit()

    print("\nsabotage: scoring only `leases` (the pre-fix behaviour) "
          "misses the exact live gap above -- reproducing it directly "
          "confirms the fixture is real, not merely plausible")
    state.try_claim_node(node.name, "gemma4-e4b-fixed:latest",
                        max_parallel=node.max_parallel)

    def leases_only_node_load(state_, node_):
        state_.prune_dead_leases()
        depth_, queue_s_ = state_.load_for(node_.name)
        return depth_ >= node_.max_parallel, queue_s_

    sabotage_busy, _ = leases_only_node_load(state, node)
    check("sabotage: leases-only scoring reads this genuinely-locked node "
          "as idle, reproducing the live false-idle bug",
          sabotage_busy is False, sabotage_busy)
    check("cross-check: the real, fixed `_node_load` disagrees with the "
          "sabotaged version on this exact fixture",
          team._node_load(state, node)[0] is True,
          "regression: fixed _node_load no longer catches this gap")
    state.release_node(node.name)

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    sys.exit(1)
