#!/usr/bin/env python3
"""Bulk report over saved plans: which successful runs actually struggled.

`team.py`'s per-component ladder already tracks, for every component in
every `do` call, how many attempts it took, how long it took in total, and
-- since the "needed N rung(s)" fix -- a one-line-per-rung trail of *why*
each attempt beyond the first was needed, with a per-attempt duration on
each line. `save_plan` writes all of that to `.swarm/plans/<hash>.json`,
keyed by a hash of the calling directory, regardless of whether the
component ultimately passed.

None of that was ever surfaced in bulk. Each plan file answers "how did
*this* directory's last run go", and finding out required either watching
the live log at the time (gone the moment the terminal scrolls, and mostly
invisible anyway until the stdout/stderr buffering fix -- see team.py's
`interface_gate`) or knowing to go look at one specific plan file. A batch
of many runs -- `run_001` through `run_NNN`, each with several step
subdirectories -- has no single place that says which of those steps, out
of ones that all eventually reported success, actually needed a restart, a
race, an escalation, or several minutes on a slow box to get there.

This walks one or more roots, finds every subdirectory that has a saved
plan (by trying the same `session.project_key` hash `team.py` itself uses
-- there is no separate index to fall out of sync with), and reports every
component whose own record shows it did not just sail through: more than
one attempt, a non-empty rung trail, or `--min-seconds` worth of wall
clock. Sorted slowest first, since that is almost always the question:
"which of these successful runs took disproportionately long, and why."

Usage:
    python3 bulk_plan_report.py bulk_generation_test/
    python3 bulk_plan_report.py run_001/ run_002/ --min-seconds 30
    python3 bulk_plan_report.py bulk_generation_test/ --trail --all
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

import codegen
import session
import team

# Directories that are never step directories themselves and are wasteful
# to hash-check on every walk -- skipping them is purely a speed optimism,
# never a correctness requirement, since a directory with no plan on disk
# is simply skipped anyway.
_SKIP_DIRS = {".git", ".swarm", "node_modules", "__pycache__", ".venv"}


def find_plans(root: str) -> List[Dict[str, Any]]:
    """Every saved plan reachable under `root`, each tagged with the real
    directory it belongs to.

    A plan file's own `root` field already records this (see `save_plan`),
    which is used in preference to the walked path when present -- the
    walked path is only a fallback for a plan saved before that field
    existed. Either way this is read off the plan itself, never guessed
    from the directory name, so a step directory that was renamed after
    the fact is still reported under the name it actually ran with.
    """
    found: List[Dict[str, Any]] = []
    seen_paths = set()
    for base, dirnames, _files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        real = os.path.realpath(base)
        if real in seen_paths:
            continue
        seen_paths.add(real)
        plan = team.load_plan(real)
        if plan is None:
            continue
        plan["_walked_path"] = base
        found.append(plan)
    return found


def _flagged(comp: Dict[str, Any], min_seconds: float) -> bool:
    if comp.get("history_trail"):
        return True
    if int(comp.get("attempts") or 0) > 1:
        return True
    if float(comp.get("seconds") or 0.0) >= min_seconds:
        return True
    return False


def report(roots: List[str], min_seconds: float, show_all: bool,
          show_trail: bool) -> int:
    rows: List[Dict[str, Any]] = []
    for root in roots:
        for plan in find_plans(root):
            display_dir = plan.get("root") or plan["_walked_path"]
            for comp in plan.get("components") or []:
                if not show_all and not _flagged(comp, min_seconds):
                    continue
                path = comp.get("path") or ""
                failure_dir = None
                if path:
                    # `codegen.save_failed_attempt` keys its folder off
                    # the real, absolute out_path -- every attempt that
                    # ever failed (or was truncated, or hit the
                    # no-mock-sink case) has its actual source and error
                    # text sitting there, not just the condensed one-line
                    # summary this report already prints. Pointed at
                    # directly so "which attempt actually did what" is
                    # one `ls` away instead of a rerun.
                    out_path = os.path.join(display_dir, path)
                    candidate = os.path.dirname(
                        codegen.backup_path(out_path)).replace(
                            codegen.BACKUP_DIR, codegen.FAILURE_DIR)
                    if os.path.isdir(candidate):
                        failure_dir = candidate
                rows.append({
                    "dir": display_dir,
                    "name": comp.get("name") or comp.get("path") or "?",
                    "ok": bool(comp.get("ok")),
                    "attempts": int(comp.get("attempts") or 0),
                    "seconds": float(comp.get("seconds") or 0.0),
                    "trail": comp.get("history_trail") or [],
                    "error": comp.get("error") or "",
                    "failure_dir": failure_dir,
                })

    rows.sort(key=lambda r: r["seconds"], reverse=True)

    if not rows:
        print("no %scomponents found under: %s"
              % ("" if show_all else "flagged ", ", ".join(roots)))
        return 0

    for r in rows:
        status = "ok  " if r["ok"] else "FAIL"
        print("%s  %7.1fs  %2d attempt(s)  %-30s  %s"
             % (status, r["seconds"], r["attempts"], r["name"], r["dir"]))
        if show_trail:
            for line in r["trail"]:
                print("         %s" % line)
        elif r["trail"]:
            print("         needed %d rung(s) -- pass --trail to see them"
                 % len(r["trail"]))
        if r["failure_dir"]:
            print("         failed/truncated attempt sources: %s"
                 % r["failure_dir"])
        if not r["ok"] and r["error"]:
            print("         last error: %s" % r["error"].splitlines()[0][:120])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="directories to walk")
    ap.add_argument("--min-seconds", type=float, default=30.0,
                    help="flag a clean, one-attempt component if it still "
                         "took at least this long (default 30s)")
    ap.add_argument("--all", action="store_true",
                    help="show every component, not just flagged ones")
    ap.add_argument("--trail", action="store_true",
                    help="print each component's full rung-by-rung trail")
    args = ap.parse_args()
    return report(args.roots, args.min_seconds, args.all, args.trail)


if __name__ == "__main__":
    sys.exit(main())
