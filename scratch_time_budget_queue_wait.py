#!/usr/bin/env python3
"""Validates that codegen.generate_file's TIME_BUDGET clock starts AFTER
node_claim is acquired, not from the moment generate_file was called --
so legitimate queue wait behind other siblings on a busy max_parallel=1
node is never charged against a component's own generation budget.

Measured live: a component reported "gave up after 8m08s; the node
stopped answering" while the node in fact answered every one of several
queued siblings throughout that window -- it simply spent most of its
600s budget waiting in line for node_claim (now allowed to wait up to an
hour) before it ever got a turn to generate anything.
"""
import time
import types
import unittest.mock as mock

import codegen
import swarm


def make_stub_state(claim_delay_s):
    """A fake state/node_claim shaped like the real one, except
    `node_claim` blocks for `claim_delay_s` before yielding -- simulating
    several siblings queued ahead of this call on a busy node."""
    calls = {"chat": 0}

    class StubState:
        def throughput(self, *a, **k):
            return None

        def acquire_lease(self, *a, **k):
            return None

        def release_lease(self, *a, **k):
            return None

        def resident_ctx(self, *a, **k):
            return None

        def mark_resident_ctx(self, *a, **k):
            pass

        def resident_model(self, *a, **k):
            return None

        def mark_resident(self, *a, **k):
            pass

        def record_perf(self, *a, **k):
            pass

    return StubState(), calls


def run_with_claim_delay(claim_delay_s, time_budget):
    state, calls = make_stub_state(claim_delay_s)
    node = types.SimpleNamespace(name="n", max_ctx=lambda m: 32768,
                                 max_parallel=1)
    cfg = swarm.Config({"models": {"m": {"decode_tps_hint": 20,
                                          "max_ctx": 32768}},
                        "context": {}, "team": {}})

    real_sleep = time.sleep

    class _FakeClaim:
        """Simulates the real node_claim's wait: blocks for
        claim_delay_s (siblings queued ahead) before the caller ever
        gets inside the `with` block."""
        def __enter__(self):
            real_sleep(claim_delay_s)
            return self

        def __exit__(self, *a):
            return False

    def fake_node_claim(state_, node_, model_):
        return _FakeClaim()

    def fake_chat_complete(node_, model_, messages, **kwargs):
        calls["chat"] += 1
        return {"text": "```python\nprint('ok')\n```",
               "thinking": "", "sample": {}, "raw": {}}

    with mock.patch.object(swarm, "node_claim", fake_node_claim), \
         mock.patch.object(swarm, "chat_complete", fake_chat_complete), \
         mock.patch.object(codegen, "plan_context", lambda *a, **k: 4096):
        result = codegen.generate_file(
            "print hello", "/tmp/scratch_tbqw_out.py", node, "m", cfg, state,
            attempts=3, answer_tokens=50, thinking=0, smoke=False,
            verbose=False, time_budget=time_budget)
    return result, calls


failures = []


def check(name, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print("%s %s%s" % (status, name, ("  -- " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# A claim delay of 5s against a 6s time_budget: if the budget clock
# started before the claim (the bug), 6 - 5 = 1s left, which is below
# MIN_ATTEMPT_SECONDS (120s) regardless -- so use a large enough
# time_budget that the OLD code would have failed but the NEW code,
# whose clock starts after the delay, still has the full budget left.
result, calls = run_with_claim_delay(claim_delay_s=3.0, time_budget=130.0)
check("a call queued for 3s behind a busy node still gets its full "
      "generation budget once claimed (at least one real chat_complete "
      "call happened, not an immediate budget give-up)",
      calls["chat"] >= 1,
      "history=%r" % result.get("history"))
check("no 'gave up after ...; the node stopped answering' entry from "
      "queue wait alone",
      not any(h.get("stage") == "budget" for h in result.get("history", [])),
      "history=%r" % result.get("history"))

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
