#!/usr/bin/env python3
"""Validates intent.is_pure_static_file_request.

Motivating live failure: `--api maven "create a survey.html"` ran the full
--api pipeline anyway, `propose_multi` found "2 endpoints" for a request
that could not possibly use one, and the resulting CONTRACT biased the
planner into inventing survey_generator.py + data_processor.py around a
static three-question form. This checks the detector that lets team.py
skip --api for exactly that shape of request, and only that shape.
"""
import intent

failures = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("%-4s %s%s" % (status, name, ("  (%s)" % detail) if detail else ""))
    if not cond:
        failures.append(name)


POSITIVE = [
    ("create a survey.html", "survey.html"),
    ("create survey.html", "survey.html"),
    ("write a index.html", "index.html"),
    ("make the styles.css", "styles.css"),
    ("generate a report.md", "report.md"),
    ("create a survey.html, a three-question customer feedback survey "
     "with a color picker", "survey.html"),
    # Live failure: `_FOLLOWUP_ACTION_RE` used to bare-word-match "point"
    # anywhere in the description, so a completely ordinary Likert-scale
    # phrase ("5 point rating axes") was misread as the verb "point the
    # project at it" and this returned None -- the request never even
    # reached the --single-file auto-detection that depends on it.
    ("create a survey.html that shows 5 5 point rating axes with stars "
     "that ask for opinions on different breakfast foods", "survey.html"),
    ("write a report.md with a 10-point scale", "report.md"),
]

for request, expect in POSITIVE:
    got = intent.is_pure_static_file_request(request)
    check("matches: %r" % request, got == expect, "got %r" % got)

NEGATIVE = [
    # The real live failure's sibling steps -- these really do need --api.
    "push survey.html (at ../04_create_survey/survey.html) as a new "
    "version of the applet whose applet_id is read from "
    "../05_create_applet/output.json",
    "create a project; save its id in this directory's own output.json",
    "create a survey.html; the runnable entry point is main.py",
    "create a survey.html and push it to the applet",
    "create a survey.html that reads ../01_create_project/output.json",
    # Python files always run something, and are never in the static set.
    "create a main.py that prints hello",
    "create a client.py",
    "create an applet",
    # Sanity: something with no filename at all.
    "upload 3 units to the task",
]

for request in NEGATIVE:
    got = intent.is_pure_static_file_request(request)
    check("does not match: %r" % request, got is None, "got %r" % got)

# Sabotage: a version of the regex that also matched .py must never be
# used, since a Python file always needs its own gates/behaviour and
# --api may be exactly what makes it correct. Confirm .py is excluded by
# construction rather than by accident of the current test set.
check("py extension is not in the allowed static set",
      ".py" not in intent._STATIC_ASSET_EXT)

# Sabotage: reproduce the pre-fix bare-word scan directly and confirm it
# genuinely misreads the live shape -- proving the fixture above is not
# vacuous, and that the fix is the clause-boundary requirement itself,
# not something unrelated that happens to look right on this one input.
_OLD_FOLLOWUP_ACTION_RE = intent.re.compile(
    r"\b(push|upload|save|point|update|assign|create|read|call|post|"
    r"patch|delete|fetch|send|submit|entry point)\b", intent.re.I)


def _old_is_pure_static_file_request(request):
    m = intent._PURE_STATIC_FILE_RE.match(request.strip())
    if not m:
        return None
    rest = request.strip()[m.end():]
    if "../" in rest or ";" in rest or _OLD_FOLLOWUP_ACTION_RE.search(rest):
        return None
    return m.group(1)


live_request = ("create a survey.html that shows 5 5 point rating axes "
               "with stars that ask for opinions on different breakfast "
               "foods")
check("sabotage: the old bare-word scan reproduces the live false "
     "negative on this exact request",
      _old_is_pure_static_file_request(live_request) is None)
check("the fixed function does not reproduce that false negative",
      intent.is_pure_static_file_request(live_request) == "survey.html")

if failures:
    print("\n%d check(s) failed: %s" % (len(failures), ", ".join(failures)))
    raise SystemExit(1)
print("\nall checks passed")
