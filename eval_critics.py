#!/usr/bin/env python3
"""Measure whether a critic is worth its wall-clock.

Claims about "independent review from a different model lineage" are cheap to
make and easy to get wrong, so this measures it. Each item is a task paired
with a draft answer that contains exactly one objectively checkable error, or
no error at all. A useful critic catches the planted errors without raising
alarms on the clean ones -- a critic that always says MAJOR finds every bug and
is worthless.

    ./eval_critics.py                 # every configured critic
    ./eval_critics.py --node e2b-2    # just one box

Scoring is deliberately generous to the critic: flagging a wrong answer counts
as a catch if the verdict is non-AGREE and any required keyword shows up.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import swarm
import team

# Each item: task, draft answer, and either the keywords that identify the
# planted error, or None when the draft is correct and should be left alone.
ITEMS: List[Dict[str, Any]] = [
    {
        "id": "pct",
        "task": "What is 17% of 240?",
        "draft": "17% of 240 is 42.8.",
        "keywords": ["40.8", "40,8", "incorrect", "wrong", "arithmetic", "42.8"],
    },
    {
        "id": "range",
        "task": "In Python, how many elements does list(range(1, 10)) contain, and what are they?",
        "draft": "It contains 10 elements: the integers 1 through 10 inclusive.",
        "keywords": ["9", "nine", "exclusive", "excludes", "up to 9", "1 through 9"],
    },
    {
        "id": "speed",
        "task": "A car travels 60 miles in 45 minutes. What is its average speed in mph?",
        "draft": "45 minutes is 45/60 of an hour, so the average speed is 45 mph.",
        "keywords": ["80", "eighty"],
    },
    {
        "id": "strip",
        "task": "In Python, what does 'abcdefcba'.strip('abc') return?",
        "draft": "strip('abc') removes the exact substring 'abc' from both ends, "
                 "so the result is 'defcba'.",
        "keywords": ["def", "set of characters", "any of", "individual char",
                     "not a substring", "each character"],
    },
    {
        "id": "clean_dict",
        "task": "In Python 3.9, does a regular dict preserve insertion order?",
        "draft": "Yes. Since Python 3.7 the insertion-order preservation of dict "
                 "is part of the language specification, so in 3.9 a regular dict "
                 "preserves insertion order.",
        "keywords": None,  # correct: any non-AGREE verdict is a false alarm
    },
    {
        "id": "clean_gil",
        "task": "Briefly: why doesn't threading speed up CPU-bound pure-Python loops in CPython 3.9?",
        "draft": "Because CPython uses a global interpreter lock, so only one thread "
                 "executes Python bytecode at a time. Threads help with I/O-bound work "
                 "where the lock is released while waiting, but CPU-bound pure-Python "
                 "loops just take turns and see no speedup.",
        "keywords": None,
    },
]


def run_critic(node: swarm.Node, model: str, cfg: swarm.Config, state: swarm.State,
               think: bool = False) -> Dict[str, Any]:
    per_item: List[Dict[str, Any]] = []
    for item in ITEMS:
        prompt = team.CRITIC_PROMPT.format(task=item["task"], answer=item["draft"])
        t0 = time.monotonic()
        try:
            # A verdict plus a few bullets is a short answer, but reasoning is
            # billed from the same budget, so ask for the answer length and let
            # chat_complete reserve room to think on top of it.
            r = swarm.chat_complete(
                node, model, [{"role": "user", "content": prompt}],
                num_ctx=swarm.pick_num_ctx(cfg, node, model,
                                           swarm.est_tokens(prompt), 2400),
                answer_tokens=250, think=(None if think else False),
                headroom=8.0, temperature=0.0, timeout=900)
            elapsed = time.monotonic() - t0
            state.record_perf(node.name, model, r["sample"])
            state.mark_resident(node.name, model)
            verdict, issues = team.parse_verdict(r["text"])
            blob = (" ".join(issues) + " " + r["text"]).lower()
            think_chars = len(r.get("thinking") or "")
        except swarm.OllamaError as exc:
            per_item.append({"id": item["id"], "error": str(exc), "seconds": 0.0})
            continue

        if item["keywords"] is None:
            ok = verdict == "AGREE"
            kind = "clean"
        else:
            hit = any(k.lower() in blob for k in item["keywords"])
            ok = verdict in ("MINOR", "MAJOR") and hit
            kind = "planted"
        per_item.append({"id": item["id"], "kind": kind, "verdict": verdict,
                         "correct": ok, "seconds": round(elapsed, 1),
                         "think_chars": think_chars,
                         "answer_chars": len(r["text"]),
                         "issues": issues[:3]})

    planted = [p for p in per_item if p.get("kind") == "planted"]
    clean = [p for p in per_item if p.get("kind") == "clean"]
    caught = sum(1 for p in planted if p["correct"])
    quiet = sum(1 for p in clean if p["correct"])
    times = [p["seconds"] for p in per_item if p.get("seconds")]
    tc = [p["think_chars"] for p in per_item if p.get("think_chars")]
    ac = [p["answer_chars"] for p in per_item if p.get("answer_chars")]
    return {
        "target": "%s/%s" % (node.name, model),
        "think": think,
        "family": cfg.model_meta(model).get("family"),
        "caught": "%d/%d" % (caught, len(planted)),
        "false_alarms": "%d/%d" % (len(clean) - quiet, len(clean)),
        "mean_seconds": round(sum(times) / len(times), 1) if times else None,
        "think_to_answer_ratio": round(sum(tc) / sum(ac), 2) if tc and sum(ac) else 0.0,
        "total_seconds": round(sum(times), 1),
        "items": per_item,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", action="append")
    ap.add_argument("--think", action="store_true",
                    help="let the critic reason first (slower, bigger budget)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="critic_eval.json")
    args = ap.parse_args(argv)

    cfg = swarm.Config.load()
    state = swarm.State()

    targets: List[Tuple[swarm.Node, str]] = []
    for node in cfg.enabled_nodes():
        if args.node and node.name not in args.node:
            continue
        for model in node.models:
            if float(node.model_bias.get(model, 1.0)) < 0.5 and \
                    cfg.model_meta(model).get("family") == cfg.data.get(
                        "team", {}).get("primary_family"):
                continue  # same-family models pinned out of rotation
            targets.append((node, model))

    results: List[Dict[str, Any]] = []
    lock = threading.Lock()
    by_node: Dict[str, List[Tuple[swarm.Node, str]]] = {}
    for n, m in targets:
        by_node.setdefault(n.name, []).append((n, m))

    def worker(items: List[Tuple[swarm.Node, str]]) -> None:
        for n, m in items:
            sys.stderr.write("  running %s/%s\n" % (n.name, m))
            sys.stderr.flush()
            res = run_critic(n, m, cfg, state, think=args.think)
            with lock:
                results.append(res)

    threads = [threading.Thread(target=worker, args=(v,), daemon=True)
               for v in by_node.values()]
    sys.stderr.write("%d critics x %d items, nodes in parallel\n"
                     % (len(targets), len(ITEMS)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    results.sort(key=lambda r: r["target"])
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print("\n%-26s %-8s %-8s %-13s %-9s  (think=%s)" %
          ("critic", "family", "caught", "false alarms", "mean s", args.think))
    for r in results:
        print("%-26s %-8s %-8s %-13s %-9s" %
              (r["target"], r["family"], r["caught"], r["false_alarms"],
               r["mean_seconds"]))
    print("\nfull detail: %s" % args.out)
    state.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
