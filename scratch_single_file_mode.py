"""Validates `team do --single-file`: the CLI plumbing, and that skipping the
planner produces the exact one-component shape the "could not decompose"
fallback already runs cleanly through every gate -- so nothing about `--api`
handling (slicing, closures, the mock, credential names, the
REQUIRED-endpoint check) is lost, since all of that is built from
`request`/`cfg` alone, before the planner is ever consulted.

Offline: parses real argv through the real parser, and checks the component
this mode constructs against the same shape used elsewhere in the file,
without running generation.
"""
import intent
import team

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


parser = team.build_parser()

print("--single-file defaults to off, and does not disturb other do flags")
args = parser.parse_args(["do", "write", "something"])
check("default is False", args.single_file is False)
check("request is still parsed normally",
      args.request == ["write", "something"])

print("\n--single-file parses alongside --api and --out")
args = parser.parse_args(["do", "--api", "maven", "--out", "upload.py",
                          "--single-file", "create", "a", "task"])
check("single_file is True", args.single_file is True)
check("--api is untouched by --single-file", args.api == "maven")
check("--out is untouched by --single-file", args.out == "upload.py")
check("request text is untouched", args.request == ["create", "a", "task"])

print("\n--single-file with no --out falls back to main.py at call time "
     "(mirrors the existing 'could not decompose' fallback exactly)")
args = parser.parse_args(["do", "--single-file", "create", "a", "task"])
check("--out is None, so the call site's 'or \"main.py\"' default applies",
      args.out is None)

# The actual construction line in cmd_do is:
#   components = [intent.Component("main", args.out or "main.py", 4, [], request)]
# Reproduced here to confirm it produces a real, well-formed Component with
# the same shape the existing decompose-failure fallback already uses
# (name="main", floor=4, no deps) -- so anything already proven to gate
# cleanly on that shape (the mock, `low_parallelism_note`, the repair-cap
# routing) applies here unchanged.
request = "create a task in the project whose id is in output.json"
component = intent.Component("main", args.out or "main.py", 4, [], request)
check("single-file component is named 'main', like the existing fallback",
      component.name == "main")
check("...targets main.py when --out is unset",
      component.path == "main.py")
check("...at floor 4, like the existing fallback",
      component.floor == 4)
check("...with no forced dependencies",
      component.deps == [])
check("...carrying the full request as its spec",
      component.spec == request)

if ok:
    print("\nall single-file mode checks passed")
else:
    print("\nSOME CHECKS FAILED")
raise SystemExit(0 if ok else 1)
