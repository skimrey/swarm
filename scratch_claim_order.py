"""Does the claim loop hand the critical tail to the fastest idle worker?

Simulates the loop's decision rule against a fast and a slow worker so the
behaviour can be checked without spending minutes of real generation.
"""
import threading
import time

FAST, SLOW = 43.1, 14.9


def simulate(ready_count, workers, seconds):
    """Returns {worker: [tasks taken]} under the deferral rule."""
    pending = {"t%d" % i: True for i in range(ready_count)}
    done = []
    idle = {}
    lock = threading.Lock()
    taken = {name: [] for name, _ in workers}

    def claim_real(my_rate):
        with lock:
            if not pending:
                return None
            faster = sum(1 for _, r in idle.items() if r > my_rate)
            if faster and len(pending) <= faster:
                return None
            key = sorted(pending)[0]
            del pending[key]
            return key

    def worker(name, rate):
        with lock:
            idle[name] = rate
        while True:
            with lock:
                empty = not pending
            if empty:
                return
            task = claim_real(rate)
            if task is None:
                time.sleep(0.01)
                continue
            with lock:
                idle.pop(name, None)
            taken[name].append(task)
            time.sleep(seconds[name])
            with lock:
                idle[name] = rate

    threads = [threading.Thread(target=worker, args=w) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "deadlock: a worker hung"
    return taken


def main():
    workers = [("fast", FAST), ("slow", SLOW)]

    # The case from tonight's run: one task left, both boxes idle. It must go
    # to the fast one every time, not to whoever polls first.
    for trial in range(20):
        taken = simulate(1, workers, {"fast": 0.05, "slow": 0.05})
        assert taken["fast"] == ["t0"], "trial %d: slow box took the tail (%r)" % (
            trial, taken)
    print("tail task -> fast worker in 20/20 trials")

    # Plenty of work: deferring must not idle the slow box.
    taken = simulate(8, workers, {"fast": 0.02, "slow": 0.02})
    assert taken["slow"], "slow worker starved when there was work for it"
    print("wide plan: both workers used (fast=%d, slow=%d)"
          % (len(taken["fast"]), len(taken["slow"])))

    # Equal rates, nobody is faster, so nobody may defer forever.
    taken = simulate(4, [("a", 20.0), ("b", 20.0)], {"a": 0.02, "b": 0.02})
    assert len(taken["a"]) + len(taken["b"]) == 4, "tasks lost on a tie"
    print("tie: all 4 tasks completed, no deadlock")

    # Three tiers, one task: only the fastest may take it.
    trio = [("fast", 43.1), ("mid", 41.4), ("slow", 14.9)]
    taken = simulate(1, trio, {"fast": 0.05, "mid": 0.05, "slow": 0.05})
    assert taken["fast"] == ["t0"], "three-way tail went to %r" % taken
    print("three workers, one task -> fastest")
    print("OK")


if __name__ == "__main__":
    main()
