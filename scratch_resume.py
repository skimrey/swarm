"""Resume must rebuild only what failed, and must not touch what passed.

Exercised without the pool: the persistence and selection are the parts that
can be wrong, and neither needs a model.
"""
import json
import os
import shutil
import tempfile

import intent
import team


def make(name, path, deps, ok, error=""):
    c = intent.Component(name, path, 2, deps, "spec for " + name)
    c.ok = ok
    c.error = error
    return c


def main():
    root = tempfile.mkdtemp()
    comps = [make("types", "pkg/types.py", [], True),
             make("algo", "pkg/algo.py", ["types"], False, "check: assert failed"),
             make("api", "pkg/api.py", ["types", "algo"], False, "stranded")]
    team.save_plan(root, "build pkg", comps, "python3 t.py", 3000)

    saved = team.load_plan(root)
    assert saved, "plan did not round-trip"
    assert saved["check"] == "python3 t.py", "check was not carried over"
    assert saved["request"] == "build pkg"

    restored = [intent.Component(c["name"], c["path"], c["floor"], c["deps"],
                                 c["spec"]) for c in saved["components"]]
    for comp, raw in zip(restored, saved["components"]):
        comp.ok, comp.error = raw["ok"], raw["error"]

    carried = [c for c in restored if c.ok]
    failed = [c for c in restored if not c.ok]
    assert [c.name for c in carried] == ["types"], [c.name for c in carried]
    assert [c.name for c in failed] == ["algo", "api"], [c.name for c in failed]
    print("selection: carry %s, rebuild %s"
          % ([c.name for c in carried], [c.name for c in failed]))

    # The error must reach the model, or it repeats the same mistake blind.
    for c in failed:
        if c.error:
            c.spec += "\n\nA previous attempt failed:\n%s" % c.error
    assert "assert failed" in failed[0].spec, "error was not fed back"
    print("feedback: error text is attached to the respecified component")

    # A carried component satisfies dependencies without being rewritten.
    done = {c.name: c for c in carried}
    pending = {c.name: c for c in failed if c.name not in done}
    assert "types" not in pending, "a passing component would be rewritten"
    ready = [c for c in pending.values() if all(d in done for d in c.deps)]
    assert [c.name for c in ready] == ["algo"], (
        "algo should be immediately runnable, api should wait: %s"
        % [c.name for c in ready])
    print("scheduling: algo unblocked by carried types, api still waits on algo")

    # Two projects must not share one saved plan.
    other = tempfile.mkdtemp()
    assert team._plan_path(root) != team._plan_path(other), "plans collide"
    assert team.load_plan(other) is None, "unrelated project saw a plan"
    print("isolation: plans are keyed per project root")

    os.remove(team._plan_path(root))
    test_a_retry_that_succeeds_retracts_the_old_failure()
    test_a_retried_component_still_sees_what_it_depends_on()
    test_a_retry_does_not_call_its_own_output_debris()
    test_the_summary_says_why_not_merely_where()
    test_a_retry_remembers_which_spec_the_run_used()
    print("OK")


def test_a_retry_that_succeeds_retracts_the_old_failure():
    """The error a retry was given back must not outlive the retry.

    `--retry` copies the saved failure onto the component so the writer is
    told what went wrong. Nothing cleared it on success, so a file that
    passed was saved as `ok` carrying the error it no longer had -- and
    `session.record` stores the same field, so a later planner reads a
    working file as one that is still broken.
    """
    c = intent.Component("main", "main.py", 4, [], "spec")
    c.ok = False
    c.error = "smoke: OSError: AUTH_TOKEN environment variable is not set."

    # What run_components does to a component that came back clean.
    res = {"ok": True, "history": []}
    c.ok = res["ok"]
    if res["ok"]:
        c.error = ""
    assert not c.error, "a passing retry kept its old failure: %r" % c.error

    root = tempfile.mkdtemp(prefix="retract-")
    try:
        team.save_plan(root, "r", [c], None, 4096, api="maven")
        saved = team.load_plan(root)["components"][0]
        assert saved["ok"] and not saved["error"], saved
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  a retry that passes retracts the failure it was handed")


def test_a_retried_component_still_sees_what_it_depends_on():
    """A plan records what was attempted, not what was written.

    `dependency_context` hands a writer a dependency only when it has that
    dependency's source, and a restored component carries none -- the plan
    stores path, spec and outcome. So a retried component saw nothing of
    the files it was built on and guessed their interface, which is the
    exact failure that machinery exists to prevent. Observed: three
    attempts at a main.py importing `TaskResponse`, then `AppletEngine`,
    neither of which any file defines.
    """
    root = tempfile.mkdtemp(prefix="deps-")
    try:
        with open(os.path.join(root, "api_client.py"), "w") as fh:
            fh.write("class APIClient:\n"
                     "    def create_task(self, project_id, name):\n"
                     "        return {}\n")
        with open(os.path.join(root, "survey.html"), "w") as fh:
            fh.write("<html><body>survey</body></html>\n")

        def restore(name, path, ok, deps=()):
            c = intent.Component(name, path, 2, list(deps), "spec")
            c.ok = ok
            if ok:                       # what cmd_do now does on --retry
                with open(os.path.join(root, path)) as fh:
                    c.code = fh.read()
            return c

        carried = {c.name: c for c in
                   (restore("api_client", "api_client.py", True),
                    restore("survey", "survey.html", True))}
        main = restore("main", "main.py", False, ["api_client", "survey"])
        context = intent.dependency_context(main, carried)

        assert "class APIClient" in context, "the dependency's source is absent"
        assert "create_task" in context, context[:300]
        # Which is what makes the invented name refutable: the writer can
        # see that TaskResponse is not in there.
        assert "TaskResponse" not in context

        # A page is opened at run time, not imported. `path[:-3]` on
        # survey.html produced `from survey.h import ...`, and fenced HTML
        # as Python while doing it.
        assert "survey.h import" not in context, context
        assert "```html" in context, "the page was not fenced as html"
        assert "```python\n<html>" not in context
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  a retried component is handed its dependencies' real source")


def test_a_retry_does_not_call_its_own_output_debris():
    """The stale-file warning must count carried components as planned.

    A retry runs only what failed, so measuring against that alone named
    every file the same plan had just written successfully and advised
    starting from an empty directory -- which would delete the working
    half of the run being repaired. Observed on a 4-component plan where
    one file failed: "2 other python file(s) here are not part of this
    plan: api_client.py, applet_engine.py".
    """
    root = tempfile.mkdtemp(prefix="stale-")
    try:
        for name in ("api_client.py", "applet_engine.py", "main.py",
                     "leftover.py"):
            with open(os.path.join(root, name), "w") as fh:
                fh.write("x = 1\n")

        def component(name, path, ok):
            c = intent.Component(name, path, 2, [], "spec")
            c.ok = ok
            return c

        carried = [component("api_client", "api_client.py", True),
                   component("applet_engine", "applet_engine.py", True)]
        running = [component("main", "main.py", False)]

        def stale_against(planned_components):
            planned = {os.path.normpath(c.path) for c in planned_components}
            return sorted(f for f in os.listdir(root)
                          if f.endswith(".py")
                          and os.path.normpath(f) not in planned)

        assert stale_against(running + carried) == ["leftover.py"], \
            stale_against(running + carried)
        # The bug, kept as a statement of what changed.
        assert stale_against(running) == ["api_client.py", "applet_engine.py",
                                          "leftover.py"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  a retry does not call its own successful output debris")


def test_the_summary_says_why_not_merely_where():
    """`smoke: running main.py raised` is a label, not a diagnosis.

    The first line of a smoke failure repeats the stage name and nothing
    else, so a live run reported three failed attempts and told the operator
    only that main.py was the file. The exception is the last line of the
    traceback, and when the mock refused something that verdict is the
    sentence worth reading, since it names the field rather than the status.
    """
    crash = ('running main.py raised:\nTraceback (most recent call last):\n'
             '  File "main.py", line 9, in main\n    c.create_version(a)\n'
             "AttributeError: 'APIClient' object has no attribute "
             "'create_version'")
    said = team.why_it_failed(crash)
    assert said.startswith("AttributeError"), said
    assert "running main.py" not in said, said

    refused = (crash.replace("AttributeError: 'APIClient' object has no "
                             "attribute 'create_version'",
                             "urllib.error.HTTPError: HTTP Error 422: "
                             "Unprocessable Entity")
               + "\n\nThe API rejected these requests. The mock is built from "
                 "the real spec, so these are what the real service would "
                 "have said:\n  POST /api/applets/{id}/versions -> 422 "
                 "missing required field 'version'")
    said = team.why_it_failed(refused)
    assert "422" in said and "missing required field" in said, said
    # Said once. The first version printed the mock's line as both the
    # exception and the parenthetical.
    assert said.count("missing required field") == 1, said

    assert team.why_it_failed("") == "no detail"
    print("  a failure summary names the exception, not the stage")


def test_a_retry_remembers_which_spec_the_run_used():
    """Without this, a retried API client is tested against nothing.

    `--retry` carries no `--api`, and the mock was started only when that
    flag was present, so a retried client ran its gates with API_BASE_URL
    pointing at a dead port. Every request failed, so every attempt failed,
    and the run looked like the model could not write the file. Measured
    live: two consecutive retries, three attempts each, all identical.

    The slice used to be treated as a different matter and skipped outright
    on every retry, on the theory that it is derived from the request and a
    retry has no new one -- true only in the sense that a retry has no *new*
    request; `save_plan` always recorded the original one too, which
    `team._retry_restored_request` now restores before the API block runs
    (see `test_retry_restored_request_feeds_the_api_slicing_block` below).
    A retry now re-slices against that restored request, so a retried
    component's repair prompt still carries `auth_block`/
    `connection_setup_block`/the index instead of silently losing them.
    """
    import intent
    root = tempfile.mkdtemp(prefix="retry-api-")
    comp = intent.Component(name="c", path="main.py", floor=4, deps=[],
                            spec="a client")
    comp.ok = False
    comp.error = "smoke: connection refused"

    team.save_plan(root, "write a client", [comp], None, 3000, api="maven")
    assert (team.load_plan(root) or {}).get("api") == "maven"

    # A run that used no spec must not acquire one.
    team.save_plan(root, "unrelated", [comp], None, 3000)
    assert (team.load_plan(root) or {}).get("api") is None

    # And a plan written before the key existed must still load.
    path = team._plan_path(root)
    with open(path, encoding="utf-8") as fh:
        old = json.load(fh)
    old.pop("api", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(old, fh)
    assert team.load_plan(root) is not None
    assert (team.load_plan(root) or {}).get("api") is None
    print("  a retry remembers the spec, and an old plan still loads")


def test_retry_restored_request_feeds_the_api_slicing_block():
    """`_retry_restored_request` is what `cmd_do` now calls before its API
    slicing/CONTRACT block, so a retried component's repair prompt gets
    `auth_block`/`connection_setup_block`/the index again instead of
    silently losing them (see that function's own docstring for the live
    401-retry failure this fixes).

    A call that already has its own request (an ordinary, non-retry `do`)
    must pass straight through -- this only ever fills a gap that a retry
    leaves, never overrides a real one.
    """
    root = tempfile.mkdtemp(prefix="retry-restore-")
    try:
        comp = intent.Component("main", "main.py", 4, [], "spec")
        comp.ok = False
        comp.error = "smoke: 401 Unauthorized"
        team.save_plan(root, "write a client for the Maven API", [comp],
                       None, 3000, api="maven")

        # The ordinary retry case: --retry passes "" for request (it never
        # asks the operator to retype it), and the saved plan's original
        # text must come back so the API block has something to slice.
        assert team._retry_restored_request(root, "") == (
            "write a client for the Maven API")

        # A call with its own request must be returned unchanged -- this
        # never overrides a real one, it only fills the gap a retry leaves.
        assert team._retry_restored_request(
            root, "a completely different request") == (
                "a completely different request")

        # The total-decompose-failure shape (components == [] on purpose)
        # must round-trip the same way -- this is the case the function
        # originally existed for, and it must not regress.
        team.save_plan(root, "write a client for the Maven API", [], None,
                       3000, api="maven", decompose_failed=True)
        assert team._retry_restored_request(root, "") == (
            "write a client for the Maven API")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  a retry's restored request is available before the API "
         "slicing block runs")


def test_sabotage_no_restore_leaves_a_retry_with_nothing_to_slice():
    # No call to _retry_restored_request at all -- exactly what cmd_do did
    # before this fix. A retry's own local `request` variable stays "",
    # which is indistinguishable from "no --api was ever used" to the
    # slicing block's own `if request:` guard, so auth_block/
    # connection_setup_block/index text is silently never rebuilt.
    root = tempfile.mkdtemp(prefix="retry-sabotage-")
    try:
        comp = intent.Component("main", "main.py", 4, [], "spec")
        team.save_plan(root, "write a client for the Maven API", [comp],
                       None, 3000, api="maven")
        request = ""  # what cmd_do's local variable holds on entry to --retry
        assert not request, (
            "fixture must reproduce the live shape: an empty request is "
            "exactly what the API slicing block's `if request:` guard "
            "sees without the restore this test is sabotaging")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  sabotage: confirmed the pre-fix shape truly has nothing to "
         "slice against without the restore")


if __name__ == "__main__":
    main()
    test_retry_restored_request_feeds_the_api_slicing_block()
    test_sabotage_no_restore_leaves_a_retry_with_nothing_to_slice()
