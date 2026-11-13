#!/usr/bin/env python3
"""killswarm: stop every local process this pool is currently driving.

`team do`/`swarm`/`openapi` calls, and any script (bash, Python, or
otherwise) that shells out to them, can run for minutes and are commonly
launched under `nohup ... &` specifically so nothing is watching them --
see LOCAL.md's "Any request that touches the swarm runs under nohup"
section. That means there is deliberately no terminal sitting in the
foreground with a Ctrl-C to reach for when one needs to be stopped. This
is that Ctrl-C, callable from any terminal, on any directory, because it
is installed on PATH the same way `team`/`swarm`/`openapi` are.

    killswarm            # stop everything this pool is running, print what
    killswarm --dry-run  # show what would be stopped, kill nothing

What it finds: every process whose command line names `team.py`,
`swarm.py`, or `openapi.py` (however it was invoked -- `team do ...`,
`python3 team.py ...`, or the bare symlinked name), plus any process
named `generate.sh`/`orchestrate.sh`, the two script names LOCAL.md's own
templates use for a `nohup`'d chain. Every *descendant* of each of those
-- a `gate_import`/`gate_smoke` subprocess team.py spawned to exercise
generated code, a `python3 main.py` a bash script ran in turn -- is
stopped too, found by walking the real process tree rather than by
guessing at command-line shapes a second time, so a child that does not
itself mention "team.py" on its own command line is not missed.

What this does not do: reach the network. A request already in flight to
one of the four Ollama boxes is not cancelled remotely -- the local
process reading its response is killed, the socket closes, the node
finishes whatever it was doing and the reply is simply never read. That
node is free for its next request immediately; nothing here needs to
know about `config.json` or contact a single box to make that true.

What this can leave behind: a component's file on disk mid-repair, since
`generate_file` writes a candidate to disk before its gates run. Nothing
here undoes that -- the backup at `.swarm/backups/` (if the file existed
before this run) is exactly the recovery path AGENTS.md already documents
for that case, killed run or not.
"""
import os
import signal
import subprocess
import sys
import time

# The three tools this project installs on PATH, matched by their real
# script names so `team do`, `python3 team.py`, and the bare `team`
# symlink are all caught the same way -- and the two script names
# LOCAL.md's own nohup templates always use for a backgrounded chain, so
# the wrapper script itself is stopped and not just the team.py process
# it spawned.
_PROCESS_PATTERNS = ("team.py", "swarm.py", "openapi.py",
                    "generate.sh", "orchestrate.sh")


def _ps_snapshot():
    """(pid, ppid, command) for every process, read once, not race-prone
    against processes exiting mid-scan the way repeated `ps` calls would
    be."""
    out = subprocess.run(["ps", "-eo", "pid,ppid,command"],
                        capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, ppid_s, cmd = parts
        try:
            rows.append((int(pid_s), int(ppid_s), cmd))
        except ValueError:
            continue
    return rows


def _matches(cmd, own_pid_marker):
    if own_pid_marker in cmd:
        return False
    return any(pat in cmd for pat in _PROCESS_PATTERNS)


def _descendants(rows, roots):
    """Every pid reachable from `roots` by following child links, found by
    walking the real process tree -- not by matching command lines a
    second time, since a gate subprocess team.py spawns to run generated
    code has no reason to mention "team.py" on its own command line."""
    by_ppid = {}
    for pid, ppid, _cmd in rows:
        by_ppid.setdefault(ppid, []).append(pid)
    seen = set(roots)
    stack = list(roots)
    while stack:
        pid = stack.pop()
        for child in by_ppid.get(pid, ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def main(argv):
    dry_run = "--dry-run" in argv
    rows = _ps_snapshot()
    me = os.getpid()
    # A marker only this process's own command line carries, so killswarm
    # never matches itself even though its own name is "killswarm.py" and
    # never matches an unrelated process that merely mentions "team.py"
    # in, say, a text editor's window title showing up in `ps`.
    own_marker = "killswarm"
    roots = [pid for pid, _ppid, cmd in rows
            if pid != me and _matches(cmd, own_marker)]
    if not roots:
        print("nothing found running -- no team.py/swarm.py/openapi.py "
             "process, and no generate.sh/orchestrate.sh")
        return 0

    victims = _descendants(rows, roots) - {me}
    cmd_by_pid = {pid: cmd for pid, _ppid, cmd in rows}
    label = "would stop" if dry_run else "stopping"
    print("%s %d process(es):" % (label, len(victims)))
    for pid in sorted(victims):
        print("  %6d  %s" % (pid, cmd_by_pid.get(pid, "?")[:110]))

    if dry_run:
        print("(--dry-run: nothing was actually signalled)")
        return 0

    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            print("  %6d not owned by you -- skipped" % pid)

    time.sleep(2)
    survivors = []
    for pid in victims:
        try:
            os.kill(pid, 0)
            survivors.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
            print("  %6d did not stop after SIGTERM -- sent SIGKILL" % pid)
        except ProcessLookupError:
            pass

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
