"""Validates intent.backfill_entry_point_deps against the exact live
failure: a 4-component Maven plan (api_client.py, utils.py, survey.html,
main.py) parsed with every component's `deps` at [], even though the
CONTRACT names api_client.py and utils.py as owning real symbols main.py's
own SPEC needs. That left `dependency_context`, `gate_uses` and
`gate_interfaces` with nothing to check, and main.py shipped a
self-contained fake client that fabricated IDs and made no real API call.
"""
import intent

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


CONTRACT = (
    "api_client.py owns Client(api_base_url: str, api_key: str) with "
    "create_applet(slug: str, name: str) -> str; "
    "api_client.py owns get_ui_url(api_base_url: str, project_id: str, "
    "ui_url: str) -> str; "
    "utils.py owns get_stamp() -> str")


def make(name, path, deps, contract=CONTRACT):
    c = intent.Component(name, path, 2, deps, "spec for " + name)
    c.contract = contract
    return c


# 1. The exact live shape: four components, every deps list empty, main.py
#    literally named "main.py" (trusted outright by plan_entry_point).
survey = make("survey.html", "survey.html", [])
api_client = make("api_client.py", "api_client.py", [])
utils = make("utils.py", "utils.py", [])
main = make("main.py", "main.py", [])
components = [survey, api_client, utils, main]

fixed = intent.backfill_entry_point_deps(components)
check("fires on the entry point", fixed == "main.py")
check("wires in the file the CONTRACT says owns Client",
      "api_client.py" in main.deps)
check("wires in the file the CONTRACT says owns get_stamp",
      "utils.py" in main.deps)
check("does not invent a dependency on itself", "main.py" not in main.deps)
check("does not touch a sibling with no owner clause pointing at it, but "
      "harmlessly may or may not include survey.html -- either is fine "
      "since only the two real owners matter here",
      set(main.deps) <= {"survey.html", "api_client.py", "utils.py"})

# 2. A component that already declared a real dependency made a real
#    decision -- must be left alone, not second-guessed.
survey2 = make("survey.html", "survey.html", [])
api_client2 = make("api_client.py", "api_client.py", [])
main2 = make("main.py", "main.py", ["api_client.py"])
components2 = [survey2, api_client2, main2]
fixed2 = intent.backfill_entry_point_deps(components2)
check("a component with a real (partial) deps list is never touched",
      fixed2 is None and main2.deps == ["api_client.py"])

# 3. A genuinely standalone entry point (one file, no CONTRACT) has no
#    owner clauses to backfill from -- must never fire.
lone = make("main.py", "main.py", [], contract="")
fixed3 = intent.backfill_entry_point_deps([lone])
check("a single-file plan with no CONTRACT is left alone",
      fixed3 is None and lone.deps == [])

# 4. No entry point identifiable at all (ambiguous plan) -- must not crash
#    or guess.
a = make("a.py", "a.py", [])
b = make("b.py", "b.py", [])
fixed4 = intent.backfill_entry_point_deps([a, b])
check("an unidentifiable entry point is left alone, not guessed at",
      fixed4 is None and a.deps == [] and b.deps == [])

# 5. Sabotage: prove this is load-bearing, not a vacuous no-op -- confirm
#    the exact live symptom (gate_uses/gate_interfaces silently skipped)
#    would have followed from the unfixed plan, and does not follow once
#    fixed.
import team as _team
built = {c.name: c for c in (survey, api_client, utils)}
for c in built.values():
    c.ok = True
before_main = make("main.py", "main.py", [], contract=CONTRACT)  # unfixed
before = _team.dep_modules(before_main, built, "")
after_main = make("main.py", "main.py", [], contract=CONTRACT)
intent.backfill_entry_point_deps([survey, api_client, utils, after_main])
after = _team.dep_modules(after_main, built, ".")
check("before the fix, dep_modules (and so gate_uses) sees nothing",
      before == {})
check("after the fix, dep_modules actually carries api_client/utils",
      "api_client" in after and "utils" in after)

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
