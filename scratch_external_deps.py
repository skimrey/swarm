"""A new file depending on an existing, untouched file gets its real source.

Live: a plan named `client.py` as a dependency of a new entry point while
only rewriting `client.py` itself in the same plan; that case worked because
both were components. The general case did not: `existing_files_block`
tells the planner to "list the existing files it needs as dependencies" for
anything it is not rewriting, but `intent.parse_components` only kept a
dependency token that matched another component IN THE SAME RESPONSE, so
following that instruction was self-defeating -- the token named a file, not
a component, and was silently dropped. The writer then had nothing but the
file's bare name from `session.existing_files_block`, which only the
planner ever sees.

Two things have to both be true for this to work end to end:
  1. `intent.parse_components` must keep the dependency (a `root` check).
  2. `team.external_dependencies` must make the scheduler treat it as
     already satisfied, or a component waiting on a name that will never
     appear in `done` waits forever.
"""
import os
import tempfile

import intent
import team


def a_project(files):
    root = tempfile.mkdtemp()
    for name, content in files.items():
        with open(os.path.join(root, name), "w") as fh:
            fh.write(content)
    return root


PLAN = ("CONTRACT: client.py owns APIClient with import_units(task_id, "
        "units) -> dict\n\n"
        "COMPONENT: upload\nFILE: upload_unit.py\nFLOOR: 4\n"
        "DEPS: client.py\nSPEC: call client.import_units\n")


def test_a_dep_naming_an_existing_file_is_kept_when_root_is_given():
    root = a_project({"client.py": "class APIClient:\n    pass\n"})
    comps = intent.parse_components(PLAN, root)
    assert len(comps) == 1, comps
    assert comps[0].deps == ["client.py"], comps[0].deps
    print("  a dependency naming an existing file survives parsing")


def test_the_same_plan_with_no_root_drops_it_as_before():
    comps = intent.parse_components(PLAN)
    assert comps[0].deps == [], comps[0].deps
    print("  no root given: unchanged, matches every caller before this")


def test_a_dep_naming_nothing_real_is_still_dropped():
    root = a_project({"unrelated.py": "x = 1\n"})
    comps = intent.parse_components(PLAN, root)
    assert comps[0].deps == [], comps[0].deps
    print("  a dependency naming neither a component nor a real file is still dropped")


def test_a_cycle_through_an_external_name_cannot_happen():
    """`_acyclic` must not crash when a dep has no Component behind it --
    there is nothing to recurse into, so nothing to find a cycle through."""
    root = a_project({"client.py": "x = 1\n"})
    comps = intent.parse_components(PLAN, root)
    assert comps[0].deps == ["client.py"]
    print("  parsing does not crash on a dependency with no Component")


def test_external_dependencies_reads_the_real_file():
    root = a_project({"client.py": "class APIClient:\n    def f(self): pass\n"})
    comps = intent.parse_components(PLAN, root)
    stubs = team.external_dependencies(comps, [], root)
    assert len(stubs) == 1, stubs
    assert stubs[0].name == "client.py"
    assert stubs[0].ok is True
    assert "class APIClient" in stubs[0].code
    print("  a stub is built from the file's real, current content")


def test_external_dependencies_skips_names_already_covered():
    """A name that is a component (about to be written) or already carried
    (from a retry) is not re-read from disk -- that would race the file
    currently being replaced."""
    root = a_project({"client.py": "stale content"})
    comps = [intent.Component("upload", "upload_unit.py", 4, ["client.py"], "x")]
    carried = [intent.Component("client.py", "client.py", 2, [], "")]
    carried[0].ok = True
    stubs = team.external_dependencies(comps, carried, root)
    assert stubs == [], stubs
    print("  a name already covered by components or carried is not duplicated")


def test_external_dependencies_ignores_a_name_with_no_file():
    comps = [intent.Component("upload", "upload_unit.py", 4, ["ghost.py"], "x")]
    stubs = team.external_dependencies(comps, [], tempfile.mkdtemp())
    assert stubs == [], stubs
    print("  a dependency naming nothing on disk produces no stub")


def test_the_stub_feeds_dependency_context_like_any_other_component():
    """The point of all of this: the writer of the new file ends up seeing
    the real source, exactly the way it would for a sibling written in the
    same run."""
    root = a_project({"client.py": "class APIClient:\n    def import_units(self):\n        pass\n"})
    comps = intent.parse_components(PLAN, root)
    stubs = team.external_dependencies(comps, [], root)
    done = {s.name: s for s in stubs}
    ctx = intent.dependency_context(comps[0], done)
    assert "class APIClient" in ctx, ctx
    assert "import_units" in ctx, ctx
    assert "client.py" in ctx, ctx
    print("  dependency_context resolves the stub into the real source")


def test_a_component_depending_on_an_external_file_is_schedulable():
    """`claim()`'s readiness check is `all(d in done for d in c.deps)`.
    Seeding `done` with the stub is what makes that true on the very
    first check, rather than never."""
    root = a_project({"client.py": "x = 1\n"})
    comps = intent.parse_components(PLAN, root)
    stubs = team.external_dependencies(comps, [], root)
    done = {s.name: s for s in stubs}
    assert all(d in done for d in comps[0].deps)
    print("  a dependency on an external file is satisfied from the start")


def main():
    test_a_dep_naming_an_existing_file_is_kept_when_root_is_given()
    test_the_same_plan_with_no_root_drops_it_as_before()
    test_a_dep_naming_nothing_real_is_still_dropped()
    test_a_cycle_through_an_external_name_cannot_happen()
    test_external_dependencies_reads_the_real_file()
    test_external_dependencies_skips_names_already_covered()
    test_external_dependencies_ignores_a_name_with_no_file()
    test_the_stub_feeds_dependency_context_like_any_other_component()
    test_a_component_depending_on_an_external_file_is_schedulable()
    print("OK")


if __name__ == "__main__":
    main()
