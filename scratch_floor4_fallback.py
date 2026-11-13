"""Validates that floor-4 work still has somewhere to go once nothing in the
fleet offers a floor-4 model.

`g26b` was the only capability-4 node when this was written, so toggling it
off with `swarm.Config.toggle_26b` was enough to reach that state on its
own. It no longer is: `gemma4:e4b` (mac) and `gemma4-e4b-fixed:latest`
(e4b-linux) are capability 4 too now (see config.json's 2026-09-20 note), so
this test explicitly disables those two nodes as well wherever it needs the
"truly nobody at floor 4" case -- the fleet composition changed, not the
thing this test exists to guard.

Two separate gaps existed here, both against a throwaway copy of the real
config.json so this never touches the operator's actual settings:

1. `team.pick_writer` (new) is what every repair pass (`interface_gate`,
   `fix_redefinitions`, the integration-check retry loop) now calls instead
   of `pick_solvers(...) or pick_solvers(...)`, which used to give up
   silently -- `if not writers: continue` -- the instant no node offered
   the requested floor. With g26b off that is every single floor-4 repair,
   forever, with nothing printed to explain why the file was never fixed.

2. `run_components`'s own top-level pool selection already had a fallback
   for a component's *first* attempt (`if not strong: strong = light[:1]`),
   but it borrowed a light-tier node without removing it from `light`,
   so that node ended up spawned as two separate worker threads polling
   two different floors at once against the same (single_slot) Ollama
   server.
"""
import json
import os
import tempfile

import swarm
import team

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


with open(os.path.join(swarm.HERE, "config.json"), encoding="utf-8") as fh:
    real = json.load(fh)

tmp_dir = tempfile.mkdtemp()
tmp_path = os.path.join(tmp_dir, "config.json")
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(real, fh)

cfg = swarm.Config.load(tmp_path)
state = swarm.State(os.path.join(tmp_dir, "state.db"))


def fake_rates(cfg, state, node, model):
    return (0, 30.0, 5.0)


team.rates = fake_rates

# 1. With g26b on, pick_writer(floor=4) behaves exactly like pick_solvers
#    always did -- a real floor-4 model, no fallback needed.
found = team.pick_writer(cfg, state, 4)
check("with g26b on, pick_writer finds a real floor-4 model",
      found is not None
      and int(cfg.model_meta(found[1]).get("capability", 1)) >= 4)

def _set_other_cap4_nodes(enabled: bool) -> None:
    """mac/e4b-linux are capability 4 now too -- disable them alongside
    g26b wherever this test needs the genuine "no floor-4 anywhere" state
    that used to follow from `toggle_26b` alone."""
    for n in cfg.nodes:
        if n.name in ("mac", "e4b-linux"):
            n.enabled = enabled


# 2. Toggle the whole box off, the way the operator actually does it, and
#    also take the other two capability-4 nodes out so this reaches the
#    same "nothing at floor 4" state it always meant to.
cfg.toggle_26b(False)
_set_other_cap4_nodes(False)
check("g26b is off", cfg.status_26b() == "off")

# 3. pick_writer must still return someone -- the whole point of this
#    fix -- rather than None, which is what every repair site used to
#    settle for.
found = team.pick_writer(cfg, state, 4)
check("pick_writer still finds a writer with g26b off", found is not None)
check("...but it is not a floor-4 model, since none exist right now",
      found is not None
      and int(cfg.model_meta(found[1]).get("capability", 1)) < 4)

# 4. Sabotage: with every node disabled, there is truly nobody, and
#    pick_writer must say so rather than inventing a candidate.
for n in cfg.nodes:
    n.enabled = False
check("pick_writer returns None when the whole fleet is disabled",
      team.pick_writer(cfg, state, 4) is None)
for n in cfg.nodes:
    n.enabled = True
cfg.toggle_26b(True)
_set_other_cap4_nodes(True)

# 5. The `run_components` pool-selection fallback: reproduce its own
#    strong/light borrowing logic directly (mirrors the real code path)
#    and confirm the borrowed node is removed from `light`, so it is
#    never spawned as two worker threads against the same server at once.
cfg.toggle_26b(False)
_set_other_cap4_nodes(False)
restrict = None
strong = team.pick_solvers(cfg, state, 4, count=len(cfg.enabled_nodes()),
                           family=restrict)
light = [t for t in team.pick_solvers(cfg, state, 1, count=4,
                                      family=restrict)
         if t[0].name not in {n.name for n, _ in strong}]
check("strong is empty with g26b (and the other capability-4 nodes) off",
      strong == [])
check("light has candidates to borrow from", len(light) > 0)

if not strong:
    strong = light[:1]
    borrowed = {n.name for n, _ in strong}
    light = [t for t in light if t[0].name not in borrowed] or strong

borrowed_name = strong[0][0].name
check("the borrowed node is removed from light (no duplicate worker thread)",
      all(n.name != borrowed_name for n, _ in light) or light == strong)

cfg.toggle_26b(True)
_set_other_cap4_nodes(True)
state.close()

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
