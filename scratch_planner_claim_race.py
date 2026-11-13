"""Planner selection must try every eligible candidate before queueing,
the same way write-phase claiming already does -- but never demote to a
lower floor just because the higher one is momentarily busy.

`pick_role_model` ranks candidates from a snapshot of busyness taken once
at call time, and the old call sites (`decompose`, `replan_component`)
then did `pick_role_model(4) or pick_role_model(2)` followed by a single
`swarm.node_claim` on exactly that one choice. Several parallel `team do`
processes reading the same snapshot all rank the same idle, higher-
quality node top -- once a second floor-4 node existed
(`gemma4-e4b-fixed:latest` alongside `g26b`), every concurrent planner
call agreed g26b was best, all raced to claim it, and the losers
*waited* for it rather than immediately trying e4b-linux, also floor 4
and genuinely idle the whole time. Measured live: this is exactly the
"g26b is planning something else right now; waiting for it" line a real
multi-step orchestration run hit on every single one of its six parallel
steps.

`claim_first_free_role` fixes that by trying every ranked candidate
non-blockingly (`state.try_claim_node`) before ever waiting, within one
floor. A first version of the fix went further than intended and pooled
every floor into one flat list tried in a single pass -- which meant a
momentary lull in floor-4 availability (both real floor-4 nodes simply
busy generating something else for a few seconds, the ordinary case
under any real parallel fan-out) silently demoted planning to whichever
floor-2 node happened to be idle at that instant. Measured live: a plan
that should have used a floor-4 model planned on `e2b-2/gemma4:e2b`
instead, purely because `g26b` and `e4b-linux` were both busy for a
moment; the weak model failed to decompose the request at all, and the
whole step failed. This file checks both the original fix and this
follow-on regression.
"""
import os
import tempfile

import swarm
import team


class FakeNode:
    def __init__(self, name, max_parallel=1):
        self.name = name
        self.max_parallel = max_parallel


def _install_fake_candidates(by_floor):
    """Replace team.pick_role_candidates with one that returns a fixed
    list per floor, the same shape the real ranking returns."""
    real = team.pick_role_candidates
    team.pick_role_candidates = (
        lambda cfg, state_, floor, family=None, exclude_family=None, \
              exclude_nodes=None:
            list(by_floor.get(floor, ())))
    return real


def main():
    strong1 = FakeNode("g26b")
    strong2 = FakeNode("e4b-linux")
    weak = FakeNode("e2b-2")

    # Both floor 4 and floor 2 have candidates, mirroring a real fleet
    # with two floor-4 nodes and one floor-2 node.
    by_floor = {
        4: [(strong1, "modelA"), (strong2, "modelB")],
        2: [(weak, "modelC")],
    }

    with tempfile.TemporaryDirectory() as d:
        state = swarm.State(path=os.path.join(d, "state.db"))
        real_candidates_fn = _install_fake_candidates(by_floor)
        try:
            # Case 1: the top floor-4 candidate is held by a concurrent
            # caller; the second floor-4 candidate is free. Must claim
            # the second one, not fall through to floor 2 and not wait.
            state.try_claim_node(strong1.name, "modelA", max_parallel=1)
            node, model, held = team.claim_first_free_role(
                cfg=None, state=state, floors=[4, 2])
            assert held and node is strong2, (
                "did not fall back to the free second floor-4 candidate: "
                "got node=%r held=%r" % (node.name if node else None, held))
            print("OK: fell back to the free floor-4 candidate (%s) "
                 "instead of queueing on the busy top choice" % node.name)
            state.release_node(strong1.name)
            state.release_node(strong2.name)

            # Case 2: nobody busy -- the top floor-4 candidate still wins.
            node2, model2, held2 = team.claim_first_free_role(
                cfg=None, state=state, floors=[4, 2])
            assert held2 and node2 is strong1, (
                "with nothing busy, the top floor-4 candidate should "
                "still win: got %r" % (node2.name if node2 else None))
            print("OK: with nothing busy, the top floor-4 candidate (%s) "
                 "still wins" % node2.name)
            state.release_node(node2.name)

            # Case 3 (the actual regression): every floor-4 candidate is
            # busy, and a floor-2 candidate is idle. Must NOT fall
            # through to floor 2 -- must return the busy floor-4
            # candidate with held=False, so the caller blocking-waits
            # for it exactly as `pick_role_model(4)` alone would have.
            state.try_claim_node(strong1.name, "modelA", max_parallel=1)
            state.try_claim_node(strong2.name, "modelB", max_parallel=1)
            node3, model3, held3 = team.claim_first_free_role(
                cfg=None, state=state, floors=[4, 2])
            assert not held3 and node3 in (strong1, strong2), (
                "with every floor-4 candidate busy but a floor-2 "
                "candidate idle, this must still return a floor-4 "
                "candidate (unclaimed, to block-wait on) -- not demote "
                "to floor 2. Got node=%r held=%r"
                % (node3.name if node3 else None, held3))
            print("OK: with every floor-4 candidate busy, still returns "
                 "a floor-4 candidate to block-wait on, never demoting "
                 "to the idle floor-2 one (%s)" % weak.name)
            state.release_node(strong1.name)
            state.release_node(strong2.name)

            # Case 4: floor 4 has NO candidates at all (not merely busy --
            # genuinely absent). This is the one case that should fall
            # through to floor 2, matching the original
            # `pick_role_model(4) or pick_role_model(2)` semantics.
            empty_floor4 = {2: [(weak, "modelC")]}
            team.pick_role_candidates = (
                lambda cfg, state_, floor, family=None, exclude_family=None, \
                      exclude_nodes=None:
                    list(empty_floor4.get(floor, ())))
            node4, model4, held4 = team.claim_first_free_role(
                cfg=None, state=state, floors=[4, 2])
            assert held4 and node4 is weak, (
                "with zero floor-4 candidates configured, must fall "
                "through to the floor-2 candidate: got %r"
                % (node4.name if node4 else None))
            print("OK: with zero floor-4 candidates at all, correctly "
                 "falls through to the floor-2 candidate (%s)"
                 % node4.name)
            state.release_node(node4.name)
        finally:
            team.pick_role_candidates = real_candidates_fn
        state.close()


def sabotage():
    """Confirms the fixture is real by replaying the flat-pool version of
    `claim_first_free_role` this replaces (every floor's candidates
    pooled into one list, tried in a single non-blocking pass with no
    floor boundary) against the exact regression shape: floor-4 all
    busy, floor-2 idle. The flat version must incorrectly claim the
    floor-2 candidate, proving this fixture would have caught the live
    bug.
    """
    strong1 = FakeNode("g26b")
    strong2 = FakeNode("e4b-linux")
    weak = FakeNode("e2b-2")
    by_floor = {
        4: [(strong1, "modelA"), (strong2, "modelB")],
        2: [(weak, "modelC")],
    }

    def flat_claim_first_free_role(cfg, state, floors, family=None,
                                   exclude_family=None):
        seen = []
        for floor in floors:
            for cand in by_floor.get(floor, ()):
                if cand not in seen:
                    seen.append(cand)
        for node, model in seen:
            if state.try_claim_node(node.name, model,
                                    max_parallel=node.max_parallel):
                return node, model, True
        if seen:
            node, model = seen[0]
            return node, model, False
        return None, None, False

    with tempfile.TemporaryDirectory() as d:
        state = swarm.State(path=os.path.join(d, "state.db"))
        state.try_claim_node(strong1.name, "modelA", max_parallel=1)
        state.try_claim_node(strong2.name, "modelB", max_parallel=1)

        node, model, held = flat_claim_first_free_role(
            cfg=None, state=state, floors=[4, 2])
        assert held and node is weak, (
            "sabotage did not reproduce the live bug -- the flat pool "
            "version should have wrongly claimed the floor-2 candidate "
            "while floor-4 was merely busy: got node=%r held=%r"
            % (node.name if node else None, held))
        print("sabotage OK: the flat-pool version wrongly demotes to "
             "floor 2 (%s) while floor-4 candidates are merely busy -- "
             "exactly the live 'planning on e2b-2/gemma4:e2b' failure "
             "this fixture proves the real fix avoids" % node.name)
        state.release_node(weak.name)
        state.close()


if __name__ == "__main__":
    main()
    sabotage()
