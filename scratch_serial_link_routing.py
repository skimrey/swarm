"""Validates the auto-routing `cmd_do` now applies when
`intent.low_parallelism_note` fires *and* the fleet has no real writer at
the component's own floor: a single-component step chained off a sibling
step's output gets its repair budget capped instead of grinding the full
attempt count, and does not pay for a full-plan restart against the same
underpowered fleet.

Deliberately conditioned on "no real writer available," not merely on the
step being single-file -- an earlier version of this skipped the restart
for every single-file chain link on the theory that "there is no CONTRACT
to have gotten wrong with one file," which is false: a single file's
planner-generated CONTRACT can still miss a REQUIRED endpoint (a real
planning defect), and a *capable* writer stuck on a bad CONTRACT deserves
the ordinary restart's fresh planning roll, same as any multi-file plan.

Live cost this exists to stop paying twice, in the case it actually
applies to (no real writer in the fleet): this shape burned 6 attempts
(8m15s) against a borrowed model, then a full plan restart, then 5m20s
more against the same borrowed model to finally "pass."

This mirrors `cmd_do`'s own decision expressions directly (the same style
`scratch_floor4_fallback.py` already uses for the borrow-and-remove logic),
rather than driving the whole `do` command, which needs a live mock server
and real generation to reach this branch.
"""
import json
import os
import tempfile

import intent
import swarm
import team

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


def component(path, floor=4):
    return intent.Component(name=path, path=path, floor=floor, deps=[], spec="")


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

serial_request = ("write main.py exposing create a task in the project "
                  "whose id is read from ../01_create_project/output.json")
wide_request = "write client.py and main.py exposing create a project"
restrict = None

# --------------------------------------------------------------------------
print("with g26b on: a floor-4 component chained off a sibling gets no cap")
cfg.toggle_26b(True)
serial_components = [component("main.py")]
serial_link = intent.low_parallelism_note(serial_request, serial_components)
has_real_floor4 = bool(team.pick_solvers(cfg, state, 4, count=1, family=restrict))
should_cap = bool(serial_link and serial_components[0].floor >= 4
                  and not has_real_floor4)
check("the note fires for the chained single-component request",
      serial_link == "main.py")
check("g26b provides a real floor-4 writer", has_real_floor4)
check("...so no cap applies while a real writer exists", not should_cap)

# --------------------------------------------------------------------------
print("\nwith g26b off: the same request gets its repair budget capped")
cfg.toggle_26b(False)
# g26b used to be the only capability-4 node, so toggling it off alone was
# enough to reach "no real writer in the fleet." `gemma4:e4b` (mac) and
# `gemma4-e4b-fixed:latest` (e4b-linux) are capability 4 too now (see
# config.json's 2026-09-20 note), so those two are disabled here as well --
# the scenario this test guards (no real floor-4 writer available) still
# exists, it just needs a wider toggle to reach with today's fleet.
for n in cfg.nodes:
    if n.name in ("mac", "e4b-linux"):
        n.enabled = False
has_real_floor4 = bool(team.pick_solvers(cfg, state, 4, count=1, family=restrict))
should_cap = bool(serial_link and serial_components[0].floor >= 4
                  and not has_real_floor4)
effective_repair = min(6, 2) if should_cap else 6
check("no real floor-4 writer is available with g26b off", not has_real_floor4)
check("the cap now applies", should_cap)
check("effective repair drops from the requested 6 to 2",
      effective_repair == 2)

# --------------------------------------------------------------------------
print("\nsabotage: a wide (multi-component) plan is never capped or skipped, "
      "even with g26b off -- there is real parallelism to gain there")
wide_components = [component("client.py"), component("main.py")]
wide_link = intent.low_parallelism_note(wide_request, wide_components)
check("no note for a multi-component plan", wide_link is None)
wide_should_cap = bool(wide_link and wide_components[0].floor >= 4
                       and not has_real_floor4)
check("...so the wide plan is never capped even with g26b off",
      not wide_should_cap)

# --------------------------------------------------------------------------
print("\nsabotage: a single-component plan with NO sibling reference is not "
      "capped either -- capping every lone file regardless of shape would "
      "punish a genuinely self-contained one-file request")
standalone_components = [component("main.py")]
standalone_link = intent.low_parallelism_note(wide_request, standalone_components)
check("no note for a self-contained single-component request",
      standalone_link is None)
standalone_should_cap = bool(standalone_link and standalone_components[0].floor >= 4
                             and not has_real_floor4)
check("...so it is never capped", not standalone_should_cap)

cfg.toggle_26b(True)
state.close()

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
