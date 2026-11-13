"""Validates the `intent.plan_entry_point` fix for the exact live failure:
a single-component, `--single-file`/`--api` chain step, retried in its own
directory, could never resolve its own entry point.

Live shape: `01_create_project`'s saved plan had exactly one component,
name `"main_script"`, path `"main.py"`, no dependencies (nothing else in
that directory's own plan). `team do --retry --run` inside that directory
called `resolve_run_target(None, restored)` -> `intent.plan_entry_point`,
which returned `None` for two independent reasons that both had to be
wrong at once for this to surface: the "is this literally main.py"
check tested `"main.py" in by_name`, where `by_name` is keyed by each
component's short *name* ("main_script"/"main"), never its path -- so
the check could never fire for any real component. And the fallback sink
check required `sinks[0].deps` to be non-empty, which a lone,
zero-dependency, *total* plan trivially fails, even though there is
nothing else in the plan it could possibly be confused with.

The consequence: `--run needs --file PATH -- this plan has no single
obvious entry point` was printed, and `cmd_do` returned 1 without ever
re-running the file for real -- so `orchestrate.sh`'s "it depends on
01_create_project, so repairing those next" step did nothing whatsoever,
and the real, still-broken `output.json` on disk was never touched.
"""
import sys

import intent


def check(cond, msg):
    if not cond:
        print("FAIL: " + msg)
        sys.exit(1)
    print("ok: " + msg)


def main():
    # The exact live shape: one component, custom name (not "main"),
    # path "main.py", no dependencies at all.
    live_shape = [intent.Component("main_script", "main.py", 4, [], "spec")]
    check(intent.plan_entry_point(live_shape) == "main.py",
          "a lone component named something other than 'main.py' still "
          "resolves by path")

    # Same shape but with the `--single-file` convention's actual name,
    # "main" -- also never equal to the literal string "main.py", so the
    # old by-name check would have missed this one too.
    single_file_shape = [intent.Component("main", "main.py", 4, [], "spec")]
    check(intent.plan_entry_point(single_file_shape) == "main.py",
          "the --single-file naming convention ('main', not 'main.py') "
          "also resolves correctly")

    # A lone component with some other path entirely -- the general case
    # this project relies on for a non-main-named single-file component
    # (e.g. a descriptive filename for a bare library request).
    lone_other_name = [intent.Component("upload_units", "upload_units.py",
                                        2, [], "spec")]
    check(intent.plan_entry_point(lone_other_name) == "upload_units.py",
          "a lone component under any other name/path is still "
          "unambiguously the entry point")

    # Still correctly refuses to guess when there genuinely is more than
    # one plausible file and no dependency graph disambiguates it.
    two_sinks = [intent.Component("a", "a.py", 2, [], "s"),
                intent.Component("b", "b.py", 2, [], "s")]
    check(intent.plan_entry_point(two_sinks) is None,
          "a genuinely ambiguous multi-component plan still returns None")

    check(intent.plan_entry_point([]) is None,
          "an empty plan still returns None")

    # Sabotage: reproduce the exact pre-fix logic and confirm it fails on
    # the live shape, proving the fixture actually exercises the bug.
    def old_broken_plan_entry_point(components):
        if not components:
            return None
        by_name = {c.name: c.path for c in components}
        if "main.py" in by_name:
            return by_name["main.py"]
        depended_on = {dep for c in components for dep in c.deps}
        sinks = [c for c in components if c.name not in depended_on]
        if len(sinks) == 1 and sinks[0].deps:
            return sinks[0].path
        return None

    check(old_broken_plan_entry_point(live_shape) is None,
          "sabotage: the pre-fix logic reproduces the live failure "
          "(returns None for the exact shape that broke --retry --run)")
    check(old_broken_plan_entry_point(single_file_shape) is None,
          "sabotage: the pre-fix logic also breaks on --single-file's "
          "own naming convention")

    print("\nall lone-entry-point checks passed")


if __name__ == "__main__":
    main()
