"""Validates openapi._actions_relate_to_request against the exact live
failure: `gemma4:e4b`, asked to split "write a script that uploads n units
to a task id", echoed DECOMPOSE_ACTIONS_PROMPT's own worked example back
almost verbatim ("Create the new version" / "Update the task to use the
new version") instead of analysing the actual request, which never
mentions a version at all.
"""
import openapi

LIVE_REQUEST = "write a script that uploads n units to a task id"
LIVE_HALLUCINATED_SPLIT = [
    "Create the new version",
    "Update the task to use the new version",
]

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


# 1. The live hallucination is rejected -- it shares no real vocabulary
#    with the request (the coincidental stopword "task" alone must not
#    save it).
check("the live echoed split is rejected",
      not openapi._actions_relate_to_request(
          LIVE_HALLUCINATED_SPLIT, LIVE_REQUEST))

# 2. A genuine split of the SAME shape the prompt's example describes,
#    but actually naming the request's own nouns, is accepted -- this is
#    the positive case the mechanism must not break.
GENUINE_SPLIT = [
    "Upload n units to the task",
    "Confirm the task accepts every uploaded unit",
]
check("a genuine, on-topic split is accepted",
      openapi._actions_relate_to_request(GENUINE_SPLIT, LIVE_REQUEST))

# 3. The real motivating case from this project's own history: "push a new
#    applet version... there is already an applet here" genuinely does
#    split into a version-creation action and a project-pointer action,
#    and both legitimately share "version"/"applet"/"project" vocabulary
#    with a request that actually says those words.
VERSION_REQUEST = ("push a new applet version to the applet that already "
                   "exists on this project")
VERSION_SPLIT = [
    "Create a new version of the applet",
    "Point the project at the new applet version",
]
check("the genuine version-push split is accepted",
      openapi._actions_relate_to_request(VERSION_SPLIT, VERSION_REQUEST))

# 4. Sabotage: an empty request (no content words) must not crash and must
#    default to accepting -- there is nothing to check against, and this
#    function must never be the reason a legitimate call fails.
check("an empty request is never rejected (nothing to compare)",
      openapi._actions_relate_to_request(["anything"], "   "))

# 5. Wired end-to-end: decompose_actions itself must fall back to a single
#    action, not raise, when given a request/model pair. Stub cfg/state so
#    no network node exists, forcing the early "no node" fallback path --
#    this only proves the wiring does not crash; the vocabulary check
#    itself is exercised directly above.
class _NoNode:
    def node(self, name):
        return None


actions = openapi.decompose_actions(LIVE_REQUEST, _NoNode(), None,
                                    verbose=False)
check("decompose_actions falls back to [request] with no local node",
      actions == [LIVE_REQUEST])

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
