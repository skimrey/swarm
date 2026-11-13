"""Every deciding role must use the trusted judge, not the fastest capable one.

The bug this guards against is silent: promoting a model so it can write hard
components also promotes it into every judging role that asks for the most
capable model at that floor.
"""
import swarm
import team

ROLES = ("judge_floor", "reduce_floor")


def main():
    cfg = swarm.Config.load()
    state = swarm.State()
    pinned = cfg.data.get("team", {}).get("judge_model")
    assert pinned, "no team.judge_model configured; nothing to enforce"
    node, model = team.named_model(cfg, pinned)
    print("pinned judge: %s/%s" % (node.name, model))

    for key in ROLES:
        floor = int(cfg.data.get("team", {}).get(key, 4))
        got = team.judging_model(cfg, state, floor)
        assert got is not None, "%s: no judge found" % key
        assert got[1] == pinned, "%s selected %s, not the pinned %s" % (
            key, got[1], pinned)
        print("  %-14s (floor %d) -> %s/%s" % (key, floor, got[0].name, got[1]))

    # Writing is unaffected: the writer is chosen on throughput, and pinning
    # the judge must not drag the slow model back into generation.
    restrict = cfg.data.get("team", {}).get("restrict_to_family")
    writers = team.pick_solvers(cfg, state, 4, count=1, family=restrict)
    assert writers, "no floor-4 writer available"
    assert writers[0][1] != pinned, (
        "the judge pin leaked into writing: writer is %s" % writers[0][1])
    print("  writer stays %s/%s (%.1f tok/s)"
          % (writers[0][0].name, writers[0][1],
             team.measured_rate(state, writers[0][0].name, writers[0][1])))

    # An explicit family exclusion cannot be honoured by a pin naming one
    # model, so it must fall through rather than silently ignore the request.
    other = team.judging_model(cfg, state, 4, exclude_family="gemma")
    assert other is None or other[1] != pinned, (
        "family exclusion ignored: still returned %r" % (other,))
    print("  exclude_family respected (returned %r)"
          % (None if other is None else other[1]))
    state.close()
    print("OK")


if __name__ == "__main__":
    main()
