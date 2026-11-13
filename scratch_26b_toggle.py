"""Validates swarm.Config.toggle_26b/status_26b and their enforcement in
team.pick_solvers and team.named_model, against a throwaway copy of the
real config.json so this never mutates the operator's actual settings.

"26b" is shorthand for the whole g26b box, not just the MoE model that
happens to be its default writer today -- an earlier version of this
toggle disabled only that one model and let gemma4:12b take over floor-4
work on the same node, which is not what "off" was asked to mean. Off now
flips the box's own `enabled` flag, the same one `cfg.enabled_nodes()`
already checks everywhere a model gets picked, so nothing on that box runs
for any purpose while it's off -- not the 26b, not 12b, not the dense
gemma4-fixed fallback either.
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

# 1. Starting state matches the real config -- g26b enabled by default.
check("status starts on (matches the real config's own default)",
      cfg.status_26b() == "on")

# 2. Turning it off flips the whole node's `enabled` flag, not a
#    per-model bias.
result = cfg.toggle_26b(False)
node = cfg.node("g26b")
check("toggle_26b(False) reports off", result == "off")
check("status_26b reports off", cfg.status_26b() == "off")
check("the node's own enabled flag is false", node.enabled is False)
check("g26b is absent from enabled_nodes()",
      all(n.name != "g26b" for n in cfg.enabled_nodes()))

# 3. Nothing on g26b is offered at floor 4 while off -- not the 26b, not
#    12b, not the dense fallback -- because the whole box is gone. This
#    used to mean pick_solvers returned nothing at all, back when g26b was
#    the only capability-4 node in the fleet; `gemma4:e4b` (mac) and
#    `gemma4-e4b-fixed:latest` (e4b-linux) are capability 4 too now (see
#    config.json's 2026-09-20 note), so the assertion here is scoped to
#    what toggle_26b actually controls -- g26b's own absence -- rather than
#    the now-outdated claim that no other node can ever cover floor 4.
picks = team.pick_solvers(cfg, state, floor=4, count=3)
check("pick_solvers offers no g26b model at floor 4 while off",
      all(n.name != "g26b" for n, _ in picks))

# 4. named_model refuses every model on that box by exact name too, not
#    just the 26b -- this is a whole-node switch.
check("named_model refuses the 26b while off",
      team.named_model(cfg, "gemma4:26b-a4b-it-mtp-q4_K_M") is None)
check("named_model refuses 12b on the same box while off",
      team.named_model(cfg, "gemma4:12b") is None)
check("named_model refuses the dense gemma4-fixed fallback while off",
      team.named_model(cfg, "gemma4-fixed:latest") is None)

# 5. Turning it back on restores normal operation, exactly as if the
#    operator had hand-edited "enabled": true back into config.json --
#    model_bias values are untouched throughout, since this never
#    touched them.
before_bias = dict(node.model_bias)
result = cfg.toggle_26b(True)
check("toggle_26b(True) reports on", result == "on")
check("status_26b reports on again", cfg.status_26b() == "on")
check("the node's own enabled flag is true again", node.enabled is True)
check("model_bias was never touched by either toggle",
      node.model_bias == before_bias)
found = team.named_model(cfg, "gemma4:26b-a4b-it-mtp-q4_K_M")
check("named_model finds the 26b again once re-enabled",
      found is not None and found[1] == "gemma4:26b-a4b-it-mtp-q4_K_M")
picks = team.pick_solvers(cfg, state, floor=4, count=3)
check("pick_solvers offers g26b again once re-enabled",
      any(n.name == "g26b" for n, _m in picks))

# 6. Sabotage: turning it off must not touch any other node's `enabled`
#    flag -- this is a scoped switch, not a global kill.
cfg.toggle_26b(False)
other_nodes_untouched = all(
    n.enabled for n in cfg.nodes if n.name != "g26b"
)
check("no other node is disabled by the toggle", other_nodes_untouched)
cfg.toggle_26b(True)

# 7. `cmd_26b`'s off-warning used to hardcode "g26b is the only node
#    with one" (floor-4 capacity), which stopped being true the moment
#    the e4b nodes were promoted to capability 4 without this message
#    being revisited -- see `Config.other_floor4_nodes`'s own docstring.
#    Against this test's copy of the *real* config.json, both `mac`
#    and `e4b-linux` are enabled and capability-4, so the fixed check
#    must name them once g26b is off, not claim the fleet is empty.
cfg.toggle_26b(False)
others = cfg.other_floor4_nodes()
check("other_floor4_nodes finds the promoted e4b nodes while g26b is off",
      set(others) == {"mac", "e4b-linux"})
cfg.toggle_26b(True)
check("other_floor4_nodes excludes g26b itself while g26b is on",
      "g26b" not in cfg.other_floor4_nodes())

# 8. Sabotage: reproduce the pre-fix reasoning directly -- if g26b were
#    genuinely the only capability-4 node (e.g. against an older config
#    where the e4b promotions never happened), other_floor4_nodes must
#    correctly come back empty rather than always finding something.
demoted = json.loads(json.dumps(real))
for n in demoted["nodes"]:
    if n["name"] != "g26b":
        for m in n.get("models", []):
            demoted.setdefault("models", {}).setdefault(m, {})["capability"] = 2
demoted_path = os.path.join(tmp_dir, "demoted_config.json")
with open(demoted_path, "w", encoding="utf-8") as fh:
    json.dump(demoted, fh)
demoted_cfg = swarm.Config.load(demoted_path)
check("other_floor4_nodes correctly comes back empty when no promotion "
     "exists (the pre-2026-09-20 fleet shape)",
      demoted_cfg.other_floor4_nodes() == [])

# 7. The change actually persisted to disk (Config.save was really called,
#    not just mutated in memory).
with open(tmp_path, encoding="utf-8") as fh:
    on_disk = json.load(fh)
on_disk_g26b = [n for n in on_disk["nodes"] if n["name"] == "g26b"][0]
check("the on-disk config reflects the current (on) state",
      on_disk_g26b.get("enabled") is True)

state.close()

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
