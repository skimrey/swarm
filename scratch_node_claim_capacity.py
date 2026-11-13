"""`node_claim` must enforce `max_parallel`, not just score it.

Measured live: six parallel `team do` processes each ran their own
`pick_solvers` before any of the others had registered a write-phase
lease (planning happens on a different node than writing, so nothing
naturally staggers this), and four of them independently picked the same
single-model, max_parallel=1 node as their writer. `node_claim` let all
four straight through concurrently -- "concurrent same-model requests
cost nothing extra" -- so Ollama itself serialised them anyway, and the
slowest ended up burning its whole time budget queued behind siblings it
never knew about, reporting "the node stopped answering" for a node that
was busy the entire time, just not with it.

This starts two threads that both call `node_claim` for the SAME
max_parallel=1 node and model, and asserts they are never inside the
claimed block at the same time -- the exact oversubscription the live
failure exhibited.
"""
import os
import tempfile
import threading
import time

import swarm


def main():
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    node.max_parallel = 1
    model = node.models[0]

    with tempfile.TemporaryDirectory() as d:
        state = swarm.State(path=os.path.join(d, "state.db"))

        concurrent_peak = [0]
        lock = threading.Lock()
        results = []

        def worker(tag):
            with swarm.node_claim(state, node, model, wait_s=10.0,
                                  poll_s=0.05):
                with lock:
                    concurrent_peak[0] += 1
                    peak_now = concurrent_peak[0]
                results.append((tag, peak_now))
                # Simulate real generation work with a registered lease,
                # the same as codegen.generate_file does per attempt.
                lease = state.acquire_lease(node.name, model, 0.4)
                time.sleep(0.3)
                state.release_lease(lease)
                with lock:
                    concurrent_peak[0] -= 1

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        # Give the first thread a moment's head start so it is the one
        # that actually holds a lease when the second checks capacity --
        # without the fix this ordering does not matter at all, since
        # both are simply admitted immediately regardless of the other.
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        state.close()

    print("results:", results)
    max_seen = max(p for _t, p in results) if results else 0
    assert max_seen <= 1, (
        "two same-model callers were inside node_claim at once on a "
        "max_parallel=1 node: peak concurrency %d" % max_seen)
    print("OK: peak concurrency on the max_parallel=1 node was %d" % max_seen)


def sabotage() -> None:
    """Confirms the test above actually exercises the fix: with
    `max_parallel` ignored (the old behaviour), the same scenario must
    reproduce the live oversubscription.
    """
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    node.max_parallel = 1
    model = node.models[0]

    real_try_claim = swarm.State.try_claim_node

    def unbounded(self, node_name, model_name, stale_after=3600.0,
                  max_parallel=1):
        return real_try_claim(self, node_name, model_name,
                              stale_after=stale_after, max_parallel=999999)

    swarm.State.try_claim_node = unbounded
    try:
        with tempfile.TemporaryDirectory() as d:
            state = swarm.State(path=os.path.join(d, "state.db"))
            concurrent_peak = [0]
            lock = threading.Lock()
            results = []

            def worker(tag):
                with swarm.node_claim(state, node, model, wait_s=10.0,
                                      poll_s=0.05):
                    with lock:
                        concurrent_peak[0] += 1
                        peak_now = concurrent_peak[0]
                    results.append((tag, peak_now))
                    lease = state.acquire_lease(node.name, model, 0.4)
                    time.sleep(0.3)
                    state.release_lease(lease)
                    with lock:
                        concurrent_peak[0] -= 1

            t1 = threading.Thread(target=worker, args=("a",))
            t2 = threading.Thread(target=worker, args=("b",))
            t1.start()
            time.sleep(0.05)
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)
            state.close()
    finally:
        swarm.State.try_claim_node = real_try_claim

    max_seen = max(p for _t, p in results) if results else 0
    assert max_seen > 1, (
        "sabotage did not reproduce the old oversubscription bug -- this "
        "test would not have caught the live failure")
    print("sabotage OK: with max_parallel ignored, peak concurrency was %d "
         "(the exact bug this test guards against)" % max_seen)


if __name__ == "__main__":
    main()
    sabotage()
