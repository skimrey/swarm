"""Does the how-to-run block name the thing to run, or everything runnable?

Every generated component carries a demo block by construction, so "has a
__main__" is true of all of them. A four-file package printed four
invocations and no indication which one did the job the request asked for.
"""
import intent
import team


def plan(*spec):
    out = []
    for name, path, deps, ok in spec:
        c = intent.Component(name, path, 2, list(deps), "spec")
        c.ok = ok
        out.append(c)
    return out


def test_only_the_sink_is_offered():
    built = plan(("api_client", "api_client.py", [], True),
                 ("survey", "survey.html", [], True),
                 ("applet_engine", "applet_engine.py", ["api_client"], True),
                 ("main", "main.py",
                  ["api_client", "survey", "applet_engine"], True))
    assert team.entry_points(built) == ["main.py"], team.entry_points(built)
    print("  a four-file package offers one command")


def test_two_independent_tools_give_two_answers():
    """Honest rather than tidy: a package with two sinks has two commands."""
    built = plan(("shared", "shared.py", [], True),
                 ("ingest", "ingest.py", ["shared"], True),
                 ("report", "report.py", ["shared"], True))
    assert team.entry_points(built) == ["ingest.py", "report.py"], \
        team.entry_points(built)
    print("  two independent tools are both offered")


def test_a_single_file_is_its_own_answer():
    built = plan(("only", "only.py", [], True))
    assert team.entry_points(built) == ["only.py"]
    print("  a one-file plan needs no graph")


def test_a_failed_sink_does_not_hide_what_did_get_built():
    """The sink is what failed, so there is no entry point to offer.

    Falling silent would be worse than naming the parts that exist, and a
    failed component has nothing to run anyway.
    """
    built = plan(("api_client", "api_client.py", [], True),
                 ("main", "main.py", ["api_client"], False))
    assert team.entry_points(built) == ["api_client.py"], \
        team.entry_points(built)
    print("  a failed entry point falls back to what was built")


def test_a_cycle_says_too_much_rather_than_nothing():
    built = plan(("a", "a.py", ["b"], True),
                 ("b", "b.py", ["a"], True))
    assert sorted(team.entry_points(built)) == ["a.py", "b.py"]
    print("  a graph with no sink still answers")


def main():
    test_only_the_sink_is_offered()
    test_two_independent_tools_give_two_answers()
    test_a_single_file_is_its_own_answer()
    test_a_failed_sink_does_not_hide_what_did_get_built()
    test_a_cycle_says_too_much_rather_than_nothing()
    print("OK")


if __name__ == "__main__":
    main()
