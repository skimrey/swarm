"""Reproduce the planner call on its own, with timing and stop reason."""
import sys
import time

import intent
import swarm
import team

REQUEST = sys.argv[1] if len(sys.argv) > 1 else "Build a Python package"

cfg = swarm.Config.load()
state = swarm.State()
node, model = team.pick_role_model(cfg, state, 4)

restrict = cfg.data.get("team", {}).get("restrict_to_family")
strong_pool = team.pick_solvers(cfg, state, 4, count=4, family=restrict)
all_pool = team.pick_solvers(cfg, state, 1, count=6, family=restrict)
strong = max(1, len(strong_pool))
workers = max(strong, len(all_pool))

prompt = intent.DECOMPOSE_PROMPT.format(
    request=REQUEST, context="", workers=workers, strong=strong,
    light=max(1, workers - strong), idle=max(1, workers - 1),
    is_are="is" if strong == 1 else "are")

ptok = swarm.est_tokens(prompt)
ctx = swarm.pick_num_ctx(cfg, node, model, ptok, 1600, reasoning=False)
print("node=%s/%s prompt_tokens=%d num_ctx=%d" % (node.name, model, ptok, ctx))

started = time.monotonic()
r = swarm.chat_complete(
    node, model, [{"role": "user", "content": prompt}],
    num_ctx=ctx, answer_tokens=1600, headroom=1.8, think=False,
    temperature=0.1, timeout=420)
elapsed = time.monotonic() - started

raw = r.get("raw") or {}
print("elapsed=%.1fs done_reason=%r" % (elapsed, raw.get("done_reason")))
print("eval_count=%s prompt_eval_count=%s"
      % (raw.get("eval_count"), raw.get("prompt_eval_count")))
print("thinking_chars=%d answer_chars=%d"
      % (len(r.get("thinking") or ""), len(r.get("text") or "")))
print("attempts=%s" % r.get("attempts"))
print("--- first 600 chars of answer ---")
print((r.get("text") or "")[:600])
print("--- parsed ---")
for c in intent.parse_components(r["text"]):
    print("  %-18s %-26s floor %d  deps=%s"
          % (c.name[:18], c.path, c.floor, c.deps))
state.close()
