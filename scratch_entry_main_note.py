#!/usr/bin/env python3
"""Validates intent.plan_entry_point + ownership_note's structural `main`
prohibition against the live failure: a CONTRACT with no `main()` clause
left `api_client.py` free to define its own duplicate `main()` because
`ownership_note` had never heard of `main` at all.
"""
import intent

# Mirrors the live plan: main.py depends on api_client.py and survey.html;
# nothing depends on main.py.
LIVE_COMPONENTS = [
    intent.Component("api_client.py", "api_client.py", 4, [], "client spec"),
    intent.Component("survey.html", "survey.html", 2, [], "survey spec"),
    intent.Component("main.py", "main.py", 2,
                     ["api_client.py", "survey.html"], "entry spec"),
]

# The live CONTRACT text, which never mentioned main() at all this round.
LIVE_CONTRACT = (
    "api_client.py owns Client(api_base_url: str, api_key: str) with "
    "create_applet(slug: str, name: str) -> str; "
    "import_units(task_id: str, units: list[dict]) -> None; "
    "api_client.py owns get_ui_url(api_base_url: str, project_id: str) -> str;"
)


def test_plan_entry_point_finds_main_py_directly():
    assert intent.plan_entry_point(LIVE_COMPONENTS) == "main.py"


def test_plan_entry_point_finds_the_sink_when_unnamed():
    renamed = [
        intent.Component("api_client.py", "api_client.py", 4, [], "s"),
        intent.Component("survey.html", "survey.html", 2, [], "s"),
        intent.Component("upload_unit.py", "upload_unit.py", 2,
                         ["api_client.py", "survey.html"], "s"),
    ]
    assert intent.plan_entry_point(renamed) == "upload_unit.py"


def test_plan_entry_point_gives_up_on_ambiguity():
    two_sinks = [
        intent.Component("a.py", "a.py", 2, [], "s"),
        intent.Component("b.py", "b.py", 2, ["a.py"], "s"),
        intent.Component("c.py", "c.py", 2, ["a.py"], "s"),
    ]
    assert intent.plan_entry_point(two_sinks) is None
    assert intent.plan_entry_point([]) is None
    # A plan with exactly one component *in total* is never actually
    # ambiguous, even though that lone component has no dependencies --
    # there is nothing else in the plan for it to be confused with. This
    # used to return None here too (the same "lone file, no deps" shape
    # the multi-component exclusion above is really about), and that was
    # the live bug in `resolve_run_target`/`team do --retry --run`: every
    # single-component, `--single-file`/`--api` chain step retried in its
    # own directory is exactly this shape, so `--retry --run` could never
    # find its own entry point and exited without ever re-running the
    # file for real. See `scratch_run_capture.py` for the case this
    # function must still refuse (more than one plausible file).
    lone = [intent.Component("only.py", "only.py", 2, [], "s")]
    assert intent.plan_entry_point(lone) == "only.py"


def test_api_client_now_gets_told_not_to_define_main():
    entry = intent.plan_entry_point(LIVE_COMPONENTS)
    note = intent.ownership_note(LIVE_CONTRACT, "api_client.py", entry)
    assert "Do NOT define main -- main.py does" in note, note


def test_main_py_itself_is_not_told_to_avoid_its_own_name():
    entry = intent.plan_entry_point(LIVE_COMPONENTS)
    note = intent.ownership_note(LIVE_CONTRACT, "main.py", entry)
    assert "Do NOT define main" not in note


def test_explicit_contract_clause_still_wins_over_the_structural_guess():
    # If the CONTRACT *did* name an owner for main (even a surprising one),
    # that explicit statement must not be silently overridden.
    explicit = LIVE_CONTRACT + " weird.py owns main()"
    entry = intent.plan_entry_point(LIVE_COMPONENTS)
    note = intent.ownership_note(explicit, "api_client.py", entry)
    assert "Do NOT define main -- weird.py does" in note
    assert "main.py does" not in note


def test_no_entry_point_behaves_exactly_as_before():
    # entry_point=None (the old call shape) must reproduce old behaviour
    # exactly: no synthetic main-ownership is invented.
    note_old = intent.ownership_note(LIVE_CONTRACT, "api_client.py")
    note_none = intent.ownership_note(LIVE_CONTRACT, "api_client.py", None)
    assert note_old == note_none
    assert "main" not in note_old.lower()


def test_sabotage_without_the_fix_reproduces_the_live_bug():
    # The exact call shape used before this fix: no entry_point argument.
    note = intent.ownership_note(LIVE_CONTRACT, "api_client.py")
    assert "main" not in note.lower(), (
        "without the fix, api_client.py receives no prohibition on `main` "
        "at all when the CONTRACT text omits it -- confirming the live bug "
        "was reachable")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print("%d/%d passed" % (len(tests), len(tests)))
