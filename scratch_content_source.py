"""Validates `intent.content_source_reference` /
`intent.chain_missing_content_source_problems` against the exact live
failure this was built from: `05_create_applet`'s own request never named
`survey.html`, the API's own endpoint closure pulled in a version-creation
requirement anyway, and the writer fabricated placeholder content
(`data:text/html;base64,` of "Hello World") to satisfy it.

Also checks the false positive this regex nearly shipped with: "update the
project with the new applet version" updates a *project*, mentioning an
applet only as the thing being pointed at -- an earlier, looser version of
the action regex matched it anyway.
"""
import intent


def check(name, cond, detail=None):
    status = "ok  " if cond else "FAIL"
    print("%s %s%s" % (status, name, (" -- " + str(detail)) if detail and not cond else ""))
    return cond


LIVE_STEPS = [
    ("01_create_project",
     "create a project; save its id as project_id in a new output.json "
     "in the same directory as the script"),
    ("02_create_task",
     "read project_id from ../01_create_project/output.json; create a "
     "task in that project; save its id as task_id in a new output.json "
     "in the same directory as the script"),
    ("03_upload_units",
     "read task_id from ../02_create_task/output.json; upload 3 units "
     "to that task; save its own status as success in a new output.json "
     "in the same directory as the script"),
    ("04_create_survey",
     "create a survey.html that shows 5 5 point rating axes with stars "
     "that ask for opinions on different breakfast foods"),
    ("05_create_applet",
     "create an applet; save its id as applet_id in a new output.json "
     "in the same directory as the script"),
    ("06_update_applet",
     "read applet_id from ../05_create_applet/output.json; read "
     "survey.html from ../04_create_survey/survey.html; update the "
     "applet with the html; save its own status as success in a new "
     "output.json in the same directory as the script"),
    ("07_update_project",
     "read project_id from ../01_create_project/output.json; read "
     "applet_id from ../05_create_applet/output.json; update the "
     "project with the new applet version; save its own status as "
     "success in a new output.json in the same directory as the "
     "script"),
]

results = []

# --- the live shape: 05_create_applet is exactly the failure -----------

results.append(check(
    "05_create_applet (no content source) is flagged",
    intent.content_source_reference(LIVE_STEPS[4][1])))

results.append(check(
    "06_update_applet (real sibling html) is not flagged",
    not intent.content_source_reference(LIVE_STEPS[5][1])))

results.append(check(
    "07_update_project (updates the project, not the applet) is not "
    "flagged",
    not intent.content_source_reference(LIVE_STEPS[6][1])))

findings = intent.content_source_findings(LIVE_STEPS)
results.append(check(
    "chain findings name exactly 05_create_applet, producer "
    "04_create_survey",
    findings == [(4, "05_create_applet", "04_create_survey")],
    findings))

problems = intent.chain_missing_content_source_problems(LIVE_STEPS)
results.append(check(
    "exactly one problem reported, naming both dirs",
    len(problems) == 1
    and "05_create_applet" in problems[0]
    and "04_create_survey" in problems[0],
    problems))

# --- positive controls ---------------------------------------------------

results.append(check(
    "a bare 'update the applet with new content' with no source at all "
    "is flagged",
    intent.content_source_reference(
        "update the applet with new content; save status")))

no_producer = intent.content_source_findings(
    [("only_step", "create an applet; save its id as applet_id")])
results.append(check(
    "no earlier step creating a content asset -> producer is None",
    no_producer == [(0, "only_step", None)],
    no_producer))

# --- negative controls: must never fire on unrelated requests -----------

results.append(check(
    "a request naming no applet at all is never flagged",
    not intent.content_source_reference(
        "create a project; save its id as project_id")))

results.append(check(
    "'create a task' does not misfire just for containing 'create'",
    not intent.content_source_reference(
        "create a task in that project; save its id as task_id")))

results.append(check(
    "deleting/reading an applet (not create/update) is not flagged",
    not intent.content_source_reference("get the applet status")
    and not intent.content_source_reference("delete the applet")))

# --- sabotage: prove the fixture actually exercises the false positive --
# the ORIGINAL, looser action regex (`[^.;]{0,80}?` between verb and
# "applet") is what nearly shipped; confirm it really does misfire on
# the exact live sentence, so the tightened version above is known to be
# load-bearing and not just cosmetically different.
import re
_LOOSE_RE = re.compile(
    r'\b(?:create|creates|creating|update|updates|updating|push|pushes|'
    r'pushing|attach|attaches|attaching)\b[^.;]{0,80}?\bapplet\b',
    re.IGNORECASE)
loose_misfires = bool(_LOOSE_RE.search(LIVE_STEPS[6][1]))
results.append(check(
    "sabotage: the original loose gap regex really did misfire on "
    "07_update_project's project-update sentence",
    loose_misfires))

# --- autofix_chain_missing_content_source: the mechanical fix ---------
# Measured live, verbatim: a real breakfast-survey chain's own
# 05_create_applet step read exactly "create an applet; save its id as
# applet_id in a new output.json in the same directory as the script",
# with 04_create_survey's own request naming survey.html unqualified
# right above it -- the shape this fix exists for.

LIVE_BREAKFAST_STEPS = [
    ("01_create_project",
     "create a project; save its id as project_id in a new output.json "
     "in the same directory as the script"),
    ("02_create_task",
     "create a task in the project whose project_id is read from "
     "../01_create_project/output.json; save its id as task_id in a "
     "new output.json in the same directory as the script"),
    ("03_upload_units",
     "upload 3 units to the task whose task_id is read from "
     "../02_create_task/output.json; save its status in a new "
     "output.json in the same directory as the script"),
    ("04_create_survey",
     "create a survey.html that shows 5 5-point rating axes with stars "
     "that ask for opinions on different breakfast foods; save the "
     "path to survey.html in a new output.json in the same directory "
     "as the script"),
    ("05_create_applet",
     "create an applet; save its id as applet_id in a new output.json "
     "in the same directory as the script"),
    ("06_update_applet_and_project",
     "push survey.html (at ../04_create_survey/survey.html) as a new "
     "version of the applet whose applet_id is read from "
     "../05_create_applet/output.json, and point the project whose "
     "project_id is read from ../01_create_project/output.json at "
     "that new version; save the new version's id as "
     "applet_version_id in a new output.json in the same directory as "
     "the script"),
]

fixed, remaining = intent.autofix_chain_missing_content_source(
    LIVE_BREAKFAST_STEPS)
changed = [(d, r) for (d, r), (_, orig) in zip(fixed, LIVE_BREAKFAST_STEPS)
          if r != orig]
results.append(check(
    "autofix: exactly one step changed, the flagged one",
    [d for d, _ in changed] == ["05_create_applet"],
    changed))
results.append(check(
    "autofix: the fixed request names the real producer file verbatim",
    changed and "../04_create_survey/survey.html" in changed[0][1],
    changed))
results.append(check(
    "autofix: no problems remain after the fix",
    remaining == [],
    remaining))
results.append(check(
    "autofix: the fixed steps no longer trip the detector at all",
    intent.chain_missing_content_source_problems(fixed) == [],
    fixed))
results.append(check(
    "autofix: every other step's request is untouched, byte-identical",
    all(r == orig for (d, r), (_, orig) in zip(fixed, LIVE_BREAKFAST_STEPS)
        if d != "05_create_applet")))

# A chain with genuinely nothing to copy from must still be left alone
# and still reported -- the general case this fix was never meant to
# resolve.
UNFIXABLE_STEPS = [
    ("only_step", "create an applet; save its id as applet_id"),
]
fixed2, remaining2 = intent.autofix_chain_missing_content_source(
    UNFIXABLE_STEPS)
results.append(check(
    "autofix: a chain with no content producer at all is left "
    "unchanged",
    fixed2 == UNFIXABLE_STEPS,
    fixed2))
results.append(check(
    "autofix: ...and still reported as a real, unresolved problem",
    len(remaining2) == 1 and "only_step" in remaining2[0],
    remaining2))

# A correct chain (already naming its content source) must come back
# byte-identical -- the same "no change to correct input" guarantee
# `autofix_chain_output_json` already gives. Built from LIVE_STEPS'
# own 06/07 (which already read a real sibling html/applet correctly)
# rather than reusing LIVE_STEPS wholesale, since LIVE_STEPS[4]
# (05_create_applet) is deliberately the *flagged* fixture, not a
# clean one.
ALREADY_CORRECT_STEPS = [LIVE_STEPS[3], LIVE_STEPS[5], LIVE_STEPS[6]]
fixed3, remaining3 = intent.autofix_chain_missing_content_source(
    ALREADY_CORRECT_STEPS)
results.append(check(
    "autofix: a chain with no problems at all is untouched",
    fixed3 == ALREADY_CORRECT_STEPS and remaining3 == [],
    (fixed3, remaining3)))

if all(results):
    print("\nALL PASSED (%d/%d)" % (len(results), len(results)))
else:
    print("\nFAILED (%d/%d passed)"
          % (sum(1 for r in results if r), len(results)))
    raise SystemExit(1)
