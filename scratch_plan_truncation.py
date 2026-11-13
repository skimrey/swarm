#!/usr/bin/env python3
"""A plan cut off mid-emission must be noticed, not silently obeyed.

This is the worst-behaved failure in the pipeline, because every visible
signal says the run went fine. The components emitted before the cut parse
perfectly, they generate, they pass their gates, and the summary reports
everything green -- against a fraction of the work that was asked for.

Measured live: a request for an applet script plus an editable HTML page
came back as one component, `api_client.py`, with a contract reading
`api_client.py owns APIClient(...); client.py owns main()`. No `client.py`
was ever planned and nothing said so. Ollama had reported the cut in
`done_reason`, and nobody read it -- `codegen` handles this for writers and
the planner did not.

Two independent guards, because they fail in different places. Growing the
budget on a reported cut is the fix; the contract check is the net for when
a plan is short for some other reason, and it needs no second call to
decide, both halves coming out of the one reply.

The pool is stubbed. Nothing here contacts a node.
"""

import sys

import intent
import team

FAILURES = []


def check(label, condition, detail=""):
    print("  %-4s %s%s" % ("ok" if condition else "FAIL", label,
                           "" if condition else "  <- " + str(detail)[:200]))
    if not condition:
        FAILURES.append(label)


# The reply exactly as the live run produced it, cut where it was cut.
TRUNCATED = (
    "CONTRACT: api_client.py owns APIClient(base_url: str, auth_token: str) "
    "with create_applet(slug: str, name: str) -> AppletResponse; "
    "create_project(name: str) -> ProjectResponse; client.py owns main()\n\n"
    "COMPONENT: api_client\nFILE: api_client.py\nFLOOR: 2\nDEPS: NONE\n"
    "SPEC: the platform client\n")

COMPLETE = TRUNCATED + (
    "\nCOMPONENT: client\nFILE: client.py\nFLOOR: 4\nDEPS: api_client\n"
    "SPEC: entry point that creates the applet and prints the link\n")


class FakeResult(dict):
    """What `swarm.chat_complete` hands back, with the cut flag in `raw`."""

    def __init__(self, text, cut):
        super().__init__(text=text, sample=None,
                         raw={"done_reason": "length" if cut else "stop"})


def growth_loop(replies, start=team.PLAN_TOKENS, ceiling=team.PLAN_TOKENS_MAX):
    """The planner's retry rule, isolated from the network.

    Mirrors `build_components`: double on a reported cut, stop at the
    ceiling, and never retry a reply that finished on its own.
    """
    budget = start
    asked = []
    for reply in replies:
        asked.append(budget)
        if reply["raw"].get("done_reason") != "length" or budget >= ceiling:
            return reply["text"], asked
        budget = min(budget * 2, ceiling)
    return replies[-1]["text"], asked


def main():
    print("the contract names a file nobody writes")
    comps = intent.parse_components(TRUNCATED)
    check("the truncated plan does parse -- that is the problem",
          [c.path for c in comps] == ["api_client.py"], comps)
    check("the orphan is named",
          intent.unbuilt_contract_files(TRUNCATED, comps) == ["client.py"],
          intent.unbuilt_contract_files(TRUNCATED, comps))

    # The other direction matters more: this warning prints on a normal run,
    # so a version that fires on a complete plan would cry wolf every time.
    done = intent.parse_components(COMPLETE)
    check("a complete plan reports nothing",
          intent.unbuilt_contract_files(COMPLETE, done) == [], done)
    check("CONTRACT: NONE reports nothing",
          intent.unbuilt_contract_files("CONTRACT: NONE\n\nCOMPONENT: a\n"
                                        "FILE: a.py\nFLOOR: 2\nDEPS: NONE\n"
                                        "SPEC: x\n", []) == [])
    check("a plan with no contract at all reports nothing",
          intent.unbuilt_contract_files("COMPONENT: a\nFILE: a.py\nFLOOR: 2\n"
                                        "DEPS: NONE\nSPEC: x\n", []) == [])

    html = ("CONTRACT: survey.html owns the markup; push.py owns main()\n\n"
            "COMPONENT: page\nFILE: survey.html\nFLOOR: 2\nDEPS: NONE\n"
            "SPEC: the page\n")
    check("a non-Python file is noticed as an orphan too",
          intent.unbuilt_contract_files(html, intent.parse_components(html))
          == ["push.py"])
    # Paths are compared by basename, or a contract saying `client.py` against
    # a component at `src/client.py` would be reported as missing.
    nested = ("CONTRACT: client.py owns main()\n\nCOMPONENT: c\n"
              "FILE: src/client.py\nFLOOR: 2\nDEPS: NONE\nSPEC: x\n")
    check("a directory in the path is not a mismatch",
          intent.unbuilt_contract_files(nested,
                                        intent.parse_components(nested)) == [])

    print("\nthe budget grows only on a reported cut")
    text, asked = growth_loop([FakeResult(TRUNCATED, True),
                               FakeResult(COMPLETE, False)])
    check("a cut reply is retried with more room",
          asked == [team.PLAN_TOKENS,
                    min(team.PLAN_TOKENS * 2, team.PLAN_TOKENS_MAX)], asked)
    check("the retry's answer is the one used",
          intent.unbuilt_contract_files(text,
                                        intent.parse_components(text)) == [])

    _, asked = growth_loop([FakeResult(COMPLETE, False),
                            FakeResult(COMPLETE, False)])
    check("a reply that finished on its own is not retried",
          asked == [team.PLAN_TOKENS], asked)

    # Doubling forever would turn one bad plan into four slow ones.
    _, asked = growth_loop([FakeResult(TRUNCATED, True)] * 6)
    check("the budget stops at the ceiling",
          asked[-1] == team.PLAN_TOKENS_MAX and len(asked) <= 4, asked)
    # The point of raising the start: a plan that needed a retry at 1600 now
    # fits the first attempt, and the retry is the rare path rather than the
    # one a long CONTRACT line reaches every time.
    check("the starting cap is roomy enough for a real plan",
          team.PLAN_TOKENS >= 4096, team.PLAN_TOKENS)

    print("\ntwo entry points cannot run at the same time")
    # The live plan's shape. api_client and the page ran in parallel; then
    # client.py and main.py ran one after the other with two boxes idle,
    # because the contract gave main() to client.py and the plan carried a
    # main.py depending on it anyway.
    LIVE = ("CONTRACT: api_client.py owns APIClient(base_url: str); "
            "client.py owns main()\n\n"
            "COMPONENT: api_client\nFILE: api_client.py\nFLOOR: 2\n"
            "DEPS: NONE\nSPEC: x\n\n"
            "COMPONENT: page\nFILE: applet_template.html\nFLOOR: 2\n"
            "DEPS: NONE\nSPEC: x\n\n"
            "COMPONENT: client\nFILE: client.py\nFLOOR: 4\n"
            "DEPS: api_client, page\nSPEC: x\n\n"
            "COMPONENT: main\nFILE: main.py\nFLOOR: 2\nDEPS: client\nSPEC: x\n")
    check("both entry points are named",
          intent.duplicate_entry_points(LIVE, intent.parse_components(LIVE))
          == ["client.py", "main.py"])

    # The three ways this must stay quiet. It prints on ordinary runs, so a
    # detector that fires on a correct plan is worse than no detector.
    same = LIVE.replace("client.py owns main()", "main.py owns main()")
    check("a contract naming the same file is not a duplicate",
          intent.duplicate_entry_points(same,
                                        intent.parse_components(same)) == [])
    plain = ("CONTRACT: shapes.py owns Shape(x, y) with draw(surface)\n\n"
             "COMPONENT: s\nFILE: shapes.py\nFLOOR: 2\nDEPS: NONE\nSPEC: x\n\n"
             "COMPONENT: m\nFILE: main.py\nFLOOR: 2\nDEPS: s\nSPEC: x\n")
    check("an ordinary plan with one main.py is quiet",
          intent.duplicate_entry_points(plain,
                                        intent.parse_components(plain)) == [])
    lone = ("CONTRACT: client.py owns main()\n\nCOMPONENT: c\n"
            "FILE: client.py\nFLOOR: 2\nDEPS: NONE\nSPEC: x\n")
    check("a single entry point that is not main.py is quiet",
          intent.duplicate_entry_points(lone,
                                        intent.parse_components(lone)) == [])

    check("the planner is told what a dependency costs",
          "has to wait" in intent.DECOMPOSE_PROMPT
          and "Exactly one means one" in intent.DECOMPOSE_PROMPT)

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all plan truncation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
