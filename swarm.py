#!/usr/bin/env python3
"""Swarm: a dependency-free dispatcher for a pool of Ollama nodes.

Routes a prompt to the cheapest node that can plausibly handle it, preferring
whichever eligible node will finish soonest given measured throughput and
current queue depth. Escalates to a stronger model when the answer comes back
flagged or empty.

Standard library only, Python 3.9+. Copy this one file (plus config.json) to
any machine in the pool and it works.

    ./swarm.py ask "why is my build failing"
    ./swarm.py route "what time is it"        # dry run, no generation
    ./swarm.py bench                          # measure nodes, tune routing
    ./swarm.py nodes                          # health + live load
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# `realpath`, so the scripts can be symlinked onto PATH. With `abspath` a
# `~/bin/team` symlink makes HERE `~/bin`, and the pool then looks for its
# config, state and backups next to the link instead of next to itself.
HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG_PATH = os.environ.get("SWARM_CONFIG", os.path.join(HERE, "config.json"))
STATE_PATH = os.environ.get("SWARM_STATE", os.path.join(HERE, ".swarm", "state.db"))
LOG_DIR = os.environ.get("SWARM_LOGS", os.path.join(HERE, ".swarm", "runs"))
CONTEXT_DIR = os.environ.get("SWARM_CONTEXT", os.path.join(HERE, "context"))

NS = 1_000_000_000  # ollama reports durations in nanoseconds


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "nodes": [],
    "models": {},
    "routing": {
        "speed_weight": 1.0,
        "quality_weight": 1.0,
        "max_escalations": 1,
        "classifier_model": "gemma4:e2b",
        "classifier_timeout_s": 8,
        "ambiguous_band": [0.35, 0.65],
        "default_output_tokens": 400,
        "ctx_slack_tokens": 512,
        "ctx_granularity": 1024,
        "cold_start_penalty_s": 6.0,
        "stale_lease_s": 900,
    },
    "context": {"enabled": True, "budget_fraction": 0.25, "max_cards": 12},
}


# The box this toggle turns on/off. Named here once so `Config.toggle_26b`/
# `status_26b` and the CLI below share one spelling instead of two that
# could drift apart.
_26B_NODE = "g26b"

# Same pattern, for the second local Mac Ollama instance (port 11435,
# see config.json's own note on it -- a separate process, separate
# resident weights, on the same 16GB-unified-memory machine as `mac`).
# Named here once for the same reason as _26B_NODE above.
_MAC2_NODE = "mac2"


class Config:
    def __init__(self, data: Dict[str, Any], path: str):
        self.path = path
        self.data = data
        self.routing = dict(DEFAULT_CONFIG["routing"])
        self.routing.update(data.get("routing", {}))
        self.context = dict(DEFAULT_CONFIG["context"])
        self.context.update(data.get("context", {}))
        self.models = data.get("models", {})
        self.nodes = [Node(n, self) for n in data.get("nodes", [])]

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "Config":
        if not os.path.exists(path):
            return cls(json.loads(json.dumps(DEFAULT_CONFIG)), path)
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh), path)

    def save(self) -> None:
        self.data["routing"] = self.routing
        self.data["context"] = self.context
        self.data["models"] = self.models
        self.data["nodes"] = [n.raw for n in self.nodes]
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, self.path)

    def node(self, name: str) -> Optional["Node"]:
        for n in self.nodes:
            if n.name == name:
                return n
        return None

    def enabled_nodes(self) -> List["Node"]:
        return [n for n in self.nodes if n.enabled]

    def toggle_26b(self, enable: bool) -> str:
        """Turn the whole `g26b` box on or off.

        "26b" is shorthand for the box, not just the MoE model that happens
        to be its default writer today -- corrected after an earlier
        version of this toggled only that one model and let `gemma4:12b`
        take over floor-4 work on the same node, which is not what "off"
        was asked to mean. Off now flips the same `enabled` flag
        `cfg.enabled_nodes()` already checks everywhere a model gets
        picked (`team.pick_solvers`, `team.named_model`, `swarm`'s own
        routing) -- identical to hand-editing `"enabled": false` in
        config.json, so nothing on that box runs, for any purpose, until
        it is turned back on. `model_bias` is untouched either way.
        """
        node = self.node(_26B_NODE)
        if node is None:
            raise SystemExit("no node named %r in config.json" % _26B_NODE)
        node.enabled = enable
        node.raw["enabled"] = enable
        self.save()
        return "on" if enable else "off"

    def status_26b(self) -> str:
        node = self.node(_26B_NODE)
        if node is None or not node.enabled:
            return "off"
        return "on"

    def toggle_mac2(self, enable: bool) -> str:
        """Turn the second Mac Ollama instance (port 11435) on or off.

        Identical mechanism to `toggle_26b` -- flips the same `enabled`
        flag every model-picking call site already checks, so nothing
        routes work here the instant it's off, with no other side
        effects. Unlike `toggle_26b`, there is deliberately no "who else
        covers this" message on the way off: `g26b` going offline
        removes a capability nothing else in the fleet duplicates, while
        `mac2` is a second, fully separate process serving the *same*
        model `mac` itself already serves on the same machine -- turning
        it off just means `mac` alone now handles whatever load both were
        splitting, not that some capability vanishes from the fleet.

        This only ever stops team.py/swarm.py from routing work to the
        node -- it does not stop the underlying process. The launchd
        service keeps running (and keeps holding its ~3.2GB resident)
        until `launchctl bootout gui/$(id -u)/com.local.ollama2` is run
        by hand; `cmd_mac2` prints that as a reminder on the way off, so
        "off" here is never mistaken for "the memory is back."
        """
        node = self.node(_MAC2_NODE)
        if node is None:
            raise SystemExit("no node named %r in config.json" % _MAC2_NODE)
        node.enabled = enable
        node.raw["enabled"] = enable
        self.save()
        return "on" if enable else "off"

    def status_mac2(self) -> str:
        node = self.node(_MAC2_NODE)
        if node is None or not node.enabled:
            return "off"
        return "on"

    def other_floor4_nodes(self, exclude: str = _26B_NODE) -> List[str]:
        """Every *other* enabled node carrying at least one capability-4
        model -- i.e. what is actually still able to serve floor-4 work
        if `exclude` (normally `g26b`) is turned off.

        Exists because `cmd_26b`'s off-warning used to be a fixed string
        ("g26b is the only node with one") written when that was true and
        never revisited when it stopped being one: `gemma4:e4b` on `mac`
        and `gemma4-e4b-fixed:latest` on `e4b-linux` were later both
        promoted from capability 2 to capability 4 (see each model's own
        `//capability` comment in config.json), and neither promotion
        touched this message, which kept asserting the opposite of what
        the same config it reads now says. A hardcoded claim about the
        fleet's shape is exactly the kind of fact this project has
        already decided, more than once, to read off the config rather
        than restate in prose that can drift out from under it.
        """
        found: List[str] = []
        for n in self.enabled_nodes():
            if n.name == exclude:
                continue
            if any(self.model_meta(m).get("capability", 0) >= 4
                   for m in n.models):
                found.append(n.name)
        return found

    def model_meta(self, model: str) -> Dict[str, Any]:
        meta = self.models.get(model)
        if meta:
            return meta
        # Unknown tag: infer conservatively from the name so the router still works.
        low = model.lower()
        if "e2b" in low:
            return {"capability": 1, "quality": 0.55, "max_ctx": 16384}
        if "e4b" in low:
            return {"capability": 2, "quality": 0.72, "max_ctx": 16384}
        if "12b" in low:
            return {"capability": 3, "quality": 0.85, "max_ctx": 16384}
        return {"capability": 4, "quality": 1.0, "max_ctx": 16384}


class Node:
    def __init__(self, raw: Dict[str, Any], cfg: Config):
        self.raw = raw
        self.cfg = cfg
        self.name: str = raw["name"]
        self.url: str = raw["url"].rstrip("/")
        self.os: str = raw.get("os", "unknown")
        self.models: List[str] = raw.get("models", [])
        self.bias: float = float(raw.get("bias", 1.0))
        self.model_bias: Dict[str, float] = raw.get("model_bias", {}) or {}
        self.enabled: bool = bool(raw.get("enabled", True))
        self.max_parallel: int = int(raw.get("max_parallel", 1))
        # True when this node cannot keep two of its registered models resident
        # at once -- typically because OLLAMA_MAX_LOADED_MODELS=1, or because
        # the models are large enough relative to VRAM/RAM that ollama evicts
        # the previous one anyway regardless of that setting. When true, using
        # model B pays a reload cost for model A next time, even if A was used
        # a second ago -- recency alone is not enough to call A "warm".
        self.single_slot: bool = bool(raw.get("single_slot", len(self.models) > 1))
        self.notes: str = raw.get("notes", "")

    def bias_for(self, model: str) -> float:
        """Node bias, optionally scaled per model.

        Lets one tag be held in reserve on a node without disabling the node,
        which is what you want when a model fits but costs the machine dearly.
        """
        return self.bias * float(self.model_bias.get(model, 1.0))

    def max_ctx(self, model: str) -> int:
        per_model = self.raw.get("max_ctx", {})
        if isinstance(per_model, dict) and model in per_model:
            return int(per_model[model])
        if isinstance(per_model, int):
            return int(per_model)
        return int(self.cfg.model_meta(model).get("max_ctx", 16384))

    def pin_ctx(self, model: str) -> Optional[int]:
        """A fixed num_ctx for this node, or None to size each call to its prompt.

        A changed num_ctx reloads the model (~35-55s measured here), which costs more
        than the memory a bigger window reserves. Config: `"pin_ctx": 65536` on the node,
        or `{"model:tag": 65536}` per model. Never above max_ctx.
        """
        pin = self.raw.get("pin_ctx")
        if isinstance(pin, dict):
            pin = pin.get(model)
        if not pin:
            return None
        return min(int(pin), self.max_ctx(model))

    def __repr__(self) -> str:
        return "<Node %s %s>" % (self.name, self.url)


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


def est_tokens(text: str) -> int:
    """Cheap token estimate. Gemma's 262K vocab averages close to 4 chars/token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def now() -> float:
    return time.time()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def human(seconds: float) -> str:
    if seconds < 1:
        return "%dms" % int(seconds * 1000)
    if seconds < 60:
        return "%.1fs" % seconds
    return "%dm%02ds" % (int(seconds // 60), int(seconds % 60))


# --------------------------------------------------------------------------
# state: cross-process load tracking, throughput history, run log
# --------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """Whether the process holding a claim still exists.

    Everything here is launched from this machine, so a claim left behind by a
    killed run can be identified and taken rather than blocking the pool until
    it expires.
    """
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


SCHEMA = """
CREATE TABLE IF NOT EXISTS node_claims (
    node TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    holders INTEGER NOT NULL DEFAULT 0,
    updated REAL NOT NULL,
    pid INTEGER
);
CREATE TABLE IF NOT EXISTS leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node TEXT NOT NULL,
    model TEXT NOT NULL,
    started REAL NOT NULL,
    est_seconds REAL NOT NULL,
    pid INTEGER
);
CREATE TABLE IF NOT EXISTS perf (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node TEXT NOT NULL,
    model TEXT NOT NULL,
    ts REAL NOT NULL,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    prefill_tps REAL,
    decode_tps REAL,
    load_s REAL,
    total_s REAL
);
CREATE INDEX IF NOT EXISTS perf_lookup ON perf (node, model, ts);
CREATE TABLE IF NOT EXISTS resident (
    node TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    ts REAL NOT NULL
);
-- Separate from `resident` on purpose rather than an added column: an
-- existing database's `resident` table was created without one, and
-- `CREATE TABLE IF NOT EXISTS` does not add columns to a table that
-- already exists. A new table needs no migration.
CREATE TABLE IF NOT EXISTS resident_ctx (
    node TEXT NOT NULL,
    model TEXT NOT NULL,
    ctx INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (node, model)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    prompt TEXT,
    tier INTEGER,
    reason TEXT,
    node TEXT,
    model TEXT,
    escalated INTEGER DEFAULT 0,
    seconds REAL,
    output_tokens INTEGER,
    ok INTEGER,
    logfile TEXT
);
-- How many processes currently want this node, whether they are holding
-- it or still blocked in `node_claim`'s poll loop waiting to. `node_
-- claims.holders` only ever counts *same-model* concurrent holders, and
-- resets to a different value the moment a different model takes the
-- claim -- it was never a general "how many are queued here" counter,
-- and nothing else recorded one. See `State.register_waiter` for the
-- live failure this exists to fix: several independent `team do`
-- processes each scored the same node as available (it read "busy" or
-- "not busy", never "busy behind how many others"), so every one of
-- them queued a worker thread onto it while genuinely idle nodes sat
-- untouched -- the queue concentrated instead of spreading out.
CREATE TABLE IF NOT EXISTS node_waiters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node TEXT NOT NULL,
    pid INTEGER,
    started REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS node_waiters_lookup ON node_waiters (node);
"""


class State:
    """SQLite-backed shared state.

    The connection is shared across the probe threads in `nodes`, so every
    access is serialised behind a lock rather than opened per thread.
    """

    def __init__(self, path: str = STATE_PATH):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # -- leases ---------------------------------------------------------
    def prune_leases(self, stale_after: float) -> None:
        with self.lock:
            self.db.execute("DELETE FROM leases WHERE started < ?", (now() - stale_after,))
            self.db.commit()

    def prune_dead_leases(self) -> None:
        """Drop any lease whose process no longer exists, regardless of age.

        `prune_leases` only clears a lease once it has sat past
        `stale_lease_s` (900s) -- a reasonable bound for "this estimate was
        wrong," but not for "the process holding it is gone." A crashed
        writer (killed, not caught by its own `finally: release_lease`, or
        simply `kill -9`'d) leaves a row that reports the node busy for up
        to fifteen minutes after nothing is actually running against it --
        measured live: two such rows, ~8 minutes old, made `e4b-linux` and
        `g26b` both read as occupied while `ps` showed neither PID existed.
        That directly defeats the busy-check this exists to back
        (`team._node_load`, `ground.pick_reader`), which is the reason this
        is checked on every read rather than left to the time-based prune
        alone.

        `os.kill(pid, 0)` sends no signal, only asks whether the OS still
        has that PID -- `ProcessLookupError` means gone, `PermissionError`
        means it exists but is owned by someone else (still alive, leave
        it), and `OSError`/`ValueError` (a null or malformed pid, or a
        platform where this call itself fails) are left alone rather than
        guessed at, since an unreadable liveness check is not evidence
        either way.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT id, pid FROM leases WHERE pid IS NOT NULL").fetchall()
        dead = []
        for r in rows:
            try:
                os.kill(int(r["pid"]), 0)
            except ProcessLookupError:
                dead.append(r["id"])
            except (PermissionError, OSError, ValueError):
                continue
        if dead:
            with self.lock:
                self.db.executemany("DELETE FROM leases WHERE id = ?",
                                    [(i,) for i in dead])
                self.db.commit()

    def acquire_lease(self, node: str, model: str, est_seconds: float) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO leases (node, model, started, est_seconds, pid)"
                " VALUES (?,?,?,?,?)",
                (node, model, now(), est_seconds, os.getpid()),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def release_lease(self, lease_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM leases WHERE id = ?", (lease_id,))
            self.db.commit()

    # -- node claims ----------------------------------------------------
    # Leases above are bookkeeping: they record what is in flight so the
    # router can estimate queueing, and acquiring one always succeeds. That is
    # fine within a process, and useless between two. Two commands run at once
    # will each pick the box they like best, and if they want different models
    # the node reloads on every single request -- 35 seconds each time, which
    # is more than most tasks take. Two runs launched together made no visible
    # progress in nineteen minutes.
    #
    # So a node is claimed exclusively for one model. Another process wanting
    # the same model may share it, since concurrent same-model requests cost
    # nothing extra; one wanting a different model waits. Waiting is genuinely
    # cheaper than swapping.

    def try_claim_node(self, node: str, model: str, stale_after: float = 3600.0,
                       max_parallel: int = 1) -> bool:
        """Claim a node for a model, or report that someone else holds it.

        `holders` used to grow without limit for same-model callers --
        "concurrent same-model requests cost nothing extra," since there is
        no swap to pay for. That is true of the swap and false of the GPU
        underneath it: `max_parallel` exists specifically because a light
        node's own config says concurrent generation there is unstable or
        slow, and nothing before this enforced that number anywhere, only
        used it for scoring which node to pick. So a same-model caller now
        only gets to increment `holders` while doing so keeps it under
        `max_parallel`, checked and incremented inside the same `BEGIN
        IMMEDIATE` transaction the swap-avoidance check already uses -- the
        two are the same atomic decision, not a check followed by a
        separate call that something else could interleave with.
        """
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT model, holders, updated, pid FROM node_claims "
                    "WHERE node = ?", (node,)).fetchone()
                if row is not None:
                    expired = (now() - float(row["updated"])) > stale_after
                    if not expired and int(row["holders"]) > 0 \
                            and _pid_alive(int(row["pid"])) \
                            and row["model"] != model:
                        self.db.execute("ROLLBACK")
                        return False
                    if not expired and row["model"] == model \
                            and _pid_alive(int(row["pid"])):
                        if int(row["holders"]) >= max_parallel:
                            self.db.execute("ROLLBACK")
                            return False
                        self.db.execute(
                            "UPDATE node_claims SET holders = holders + 1, "
                            "updated = ? WHERE node = ?", (now(), node))
                        self.db.execute("COMMIT")
                        return True
                    self.db.execute("DELETE FROM node_claims WHERE node = ?",
                                    (node,))
                self.db.execute(
                    "INSERT INTO node_claims (node, model, holders, updated, pid)"
                    " VALUES (?,?,?,?,?)",
                    (node, model, 1, now(), os.getpid()))
                self.db.execute("COMMIT")
                return True
            except sqlite3.Error:
                try:
                    self.db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                # A contended database should not stop work; the worst case is
                # the thrash this exists to avoid, which is what we had before.
                return True

    def release_node(self, node: str) -> None:
        with self.lock:
            try:
                self.db.execute(
                    "UPDATE node_claims SET holders = holders - 1, updated = ? "
                    "WHERE node = ?", (now(), node))
                self.db.execute(
                    "DELETE FROM node_claims WHERE node = ? AND holders <= 0",
                    (node,))
                self.db.commit()
            except sqlite3.Error:
                pass

    def active_claim(self, node: str, stale_after: float = 3600.0
                     ) -> Optional[Tuple[str, int]]:
        """Read-only peek at `node_claims`: `(model, holders)` if some live
        process genuinely holds this node right now, `None` otherwise.

        `try_claim_node` already does this exact staleness/pid-alive check
        every time it decides whether to grant a claim; this is the same
        check with no side effect, for a caller that only wants to know
        the current answer, not change it. It exists because `_node_load`
        (team.py) -- the one thing every independent process's `pick_
        solvers` call actually asks before ranking a node -- had no way to
        see this table at all, and read `leases` instead, a *different*
        table that is only populated for the few hundred milliseconds of
        an actual in-flight `chat_complete` round trip, not for the whole
        `node_claim`-held attempt loop around it (see `codegen.
        generate_file`, which holds `node_claim` for every attempt but
        only holds a `leases` row during each individual request inside
        that loop). Measured live: several independent `team do` processes
        -- one per step of a parallel fan-out, each running its own
        decompose-and-write with no visibility into the others -- all
        scored the same fast, always-warm, single-model node as their top
        pick, because between two of that node's own chat requests there
        was a `leases`-table gap during which `load_for` legitimately
        returned zero, even though `node_claims` still showed it locked
        for the whole loop. The result looked exactly like several
        `main.py`s "queued" for the same box while genuinely idle boxes
        sat untouched -- correct, atomic queueing underneath (no two
        attempts ever actually ran at once; `try_claim_node` already
        guarantees that), but the *scoring* that decided who queued for
        what had no way to prefer an idle box over one already spoken
        for by a sibling process it could not otherwise know about.
        """
        with self.lock:
            row = self.db.execute(
                "SELECT model, holders, updated, pid FROM node_claims "
                "WHERE node = ?", (node,)).fetchone()
        if row is None:
            return None
        if (now() - float(row["updated"])) > stale_after:
            return None
        if int(row["holders"]) <= 0:
            return None
        if not _pid_alive(int(row["pid"])):
            return None
        return row["model"], int(row["holders"])

    # -- waiters ----------------------------------------------------------
    def register_waiter(self, node: str) -> int:
        """Record that this process now wants `node`, whether it gets it
        immediately or has to poll for it. Call once on entering
        `node_claim`, before the first `try_claim_node` attempt, and
        release the returned id in a `finally` regardless of outcome.

        Deliberately model-agnostic and deliberately covers the whole
        hold-or-wait span, not just the waiting part: a process that is
        already generating against this node for several minutes (`node_
        claims.holders`, held for the whole attempt loop, not per-request)
        is exactly as much a reason for a sibling process to look
        elsewhere as one still polling to get in.
        """
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO node_waiters (node, pid, started) VALUES (?,?,?)",
                (node, os.getpid(), now()))
            self.db.commit()
            return int(cur.lastrowid)

    def unregister_waiter(self, waiter_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM node_waiters WHERE id = ?", (waiter_id,))
            self.db.commit()

    def waiter_count(self, node: str, stale_after: float = 3600.0) -> int:
        """How many processes currently want `node`, after dropping rows
        left behind by a process that no longer exists (killed, crashed,
        or simply never reached its own `finally`) -- the same dead-pid
        and staleness rules `prune_dead_leases`/`try_claim_node` already
        apply elsewhere, so an old crash cannot permanently inflate this
        count and starve a node nothing is actually using anymore.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT id, pid, started FROM node_waiters WHERE node = ?",
                (node,)).fetchall()
        stale_cutoff = now() - stale_after
        dead = []
        alive = 0
        for r in rows:
            if float(r["started"]) < stale_cutoff:
                dead.append(r["id"])
                continue
            pid = r["pid"]
            if pid is not None and not _pid_alive(int(pid)):
                dead.append(r["id"])
                continue
            alive += 1
        if dead:
            with self.lock:
                self.db.executemany(
                    "DELETE FROM node_waiters WHERE id = ?", [(i,) for i in dead])
                self.db.commit()
        return alive

    def load_for(self, node: str) -> Tuple[int, float]:
        """Return (in-flight count, estimated seconds until the node frees up)."""
        with self.lock:
            rows = self.db.execute(
                "SELECT started, est_seconds FROM leases WHERE node = ?", (node,)
            ).fetchall()
        if not rows:
            return 0, 0.0
        remaining = [max(0.0, r["est_seconds"] - (now() - r["started"])) for r in rows]
        return len(rows), sum(remaining)

    # -- perf -----------------------------------------------------------
    # Below this many prompt tokens, prompt_eval_duration is mostly fixed
    # per-request overhead rather than actual prefill work, so the implied
    # rate comes out absurdly low -- short benchmark prompts were reporting
    # 32 tok/s prefill on a GPU that does thousands. Recording those numbers
    # poisons every later estimate of how long a long prompt will take, so
    # short samples contribute decode timing only.
    MIN_PREFILL_SAMPLE_TOKENS = 256
    # A handful of decoded tokens is mostly startup jitter, and a one-token
    # warmup probe can imply a decode rate of thousands per second, which is
    # enough to convince the scheduler every task is instantaneous.
    MIN_DECODE_SAMPLE_TOKENS = 16

    def record_perf(self, node: str, model: str, sample: Dict[str, Any]) -> None:
        prompt_tokens = sample.get("prompt_tokens") or 0
        output_tokens = sample.get("output_tokens") or 0
        prefill = sample.get("prefill_tps")
        decode = sample.get("decode_tps")
        if prompt_tokens < self.MIN_PREFILL_SAMPLE_TOKENS:
            prefill = None
        if output_tokens < self.MIN_DECODE_SAMPLE_TOKENS:
            decode = None
        if prefill is None and decode is None:
            return  # nothing worth keeping
        with self.lock:
            self.db.execute(
                "INSERT INTO perf (node, model, ts, prompt_tokens, output_tokens,"
                " prefill_tps, decode_tps, load_s, total_s) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    node,
                    model,
                    now(),
                    sample.get("prompt_tokens"),
                    sample.get("output_tokens"),
                    prefill,
                    decode,
                    sample.get("load_s"),
                    sample.get("total_s"),
                ),
            )
            self.db.commit()

    def throughput(self, node: str, model: str, samples: int = 12) -> Optional[Dict[str, float]]:
        """Recency-weighted average of recent throughput samples."""
        with self.lock:
            rows = self.db.execute(
                "SELECT prefill_tps, decode_tps, load_s FROM perf"
                " WHERE node = ? AND model = ? AND decode_tps > 0"
                " ORDER BY ts DESC LIMIT ?",
                (node, model, samples),
            ).fetchall()
        if not rows:
            return None
        weight = 1.0
        acc = {"prefill_tps": 0.0, "decode_tps": 0.0, "load_s": 0.0}
        # Each metric carries its own weight total: prefill is absent from
        # short samples, and counting those as zero would understate it.
        wsum = {k: 0.0 for k in acc}
        for row in rows:
            for key in acc:
                value = row[key]
                if value is None:
                    continue
                acc[key] += value * weight
                wsum[key] += weight
            weight *= 0.75  # newer samples dominate
        return {k: (acc[k] / wsum[k] if wsum[k] else 0.0) for k in acc}

    def last_used(self, node: str, model: str) -> Optional[float]:
        with self.lock:
            row = self.db.execute(
                "SELECT MAX(ts) AS ts FROM perf WHERE node = ? AND model = ?",
                (node, model),
            ).fetchone()
        return row["ts"] if row and row["ts"] else None

    # -- residency --------------------------------------------------------
    def mark_resident(self, node: str, model: str) -> None:
        """Record that this model now holds the node's memory.

        On a single-slot node this implicitly evicts whatever was resident
        before, which is the whole point of tracking it separately from
        `last_used`: two models can each have a recent timestamp while only
        one of them is actually still loaded.
        """
        with self.lock:
            self.db.execute(
                "INSERT INTO resident (node, model, ts) VALUES (?,?,?)"
                " ON CONFLICT(node) DO UPDATE SET model=excluded.model, ts=excluded.ts",
                (node, model, now()),
            )
            self.db.commit()

    def resident_model(self, node: str) -> Optional[Tuple[str, float]]:
        with self.lock:
            row = self.db.execute(
                "SELECT model, ts FROM resident WHERE node = ?", (node,)
            ).fetchone()
        return (row["model"], row["ts"]) if row else None

    def resident_ctx(self, node: str, model: str) -> Optional[int]:
        """The largest `num_ctx` this (node, model) pair was last loaded at.

        Ollama reloads the weights whenever `num_ctx` changes, in either
        direction -- measured directly, warming at 1024 then requesting at
        8192 paid the full ~35s load again, and dropping back to 1024 paid
        it a third time. `codegen.plan_context` already fixes this within
        one component's own repair loop by sizing once for the whole
        thing, but a separate `do`/`code` invocation is a separate process
        with no memory of the last one, and an operator running a small
        request right after an `--api`-heavy one was paying a full reload
        purely because the new prompt happened to be smaller. Persisted
        here so the floor survives across invocations, not just within
        one -- reading it does not, by itself, get anything on GPU; only
        the next call that actually uses this many tokens does.
        """
        with self.lock:
            row = self.db.execute(
                "SELECT ctx FROM resident_ctx WHERE node = ? AND model = ?",
                (node, model)).fetchone()
        return int(row["ctx"]) if row else None

    def mark_resident_ctx(self, node: str, model: str, ctx: int) -> None:
        """Record the window a call actually asked Ollama to load.

        Monotonic by the caller's own choice, not enforced here -- the sole
        caller, `codegen.plan_context`, already takes the max of this and
        its own fresh estimate before ever calling back in, so what lands
        here is always the high-water mark, never a shrink.
        """
        with self.lock:
            self.db.execute(
                "INSERT INTO resident_ctx (node, model, ctx, ts) "
                "VALUES (?,?,?,?) ON CONFLICT(node, model) DO UPDATE "
                "SET ctx=excluded.ctx, ts=excluded.ts",
                (node, model, ctx, now()))
            self.db.commit()

    def cold_load_s(self, node: str, model: str) -> Optional[float]:
        """What a cold start on this pair really costs, as p75 of real loads.

        Only samples that actually loaded weights count -- a warm hit reports a
        load of roughly zero, so averaging everything would say a 35s load is
        free. Among real loads the times are tight (one box measured 32.7-36.9s
        across eight loads), so the estimate is stable, but a stray sample can
        be wild: the same machine that usually loads in 7.9s produced a 23.5s
        outlier, and a slow box produced 168s against a 46s median. Taking the
        maximum lets one bad afternoon permanently mislabel a node as hopeless,
        so this takes p75 -- above typical, below the tail.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT load_s FROM perf WHERE node = ? AND model = ? "
                "AND load_s > 0.5 ORDER BY load_s",
                (node, model),
            ).fetchall()
        loads = [float(r["load_s"]) for r in rows]
        if not loads:
            return None
        return loads[min(len(loads) - 1, int(len(loads) * 0.75))]

    # -- runs -----------------------------------------------------------
    def record_run(self, **kw: Any) -> int:
        cols = ",".join(kw.keys())
        marks = ",".join("?" for _ in kw)
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO runs (ts,%s) VALUES (?,%s)" % (cols, marks),
                tuple([now()] + list(kw.values())),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def recent_runs(self, limit: int = 20) -> List[sqlite3.Row]:
        with self.lock:
            return self.db.execute(
                "SELECT * FROM runs ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()


# --------------------------------------------------------------------------
# ollama http client
# --------------------------------------------------------------------------


class OllamaError(RuntimeError):
    pass


def _request(url: str, payload: Optional[Dict[str, Any]], timeout: float, stream: bool = False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise OllamaError("HTTP %s from %s: %s" % (exc.code, url, body))
    except urllib.error.URLError as exc:
        raise OllamaError("cannot reach %s: %s" % (url, exc.reason))
    except OSError as exc:
        raise OllamaError("cannot reach %s: %s" % (url, exc))


def list_models(node: Node, timeout: float = 5.0) -> List[str]:
    resp = _request(node.url + "/api/tags", None, timeout)
    payload = json.loads(resp.read().decode("utf-8"))
    return sorted(m["name"] for m in payload.get("models", []))


def loaded_models(node: Node, timeout: float = 5.0) -> List[str]:
    try:
        resp = _request(node.url + "/api/ps", None, timeout)
        payload = json.loads(resp.read().decode("utf-8"))
        return sorted(m["name"] for m in payload.get("models", []))
    except OllamaError:
        return []


def _cooldown_file(node: Node) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", node.url)
    return os.path.join(os.path.dirname(STATE_PATH), "cooldown-%s.txt" % safe)


def cooldown_wait(node: Node) -> float:
    """Leave a node idle for `cooldown_s` seconds between two of its requests.

    A card that is run flat out for an hour runs hot. The gap is counted from
    when the node's last request ended, in a file, so it holds across every
    process that talks to the node (a round, a pilot, a second shell).
    Returns the seconds waited.
    """
    try:
        gap = float(node.raw.get("cooldown_s") or 0)
    except (TypeError, ValueError):
        gap = 0.0
    if gap <= 0:
        return 0.0
    try:
        with open(_cooldown_file(node), encoding="utf-8") as fh:
            last = float(fh.read().strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    wait = last + gap - time.time()
    if wait > 0:
        time.sleep(min(wait, gap))
        return min(wait, gap)
    return 0.0


def cooldown_mark(node: Node) -> None:
    if not node.raw.get("cooldown_s"):
        return
    try:
        os.makedirs(os.path.dirname(_cooldown_file(node)), exist_ok=True)
        with open(_cooldown_file(node), "w", encoding="utf-8") as fh:
            fh.write("%.3f" % time.time())
    except OSError:
        pass


def _thinking_sample(node: Node, think: Optional[bool]) -> Dict[str, Any]:
    """Sampling that replaces a greedy decode on a thinking request.

    A node opts in with a `sample` object. Structured calls pass think=False
    and keep the caller's temperature, because those replies are parsed.
    """
    if think is False:
        return {}
    raw = node.raw.get("sample")
    if not isinstance(raw, dict):
        return {}
    picked: Dict[str, Any] = {}
    for key in ("temperature", "top_p", "top_k", "min_p"):
        if key in raw:
            picked[key] = raw[key]
    return picked


def chat(
    node: Node,
    model: str,
    messages: Sequence[Dict[str, str]],
    num_ctx: int,
    num_predict: int = -1,
    temperature: Optional[float] = None,
    timeout: float = 600.0,
    on_token: Optional[Any] = None,
    think: Optional[bool] = None,
    commit: bool = False,
    sampled: bool = False,
) -> Dict[str, Any]:
    """Streamed chat completion. Returns text plus a throughput sample.

    These are thinking models: reasoning arrives on `message.thinking` and the
    answer on `message.content`, and the two share one output budget. A capped
    num_predict can therefore be spent entirely on reasoning, ending with
    done_reason "length" and an empty answer. Pass think=False for short or
    structured replies, which skips reasoning entirely; leave it None to let
    the model reason on hard work.

    A node with `think_budget` stops a runaway thought. Ollama has no
    reasoning-budget flag, so the cap is counted here and one follow-up, with
    thinking off, carries the node's commit message.
    """
    options: Dict[str, Any] = {"num_ctx": num_ctx}
    if num_predict > 0:
        options["num_predict"] = num_predict
    if temperature is not None:
        options["temperature"] = temperature
    # `sampled` asks for the node's own sampler on a call that has thinking
    # off. Near-greedy decoding loops on some models inside a long answer.
    options.update(_thinking_sample(node, None if sampled else think))
    if sampled:
        # A fresh seed per call. Two identical asks must not be forced to
        # the same reply by a seed that never changes.
        options["seed"] = random.randrange(1, 2 ** 31)

    payload: Dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": True,
        "options": options,
    }
    if think is not None:
        payload["think"] = think
    budget = 0
    if think is not False and not commit:
        try:
            budget = int(node.raw.get("think_budget") or 0)
        except (TypeError, ValueError):
            budget = 0
    cooldown_wait(node)
    started = time.monotonic()
    resp = _request(node.url + "/api/chat", payload, timeout, stream=True)

    chunks: List[str] = []
    thoughts: List[str] = []
    thought_chars = 0
    budget_hit = False
    ttft: Optional[float] = None
    final: Dict[str, Any] = {}
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                raise OllamaError(str(event["error"]))
            msg = event.get("message") or {}
            reasoning = msg.get("thinking")
            if reasoning:
                thoughts.append(reasoning)
                thought_chars += len(reasoning)
                if budget and thought_chars // 4 >= budget:
                    budget_hit = True
                    break
            piece = msg.get("content", "")
            if piece:
                if ttft is None:
                    ttft = time.monotonic() - started
                chunks.append(piece)
                if on_token:
                    on_token(piece)
            if event.get("done"):
                final = event
    finally:
        if budget_hit:
            resp.close()
        cooldown_mark(node)

    total_s = time.monotonic() - started
    prompt_tokens = final.get("prompt_eval_count") or 0
    output_tokens = final.get("eval_count") or 0
    prompt_ns = final.get("prompt_eval_duration") or 0
    eval_ns = final.get("eval_duration") or 0
    sample = {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "prefill_tps": (prompt_tokens / (prompt_ns / NS)) if prompt_ns else None,
        "decode_tps": (output_tokens / (eval_ns / NS)) if eval_ns else None,
        "load_s": (final.get("load_duration") or 0) / NS,
        "total_s": total_s,
        "ttft_s": ttft,
    }
    text = "".join(chunks)
    thinking = "".join(thoughts)
    # An empty answer after a length stop means the budget went to reasoning.
    # Silently returning "" here is how six critics scored zero, so say so.
    starved = (not text.strip()) and bool(thinking) and \
        final.get("done_reason") == "length"
    if budget and starved:
        budget_hit = True
    if budget_hit and not commit:
        note = node.raw.get("think_budget_message") or (
            "Thinking budget reached. Stop deliberating and commit to your "
            "best answer or next action now.")
        sys.stderr.write("  %s thinking budget reached; asking for the answer\n"
                         % node.name)
        follow = list(messages) + [{"role": "user", "content": str(note)}]
        committed = chat(
            node, model, follow, num_ctx=num_ctx, num_predict=num_predict,
            temperature=temperature, timeout=timeout, on_token=on_token,
            think=False, commit=True)
        committed["budget_committed"] = True
        # The follow-up asks for the answer with thinking off. The reasoning
        # already produced is the part that was paid for.
        prior = thinking.strip()
        later = (committed.get("thinking") or "").strip()
        if prior and later:
            committed["thinking"] = prior + "\n" + later
        elif prior:
            committed["thinking"] = prior
        return committed
    return {"text": text, "thinking": thinking, "sample": sample,
            "raw": final, "thinking_starved": starved}


# --------------------------------------------------------------------------
# context store
# --------------------------------------------------------------------------

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@contextlib.contextmanager
def node_claim(state: "State", node: "Node", model: str,
               wait_s: float = 3600.0, poll_s: float = 1.5,
               on_wait: Optional[Any] = None) -> Any:
    """Hold a node exclusively for one model for the duration of a block.

    Callers each hold at most one node at a time, so there is no hold-and-wait
    across two of them and therefore nothing to deadlock. If the wait runs out
    the work proceeds anyway: a slow answer beats a refused one, and the only
    cost is the swap this was avoiding.

    `node.max_parallel` used to be read only by `_node_load`/`estimate` for
    *scoring* which node to pick, never enforced here at the one place that
    actually starts a request -- and same-model calls were let straight
    through regardless of how many were already in flight, on the reasoning
    that "concurrent same-model requests cost nothing extra" (no swap to
    pay for). That is true of the swap cost specifically and false of the
    GPU underneath it: `max_parallel` is set to 1 on every light node in
    this fleet's own config *because* concurrent generation there is
    unstable or slow, not merely because nobody measured a benefit to more.
    Measured live: six parallel `team do` processes each ran their own
    `pick_solvers` before any of the others had registered a write-phase
    lease -- planning happens on a different node than writing, so nothing
    naturally staggers this -- and four of them independently picked the
    same single-model node as their writer. Nothing here blocked any of
    them, so all four generated concurrently against one GPU that Ollama
    itself then serialised anyway, and the slowest ended up waiting out
    its own entire time budget behind three siblings it never knew it was
    queued behind, reporting "the node stopped answering" for a node that
    was answering everyone else the whole time.

    So `try_claim_node` now enforces `node.max_parallel` itself, atomically,
    inside the same transaction that already decides same-model-vs-
    different-model -- checking it here first, in a separate call, would
    leave a window between "capacity looked free" and "the claim was
    taken" for a second caller to slip through the same gap, which is
    exactly the bug this exists to close.
    """
    deadline = time.monotonic() + wait_s
    held = False
    warned = False
    # Registered for the whole span below, hold or wait alike -- see
    # `State.register_waiter`'s own doc for why a several-minute hold is
    # exactly as much a reason for a sibling process to look elsewhere as
    # someone still polling to get in, and why nothing before this made
    # that visible to `_node_load`'s scoring at all.
    waiter_id = state.register_waiter(node.name)
    try:
        while True:
            if state.try_claim_node(node.name, model, max_parallel=node.max_parallel):
                held = True
                break
            if time.monotonic() >= deadline:
                break
            if on_wait and not warned:
                warned = True
                on_wait(node.name, model)
            time.sleep(poll_s)
        try:
            yield held
        finally:
            if held:
                state.release_node(node.name)
    finally:
        state.unregister_waiter(waiter_id)


def chat_complete(
    node: Node,
    model: str,
    messages: Sequence[Dict[str, str]],
    num_ctx: int,
    answer_tokens: int = -1,
    think: Optional[bool] = None,
    headroom: float = 4.0,
    retries: int = 1,
    **kw: Any,
) -> Dict[str, Any]:
    """Generate, budgeting for reasoning on top of the answer.

    `answer_tokens` is how long the visible answer should be allowed to get.
    Reasoning is billed from the same generation budget, so the raw cap is
    scaled by `headroom` to leave the model room to think before it answers.
    If the answer still comes back empty because reasoning used the lot, the
    budget is doubled and the call retried rather than returning nothing.
    """
    budget = int(answer_tokens * headroom) if answer_tokens > 0 else -1
    attempt = 0
    prior = ""
    while True:
        result = chat(node, model, messages, num_ctx=num_ctx,
                      num_predict=budget, think=think, **kw)
        if prior:
            later = (result.get("thinking") or "").strip()
            result["thinking"] = prior + ("\n" + later if later else "")
        if result.get("budget_committed"):
            return result
        if not result.get("thinking_starved") or attempt >= retries or budget <= 0:
            return result
        prior = (result.get("thinking") or "").strip()
        budget *= 2
        attempt += 1


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip("'\"")


class Card:
    def __init__(self, path: str, meta: Dict[str, Any], body: str):
        self.path = path
        self.meta = meta
        self.body = body.strip()
        self.id: str = str(meta.get("id") or os.path.splitext(os.path.basename(path))[0])
        self.subject: str = str(meta.get("subject", "misc"))
        self.subcategory: str = str(meta.get("subcategory", ""))
        raw_tags = meta.get("tags", [])
        self.tags: List[str] = [str(t).lower() for t in raw_tags] if isinstance(raw_tags, list) else []
        self.summary: str = str(meta.get("summary", "")).strip()
        self.priority: int = int(meta.get("priority", 5))
        self.title: str = str(meta.get("title", self.id))
        # Always-cards are standing policy, not retrievable facts. They bypass
        # relevance scoring and ride along in the system prompt every call.
        self.always: bool = bool(meta.get("always", False))

    def render(self, tier: str) -> str:
        head = "## %s" % self.title
        if tier == "summary" or not self.body:
            return "%s\n%s" % (head, self.summary or self.body)
        return "%s\n%s" % (head, self.body)

    def tokens(self, tier: str) -> int:
        return est_tokens(self.render(tier))


def load_cards(directory: str = CONTEXT_DIR) -> List[Card]:
    cards: List[Card] = []
    if not os.path.isdir(directory):
        return cards
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            match = FRONTMATTER.match(text)
            meta: Dict[str, Any] = {}
            body = text
            if match:
                for line in match.group(1).splitlines():
                    if ":" not in line or line.strip().startswith("#"):
                        continue
                    key, _, value = line.partition(":")
                    meta[key.strip()] = _parse_scalar(value)
                body = text[match.end():]
            cards.append(Card(path, meta, body))
    return cards


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "to", "of", "in", "on", "for", "with", "as", "at", "by", "it", "this",
    "that", "i", "you", "we", "my", "do", "does", "how", "what", "why",
    "can", "should", "would", "please", "me", "so", "if", "then",
}


def _terms(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9_.\-]+", text.lower()) if w not in STOPWORDS and len(w) > 1]


def score_card(card: Card, query_terms: Sequence[str], tags: Sequence[str]) -> float:
    """Term overlap, weighted by where the term matched, plus tag and priority boosts."""
    if not query_terms and not tags:
        return 0.0
    haystacks = (
        (card.title.lower(), 3.0),
        (" ".join(card.tags), 3.0),
        ((card.subject + " " + card.subcategory).lower(), 2.0),
        (card.summary.lower(), 1.5),
        (card.body.lower(), 1.0),
    )
    score = 0.0
    for term in query_terms:
        for text, weight in haystacks:
            if term in text:
                score += weight
                break
    if tags:
        wanted = {t.lower() for t in tags}
        overlap = wanted & set(card.tags)
        if not overlap and wanted & {card.subject.lower(), card.subcategory.lower()}:
            overlap = wanted
        if not overlap:
            return 0.0  # explicit tag filter is a hard gate
        score += 6.0 * len(overlap)
    return score * (0.75 + card.priority / 20.0)


def pack_context(
    query: str,
    budget_tokens: int,
    tags: Sequence[str] = (),
    directory: str = CONTEXT_DIR,
    max_cards: int = 12,
    min_score: float = 4.0,
) -> Dict[str, Any]:
    """Select the most relevant cards that fit inside a token budget.

    Full bodies are used while there is room; once the budget tightens we fall
    back to each card's one-line summary rather than dropping it entirely.

    `min_score` is a relevance gate. Without it a couple of incidental word
    matches will drag a whole card into a prompt that had no use for it, which
    costs far more than the card could ever be worth.
    """
    cards = [c for c in load_cards(directory) if not c.always]
    terms = _terms(query)
    ranked = sorted(
        ((score_card(c, terms, tags), c) for c in cards),
        key=lambda pair: pair[0],
        reverse=True,
    )
    ranked = [(s, c) for s, c in ranked if s >= min_score][:max_cards]

    chosen: List[Tuple[Card, str]] = []
    used = 0
    for _score, card in ranked:
        for tier in ("full", "summary"):
            cost = card.tokens(tier)
            if used + cost <= budget_tokens:
                chosen.append((card, tier))
                used += cost
                break
    if not chosen:
        return {"text": "", "tokens": 0, "cards": []}

    blocks = [card.render(tier) for card, tier in chosen]
    manifest = [
        {"id": card.id, "tier": tier, "tokens": card.tokens(tier)} for card, tier in chosen
    ]
    return {
        "text": "# Relevant context\n\n" + "\n\n".join(blocks),
        "tokens": used,
        "cards": manifest,
    }


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

TIER_TRIVIAL, TIER_SIMPLE, TIER_MODERATE, TIER_HARD = 0, 1, 2, 3
TIER_NAMES = {0: "trivial", 1: "simple", 2: "moderate", 3: "hard"}

# Minimum model capability required per tier, overridable via routing.tier_floor.
# Capability is declared per model in config.json (e2b=1, e4b=2, 12b=3, 26b=4).
TIER_FLOOR = {0: 1, 1: 1, 2: 2, 3: 3}


def tier_floor(cfg: "Config", tier: int) -> int:
    override = cfg.routing.get("tier_floor")
    if isinstance(override, list) and len(override) == 4:
        return int(override[tier])
    return TIER_FLOOR[tier]

CODE_FENCE = re.compile(r"```")
PATH_LIKE = re.compile(
    r"\b[\w./\\-]+\.(py|js|jsx|ts|tsx|go|rs|java|rb|c|cpp|h|hpp|cs|swift|kt"
    r"|sh|bash|zsh|sql|json|ya?ml|toml|ini|cfg|md|txt|html|css)\b",
    re.IGNORECASE,
)
HARD_VERBS = re.compile(
    r"\b(implement|refactor|debug|architect|design|optimi[sz]e|migrat|profile"
    r"|benchmark|rewrite|port|integrat|parallel|concurren|thread|deadlock"
    r"|race condition|memory leak|stack trace|traceback|regression|algorithm"
    r"|prove|derive|complexity)\w*\b",
    re.IGNORECASE,
)
MODERATE_VERBS = re.compile(
    r"\b(plan|outline|compare|analy[sz]e|summari[sz]e|draft|explain|evaluate"
    r"|review|critique|weigh|trade-?off|decide|recommend|steps?)\w*\b",
    re.IGNORECASE,
)
TRIVIAL_SHAPES = re.compile(
    r"^\s*(is|are|does|do|did|was|were|can|will|has|have|should)\b.*\?\s*$"
    r"|^\s*(yes or no|true or false|status|ping|health|uptime|what time|hi|hello|hey)\b",
    re.IGNORECASE,
)
EXTRACTION = re.compile(
    r"\b(classif|categor|sentiment|extract|tag this|label|translate|rephrase"
    r"|shorten|title for|name for|one word|one line|single sentence|tl;?dr)\w*\b",
    re.IGNORECASE,
)
LONG_OUTPUT = re.compile(
    r"\b(essay|article|report|thorough|comprehensive|in depth|in-depth|detailed"
    r"|full|complete|from scratch|end to end|end-to-end)\b",
    re.IGNORECASE,
)


class Decision:
    def __init__(self, tier: int, confidence: float, reason: str, est_output: int):
        self.tier = tier
        self.confidence = confidence
        self.reason = reason
        self.est_output = est_output

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_name": TIER_NAMES[self.tier],
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "est_output_tokens": self.est_output,
        }


def classify(prompt: str, default_output: int = 400) -> Decision:
    """Deterministic first-pass triage. Free, instant, no tokens spent.

    Returns a confidence alongside the tier; low confidence is the signal to
    spend ~200ms asking e2b for a second opinion.
    """
    text = prompt.strip()
    n_tokens = est_tokens(text)
    words = len(text.split())
    signals: List[str] = []
    tier = TIER_SIMPLE
    confidence = 0.5

    has_code = bool(CODE_FENCE.search(text))
    has_path = bool(PATH_LIKE.search(text))
    hard = bool(HARD_VERBS.search(text))
    moderate = bool(MODERATE_VERBS.search(text))
    trivial_shape = bool(TRIVIAL_SHAPES.search(text))
    extraction = bool(EXTRACTION.search(text))
    long_output = bool(LONG_OUTPUT.search(text))
    numbered = len(re.findall(r"^\s*\d+[.)]\s", text, re.MULTILINE))

    if has_code or has_path:
        tier, confidence = TIER_HARD, 0.9
        signals.append("code" if has_code else "file-path")
    if hard:
        tier = TIER_HARD
        confidence = max(confidence, 0.85)
        signals.append("hard-verb")
    if long_output and tier < TIER_HARD:
        tier = TIER_HARD
        confidence = max(confidence, 0.75)
        signals.append("long-output")
    if numbered >= 3 and tier < TIER_MODERATE:
        tier, confidence = TIER_MODERATE, max(confidence, 0.7)
        signals.append("multi-step")
    if moderate and tier < TIER_MODERATE:
        tier, confidence = TIER_MODERATE, max(confidence, 0.7)
        signals.append("reasoning-verb")

    # Extraction beats the reasoning verbs, which false-positive on nouns like
    # "this review". A long extraction request is still real work, though.
    if extraction and words <= 40 and not (has_code or has_path or hard or long_output):
        tier, confidence = TIER_TRIVIAL, 0.85
        signals.append("extraction")
    elif tier <= TIER_SIMPLE:
        if trivial_shape and words <= 25:
            tier, confidence = TIER_TRIVIAL, 0.9
            signals.append("closed-question")
        elif words <= 12:
            tier, confidence = TIER_TRIVIAL, 0.7
            signals.append("very-short")

    # Long prompts carry enough material that a small model will flounder.
    if n_tokens > 1500 and tier < TIER_HARD:
        tier = TIER_HARD
        confidence = max(confidence, 0.8)
        signals.append("large-prompt")
    elif n_tokens > 500 and tier < TIER_MODERATE:
        tier = TIER_MODERATE
        confidence = max(confidence, 0.65)
        signals.append("medium-prompt")

    if not signals:
        signals.append("no-strong-signal")
        confidence = 0.4

    est_output = default_output
    if tier == TIER_TRIVIAL:
        est_output = 80
    elif tier == TIER_MODERATE:
        est_output = 600
    elif tier == TIER_HARD:
        est_output = 1200 if long_output or has_code else 800

    return Decision(tier, confidence, "+".join(signals), est_output)


CLASSIFIER_PROMPT = """You are a routing classifier. Read the user task and reply with exactly one word:
TRIVIAL - lookup, yes/no, status check, classification, extraction, one-line answer
SIMPLE - a short factual answer or small rewrite
MODERATE - multi-step reasoning, planning, comparison, or a summary of real material
HARD - writing or debugging code, architecture, math proofs, or long-form output

Reply with the single word only. No punctuation, no explanation.

TASK:
{task}"""

WORD_TO_TIER = {
    "TRIVIAL": TIER_TRIVIAL,
    "SIMPLE": TIER_SIMPLE,
    "MODERATE": TIER_MODERATE,
    "HARD": TIER_HARD,
}


def refine_with_model(
    prompt: str, cfg: Config, state: State, decision: Decision
) -> Decision:
    """Ask the fastest node for a second opinion on an ambiguous prompt."""
    model = cfg.routing["classifier_model"]
    candidates = [n for n in cfg.enabled_nodes() if model in n.models]
    if not candidates:
        return decision
    node = min(candidates, key=lambda n: state.load_for(n.name)[0])
    try:
        result = chat(
            node,
            model,
            [{"role": "user", "content": CLASSIFIER_PROMPT.format(task=prompt[:2000])}],
            num_ctx=2048,
            num_predict=4,
            temperature=0.0,
            timeout=float(cfg.routing["classifier_timeout_s"]),
        )
    except OllamaError:
        return decision
    state.record_perf(node.name, model, result["sample"])
    state.mark_resident(node.name, model)
    word = re.sub(r"[^A-Z]", "", result["text"].strip().upper())
    for key, tier in WORD_TO_TIER.items():
        if word.startswith(key):
            return Decision(
                tier, 0.75, decision.reason + "+e2b:" + key.lower(), _default_output(tier)
            )
    return decision


def _default_output(tier: int) -> int:
    return {0: 80, 1: 400, 2: 600, 3: 800}[tier]


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


class Candidate:
    def __init__(self, node: Node, model: str):
        self.node = node
        self.model = model
        self.expected_s = 0.0
        self.utility = 0.0
        self.detail: Dict[str, Any] = {}

    @property
    def label(self) -> str:
        return "%s/%s" % (self.node.name, self.model)


def candidates_for(cfg: Config, floor: int, exclude: Sequence[str] = ()) -> List[Candidate]:
    out: List[Candidate] = []
    for node in cfg.enabled_nodes():
        for model in node.models:
            meta = cfg.model_meta(model)
            if int(meta.get("capability", 1)) < floor:
                continue
            cand = Candidate(node, model)
            if cand.label in exclude:
                continue
            out.append(cand)
    return out


def weights_for_tier(cfg: "Config", tier: int) -> Tuple[float, float]:
    """Speed/quality exponents for this tier, falling back to the global pair.

    Difficulty should shift the tradeoff itself, not just which models are
    eligible: a trivial ping should stay speed-first, and a hard task should
    lean toward the best available answer even if it takes longer.
    """
    routing = cfg.routing
    sw_list = routing.get("speed_weight_by_tier")
    qw_list = routing.get("quality_weight_by_tier")
    sw = sw_list[tier] if isinstance(sw_list, list) and len(sw_list) == 4 else routing["speed_weight"]
    qw = qw_list[tier] if isinstance(qw_list, list) and len(qw_list) == 4 else routing["quality_weight"]
    return float(sw), float(qw)


def estimate(
    cand: Candidate, cfg: Config, state: State, prompt_tokens: int, out_tokens: int,
    tier: int = TIER_MODERATE,
) -> Candidate:
    """Expected wall-clock seconds, using measured throughput when we have it."""
    routing = cfg.routing
    meta = cfg.model_meta(cand.model)
    perf = state.throughput(cand.node.name, cand.model)

    if perf:
        prefill = perf["prefill_tps"] or 300.0
        decode = perf["decode_tps"] or 20.0
        measured = True
    else:
        # No measurements yet: fall back to the config hint, then to a guess.
        decode = float(meta.get("decode_tps_hint", 20.0))
        prefill = decode * 15.0
        measured = False

    depth, queue_s = state.load_for(cand.node.name)
    if depth >= cand.node.max_parallel:
        wait = queue_s
    else:
        wait = 0.0

    # A model ollama still holds in memory answers immediately; one that has
    # aged out of keep_alive has to be read off disk first.
    warm_window = float(routing.get("warm_window_s", 300))
    if cand.node.single_slot:
        # Recency of this model's own last use isn't enough here: a sibling
        # model on the same node may have run since and evicted it, even if
        # that happened one second ago. Node-wide residency is the true state.
        resident = state.resident_model(cand.node.name)
        warm = (
            resident is not None
            and resident[0] == cand.model
            and (now() - resident[1]) < warm_window
        )
    else:
        last = state.last_used(cand.node.name, cand.model)
        warm = last is not None and (now() - last) < warm_window

    if warm:
        cold = 0.0
    else:
        cold = state.cold_load_s(cand.node.name, cand.model) or float(
            routing["cold_start_penalty_s"]
        )

    compute = prompt_tokens / max(prefill, 1.0) + out_tokens / max(decode, 0.5)
    cand.expected_s = wait + cold + compute

    quality = float(meta.get("quality", 0.7))
    sw, qw = weights_for_tier(cfg, tier)
    bias = cand.node.bias_for(cand.model)
    # Maximise quality per unit of time, with both exponents tunable.
    cand.utility = (quality ** qw) / (max(cand.expected_s, 0.05) ** sw) * bias

    cand.detail = {
        "expected_s": round(cand.expected_s, 2),
        "queue_depth": depth,
        "wait_s": round(wait, 2),
        "decode_tps": round(decode, 1),
        "measured": measured,
        "warm": warm,
        "cold_s": round(cold, 1),
        "quality": quality,
        "bias": round(bias, 3),
        "speed_weight": sw,
        "quality_weight": qw,
        "utility": round(cand.utility, 4),
    }
    return cand


def choose(
    cfg: Config, state: State, floor: int, prompt_tokens: int, out_tokens: int,
    exclude: Sequence[str] = (), tier: int = TIER_MODERATE,
) -> Tuple[Optional[Candidate], List[Candidate]]:
    cands = candidates_for(cfg, floor, exclude)
    if not cands:
        # Nothing meets the floor. Take the strongest thing available instead of failing.
        cands = candidates_for(cfg, 1, exclude)
        if not cands:
            return None, []
    scored = [estimate(c, cfg, state, prompt_tokens, out_tokens, tier) for c in cands]
    scored.sort(key=lambda c: c.utility, reverse=True)
    return scored[0], scored


ESCALATE_MARK = "ESCALATE"
SYSTEM_SUFFIX = (
    "\n\nIf this task is beyond your ability to answer well, reply with exactly "
    "the single word ESCALATE and nothing else."
)


def needs_escalation(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    head = stripped[:60].upper()
    return head.startswith(ESCALATE_MARK) or stripped.upper() == ESCALATE_MARK


def standing_policy(directory: str = CONTEXT_DIR, full: bool = False) -> str:
    """Cards flagged `always: true`, rendered for the system prompt."""
    cards = [c for c in load_cards(directory) if c.always]
    cards.sort(key=lambda c: -c.priority)
    tier = "full" if full else "summary"
    parts = [c.summary if tier == "summary" and c.summary else c.render(tier)
             for c in cards]
    return "\n".join(p for p in parts if p)


def build_messages(
    prompt: str,
    system: Optional[str],
    context_text: str,
    allow_escalate: bool,
    policy: str = "",
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    sys_parts = [system] if system else []
    if policy:
        sys_parts.append(policy)
    if allow_escalate:
        sys_parts.append(SYSTEM_SUFFIX.strip())
    if sys_parts:
        messages.append({"role": "system", "content": "\n\n".join(sys_parts)})
    user = prompt if not context_text else context_text + "\n\n---\n\n" + prompt
    messages.append({"role": "user", "content": user})
    return messages


def pick_num_ctx(cfg: Config, node: Node, model: str, prompt_tokens: int,
                 out_tokens: int, reasoning: bool = True) -> int:
    """Size the KV cache to the actual request.

    Oversized num_ctx reserves VRAM that the weights need. On a box where the
    model already spills into system RAM this is the difference between usable
    and unusable speed.

    Undersized is worse, though. Reasoning tokens occupy the same window as the
    answer, so a window sized for an 80-token reply gives a thinking model no
    room to think: it fills the context, emits nothing, and looks to the router
    exactly like a model admitting it is out of its depth. Pass reasoning=False
    only when the caller has already budgeted for it.
    """
    routing = cfg.routing
    reserve = int(routing.get("reasoning_reserve_tokens", 900)) if reasoning else 0
    need = prompt_tokens + out_tokens + reserve + int(routing["ctx_slack_tokens"])
    gran = int(routing["ctx_granularity"])
    rounded = ((need + gran - 1) // gran) * gran
    pin = node.pin_ctx(model)
    if pin:
        # Pinned: always the same window, so the model stays loaded. A request that
        # genuinely needs more still gets it (one reload), up to max_ctx.
        rounded = max(rounded, pin)
    return int(clamp(rounded, gran, node.max_ctx(model)))


def run_prompt(
    cfg: Config,
    state: State,
    prompt: str,
    system: Optional[str] = None,
    force: Optional[str] = None,
    use_context: Optional[bool] = None,
    tags: Sequence[str] = (),
    stream_to: Optional[Any] = None,
    allow_escalate: bool = True,
    refine: bool = True,
    min_floor: Optional[int] = None,
    max_tokens: int = -1,
) -> Dict[str, Any]:
    routing = cfg.routing
    state.prune_leases(float(routing["stale_lease_s"]))

    decision = classify(prompt, int(routing["default_output_tokens"]))
    low, high = routing["ambiguous_band"]
    if refine and low <= decision.confidence <= high and not force:
        decision = refine_with_model(prompt, cfg, state, decision)

    floor = max(tier_floor(cfg, decision.tier), min_floor or 0)
    attempts: List[Dict[str, Any]] = []
    tried: List[str] = []
    escalations = 0
    max_escalations = int(routing["max_escalations"])

    while True:
        if force:
            cand = _resolve_force(cfg, force)
            if cand is None:
                raise SystemExit("no node/model matches %r" % force)
            ranked = [cand]
            estimate(cand, cfg, state, est_tokens(prompt), decision.est_output, decision.tier)
        else:
            cand, ranked = choose(
                cfg, state, floor, est_tokens(prompt), decision.est_output, tried,
                tier=decision.tier,
            )
        if cand is None:
            raise SystemExit("no usable node; run './swarm.py nodes' to check health")

        if use_context is None:
            # Trivial prompts are the fast path; retrieval would cost more
            # latency than the context could possibly save.
            want_context = cfg.context["enabled"] and decision.tier > TIER_TRIVIAL
        else:
            want_context = use_context
        context_text, manifest = "", []
        if want_context or tags:
            budget = int(
                cand.node.max_ctx(cand.model) * float(cfg.context["budget_fraction"])
            )
            packed = pack_context(
                prompt,
                budget,
                tags,
                max_cards=int(cfg.context["max_cards"]),
                min_score=float(cfg.context.get("min_score", 4.0)),
            )
            context_text, manifest = packed["text"], packed["cards"]

        # Reasoning earns its cost on hard work and is pure latency on a
        # status check, so it follows the tier like the other weights do.
        think_by_tier = routing.get("think_by_tier")
        think = None
        if isinstance(think_by_tier, list) and decision.tier < len(think_by_tier):
            think = bool(think_by_tier[decision.tier]) or None
            if think is None:
                think = False

        # Reasoning earns its cost on hard work and is pure latency on a
        # status check, so it follows the tier like the other weights do.
        think_by_tier = routing.get("think_by_tier")
        think = None
        if isinstance(think_by_tier, list) and decision.tier < len(think_by_tier):
            think = bool(think_by_tier[decision.tier]) or None
            if think is None:
                think = False

        policy = standing_policy() if cfg.context.get("standing_policy", True) else ""
        messages = build_messages(prompt, system, context_text, allow_escalate, policy)
        prompt_tokens = sum(est_tokens(m["content"]) for m in messages)
        num_ctx = pick_num_ctx(cfg, cand.node, cand.model, prompt_tokens, decision.est_output)

        lease = state.acquire_lease(cand.node.name, cand.model, cand.expected_s)
        started = time.monotonic()
        try:
            result = chat(
                cand.node,
                cand.model,
                messages,
                num_ctx=num_ctx,
                num_predict=max_tokens,
                on_token=stream_to,
                think=think,
            )
        except OllamaError as exc:
            state.release_lease(lease)
            attempts.append({"target": cand.label, "error": str(exc)})
            tried.append(cand.label)
            if len(tried) >= 4:
                raise SystemExit("all nodes failed: %s" % json.dumps(attempts))
            continue
        finally:
            state.release_lease(lease)

        elapsed = time.monotonic() - started
        state.record_perf(cand.node.name, cand.model, result["sample"])
        state.mark_resident(cand.node.name, cand.model)
        attempts.append(
            {
                "target": cand.label,
                "seconds": round(elapsed, 2),
                "output_tokens": result["sample"]["output_tokens"],
                "num_ctx": num_ctx,
            }
        )

        # An empty answer from a thinking model that ran out of room is not the
        # model declining the task, and escalating on it walks a trivial prompt
        # all the way up to the slowest node. Give it room and ask again.
        if result.get("thinking_starved") and cand.label not in tried:
            tried.append(cand.label)
            attempts[-1]["retried"] = "thinking overran the window"
            result = chat(
                cand.node, cand.model, messages,
                num_ctx=min(int(num_ctx * 2.5), cand.node.max_ctx(cand.model)),
                num_predict=max_tokens if max_tokens > 0 else -1,
                on_token=stream_to,
            )
            state.record_perf(cand.node.name, cand.model, result["sample"])

        if (
            allow_escalate
            and needs_escalation(result["text"])
            and escalations < max_escalations
        ):
            escalations += 1
            tried.append(cand.label)
            floor = min(4, cfg.model_meta(cand.model).get("capability", 1) + 1)
            decision.reason += "+escalated"
            continue

        logfile = _write_log(prompt, messages, result, cand, decision, manifest)
        run_id = state.record_run(
            prompt=prompt[:500],
            tier=decision.tier,
            reason=decision.reason,
            node=cand.node.name,
            model=cand.model,
            escalated=escalations,
            seconds=round(elapsed, 2),
            output_tokens=result["sample"]["output_tokens"],
            ok=1,
            logfile=logfile,
        )
        return {
            "answer": result["text"],
            "node": cand.node.name,
            "model": cand.model,
            "tier": TIER_NAMES[decision.tier],
            "reason": decision.reason,
            "seconds": round(elapsed, 2),
            "decode_tps": round(result["sample"]["decode_tps"] or 0, 1),
            "output_tokens": result["sample"]["output_tokens"],
            "prompt_tokens": result["sample"]["prompt_tokens"],
            "num_ctx": num_ctx,
            "escalations": escalations,
            "context_cards": manifest,
            "attempts": attempts,
            "run_id": run_id,
            "logfile": logfile,
            "considered": [
                {"target": c.label, **c.detail} for c in ranked[:5]
            ],
        }


def _resolve_force(cfg: Config, force: str) -> Optional[Candidate]:
    if "/" in force:
        node_name, model = force.split("/", 1)
        node = cfg.node(node_name)
        if node and model in node.models:
            return Candidate(node, model)
        return None
    node = cfg.node(force)
    if node and node.models:
        return Candidate(node, node.models[0])
    for n in cfg.enabled_nodes():
        for m in n.models:
            if m == force or m.split(":")[-1] == force:
                return Candidate(n, m)
    return None


def _write_log(prompt, messages, result, cand, decision, manifest) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + "-%s.json" % cand.node.name
    path = os.path.join(LOG_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "ts": now(),
                "prompt": prompt,
                "messages": messages,
                "decision": decision.to_dict(),
                "target": cand.label,
                "context_cards": manifest,
                "answer": result["text"],
                "sample": result["sample"],
            },
            fh,
            indent=2,
        )
    return path


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def cmd_ask(args, cfg: Config, state: State) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt or prompt == "-":
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("nothing to ask", file=sys.stderr)
        return 2

    sink = None
    if args.stream and not args.json:
        def sink(piece: str) -> None:
            sys.stdout.write(piece)
            sys.stdout.flush()

    result = run_prompt(
        cfg,
        state,
        prompt,
        system=args.system,
        force=args.to,
        use_context=None if args.context is None else args.context,
        tags=args.tag or (),
        stream_to=sink,
        allow_escalate=not args.no_escalate,
        refine=not args.no_refine,
        min_floor=args.floor,
        max_tokens=args.max_tokens,
    )

    if args.json:
        payload = dict(result)
        if args.no_answer:
            payload["answer"] = "<%d chars in %s>" % (
                len(result["answer"]), result["logfile"]
            )
        print(json.dumps(payload, indent=2))
    else:
        if not args.stream:
            print(result["answer"])
        elif not result["answer"].endswith("\n"):
            print()
        if not args.quiet:
            print(
                "\n[%s/%s | %s | %s | %.0f tok/s | ctx %d]"
                % (
                    result["node"], result["model"], result["tier"],
                    human(result["seconds"]), result["decode_tps"], result["num_ctx"],
                ),
                file=sys.stderr,
            )
    return 0


def cmd_route(args, cfg: Config, state: State) -> int:
    prompt = " ".join(args.prompt).strip() or sys.stdin.read().strip()
    state.prune_leases(float(cfg.routing["stale_lease_s"]))
    decision = classify(prompt, int(cfg.routing["default_output_tokens"]))
    low, high = cfg.routing["ambiguous_band"]
    ambiguous = low <= decision.confidence <= high
    if ambiguous and args.refine:
        decision = refine_with_model(prompt, cfg, state, decision)
    floor = max(tier_floor(cfg, decision.tier), args.floor or 0)
    best, ranked = choose(cfg, state, floor, est_tokens(prompt), decision.est_output,
                          tier=decision.tier)
    print(
        json.dumps(
            {
                "decision": decision.to_dict(),
                "ambiguous": ambiguous,
                "floor": floor,
                "choice": best.label if best else None,
                "considered": [{"target": c.label, **c.detail} for c in ranked],
            },
            indent=2,
        )
    )
    return 0


def cmd_nodes(args, cfg: Config, state: State) -> int:
    state.prune_leases(float(cfg.routing["stale_lease_s"]))
    rows = []
    lock = threading.Lock()

    def probe(node: Node) -> None:
        entry: Dict[str, Any] = {
            "name": node.name, "url": node.url, "os": node.os,
            "enabled": node.enabled, "bias": node.bias,
        }
        try:
            available = list_models(node)
            entry["reachable"] = True
            entry["configured"] = node.models
            entry["missing"] = [m for m in node.models if m not in available]
            entry["loaded"] = loaded_models(node)
        except OllamaError as exc:
            entry["reachable"] = False
            entry["error"] = str(exc)
        depth, queue_s = state.load_for(node.name)
        entry["in_flight"] = depth
        entry["queue_s"] = round(queue_s, 1)
        entry["throughput"] = {
            m: (lambda p: {k: round(v, 1) for k, v in p.items()} if p else None)(
                state.throughput(node.name, m)
            )
            for m in node.models
        }
        with lock:
            rows.append(entry)

    threads = [threading.Thread(target=probe, args=(n,)) for n in cfg.nodes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows.sort(key=lambda r: r["name"])

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        mark = "ok  " if r.get("reachable") else "DOWN"
        print("%s %-10s %-28s %s" % (mark, r["name"], r["url"], r["os"]))
        if not r.get("reachable"):
            print("       %s" % r.get("error", ""))
            continue
        for m in r["configured"]:
            tp = r["throughput"].get(m)
            speed = "%.0f tok/s" % tp["decode_tps"] if tp else "unmeasured"
            miss = "  MISSING - run: ollama pull %s" % m if m in r["missing"] else ""
            live = "  [loaded]" if m in r.get("loaded", []) else ""
            print("       %-16s %-12s%s%s" % (m, speed, live, miss))
        if r["in_flight"]:
            print("       %d in flight, ~%ss to drain" % (r["in_flight"], r["queue_s"]))
    return 0 if all(r.get("reachable") for r in rows) else 1


def cmd_add(args, cfg: Config, state: State) -> int:
    url = args.url if "://" in args.url else "http://" + args.url
    if not re.search(r":\d+$", url):
        url += ":11434"
    existing = cfg.node(args.name)
    node = Node(
        {
            "name": args.name,
            "url": url,
            "os": args.os,
            "models": args.model or [],
            "bias": args.bias,
            "max_parallel": args.max_parallel,
            "enabled": True,
            "notes": args.notes or "",
        },
        cfg,
    )
    if not node.models:
        try:
            node.models = list_models(node)
            node.raw["models"] = node.models
            print("discovered models: %s" % ", ".join(node.models))
        except OllamaError as exc:
            print("warning: could not reach node (%s); add models with --model" % exc,
                  file=sys.stderr)
    if existing:
        cfg.nodes[cfg.nodes.index(existing)] = node
    else:
        cfg.nodes.append(node)
    cfg.save()
    print("saved %s -> %s" % (args.name, url))
    return 0


def cmd_discover(args, cfg: Config, state: State) -> int:
    import socket

    base = args.subnet.rstrip(".")
    found: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def check(ip: str) -> None:
        sock = socket.socket()
        sock.settimeout(args.timeout)
        try:
            sock.connect((ip, args.port))
        except OSError:
            return
        finally:
            sock.close()
        probe = Node({"name": ip, "url": "http://%s:%d" % (ip, args.port)}, cfg)
        try:
            models = list_models(probe, timeout=4)
        except OllamaError:
            return
        with lock:
            found.append({"ip": ip, "models": models})

    threads = []
    for i in range(1, 255):
        t = threading.Thread(target=check, args=("%s.%d" % (base, i),))
        t.daemon = True
        threads.append(t)
        t.start()
        if len(threads) % 64 == 0:
            for t2 in threads:
                t2.join()
            threads = []
    for t in threads:
        t.join()

    found.sort(key=lambda f: tuple(int(p) for p in f["ip"].split(".")))
    print(json.dumps(found, indent=2))
    return 0


BENCH_PROMPT = (
    "List the first 20 prime numbers, one per line, with no commentary."
)


def cmd_bench(args, cfg: Config, state: State) -> int:
    targets: List[Tuple[Node, str]] = []
    for node in cfg.enabled_nodes():
        if args.node and node.name != args.node:
            continue
        for model in node.models:
            targets.append((node, model))

    results = []
    for node, model in targets:
        sys.stderr.write("benchmarking %s/%s ... " % (node.name, model))
        sys.stderr.flush()
        try:
            result = chat(
                node,
                model,
                [{"role": "user", "content": BENCH_PROMPT}],
                num_ctx=2048,
                num_predict=args.tokens,
                temperature=0.0,
                timeout=args.timeout,
            )
        except OllamaError as exc:
            sys.stderr.write("FAILED (%s)\n" % exc)
            results.append({"target": "%s/%s" % (node.name, model), "error": str(exc)})
            continue
        sample = result["sample"]
        state.record_perf(node.name, model, sample)
        state.mark_resident(node.name, model)
        decode = sample["decode_tps"] or 0
        sys.stderr.write(
            "%.1f tok/s  (load %.1fs, ttft %.2fs)\n"
            % (decode, sample["load_s"], sample["ttft_s"] or 0)
        )
        results.append(
            {
                "target": "%s/%s" % (node.name, model),
                "decode_tps": round(decode, 1),
                "prefill_tps": round(sample["prefill_tps"] or 0, 1),
                "load_s": round(sample["load_s"], 2),
                "ttft_s": round(sample["ttft_s"] or 0, 2),
            }
        )

    ok = [r for r in results if "decode_tps" in r]
    ok.sort(key=lambda r: r["decode_tps"], reverse=True)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\nfastest first:")
        for r in ok:
            print("  %-24s %6.1f tok/s   load %5.2fs   ttft %5.2fs"
                  % (r["target"], r["decode_tps"], r["load_s"], r["ttft_s"]))
    return 0


def cmd_ctx(args, cfg: Config, state: State) -> int:
    if args.ctx_cmd == "list":
        cards = load_cards()
        for c in sorted(cards, key=lambda c: (c.subject, c.subcategory, c.id)):
            print("%-22s %-14s %-14s p%-2d %s"
                  % (c.id, c.subject, c.subcategory, c.priority, ",".join(c.tags)))
        print("\n%d cards, %d tokens full / %d tokens summary"
              % (len(cards),
                 sum(c.tokens("full") for c in cards),
                 sum(c.tokens("summary") for c in cards)))
        return 0

    query = " ".join(args.query) if args.query else ""
    if args.ctx_cmd == "search":
        cards = load_cards()
        terms = _terms(query)
        ranked = sorted(
            ((score_card(c, terms, args.tag or ()), c) for c in cards),
            key=lambda p: p[0], reverse=True,
        )
        for score, card in ranked:
            if score <= 0:
                continue
            print("%6.2f  %-22s %s" % (score, card.id, card.summary or card.title))
        return 0

    packed = pack_context(query, args.budget, args.tag or (),
                          max_cards=int(cfg.context["max_cards"]),
                          min_score=float(cfg.context.get("min_score", 4.0)))
    if args.json:
        print(json.dumps(packed, indent=2))
    else:
        print(packed["text"])
        print("\n[%d tokens, %d cards]" % (packed["tokens"], len(packed["cards"])),
              file=sys.stderr)
    return 0


def cmd_26b(args, cfg: Config, state: State) -> int:
    """Turn the whole g26b box on or off, or report which state it's
    currently in. See `Config.toggle_26b` -- this disables the entire
    node, not just its default 26b model, so g26b itself has nowhere to
    run floor-4 work while it's off. Whether *anyone else* in the fleet
    still can is read off the live config (`other_floor4_nodes`) rather
    than asserted here, since that fact has already changed once (the
    e4b nodes were later promoted to capability 4) without this message
    being updated to match.
    """
    if args.state26b == "status":
        print(cfg.status_26b())
        return 0
    result = cfg.toggle_26b(args.state26b == "on")
    print("g26b is now %s" % result)
    if result == "off":
        others = cfg.other_floor4_nodes()
        if others:
            print("  floor-4 work now falls to: %s" % ", ".join(others))
        else:
            print("  no node in the fleet can serve floor-4 work until it's back on")
    return 0


def cmd_mac2(args, cfg: Config, state: State) -> int:
    """Turn the second local Mac Ollama instance on or off, or report
    which state it's currently in. See `Config.toggle_mac2` -- this only
    stops routing here; the process itself (and its resident memory)
    keeps running until stopped by hand at the OS level.
    """
    if args.state_mac2 == "status":
        print(cfg.status_mac2())
        return 0
    result = cfg.toggle_mac2(args.state_mac2 == "on")
    print("mac2 is now %s" % result)
    if result == "off":
        print("  the launchd service on port 11435 is still running and "
              "still holding its model resident -- this only stopped "
              "team.py/swarm.py from routing work to it. To actually "
              "free the memory: launchctl bootout gui/$(id -u)/"
              "com.local.ollama2")
    return 0


def cmd_log(args, cfg: Config, state: State) -> int:
    rows = state.recent_runs(args.limit)
    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return 0
    for r in rows:
        stamp = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
        esc = "^" if r["escalated"] else " "
        print("%s %s%-10s %-14s %-9s %6.1fs  %s"
              % (stamp, esc, r["node"], r["model"], TIER_NAMES.get(r["tier"], "?"),
                 r["seconds"] or 0, (r["prompt"] or "")[:52].replace("\n", " ")))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swarm", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="route a prompt and return the answer")
    ask.add_argument("prompt", nargs="*")
    ask.add_argument("--to", help="force a target: node, model, or node/model")
    ask.add_argument("--system", help="system prompt")
    ask.add_argument("--tag", action="append", help="restrict context to these tags")
    ask.add_argument("--context", dest="context", action="store_true", default=None)
    ask.add_argument("--no-context", dest="context", action="store_false")
    ask.add_argument("--stream", action="store_true", help="print tokens as they arrive")
    ask.add_argument("--json", action="store_true", help="machine-readable result")
    ask.add_argument("--no-answer", action="store_true",
                     help="with --json, omit the answer body and point at the log")
    ask.add_argument("--quiet", "-q", action="store_true", help="suppress the stats line")
    ask.add_argument("--no-escalate", action="store_true")
    ask.add_argument("--no-refine", action="store_true",
                     help="skip the e2b second opinion on ambiguous prompts")
    ask.add_argument("--floor", type=int, choices=[1, 2, 3, 4],
                     help="raise the minimum model capability for this call")
    ask.add_argument("--max-tokens", type=int, default=-1,
                     help="hard cap on generated tokens; -1 for no cap")
    ask.set_defaults(func=cmd_ask)

    route = sub.add_parser("route", help="show the routing decision without generating")
    route.add_argument("prompt", nargs="*")
    route.add_argument("--refine", action="store_true", help="allow the e2b second opinion")
    route.add_argument("--floor", type=int, choices=[1, 2, 3, 4])
    route.set_defaults(func=cmd_route)

    nodes = sub.add_parser("nodes", help="health, models and live load per node")
    nodes.add_argument("--json", action="store_true")
    nodes.set_defaults(func=cmd_nodes)

    add = sub.add_parser("add", help="add or replace a node in config.json")
    add.add_argument("name")
    add.add_argument("url", help="host, host:port, or full http url")
    add.add_argument("--os", default="unknown", choices=["windows", "linux", "macos", "unknown"])
    add.add_argument("--model", action="append", help="repeatable; omit to autodetect")
    add.add_argument("--bias", type=float, default=1.0,
                     help=">1 sends more traffic here, <1 sends less")
    add.add_argument("--max-parallel", type=int, default=1)
    add.add_argument("--notes", default="")
    add.set_defaults(func=cmd_add)

    disc = sub.add_parser("discover", help="scan a /24 for ollama servers")
    disc.add_argument("subnet", help="e.g. 192.168.1")
    disc.add_argument("--port", type=int, default=11434)
    disc.add_argument("--timeout", type=float, default=0.35)
    disc.set_defaults(func=cmd_discover)

    bench = sub.add_parser("bench", help="measure throughput and feed the router")
    bench.add_argument("--node")
    bench.add_argument("--tokens", type=int, default=160)
    bench.add_argument("--timeout", type=float, default=300.0)
    bench.add_argument("--json", action="store_true")
    bench.set_defaults(func=cmd_bench)

    ctx = sub.add_parser("ctx", help="inspect the context store")
    ctx.add_argument("ctx_cmd", choices=["list", "search", "pack"])
    ctx.add_argument("query", nargs="*")
    ctx.add_argument("--tag", action="append")
    ctx.add_argument("--budget", type=int, default=2000)
    ctx.add_argument("--json", action="store_true")
    ctx.set_defaults(func=cmd_ctx)

    b26 = sub.add_parser("26b", help="turn the whole g26b box on/off")
    b26.add_argument("state26b", choices=["on", "off", "status"])
    b26.set_defaults(func=cmd_26b)

    m2 = sub.add_parser("mac2",
                        help="turn the second local Mac Ollama instance "
                             "(port 11435) on/off -- routing only, does "
                             "not stop the underlying process or free "
                             "its resident memory")
    m2.add_argument("state_mac2", choices=["on", "off", "status"])
    m2.set_defaults(func=cmd_mac2)

    log = sub.add_parser("log", help="recent routing decisions")
    log.add_argument("--limit", type=int, default=20)
    log.add_argument("--json", action="store_true")
    log.set_defaults(func=cmd_log)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load()
    state = State()
    try:
        return args.func(args, cfg, state)
    except KeyboardInterrupt:
        return 130
    finally:
        state.close()


if __name__ == "__main__":
    sys.exit(main())
