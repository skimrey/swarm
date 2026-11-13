"""A component that stops progressing must stop costing time.

The per-request timeout bounds one call, so three attempts against a node that
has gone to sleep cost thirty minutes before anything is reported. Measured on
exactly that: an old laptop stopped answering mid-run and the file it was
writing sat for thirteen minutes looking like a crash.
"""
import contextlib
import types

import codegen
import swarm

# Measured on e2b-2: 31.0s to load the weights, 0.2s to answer. A request
# granted less than this cannot succeed and leaves the node worse than it
# found it, because the abandoned load is not resumed.
MIN_LOAD = 60.0


def main() -> None:
    cfg = swarm.Config.load()
    node = next(n for n in cfg.nodes if n.name == "e2b-2")
    model = "gemma4:e2b"

    asked = []
    clock = [1000.0]

    def never_answers(_node, _model, _messages, **kw):
        """A sleeping node: it consumes every second it was granted, then nothing."""
        granted = kw.get("timeout", 600)
        asked.append(granted)
        assert granted >= MIN_LOAD, (
            "asked for %ss, too short to survive a cold load -- disconnecting "
            "mid-load makes Ollama abandon it, so the retry starts over and is "
            "cut off at the same point" % granted)
        clock[0] += granted
        raise swarm.OllamaError("timed out after %ss" % granted)

    real_chat, real_claim = swarm.chat_complete, swarm.node_claim
    real_time = codegen.time
    codegen.time = types.SimpleNamespace(monotonic=lambda: clock[0],
                                         sleep=lambda _s: None)
    swarm.chat_complete = never_answers
    swarm.node_claim = lambda *a, **k: contextlib.nullcontext()
    state = types.SimpleNamespace(record_perf=lambda *a: None,
                                  mark_resident=lambda *a: None,
                                  last_used=lambda *a: 0.0,
                                  cold_load_s=lambda *a: 0.0,
                                  resident_ctx=lambda *a: None,
                                  mark_resident_ctx=lambda *a: None,
                                  # `generate_file` now leases the node for
                                  # each attempt (see `_node_load` in
                                  # team.py), reading `throughput` for a
                                  # rough duration estimate and calling
                                  # acquire/release around the request the
                                  # same way `split`/`run`'s `_run_task`
                                  # already did. This stub node is never
                                  # inspected for busy-ness in this test, so
                                  # a no-op pair is enough.
                                  throughput=lambda *a: None,
                                  acquire_lease=lambda *a: None,
                                  release_lease=lambda *a: None)
    try:
        res = codegen.generate_file("spec", "/tmp/_budget_probe.py", node,
                                    model, cfg, state, attempts=3,
                                    time_budget=120, verbose=False)
        assert len(asked) == 1, "a 120s budget must not start a second attempt"
        assert res["history"][-1]["stage"] == "budget", res["history"][-1]
        assert not res["ok"]
        print("budget honoured: %d call(s) on a 120s budget, none of them "
              "cut short" % len(asked))

        # The real default: three attempts must not cost three timeouts.
        asked.clear(); clock[0] = 1000.0
        codegen.generate_file("spec", "/tmp/_budget_probe.py", node, model,
                              cfg, state, attempts=3, verbose=False)
        assert sum(asked) <= codegen.TIME_BUDGET, asked
        print("default: %ss total, not the %ss three attempts used to cost"
              % (sum(asked), 3 * 600))

        # A cold load must never be interrupted, whatever the budget says.
        for budget in (130.0, 200.0, 599.0, 601.0):
            asked.clear(); clock[0] = 1000.0
            codegen.generate_file("spec", "/tmp/_budget_probe.py", node, model,
                                  cfg, state, attempts=3, time_budget=budget,
                                  verbose=False)
            assert all(a >= MIN_LOAD for a in asked), (budget, asked)
        print("no request is ever shortened below a cold load")

        asked.clear(); clock[0] = 1000.0
        codegen.generate_file("spec", "/tmp/_budget_probe.py", node, model,
                              cfg, state, attempts=3, time_budget=None,
                              verbose=False)
        assert asked == [600, 600, 600], asked
        print("no budget leaves the old behaviour alone: %s" % asked)
    finally:
        swarm.chat_complete, swarm.node_claim = real_chat, real_claim
        codegen.time = real_time
    print("OK")


if __name__ == "__main__":
    main()
