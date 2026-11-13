"""A lease from a dead process must not read as busy, regardless of age.

Measured live: two lease rows for PIDs that no longer existed (crashed
writers, never reaching their own `finally: release_lease`), each about
8 minutes old -- well under the 900s `stale_lease_s` time-based prune --
made `ground.pick_reader` and `team._node_load` both read genuinely idle
nodes (`e4b-linux`, `g26b`) as occupied. `prune_dead_leases` checks PID
liveness directly with `os.kill(pid, 0)` rather than waiting on age.
"""
import os
import subprocess
import sys
import tempfile

import swarm


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="dead-leases-")
    state = swarm.State(os.path.join(tmp, "state.db"))

    # A short-lived real process, so its PID is guaranteed to be free
    # afterward rather than reused by something else mid-test.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead_pid = proc.pid

    with state.lock:
        state.db.execute(
            "INSERT INTO leases (node, model, started, est_seconds, pid) "
            "VALUES ('n', 'm', ?, 60.0, ?)", (swarm.now(), dead_pid))
        state.db.commit()

    depth, _ = state.load_for("n")
    assert depth == 1, "sanity: the lease is there before pruning"

    state.prune_dead_leases()
    depth, _ = state.load_for("n")
    assert depth == 0, (
        "a lease held by a dead PID must be gone after prune_dead_leases, "
        "not counted as in-flight")
    print("dead PID's lease is gone: depth=%d" % depth)

    # A live lease (this process's own PID) must survive the same call.
    lease = state.acquire_lease("n2", "m", 60.0)
    state.prune_dead_leases()
    depth, _ = state.load_for("n2")
    assert depth == 1, (
        "a lease held by a live PID must survive prune_dead_leases -- "
        "this would false-positive every currently-running caller")
    state.release_lease(lease)
    print("live PID's lease survives: depth=%d before release" % 1)

    state.close()
    print("OK")


if __name__ == "__main__":
    main()
