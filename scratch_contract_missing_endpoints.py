"""Validates openapi.contract_missing_endpoints / describe_missing_endpoints
against the exact shape of the live failure: a CONTRACT wiring only
create_applet_version and update_project onto Client, while the api slice's
own closure logic correctly marked PATCH .../update-content as REQUIRED --
and the planner never gave any method for it.

Live CONTRACT text (verbatim from the run that motivated this):
  api_client.py owns Client(api_base_url: str, api_key: str) with
  create_applet_version(applet_id: str, version: str, artifact_url: str,
  integrity_sha256: str | None, manifest_json: dict) -> str (returns
  version_id); update_project(project_id: str, applet_version_id: str | None)
"""
import openapi

LIVE_CONTRACT = (
    "api_client.py owns Client(api_base_url: str, api_key: str) with "
    "create_applet_version(applet_id: str, version: str, artifact_url: str, "
    "integrity_sha256: str | None, manifest_json: dict) -> str (returns "
    "version_id); update_project(project_id: str, applet_version_id: str | "
    "None)"
)

CHOSEN = [
    {"method": "GET", "path": "/api/applets/{applet_id}",
     "operationId": "get_applet"},
    {"method": "GET", "path": "/api/projects/{project_id}",
     "operationId": "get_project"},
    {"method": "POST", "path": "/api/applets/{applet_id}/versions",
     "operationId": "create_applet_version"},
    {"method": "PATCH",
     "path": "/api/applet-versions/{version_id}/update-content",
     "operationId": "update_applet_version_content",
     "_closure_for": "the version just created has no real content yet"},
    {"method": "PATCH", "path": "/api/applets/{applet_id}/status",
     "operationId": "update_applet_status"},
    {"method": "GET", "path": "/api/applet-versions/{version_id}/html",
     "operationId": "get_applet_version_html"},
    {"method": "PATCH", "path": "/api/projects/{project_id}",
     "operationId": "update_project"},
    {"method": "GET", "path": "/api/applet-versions/{version_id}",
     "operationId": "get_applet_version"},
]

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


# 1. The live gap is found.
gaps = openapi.contract_missing_endpoints(LIVE_CONTRACT, CHOSEN)
check("the live CONTRACT is missing the update-content endpoint",
      len(gaps) == 1 and gaps[0][0]["path"].endswith("update-content"))

# 2. Every other REQUIRED-less endpoint (no _closure_for) is ignored --
#    this can only ever flag closure entries, never an ordinary one that
#    simply is not mentioned by name.
check("an ordinary (non-REQUIRED) endpoint is never flagged",
      all(e["path"] != "/api/applets/{applet_id}"
          for e, _ in gaps))

# 3. Sabotage in the positive direction: if the CONTRACT DID wire a method
#    covering it -- any text containing the operationId or a path keyword --
#    the gap must disappear. This is what stops the check from being
#    vacuously true (always flags everything).
covered_contract = LIVE_CONTRACT + (
    "; update_applet_version_content(version_id: str, content: str) -> None")
check("adding a covering method clears the gap",
      openapi.contract_missing_endpoints(covered_contract, CHOSEN) == [])

# 4. Sabotage in the negative direction: strip the CONTRACT to NONE --
#    still flagged, since an empty contract covers nothing.
check("an empty/NONE contract still reports the gap",
      len(openapi.contract_missing_endpoints("NONE", CHOSEN)) == 1)

# 5. No REQUIRED endpoints in the slice at all -> no gaps, ever.
no_required = [dict(e, **{"_closure_for": None}) for e in CHOSEN]
for e in no_required:
    e.pop("_closure_for", None)
check("a slice with nothing REQUIRED reports nothing",
      openapi.contract_missing_endpoints(LIVE_CONTRACT, no_required) == [])

# 6. describe_missing_endpoints renders something that names the real
#    method/path and explicitly overrides "do not define your own version".
note = openapi.describe_missing_endpoints(gaps)
check("the note names the missing endpoint",
      "update-content" in note and "PATCH" in note)
check("the note explicitly permits adding the method",
      "not a violation" in note)

# 7. Sabotage: disabling the check entirely (empty gaps) must render
#    nothing, so a clean CONTRACT is never annotated with a false gap.
check("no gaps renders no note",
      openapi.describe_missing_endpoints([]) == "")

# 8. The real live false-negative found in production: the CONTRACT's own
#    prose describing a different file's *content* ("survey.html is the
#    artifact content.") coincidentally contains the word "content", and
#    the closure entry's rare-within-slice path segment "version" is a
#    real, whole word inside the unrelated, legitimately-wired
#    `create_applet_version` method. Both looked like "coverage" to the
#    substring/single-word checks this replaced, and together they made
#    the detector report the gap as covered when no method anywhere
#    pushes real content. Matching the exact CONTRACT text from the run
#    this was measured against.
LIVE_BUG_CONTRACT = (
    "client.py owns Client(api_base_url: str, api_key: str) with "
    "create_applet(slug: str, name: str) -> str (returns applet_id); "
    "create_applet_version(applet_id: str, version: str, artifact_url: "
    "str, integrity_sha256: str = None) -> str (returns version_id); "
    "create_project(name: str, applet_version_id: str) -> str (returns "
    "project_id); create_task(project_id: str, name: str) -> str "
    "(returns task_id); import_units(task_id: str, units: list[dict]) -> "
    "None; survey.html is the artifact content.; client.py owns "
    "get_ui_url(api_base_url: str, ui_url: str, project_id: str) -> str;"
)
BUG_CHOSEN = [
    {"method": "POST", "path": "/api/applets",
     "operationId": "create_applet"},
    {"method": "POST", "path": "/api/projects",
     "operationId": "create_project"},
    {"method": "POST", "path": "/api/projects/{project_id}/clone",
     "operationId": "clone_project"},
    {"method": "POST", "path": "/api/applets/{applet_id}/versions",
     "operationId": "create_applet_version"},
    {"method": "PATCH", "path": "/api/applets/{applet_id}/status",
     "operationId": "update_applet_status"},
    {"method": "POST", "path": "/api/projects/{project_id}/tasks",
     "operationId": "create_task"},
    {"method": "POST", "path": "/api/tasks/{task_id}/units/import",
     "operationId": "import_units"},
    {"method": "POST", "path": "/api/tasks/{task_id}/units/import-csv",
     "operationId": "import_units_csv"},
    {"method": "PATCH",
     "path": "/api/applet-versions/{version_id}/update-content",
     "operationId": ("update_applet_version_content_api_applet_versions_"
                      "_version_id__update_content_patch"),
     "_closure_for": ("call this after creating the resource above -- "
                       "its create endpoint only took a URL, not "
                       "content, so the real bytes still have to be "
                       "pushed through here")},
]
bug_gaps = openapi.contract_missing_endpoints(LIVE_BUG_CONTRACT, BUG_CHOSEN)
check("the coincidental 'content'/'version' overlap no longer hides "
      "the real gap",
      len(bug_gaps) == 1 and bug_gaps[0][0]["path"].endswith("update-content"))

# 9. Sabotage in the positive direction for the same fixture: a CONTRACT
#    that actually adds a method for it must still clear the gap, so the
#    fix did not just make everything look missing.
BUG_CONTRACT_COVERED = LIVE_BUG_CONTRACT + (
    " push_content(version_id: str, html_content: str) -> None;")
check("a real covering method still clears this exact fixture",
      openapi.contract_missing_endpoints(
          BUG_CONTRACT_COVERED, BUG_CHOSEN) == [])

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
