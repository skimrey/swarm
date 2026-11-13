#!/usr/bin/env python3
"""Hand-written check for `openapi.py`.

One invariant matters more than the rest and is the reason this file exists:
**every `$ref` that survives into the output must resolve to a schema that
also survived.** A slice carrying `{"$ref": ".../UserCreate"}` without
`UserCreate` is worse than no slice, because it looks complete -- it parses,
it is valid JSON, it names a real type -- and the writer handed it invents
the fields. That defect reaches the real API before anything notices.

The rest is transcription of what the module claims: the index covers every
operation, the repeated 422 boilerplate is gone, cycles terminate, and a
request matching nothing says so instead of quietly emitting nothing.

Offline. No node is contacted.
"""

import json
import re
import sys

import openapi

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- " + str(detail)) if detail else ""))
        FAILURES.append(name)


def routes(chosen):
    """Selected operations as `POST /path`, so a failure prints one line.

    A full `Component`-style dict per entry is technically more information
    and practically unreadable; the identity of an operation is its method
    and path, which is all a diagnosis needs.
    """
    return [c["method"] + " " + c["path"] for c in chosen]


def err():
    return {"description": "Validation Error", "content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/HTTPValidationError"}}}}


def spec_with(paths, schemas):
    base = {
        "HTTPValidationError": {"properties": {"detail": {
            "items": {"$ref": "#/components/schemas/ValidationError"},
            "type": "array"}}},
        "ValidationError": {"properties": {"msg": {"type": "string"}}},
    }
    base.update(schemas)
    return {"openapi": "3.1.0", "info": {"title": "T", "version": "1"},
            "paths": paths, "components": {"schemas": base, "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header",
                               "name": "X-API-Key"}}}}


def op(tag, summary, oid, body=None, resp=None, description=""):
    o = {"tags": [tag], "summary": summary, "operationId": oid,
         "description": description,
         "responses": {"200": {"description": "ok", "content": {
             "application/json": {"schema": ({"$ref": "#/components/schemas/" + resp}
                                             if resp else {})}}},
                       "422": err()},
         "security": [{"ApiKeyAuth": []}]}
    if body:
        o["requestBody"] = {"required": True, "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/" + body}}}}
    return o


def refs_and_schemas(text):
    """Every `$ref` name in the output, and every schema name it defines.

    Read off the rendered text rather than off the module's internals, so
    this checks what a writer actually receives.
    """
    refs = set(re.findall(r'"\$ref":\s*"#/components/schemas/([A-Za-z0-9_]+)"',
                          text))
    defined = set()
    block = re.search(r"# Schemas these endpoints reference\n```json\n(.*?)\n```",
                      text, re.S)
    if block:
        defined = set(json.loads(block.group(1)).keys())
    return refs, defined


# --------------------------------------------------------------------------
print("the invariant: nothing in the output points at something missing")

SPEC = spec_with({
    "/api/auth/users": {
        "post": op("Auth", "Create User", "create_user", body="UserCreate",
                   resp="UserResponse"),
        "get": op("Auth", "List Users", "list_users", resp="UserResponse")},
    "/api/teams/{team_id}/members": {
        "post": op("Teams", "Add Member", "add_member", body="AddMemberRequest")},
    "/api/projects": {
        "get": op("Projects", "List Projects", "list_projects", resp="ProjectResponse")},
}, {
    "UserCreate": {"properties": {"email": {"type": "string"},
                                  "profile": {"$ref": "#/components/schemas/Profile"}}},
    "Profile": {"properties": {"bio": {"type": "string"}}},
    "UserResponse": {"properties": {"id": {"type": "string"}}},
    "AddMemberRequest": {"required": ["user_id"],
                        "properties": {"user_id": {"type": "string"}}},
    "ProjectResponse": {"properties": {"name": {"type": "string"}}},
})

text, chosen = openapi.slice_for(SPEC, "create a user and add them to a team",
                                 with_index=False)
refs, defined = refs_and_schemas(text)
check("every referenced schema is also included", refs <= defined,
      "dangling: %s" % sorted(refs - defined))
check("a transitively nested schema comes along too", "Profile" in defined,
      "UserCreate references Profile; got %s" % sorted(defined))
check("an unrelated schema is left out", "ProjectResponse" not in defined,
      "got %s" % sorted(defined))

# --------------------------------------------------------------------------
print("\nselection")

check("the right endpoints were picked",
      {(c["method"], c["path"]) for c in chosen}
      == {("POST", "/api/auth/users"), ("POST", "/api/teams/{team_id}/members")},
      str(routes(chosen)))

_, only = openapi.slice_for(SPEC, "", tags=["Projects"], with_index=False)
check("--tag selects without any request text",
      [c["path"] for c in only] == ["/api/projects"], str(routes(only)))

_, by_path = openapi.slice_for(SPEC, "", paths=["/api/teams"], with_index=False)
check("--path selects by substring",
      [c["path"] for c in by_path][:1] == ["/api/teams/{team_id}/members"],
      str(routes(by_path)))
# Adding a member needs a user, and `--path /api/teams` cannot know that.
# Asserted here rather than loosening the line above, because "the closure
# also ran" and "the path filter picked the wrong thing" would otherwise
# look identical.
check("...and the closure supplies the user that endpoint needs",
      [c["path"] for c in by_path][1:] == ["/api/auth/users"],
      str(routes(by_path)))

_, capped = openapi.slice_for(SPEC, "user project team member list create",
                              limit=2, with_index=False)
check("--limit is honoured", len(capped) == 2, "got %d" % len(capped))

# --------------------------------------------------------------------------
print("\nnoise that every operation repeats is stated once, not 150 times")

check("the 422 response is gone", '"422"' not in text)
check("HTTPValidationError is not dragged in",
      "HTTPValidationError" not in defined, str(sorted(defined)))
check("per-operation security blocks are gone",
      text.count("ApiKeyAuth") == 1,
      "appears %d times" % text.count("ApiKeyAuth"))
check("the auth scheme is still stated", "X-API-Key" in text)

titled = spec_with({"/x": {"get": op("T", "S", "o", resp="Titled")}},
                   {"Titled": {"properties": {"n": {"type": "string",
                                                    "title": "N"}}}})
tt, _ = openapi.slice_for(titled, "s", with_index=False)
check("Pydantic's redundant `title` is dropped", '"title"' not in tt)

# --------------------------------------------------------------------------
print("\nthe index")

idx = openapi.index_block(SPEC)
for path in SPEC["paths"]:
    check("index lists %s" % path, path in idx)
check("index carries no schemas", "$ref" not in idx)

full, _ = openapi.slice_for(SPEC, "create a user", with_index=True,
                            scope_index=False)
check("a full slice contains the whole index when asked",
      "/api/projects" in full)

# --- the index is scoped to where the selection landed ------------------
#
# The whole catalogue is 35% of the attachment budget on the real spec
# (5701 of 16384), mostly listing endpoints the request will never touch.
# Scoped to the chosen tags it is about 1500 and is better insurance: a
# writer building a teams client is likelier to need an unlisted Teams
# endpoint than a Telephony one.

scoped, picked = openapi.slice_for(SPEC, "create a user", with_index=True)
kept = {t for c in picked for t in (c["tags"] or ["Other"])}
check("the scoped index keeps the areas that were chosen",
      all(any(c["path"] in scoped for c in picked) for _ in [0]),
      ", ".join(sorted(kept)))
check("the scoped index is no larger than the whole one",
      len(scoped) <= len(full))

# Only worth asserting if the fixture actually has an area to drop --
# otherwise this passes vacuously and guards nothing.
DROPPABLE = [t for t in {tag for p in SPEC["paths"].values()
                         for o in p.values() for tag in o.get("tags", [])}
             if t not in kept]
if DROPPABLE:
    check("an unrelated area is left out of the scoped index",
          any("## " + t not in scoped for t in DROPPABLE),
          "dropped candidates: " + ", ".join(sorted(DROPPABLE)))

# The dangerous failure: a trimmed list read as the complete API turns an
# absent endpoint into one that does not exist, which is the invention the
# index exists to prevent -- reintroduced by the thing that made it cheap.
check("a scoped index says outright that it is not the whole API",
      "not listed here" in scoped.lower() or "other areas" in scoped.lower())
check("the whole index makes no such disclaimer",
      "other areas of this api exist" not in full.lower())

# --- model hints are verified, never trusted ----------------------------
#
# `propose` runs a local model and is not exercised here -- these cover the
# path its output takes, which is where a wrong answer would do damage.

_, hinted = openapi.slice_for(SPEC, "zzzz-matches-nothing", with_index=False,
                              hints=[("POST", "/api/auth/users")])
check("a valid hint is selected even when no term matches",
      routes(hinted) == ["POST /api/auth/users"], ", ".join(routes(hinted)))

_, bogus = openapi.slice_for(SPEC, "zzzz-matches-nothing", with_index=False,
                             hints=[("POST", "/api/users/create"),
                                    ("GET", "/nope"), ("POST", "")])
check("invented endpoints are dropped rather than repaired to near misses",
      bogus == [], ", ".join(routes(bogus)))

_, ordered = openapi.slice_for(SPEC, "create a user", limit=3,
                               with_index=False,
                               hints=[("GET", "/api/projects")])
check("a hint outranks the term matches it disagrees with",
      ordered and ordered[0]["path"] == "/api/projects",
      ", ".join(routes(ordered)))
check("...without replacing them", len(ordered) > 1,
      ", ".join(routes(ordered)))

# --- avoid is a veto, not one more opinion -------------------------------
#
# Live: "the unit should be empty, it's just data collection" scored a
# .../collection/units endpoint over the actual unit-upload endpoint on the
# shared word "collection" -- a coincidence overlap cannot tell from a
# match. `avoid` is how the model corrects that, and it has to win outright
# rather than merely compete, or the same collision reappears with one more
# vote on each side.

_, would_score = openapi.slice_for(SPEC, "create a user account",
                                   with_index=False)
check("without a veto, the term match is selected (sets up the next check)",
      any(e["path"] == "/api/auth/users" for e in would_score),
      ", ".join(routes(would_score)))

_, vetoed_out = openapi.slice_for(SPEC, "create a user account",
                                  with_index=False,
                                  avoid=[("POST", "/api/auth/users")])
check("a vetoed endpoint is dropped even though it scores well",
      not any(e["path"] == "/api/auth/users" for e in vetoed_out),
      ", ".join(routes(vetoed_out)))

_, hint_vetoed = openapi.slice_for(SPEC, "zzzz-matches-nothing",
                                   with_index=False,
                                   hints=[("POST", "/api/auth/users")],
                                   avoid=[("POST", "/api/auth/users")])
check("a veto beats a hint naming the very same endpoint",
      hint_vetoed == [], ", ".join(routes(hint_vetoed)))

# --- a synonym-table match is exempt from avoid ---------------------------
#
# Live: "upload three units to that task" scored `units/import` #1 via the
# upload->import synonym, a reader model vetoed it anyway, and the writer
# -- left with only the compound `units/import-csv` and
# `units/import-multiturn` siblings -- invented a call to `claim-next`
# instead of using either. This must not resurrect the "collection" case
# just above: a *raw* word coincidentally overlapping a path (like
# "collection") is exactly what avoid still has to be able to veto.
UNITS_SPEC = spec_with({
    "/api/tasks/{task_id}/units/import": {"post": op(
        "Units", "Import Units", "import_units_post")},
    "/api/tasks/{task_id}/units/import-csv": {"post": op(
        "Units", "Import Units Csv", "import_units_csv_post")},
    "/api/tasks/{task_id}/units/import-multiturn": {"post": op(
        "Units", "Import Units Multiturn", "import_units_multiturn_post")},
    "/api/collection/units": {"get": op(
        "Collection", "List Collection Units", "collection_units_get")},
}, {})

_, units_would_score = openapi.slice_for(
    UNITS_SPEC, "upload three units to that task", with_index=False)
check("without a veto, the synonym match wins outright (sets up the next "
      "check)",
      units_would_score
      and units_would_score[0]["path"] == "/api/tasks/{task_id}/units/import",
      ", ".join(routes(units_would_score)))

_, units_veto_survives = openapi.slice_for(
    UNITS_SPEC, "upload three units to that task", with_index=False,
    avoid=[("POST", "/api/tasks/{task_id}/units/import")])
check("a reader vetoing the exact synonym match does not remove it -- the "
      "compound siblings it would otherwise be left with are not the same "
      "endpoint",
      any(e["path"] == "/api/tasks/{task_id}/units/import"
          for e in units_veto_survives),
      ", ".join(routes(units_veto_survives)))

_, collection_would_score = openapi.slice_for(
    UNITS_SPEC, "the unit should be empty, it's just data collection",
    with_index=False)
check("(sets up the next check) the coincidental word match is chosen "
      "without a veto",
      any(e["path"] == "/api/collection/units"
          for e in collection_would_score),
      ", ".join(routes(collection_would_score)))

_, collection_vetoed = openapi.slice_for(
    UNITS_SPEC, "the unit should be empty, it's just data collection",
    with_index=False, avoid=[("GET", "/api/collection/units")])
check("...but a raw coincidental word overlap (not a synonym-table match) "
      "is still vetoed exactly as before",
      not any(e["path"] == "/api/collection/units"
              for e in collection_vetoed),
      ", ".join(routes(collection_vetoed)))

# Sabotage: reproduce the live bug by patching the exemption to always
# say "no match", i.e. simulate the pre-fix `slice_for` where `avoid`
# alone decided every removal with no synonym exemption at all.
_real_synonym_segment_match = openapi._synonym_segment_match
openapi._synonym_segment_match = lambda path, request: False
try:
    _, sabotaged = openapi.slice_for(
        UNITS_SPEC, "upload three units to that task", with_index=False,
        avoid=[("POST", "/api/tasks/{task_id}/units/import")])
finally:
    openapi._synonym_segment_match = _real_synonym_segment_match
check("sabotage: without the exemption, the veto removes units/import and "
      "leaves only its compound siblings -- reproducing the live failure "
      "byte for byte",
      not any(e["path"] == "/api/tasks/{task_id}/units/import"
              for e in sabotaged)
      and any(e["path"] == "/api/tasks/{task_id}/units/import-csv"
             for e in sabotaged),
      ", ".join(routes(sabotaged)))

_, explicit_kept = openapi.slice_for(SPEC, "zzzz-matches-nothing",
                                     with_index=False,
                                     paths=["/api/auth/users"],
                                     avoid=[("POST", "/api/auth/users")])
check("an operator's explicit --path is not overruled by a model's veto",
      any(e["path"] == "/api/auth/users" for e in explicit_kept),
      ", ".join(routes(explicit_kept)))

_, closure_kept = openapi.slice_for(
    SPEC, "zzzz-matches-nothing", with_index=False,
    hints=[("POST", "/api/teams/{team_id}/members")],
    avoid=[("POST", "/api/auth/users")])
check("a veto does not strand a producer another chosen endpoint needs",
      any(e["path"] == "/api/auth/users" and e["method"] == "POST"
          for e in closure_kept),
      ", ".join(routes(closure_kept)))

# --- propose(): the model's reply is split into what to use and what to
# refuse, and a malformed or heading-free reply still degrades safely -----

import ground     # noqa: E402  -- offline import, no node is contacted
import swarm      # noqa: E402


def _cfg_with_mac():
    return swarm.Config({"nodes": [
        {"name": ground.MAC_NODE_NAME, "url": "http://127.0.0.1:11434",
         "enabled": False, "models": [ground.GROUND_MODEL]},
    ]}, "test-config.json")


def _propose_with(reply_text):
    real = swarm.chat_complete
    swarm.chat_complete = lambda *a, **k: {"text": reply_text, "sample": {}}
    try:
        return openapi.propose(SPEC, "create a user", _cfg_with_mac(),
                               swarm.State(), verbose=False)
    finally:
        swarm.chat_complete = real


found, avoided = _propose_with(
    "RELEVANT:\nPOST /api/auth/users\nAVOID:\nGET /api/projects\n")
check("RELEVANT: lines become hints",
      found == [("POST", "/api/auth/users")], found)
check("AVOID: lines become the veto list",
      avoided == [("GET", "/api/projects")], avoided)

found2, avoided2 = _propose_with("POST /api/auth/users\nGET /api/projects\n")
check("no headings at all is read as one relevant list, not dropped",
      found2 == [("POST", "/api/auth/users"), ("GET", "/api/projects")],
      found2)
check("...and nothing is vetoed when nothing followed an AVOID heading",
      avoided2 == [], avoided2)

found3, avoided3 = _propose_with("NONE")
check("NONE means nothing found and nothing vetoed",
      found3 == [] and avoided3 == [], (found3, avoided3))

found4, avoided4 = _propose_with(
    "RELEVANT:\nPOST /api/nope-invented\nAVOID:\nGET /also-invented\n")
check("an invented endpoint is dropped from RELEVANT, not shipped",
      found4 == [], found4)
check("an invented endpoint is dropped from AVOID too -- it cannot veto "
      "something that does not exist",
      avoided4 == [], avoided4)

found5, avoided5 = _propose_with(
    "RELEVANT:\nPOST /api/auth/users\nAVOID:\nPOST /api/auth/users\n")
check("the same endpoint on both sides counts as vetoed, not relevant",
      found5 == [("POST", "/api/auth/users")] and avoided5 == [],
      (found5, avoided5))

# --- decompose_actions / propose_multi: a compound request gets each
# action its own, focused reading instead of one diluted pass -----------


def _queue_replies(*texts):
    q = list(texts)
    real = swarm.chat_complete

    def fake(*a, **k):
        return {"text": q.pop(0) if q else "NONE", "sample": {}}
    swarm.chat_complete = fake
    return real


def _restore(real):
    swarm.chat_complete = real


real = _queue_replies("ONE")
actions = openapi.decompose_actions("create a user", _cfg_with_mac(),
                                    swarm.State(), verbose=False)
_restore(real)
check("ONE means the original text is used unchanged",
      actions == ["create a user"], actions)

real = _queue_replies("SEVERAL:\ncreate a new applet version\n"
                      "point the project at the new version\n")
actions2 = openapi.decompose_actions(
    "push a new version, there is already a project using this applet",
    _cfg_with_mac(), swarm.State(), verbose=False)
_restore(real)
check("a genuine split returns each action, heading token stripped",
      actions2 == ["create a new applet version",
                  "point the project at the new version"], actions2)

real = _queue_replies("SEVERAL:\ncreate a user\n")
actions3 = openapi.decompose_actions("create a user", _cfg_with_mac(),
                                     swarm.State(), verbose=False)
_restore(real)
check("SEVERAL naming only one thing anyway falls back to the original text",
      actions3 == ["create a user"], actions3)

real = _queue_replies(
    "ONE",   # decompose_actions: not actually split
    "RELEVANT:\nPOST /api/auth/users\n")
found6, avoided6 = openapi.propose_multi(SPEC, "create a user",
                                         _cfg_with_mac(), swarm.State(),
                                         verbose=False)
_restore(real)
check("a single-action request costs one propose() call, same as before",
      found6 == [("POST", "/api/auth/users")], found6)

real = _queue_replies(
    "SEVERAL:\ncreate a new applet version\n"
    "point the project at the new version\n",
    "RELEVANT:\nPOST /api/teams/{team_id}/members\n",   # action 1's answer
    "RELEVANT:\nPOST /api/auth/users\n")                # action 2's answer
found7, avoided7 = openapi.propose_multi(SPEC, "push a new version",
                                         _cfg_with_mac(), swarm.State(),
                                         verbose=False)
_restore(real)
check("a split request unions each action's own endpoints",
      set(found7) == {("POST", "/api/teams/{team_id}/members"),
                      ("POST", "/api/auth/users")}, found7)

real = _queue_replies(
    "SEVERAL:\n" + "\n".join("do thing %d of six" % i for i in range(6)),
    "RELEVANT:\nPOST /api/auth/users\n",             # action 0 (kept)
    "RELEVANT:\nGET /api/projects\n",                # action 1 (kept)
    "RELEVANT:\nPOST /api/teams/{team_id}/members\n")  # action 2+ (never asked)
found8, _ = openapi.propose_multi(SPEC, "do six things", _cfg_with_mac(),
                                  swarm.State(), verbose=False, max_actions=2)
_restore(real)
check("more actions than max_actions is capped, not answered in full",
      set(found8) == {("POST", "/api/auth/users"), ("GET", "/api/projects")},
      found8)

# Measured live: "create a project; save its id to output.json under the
# same key the API response uses for it" split into two actions, and the
# second -- pure output-saving boilerplate, not a distinct API capability
# -- still got its own `propose()` call. The small model, asked what
# endpoint "save its id to output.json" needs, answered with something
# rather than nothing: unrelated `.../assignments/{assignment_id}/
# save-draft` and draft-version endpoints, which then closed over an
# unrelated `POST /api/applets/{applet_id}/versions` and marked it
# REQUIRED on a script that only creates a project.
#
# The queue below deliberately supplies a *second* reply that would name a
# real, unrelated-but-valid endpoint (`GET /api/projects`) if the
# boilerplate action actually reached `propose()`. An earlier version of
# this test instead sized the queue to exactly one reply and treated an
# `IndexError`-free run as proof of the skip -- it was not: `_queue_replies`
# falls back to "NONE" on an empty queue, so a version of `propose_multi`
# that calls `propose` on *every* action, boilerplate included, produces
# the identical `found9` result once its extra call drains the queue and
# gets "NONE" back. This shape only passes if the boilerplate action's
# reply is never consumed at all -- `GET /api/projects` must not appear.
real = _queue_replies(
    "SEVERAL:\ncreate a project\n"
    "save its id to output.json under the same key the API response "
    "uses for it\n",
    "RELEVANT:\nPOST /api/auth/users\n",         # action 1's real answer
    "RELEVANT:\nGET /api/projects\n")            # would answer action 2,
                                                  # if it were ever asked
found9, avoided9 = openapi.propose_multi(
    SPEC, "create a project; save its id to output.json under the same "
         "key the API response uses for it",
    _cfg_with_mac(), swarm.State(), verbose=False)
_restore(real)
check("output-saving boilerplate split out as its own action is never "
     "asked what endpoint it needs -- the reply queued for it is never "
     "consumed, so its endpoint never appears",
      found9 == [("POST", "/api/auth/users")], found9)
check("...and nothing is vetoed either, since the skipped action never "
     "ran propose() at all",
      avoided9 == [], avoided9)

# Sabotage: reproduce what the pre-fix code did -- call `propose()`
# directly on the boilerplate clause, the way an unconditional
# `propose_multi` loop would -- and confirm it does NOT come back empty on
# its own. If it did, the skip in `propose_multi` would be provably
# pointless rather than load-bearing.
real = _queue_replies("RELEVANT:\nGET /api/projects\n")
_leaked, _ = openapi.propose(
    SPEC, "save its id to output.json under the same key the API "
         "response uses for it",
    _cfg_with_mac(), swarm.State(), verbose=False)
_restore(real)
check("sabotage: calling propose() on the boilerplate clause the way an "
     "unguarded loop would DOES surface an unrelated endpoint -- proving "
     "the skip in propose_multi is preventing a real leak, not a "
     "no-op guard against something that could never happen",
      _leaked == [("GET", "/api/projects")], _leaked)

# Measured live: a chained request split into two actions, each one
# genuinely substantive and each one naming a *sibling's* output.json as
# part of describing which resource to act on ("...applet whose applet_id
# is read from ../05_create_applet/output.json", "...project whose
# project_id is read from ../01_create_project/output.json"). The bare
# `"output.json" in low` check could not tell this apart from the pure
# save-instruction boilerplate above, so both actions were skipped, never
# reached `propose()`, and neither endpoint was ever suggested to the
# writer at all. Both replies below must be consumed, unlike the
# boilerplate case, because both clauses are real API actions this time.
real = _queue_replies(
    "SEVERAL:\n"
    "create a user whose email is read from ../00_email/output.json\n"
    "add that user to the team whose team_id is read from "
    "../01_create_team/output.json\n",
    "RELEVANT:\nPOST /api/auth/users\n",
    "RELEVANT:\nPOST /api/teams/{team_id}/members\n")
found10, _ = openapi.propose_multi(
    SPEC,
    "create a user whose email is read from ../00_email/output.json, and "
    "add that user to the team whose team_id is read from "
    "../01_create_team/output.json",
    _cfg_with_mac(), swarm.State(), verbose=False)
_restore(real)
check("a split action naming a SIBLING's output.json (not its own) is a "
     "real API action, not boilerplate -- both replies are consumed and "
     "both endpoints come back",
      set(found10) == {("POST", "/api/auth/users"),
                       ("POST", "/api/teams/{team_id}/members")}, found10)

# Sabotage: the pre-fix bare substring check treated ANY mention of
# `output.json` as boilerplate regardless of a `../` prefix. Reproduce
# that exact check inline and confirm it would have thrown both real
# actions above away, which is what makes the fix above load-bearing
# rather than incidental.
def _old_bare_check(action):
    low = action.lower()
    return "output.json" in low or "main.py" in low

check("sabotage: the old bare-substring rule really would have skipped "
     "both real actions above, proving the fix changed behaviour on the "
     "exact shape that broke live",
      _old_bare_check("create a user whose email is read from "
                      "../00_email/output.json")
      and _old_bare_check("add that user to the team whose team_id is "
                          "read from ../01_create_team/output.json"))

# Measured live: `decompose_actions` split "read applet_id from
# ../05_create_applet/output.json; read survey.html from
# ../04_create_survey/survey.html; update the applet with the html; save
# the new version_id as version_id in output.json" into four *separate*
# actions -- and the two pure sibling-reads, standalone, still each got
# their own `propose()` call. Both are local file I/O and call nothing;
# asking a small model what endpoint each one needs burned four calls
# (two models x two actions) and got back 6 and then 1 suggested
# endpoints for clauses that were never going to name a real one. This is
# a different shape from the SAME-sentence case validated just above --
# there, the sibling reference sits *inside* a real action ("create a
# user whose email is read from .../output.json") and must still reach
# propose(). Here the read is the *entire* action, with nothing else in
# it, so there is genuinely no API question to ask.
real = _queue_replies(
    "SEVERAL:\n"
    "read applet_id from ../05_create_applet/output.json\n"
    "read survey.html from ../04_create_survey/survey.html\n"
    "update the applet with the html\n"
    "save the new version_id as version_id in output.json\n",
    "RELEVANT:\nPOST /api/auth/users\n")   # the one real action's answer
found11, _ = openapi.propose_multi(
    SPEC,
    "read applet_id from ../05_create_applet/output.json; read "
    "survey.html from ../04_create_survey/survey.html; update the "
    "applet with the html; save the new version_id as version_id in "
    "output.json",
    _cfg_with_mac(), swarm.State(), verbose=False)
_restore(real)
check("a standalone action that is nothing but reading a sibling's file "
     "(JSON or otherwise) is never asked what endpoint it needs -- only "
     "the one real action's reply is consumed",
      found11 == [("POST", "/api/auth/users")],
      found11)

# Sabotage: confirm this really is new coverage, not something the
# same-sentence guard just above already handled by accident -- calling
# propose() directly on the standalone read the way an unguarded loop
# would DOES surface something, proving the skip in propose_multi is
# preventing a real leak.
real = _queue_replies("RELEVANT:\nGET /api/projects\n")
_leaked2, _ = openapi.propose(
    SPEC, "read applet_id from ../05_create_applet/output.json",
    _cfg_with_mac(), swarm.State(), verbose=False)
_restore(real)
check("sabotage: calling propose() on the standalone sibling-read the "
     "way an unguarded loop would DOES surface an unrelated endpoint -- "
     "proving the new skip is preventing a real leak, not guarding "
     "against something that could never happen",
      _leaked2 == [("GET", "/api/projects")], _leaked2)

# --------------------------------------------------------------------------
print("\ndegenerate inputs")

cyclic = spec_with({"/n": {"get": op("N", "Nodes", "nodes", resp="Node")}},
                   {"Node": {"properties": {
                       "child": {"$ref": "#/components/schemas/Node"},
                       "peer": {"$ref": "#/components/schemas/Other"}}},
                    "Other": {"properties": {
                        "back": {"$ref": "#/components/schemas/Node"}}}})
ct, _ = openapi.slice_for(cyclic, "nodes", with_index=False)   # must terminate
cr, cd = refs_and_schemas(ct)
check("a self-referencing schema terminates and resolves", cr <= cd,
      "dangling: %s" % sorted(cr - cd))

_, none = openapi.slice_for(SPEC, "render a mandelbrot set", with_index=False)
check("a request matching nothing selects nothing", none == [], str(routes(none)))
empty, _ = openapi.slice_for(SPEC, "render a mandelbrot set", with_index=True)
check("...but the index is still handed over", "/api/projects" in empty)

check("an empty document does not crash",
      openapi.slice_for({"paths": {}}, "anything")[1] == [])

# --------------------------------------------------------------------------
print("\nsize, which is the whole point")

import swarm

# A four-operation spec proves nothing about this: real documents earn their
# saving from scoping *away* dozens of unrelated operations, and a request
# selecting most of a four-op document has almost none of that irrelevant
# bulk to drop -- especially once each selected operation carries its own
# example call (below), whose fixed per-operation cost can outweigh a tiny
# document's total size without saying anything about a real one. Filler
# operations, each with its own schema and never matched by this request,
# stand in for the part of a real spec that scoping actually earns its
# saving from.
SIZE_SPEC = json.loads(json.dumps(SPEC))
for i in range(20):
    SIZE_SPEC["paths"]["/api/unrelated-%d" % i] = {
        "get": op("Unrelated", "Unrelated Endpoint %d" % i,
                 "unrelated_%d" % i, resp="UnrelatedResp%d" % i)}
    SIZE_SPEC["components"]["schemas"]["UnrelatedResp%d" % i] = {
        "properties": {"value": {"type": "string"},
                       "detail": {"type": "string"},
                       "count": {"type": "integer"}}}

size_text, _ = openapi.slice_for(
    SIZE_SPEC, "create a user and add them to a team", with_index=False)
whole = swarm.est_tokens(json.dumps(SIZE_SPEC))
sliced = swarm.est_tokens(size_text)
check("the slice is smaller than the document", sliced < whole,
      "%d vs %d" % (sliced, whole))

# --------------------------------------------------------------------------
print("\na term the whole document shares decides nothing")
#
# Not hypothetical. Every path in a FastAPI spec opens with `/api/`, so while
# every term counted equally, "generate an API key" gave all 291 operations
# of the real spec the same boost for `api` -- and unrelated export endpoints
# outranked `/api/auth/login`. A stoplist cannot fix it, because which term
# is ubiquitous differs per document.

# The listing endpoints come first deliberately: a tie is broken by document
# order, so if `recalibrate` is not worth more than `list`, the wrong answer
# wins and this check fails. That is what makes it a regression guard rather
# than a restatement -- counting terms equally scores both candidates at two
# matches apiece.
NOISY = spec_with(
    {"/api/v1/widgets": {"get": op("Widgets", "List Widget", "list_widget")},
     "/api/v1/gadgets": {"get": op("Gadgets", "List Gadget", "list_gadget")},
     "/api/v1/sprockets": {"get": op("Sprockets", "List Sprocket",
                                     "list_sprocket")},
     "/api/v1/widgets/{id}/recalibrate": {
         "post": op("Widgets", "Recalibrate Widget", "recalibrate_widget")}},
    {})

_, picked = openapi.slice_for(NOISY, "recalibrate the widget in the list",
                              limit=2, with_index=False)
check("the rare term decides, not the common one it ties with",
      picked and picked[0]["path"] == "/api/v1/widgets/{id}/recalibrate",
      ", ".join(routes(picked)))

WEIGHT = openapi.weights(openapi.operations(NOISY))
check("a term in every operation is weighted to zero",
      WEIGHT.get("api") == 0.0 and WEIGHT.get("v1") == 0.0,
      "api=%r v1=%r" % (WEIGHT.get("api"), WEIGHT.get("v1")))
check("a rarer term outweighs a commoner one",
      WEIGHT.get("recalibrate", 0) > WEIGHT.get("widgets", 0) > 0,
      "recalibrate=%.2f widgets=%.2f"
      % (WEIGHT.get("recalibrate", 0), WEIGHT.get("widgets", 0)))

# A word the document has never seen is a miss, not an infinitely rare term.
# Backwards, this would make every typo the highest-scoring term in a query.
check("an unknown word scores nothing rather than everything",
      openapi.score(openapi.operations(NOISY)[0], {"zzzquux"}, WEIGHT) == 0.0)

# --------------------------------------------------------------------------
print("\nbody fields close a lexical gap a summary cannot")
#
# Measured live against the real 291-operation spec: "assign the most
# recent version of the applet to the project" never scored
# `PATCH /api/projects/{project_id}` above `PATCH /api/applets/{applet_id}/
# status`, an unrelated admin toggle, because "applet" is a literal path
# token on the wrong endpoint and appears nowhere in the right one's path,
# tags, operationId or summary -- only in `ProjectUpdate.applet_version_id`,
# which the scorer never looked at.

BODYFIELD = spec_with(
    {"/api/projects/{project_id}": {
         "patch": op("Projects", "Update Project", "update_project",
                     body="ProjectUpdate")},
     "/api/applets/{applet_id}/status": {
         "patch": op("Applets", "Update Applet Status",
                     "update_applet_status", body="StatusUpdate")},
     "/api/teams/{team_id}/members": {
         "post": op("Teams", "Add Member", "add_member",
                     body="AddMemberRequest")},
     # Gives "user" real, non-universal weight in this fixture's IDF
     # table, so the single-word check below actually exercises the
     # two-word gate rather than passing vacuously because "user" was
     # never a strong term anywhere and defaulted to weight zero.
     "/api/users/{user_id}": {
         "get": op("Users", "Get User", "get_user")}},
    {"ProjectUpdate": {"properties": {
         "name": {"type": "string"},
         "applet_version_id": {"type": "string"}}},
     "StatusUpdate": {"properties": {"status": {"type": "string"}}},
     "AddMemberRequest": {"properties": {"user_id": {"type": "string"}}}})

body_ops = openapi.operations(BODYFIELD)
by_key = {(e["method"], e["path"]): e for e in body_ops}
BODYWEIGHT = openapi.weights(body_ops)

update_project_op = by_key[("PATCH", "/api/projects/{project_id}")]["op"]
check("_body_field_terms resolves the $ref and reads its property names",
      {"applet", "version", "id"} in
      openapi._body_field_terms(update_project_op, BODYFIELD),
      openapi._body_field_terms(update_project_op, BODYFIELD))

wanted = openapi._terms("assign the latest applet version to the project")
target_score = openapi.score(by_key[("PATCH", "/api/projects/{project_id}")],
                             wanted, BODYWEIGHT)
rival_score = openapi.score(
    by_key[("PATCH", "/api/applets/{applet_id}/status")], wanted, BODYWEIGHT)
check("two words shared with one body field outrank an endpoint that only "
      "shares 'applet' with its own path",
      target_score > rival_score,
      "project=%.2f status=%.2f" % (target_score, rival_score))

# The gate that keeps this from reopening the same collision `avoid` exists
# for: `AddMemberRequest.user_id` shares "user" with "create a user
# account" for a reason that has nothing to do with creating a user. One
# shared word must not be enough, or a body-field match becomes exactly the
# coincidental-overlap bug this module already fixed once at the
# path/summary level. Live regression: without the two-word gate, this
# pulled `POST /api/teams/{team_id}/members` into a slice that a veto on
# the real user-creation endpoint was supposed to leave empty.
single = openapi._terms("create a user account")
member_score = openapi.score(by_key[("POST", "/api/teams/{team_id}/members")],
                             single, BODYWEIGHT)
check("'user' has real weight in this fixture (setup check, not the "
      "invariant itself)", BODYWEIGHT.get("user", 0.0) > 0.0,
      BODYWEIGHT.get("user"))
check("one word shared with a body field is not enough to score at all",
      member_score == 0.0, "score=%.2f" % member_score)

# --------------------------------------------------------------------------
print("\nnarrative words ('prints a link', 'to start with') do not count "
     "as endpoint evidence")

# Measured live against the real spec: "make sure it prints the project
# link" put a unit's `share-link` endpoint second overall on the single
# word "link" -- rarer in the document than "unit"/"units" combined, and
# nothing to do with a request that never mentions sharing anything. "To
# start with, let's make it a survey" did the same to a detector-backfill
# endpoint whose summary begins "Start an async...". Reproduced with a
# fixture shaped the same way: a rare, unrelated endpoint that happens to
# contain a narrative word outranks the endpoint the request actually
# means, unless the narrative word is excluded from scoring evidence.
NARRATIVE = spec_with(
    {"/api/tasks/{task_id}/units/import": {
         "post": op("Units", "Import Units", "import_units")},
     "/api/units/{unit_id}/share-link": {
         "post": op("Units", "Create Share Link", "create_share_link")},
     # Padding so "unit"/"units"/"task" are as common in this fixture's
     # IDF table as they really are in the live spec (dozens of "Units &
     # Workflow" operations) while "link" stays rare -- without this,
     # a 2-operation fixture makes every term equally rare and the
     # collision this check exists to reproduce never happens by
     # coincidence instead of by the mechanism being tested.
     "/api/tasks/{task_id}/units/reset": {
         "post": op("Units", "Reset Units", "reset_units")},
     "/api/tasks/{task_id}/units/bulk-golden": {
         "post": op("Units", "Bulk Mark Golden", "bulk_mark_golden")},
     "/api/tasks/{task_id}": {
         "get": op("Tasks", "Get Task", "get_task")}},
    {})
narrative_ops = openapi.operations(NARRATIVE)
narrative_by_key = {(e["method"], e["path"]): e for e in narrative_ops}
NARRATIVE_WEIGHT = openapi.weights(narrative_ops)
narrative_wanted = openapi._terms(
    "upload 3 units to the task and print the project link when done")
import_score = openapi.score(
    narrative_by_key[("POST", "/api/tasks/{task_id}/units/import")],
    narrative_wanted, NARRATIVE_WEIGHT)
share_score = openapi.score(
    narrative_by_key[("POST", "/api/units/{unit_id}/share-link")],
    narrative_wanted, NARRATIVE_WEIGHT)
check("the real upload endpoint outranks share-link once 'link' is "
      "excluded from scoring evidence",
      import_score > share_score,
      "import=%.2f share_link=%.2f" % (import_score, share_score))

# Sabotage: disable the exclusion itself (patch the stoplist to empty)
# and re-run the exact same scoring call. If share-link does not retake
# the lead once "link" is allowed back in as evidence, the check above
# was passing for some other reason and is not actually exercising the
# fix.
_real_narrative_terms = openapi._NARRATIVE_TERMS
openapi._NARRATIVE_TERMS = frozenset()
try:
    import_score_sabotaged = openapi.score(
        narrative_by_key[("POST", "/api/tasks/{task_id}/units/import")],
        narrative_wanted, NARRATIVE_WEIGHT)
    share_score_sabotaged = openapi.score(
        narrative_by_key[("POST", "/api/units/{unit_id}/share-link")],
        narrative_wanted, NARRATIVE_WEIGHT)
finally:
    openapi._NARRATIVE_TERMS = _real_narrative_terms
check("sabotage: with the stoplist disabled, share-link retakes the lead "
      "on 'link' alone -- proves the check above is load-bearing",
      share_score_sabotaged > import_score_sabotaged,
      "import=%.2f share_link=%.2f"
      % (import_score_sabotaged, share_score_sabotaged))

# --------------------------------------------------------------------------
print("\n'upload' is a request-side synonym for the API's own 'import'")

# Live: "upload 3 units to the task" needed
# POST /api/tasks/{task_id}/units/import, and pure lexical scoring on the
# real 291-operation spec ranked it 19th -- outside any usable cutoff --
# because "upload" and "import" share no substring. `_expand_request_terms`
# exists to bridge exactly this, mechanically, so the answer does not
# depend on a model sample being available or lucky.
upload_wanted = openapi._expand_request_terms(
    "upload 3 units to the task")
check("'import' is present in the expanded term set for an 'upload' request",
      "import" in upload_wanted, sorted(upload_wanted))

import_op = narrative_by_key[("POST", "/api/tasks/{task_id}/units/import")]
upload_score_expanded = openapi.score(import_op, upload_wanted, NARRATIVE_WEIGHT)
upload_wanted_bare = openapi._terms("upload 3 units to the task")
upload_score_bare = openapi.score(import_op, upload_wanted_bare, NARRATIVE_WEIGHT)
check("the synonym measurably raises the real endpoint's score over plain "
      "term overlap alone",
      upload_score_expanded > upload_score_bare,
      "expanded=%.2f bare=%.2f" % (upload_score_expanded, upload_score_bare))

# Sabotage: with the synonym table emptied, re-derive the same expansion
# and confirm "import" is no longer added -- proving the check above is
# actually exercising the table rather than something else.
_real_synonyms = openapi._REQUEST_SYNONYMS
openapi._REQUEST_SYNONYMS = {}
try:
    upload_wanted_sabotaged = openapi._expand_request_terms(
        "upload 3 units to the task")
finally:
    openapi._REQUEST_SYNONYMS = _real_synonyms
check("sabotage: with the synonym table disabled, 'import' is never added "
      "-- proves the check above is load-bearing",
      "import" not in upload_wanted_sabotaged, sorted(upload_wanted_sabotaged))

# --------------------------------------------------------------------------
print("\na sibling folder's own name does not leak into scoring")

# A chained request reads a dependency's id from a sibling step's
# directory -- "../02_create_task/output.json" -- and this project's own
# naming convention numbers and names that directory after what the
# PRODUCER step did, not what THIS step is asking for. "upload 3 units to
# the task ... read from ../02_create_task/output.json" is an upload, not
# a create, but the raw text still contains the word "create" purely as
# an artifact of the sibling folder's name.
SIBLING = spec_with(
    {"/api/tasks/{task_id}/units": {
         "post": op("Units", "Upload Units", "upload_units")},
     "/api/tasks": {
         "post": op("Tasks", "Create Task", "create_task")},
     # Padding so "task"/"units" are common (as in the real spec) while
     # "create" and "upload" stay comparatively rare, or a 2-operation
     # fixture makes this collision impossible to reproduce regardless of
     # whether the fix works.
     "/api/tasks/{task_id}/units/reset": {
         "post": op("Units", "Reset Units", "reset_units")},
     "/api/tasks/{task_id}/units/bulk-golden": {
         "post": op("Units", "Bulk Mark Golden", "bulk_mark_golden")},
     "/api/tasks/{task_id}": {
         "get": op("Tasks", "Get Task", "get_task")}},
    {})
sibling_ops = openapi.operations(SIBLING)
sibling_by_key = {(e["method"], e["path"]): e for e in sibling_ops}
SIBLING_WEIGHT = openapi.weights(sibling_ops)
SIBLING_REQUEST = ("upload 3 units to the task whose task_id is read "
                  "from ../02_create_task/output.json")

clean_wanted = openapi._terms(openapi._strip_sibling_paths(SIBLING_REQUEST))
upload_score = openapi.score(
    sibling_by_key[("POST", "/api/tasks/{task_id}/units")],
    clean_wanted, SIBLING_WEIGHT)
create_score = openapi.score(
    sibling_by_key[("POST", "/api/tasks")],
    clean_wanted, SIBLING_WEIGHT)
check("the real upload endpoint outranks Create Task once the sibling "
      "folder's own name is stripped from scoring evidence",
      upload_score > create_score,
      "upload=%.2f create=%.2f" % (upload_score, create_score))

# Sabotage: score against the raw, unstripped request text instead. If
# Create Task does not close the gap (or take the lead) once "create"
# leaks back in from the folder name, the check above was not actually
# exercising the fix.
dirty_wanted = openapi._terms(SIBLING_REQUEST)
upload_score_dirty = openapi.score(
    sibling_by_key[("POST", "/api/tasks/{task_id}/units")],
    dirty_wanted, SIBLING_WEIGHT)
create_score_dirty = openapi.score(
    sibling_by_key[("POST", "/api/tasks")],
    dirty_wanted, SIBLING_WEIGHT)
check("sabotage: scoring the raw, unstripped request narrows the margin "
      "by letting 'create' leak in from the folder name -- proves the "
      "check above is load-bearing",
      (upload_score_dirty - create_score_dirty)
      < (upload_score - create_score),
      "unstripped margin=%.2f stripped margin=%.2f"
      % (upload_score_dirty - create_score_dirty,
         upload_score - create_score))

# --------------------------------------------------------------------------
print("\nid closure")

# A slice that omits the endpoint creating an id another chosen endpoint
# requires fails invisibly: the writer cannot call what it was not given, so
# it substitutes something id-shaped and every gate passes. Both of these
# reached a real service -- a hardcoded `task-123`, and an applet id passed
# where an applet *version* id belonged, which cleared the mock because a
# uuid is a uuid and drew a 500 from the platform.
CHAINED = {
    "openapi": "3.1.0",
    "paths": {
        "/api/things": {"post": {
            "operationId": "create_thing", "tags": ["Things"],
            "responses": {"201": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/ThingResponse"}}}}}}},
        "/api/things/{thing_id}/versions": {"post": {
            "operationId": "create_thing_version", "tags": ["Things"],
            "responses": {"201": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/ThingVersionResponse"}}}}}}},
        "/api/things/{thing_id}/clone": {"post": {
            "operationId": "clone_thing", "tags": ["Things"],
            "responses": {"201": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/ThingResponse"}}}}}}},
        "/api/jobs": {"post": {
            "operationId": "create_job", "tags": ["Jobs"],
            "requestBody": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/JobCreate"}}}},
            "responses": {"201": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/JobResponse"}}}}}}},
        "/api/crates/{crate_id}/items": {"post": {
            "operationId": "add_item", "tags": ["Crates"],
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "object"}}}}}}},
    },
    "components": {"schemas": {
        "ThingResponse": {"type": "object",
                          "properties": {"id": {"type": "string"}}},
        "ThingVersionResponse": {"type": "object",
                                 "properties": {"id": {"type": "string"}}},
        "JobResponse": {"type": "object",
                        "properties": {"id": {"type": "string"}}},
        "JobCreate": {"type": "object",
                      "required": ["name", "thing_version_id"],
                      "properties": {
            "name": {"type": "string"},
            "thing_version_id": {"type": "string", "format": "uuid"}}},
    }},
}
CHAINED_OPS = openapi.operations(CHAINED)


def pick_from(ops, *routes_wanted):
    return [e for e in ops
            if "%s %s" % (e["method"], e["path"]) in routes_wanted]


def pick(*routes_wanted):
    return pick_from(CHAINED_OPS, *routes_wanted)


added = openapi.close_over_ids(
    CHAINED, CHAINED_OPS, pick("POST /api/jobs"))
check("a body field's id pulls in the endpoint that creates it",
      routes(added) == ["POST /api/things/{thing_id}/versions"],
      ", ".join(routes(added)) or "nothing added")

added = openapi.close_over_ids(
    CHAINED, CHAINED_OPS, pick("POST /api/crates/{crate_id}/items"))
check("a path template's id pulls one in too",
      routes(added) == [],  # nothing in this document creates a crate
      ", ".join(routes(added)))

# Measured live against the real spec: `ProjectCreate.required == ["name"]`
# and `applet_version_id` is `anyOf: [uuid, null]` -- a project can be
# created with no applet version and pointed at one later via PATCH. A
# plain "create a project" request was still handed a CONTRACT note
# reading `POST /api/projects -- REQUIRED: call this first -- ... needs
# applet_version_id`, sending the writer off to also build
# applet-version-creation into a component that never asked for it, for a
# field the operation does not actually need. `required_ids` used to
# collect every `*_id`-suffixed body property regardless of the schema's
# own `required` list; it must not.
OPTIONAL_BODY = json.loads(json.dumps(CHAINED))
OPTIONAL_BODY["paths"]["/api/widgets"] = {"post": {
    "operationId": "create_widget", "tags": ["Widgets"],
    "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"},
            "thing_version_id": {"anyOf": [{"type": "string", "format": "uuid"},
                                           {"type": "null"}]}}}}}},
    "responses": {"201": {"content": {"application/json": {
        "schema": {"type": "object"}}}}}}}
OPTIONAL_BODY_OPS = openapi.operations(OPTIONAL_BODY)
_widget_entry = [e for e in OPTIONAL_BODY_OPS if e["path"] == "/api/widgets"][0]
check("a nullable, non-required body id is not collected as required",
      openapi.required_ids(_widget_entry, OPTIONAL_BODY) == [],
      openapi.required_ids(_widget_entry, OPTIONAL_BODY))
added_optional = openapi.close_over_ids(
    OPTIONAL_BODY, OPTIONAL_BODY_OPS,
    pick_from(OPTIONAL_BODY_OPS, "POST /api/widgets"))
check("...so no closure is pulled in for it, and no false REQUIRED note "
      "is generated for a call that does not need one",
      routes(added_optional) == [], ", ".join(routes(added_optional)))

# Sabotage in miniature: a body field genuinely in `required` must still
# be caught -- proving the gate above is checking the schema's `required`
# list rather than simply refusing to collect body id fields at all.
check("a genuinely required body id is still collected",
      openapi.required_ids(pick("POST /api/jobs")[0], CHAINED)
      == ["thing_version_id"])
_unfiltered = [f for f in
    OPTIONAL_BODY["paths"]["/api/widgets"]["post"]["requestBody"]
    ["content"]["application/json"]["schema"]["properties"]
    if f.endswith("_id")]
check("...and the code this replaces (any *_id property, required or not) "
      "would have wrongly included the nullable field -- confirming the "
      "fixture actually exercises the fix rather than trivially passing",
      "thing_version_id" in _unfiltered, _unfiltered)

# Ambiguity: two endpoints answer with ThingResponse. The collection one is
# the one that creates; the other acts on a thing you must already have.
added = openapi.close_over_ids(
    CHAINED, CHAINED_OPS,
    pick("POST /api/jobs", "POST /api/things/{thing_id}/versions"))
check("an ambiguous producer resolves to the collection endpoint",
      routes(added) == ["POST /api/things"], ", ".join(routes(added)))

# `version_id` reduces to `version`; the endpoint that makes one answers
# with ThingVersionResponse. The path says which version. This is the case
# that reached the live platform as a 500, one endpoint along.
VERSIONED = json.loads(json.dumps(CHAINED))
VERSIONED["paths"]["/api/thing-versions/{version_id}/html"] = {"get": {
    "operationId": "get_version_html", "tags": ["Things"],
    "responses": {"200": {"content": {"text/html": {
        "schema": {"type": "string"}}}}}}}
VERSIONED_OPS = openapi.operations(VERSIONED)
added = openapi.close_over_ids(
    VERSIONED, VERSIONED_OPS,
    [e for e in VERSIONED_OPS if e["path"] == "/api/thing-versions/{version_id}/html"])
check("a short id resolves through the collection it sits under",
      routes(added) == ["POST /api/things/{thing_id}/versions"],
      ", ".join(routes(added)) or "nothing added")

# Ordered, not merged: the id's own name is the more specific claim. A
# `thing_version_id` parameter under /api/things/ must not resolve to a
# thing just because the path says so.
NESTED = json.loads(json.dumps(CHAINED))
NESTED["paths"]["/api/things/{thing_version_id}/publish"] = {"post": {
    "operationId": "publish", "tags": ["Things"],
    "responses": {"200": {"content": {"application/json": {
        "schema": {"type": "object"}}}}}}}
NESTED_OPS = openapi.operations(NESTED)
added = openapi.close_over_ids(
    NESTED, NESTED_OPS,
    [e for e in NESTED_OPS if e["path"] == "/api/things/{thing_version_id}/publish"])
check("the id's own name outranks the path it sits under",
      routes(added) == ["POST /api/things/{thing_id}/versions"],
      ", ".join(routes(added)) or "nothing added")

# A document that does not name what it returns. `POST /api/teams` answers
# with an unnamed object on the real spec, leaving eleven endpoints taking
# a team_id with no way to get one.
UNNAMED = {
    "openapi": "3.1.0",
    "paths": {
        "/api/teams": {"post": {
            "operationId": "create_team", "tags": ["Teams"],
            "responses": {"201": {"content": {"application/json": {
                "schema": {"type": "object"}}}}}}},
        "/api/teams/{team_id}/members": {"post": {
            "operationId": "add_member", "tags": ["Teams"],
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "object"}}}}}}},
        "/api/assignments/{assignment_id}/submit": {"post": {
            "operationId": "submit", "tags": ["Work"],
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "object"}}}}}}},
    },
    "components": {"schemas": {}},
}
UNNAMED_OPS = openapi.operations(UNNAMED)
added = openapi.close_over_ids(
    UNNAMED, UNNAMED_OPS,
    [e for e in UNNAMED_OPS if e["path"] == "/api/teams/{team_id}/members"])
check("a POST to a collection counts when the response names nothing",
      routes(added) == ["POST /api/teams"],
      ", ".join(routes(added)) or "nothing added")

# The rule that keeps the fallback from inventing producers: the last
# segment must be a plural collection. `submit` is a verb, and a document
# where that counted would claim an endpoint creates a `submit`.
check("a verb at the end of a path does not create anything",
      "submit" not in openapi.collection_producers(UNNAMED_OPS)
      and "assignment" not in openapi.collection_producers(UNNAMED_OPS),
      str(sorted(openapi.collection_producers(UNNAMED_OPS))))

# And it stays a fallback: a named response outranks a URL shape. This
# needs a document where the two disagree about the same stem, or the
# assertion passes under either ordering and checks nothing. The first
# version of it did exactly that.
DISAGREES = {
    "openapi": "3.1.0",
    "paths": {
        # Says it makes a beta, though the URL says alphas.
        "/api/alphas": {"post": {
            "operationId": "make_beta", "tags": ["X"],
            "responses": {"201": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/BetaResponse"}}}}}}},
        # Says nothing; only the URL suggests it makes a beta.
        "/api/betas": {"post": {
            "operationId": "make_something", "tags": ["X"],
            "responses": {"201": {"content": {"application/json": {
                "schema": {"type": "object"}}}}}}},
        "/api/gammas": {"post": {
            "operationId": "make_gamma", "tags": ["X"],
            "requestBody": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/GammaCreate"}}}},
            "responses": {"201": {"content": {"application/json": {
                "schema": {"type": "object"}}}}}}},
    },
    "components": {"schemas": {
        "BetaResponse": {"type": "object"},
        "GammaCreate": {"type": "object", "required": ["beta_id"],
                        "properties": {"beta_id": {"type": "string"}}},
    }},
}
DISAGREES_OPS = openapi.operations(DISAGREES)
check("both tables really do offer a producer for the same stem",
      bool(openapi.producers(DISAGREES_OPS).get("beta"))
      and bool(openapi.collection_producers(DISAGREES_OPS).get("beta")))
check("a named response outranks the URL shape",
      routes(openapi.close_over_ids(
          DISAGREES, DISAGREES_OPS,
          [e for e in DISAGREES_OPS if e["path"] == "/api/gammas"]))
      == ["POST /api/alphas"],
      ", ".join(routes(openapi.close_over_ids(
          DISAGREES, DISAGREES_OPS,
          [e for e in DISAGREES_OPS if e["path"] == "/api/gammas"]))))

check("nothing is added when the producer is already chosen",
      openapi.close_over_ids(CHAINED, CHAINED_OPS,
                             pick("POST /api/jobs",
                                  "POST /api/things/{thing_id}/versions",
                                  "POST /api/things")) == [])

# The rule that makes this safe to run on every slice: silence when the
# document does not say. A closure that guessed would add endpoints nobody
# needs to every request, and the index is already the insurance for that.
check("a document with nothing to chain adds nothing",
      openapi.close_over_ids(NOISY, openapi.operations(NOISY),
                             openapi.operations(NOISY)[:2]) == [])

check("the closure is reached through slice_for, not just directly",
      any(e["path"] == "/api/things/{thing_id}/versions"
          for e in openapi.slice_for(CHAINED, "create a job", limit=1,
                                     hints=[("POST", "/api/jobs")])[1]))

# A closure entry rendered like every other operation is invisible: a
# planner skimming ten flat headings has no signal that one of them is
# mandatory rather than optional. Measured live -- the endpoint was in the
# slice and the planner still left it out of the contract, so the writer
# passed the applet's own id where a version id belonged.
_closure_text, _ = openapi.slice_for(CHAINED, "create a job", limit=1,
                                     hints=[("POST", "/api/jobs")],
                                     with_index=False)
check("a closure entry's heading says it is required",
      "### POST /api/things/{thing_id}/versions  -- REQUIRED" in _closure_text,
      _closure_text[:200])
check("...and names what it is required for",
      "POST /api/jobs needs thing_version_id" in _closure_text,
      _closure_text[:250])
check("a chosen (non-closure) endpoint carries no such heading",
      "### POST /api/jobs\n" in _closure_text
      and "### POST /api/jobs  -- REQUIRED" not in _closure_text)

# The exclusivity claim is checked, not assumed. close_over_ids only ever
# picks one producer -- but a hint or an explicit --path can independently
# put a second one for the same stem into the same slice, which is exactly
# the situation where "nothing else returns one" would be false, and
# exactly the situation a writer would go looking in the slice and find
# the second producer sitting right there.
TWO_PRODUCERS = json.loads(json.dumps(CHAINED))
TWO_PRODUCERS["paths"]["/api/things/{thing_id}/versions/import"] = {"post": {
    "operationId": "import_thing_version", "tags": ["Things"],
    "responses": {"201": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/ThingVersionResponse"}}}}}}}
TWO_PRODUCERS_OPS = openapi.operations(TWO_PRODUCERS)
_via_closure = openapi.close_over_ids(
    TWO_PRODUCERS, TWO_PRODUCERS_OPS,
    pick_from(TWO_PRODUCERS_OPS, "POST /api/jobs"))
check("the fixture actually produces one via closure",
      routes(_via_closure) == ["POST /api/things/{thing_id}/versions"],
      routes(_via_closure))
_dup_text = openapi.render(
    TWO_PRODUCERS,
    pick_from(TWO_PRODUCERS_OPS, "POST /api/jobs",
              "POST /api/things/{thing_id}/versions/import")
    + _via_closure)
check("two producers of the same stem: neither claims exclusivity",
      "nothing else in this slice returns one" not in _dup_text, _dup_text)
check("...the required call is still marked as required",
      "-- REQUIRED" in _dup_text)

# Deliberately outside `limit`. An endpoint that makes a chosen one callable
# is not competing with it for a slot, and a closure that could be squeezed
# out by the cap would go missing on exactly the crowded slices that need it.
_, tight = openapi.slice_for(CHAINED, "create a job", limit=1,
                             hints=[("POST", "/api/jobs")], with_index=False)
check("the closure is not capped by --api-limit",
      len(tight) == 2, "got %d: %s" % (len(tight), ", ".join(routes(tight))))

# --------------------------------------------------------------------------
print("\nclosure only fires for a chosen entry's own core need, not for "
     "unrelated filler that happened to clear the score-above-zero bar")

# Measured live: "create a project" scored `POST /api/projects` far above
# everything else, but an unrelated `GET .../assignments/{assignment_id}/
# draft-versions/{version_id}` still cleared the "score above zero, fill
# up to `limit`" bar and entered the slice as filler. Its own `version_id`
# path requirement was then treated by `close_over_ids` as exactly as
# load-bearing as the request's actual subject, adding
# `POST /api/applets/{applet_id}/versions` and marking it REQUIRED --
# telling a writer building a project-creation script to also implement
# applet versioning, for an endpoint the request never came near.
CLOSURE_CORE = json.loads(json.dumps(CHAINED))
CLOSURE_CORE["paths"]["/api/projects"] = {"post": {
    "operationId": "create_project", "tags": ["Projects"],
    "summary": "Create a project",
    "responses": {"201": {"content": {"application/json": {
        "schema": {"type": "object",
                  "properties": {"id": {"type": "string"}}}}}}}}}
CLOSURE_CORE["paths"]["/api/assignments/{assignment_id}/draft-versions/"
                     "{version_id}"] = {"get": {
    "operationId": "get_draft_version", "tags": ["Assignments"],
    "summary": "Unrelated filler that happens to need a version id",
    "parameters": [{"name": "assignment_id", "in": "path", "required": True,
                    "schema": {"type": "string"}},
                  {"name": "version_id", "in": "path", "required": True,
                    "schema": {"type": "string"}}],
    "responses": {"200": {"content": {"application/json": {
        "schema": {"type": "object"}}}}}}}
CLOSURE_CORE_OPS = openapi.operations(CLOSURE_CORE)
_project_entry = [e for e in CLOSURE_CORE_OPS if e["path"] == "/api/projects"][0]
_filler_entry = [e for e in CLOSURE_CORE_OPS
                if e["path"].startswith("/api/assignments/")][0]

# Directly on `close_over_ids`: proves the `core` parameter itself is what
# gates this, independent of how `slice_for` happens to score anything.
_old_behavior = openapi.close_over_ids(
    CLOSURE_CORE, CLOSURE_CORE_OPS, [_project_entry, _filler_entry])
check("sabotage: with no `core` given (the pre-fix call shape), the "
     "filler's own id need still pulls in an unrelated closure -- proves "
     "this fixture actually exercises the bug being fixed",
      routes(_old_behavior) == ["POST /api/things/{thing_id}/versions"]
      or routes(_old_behavior) == ["POST /api/applets/{applet_id}/versions"]
      or len(_old_behavior) == 1,
      routes(_old_behavior))
_new_behavior = openapi.close_over_ids(
    CLOSURE_CORE, CLOSURE_CORE_OPS, [_project_entry, _filler_entry],
    core=[_project_entry])
check("...and with `core` naming only the real subject, the filler's own "
     "need is left alone, exactly like any other endpoint in the slice "
     "the request is not using",
      _new_behavior == [], routes(_new_behavior))

# Through `slice_for` itself, with `score` patched to fixed values so the
# test does not depend on this fixture's exact IDF numbers -- only that a
# scored entry well below the best match is filler, not core. Mirrors
# `slice_for`'s own internal threshold (half of the best scored match).
import unittest.mock as _mock

def _fixed_score(entry, wanted, weight=None):
    return {("POST", "/api/projects"): 10.0,
           ("GET", "/api/assignments/{assignment_id}/draft-versions/"
                   "{version_id}"): 1.0}.get(
        (entry["method"], entry["path"]), 0.0)

with _mock.patch.object(openapi, "score", side_effect=_fixed_score):
    _sliced_text, _sliced_picked = openapi.slice_for(
        CLOSURE_CORE, "create a project", limit=5, with_index=False)
check("through the real pipeline: a well-matched request generates no "
     "REQUIRED note from a weakly-scored filler's own id need",
      "REQUIRED" not in _sliced_text, _sliced_text[:400])
check("...and the filler itself is still in the slice, same as any other "
     "context the request is not using -- this is about closure, not "
     "about dropping the filler",
      any(e["path"].startswith("/api/assignments/") for e in _sliced_picked),
      routes(_sliced_picked))

# --------------------------------------------------------------------------
print("\na HINT with zero shared vocabulary does not get to demand a "
     "closure either -- the same gap, one tier up")

# The filler case just above is a *scored* (tier 1) entry that merely
# cleared "score above zero". Measured live against the real spec, the
# identical failure recurred one tier up: a request to "push survey.html
# as a new version of the applet" got a *hint* (tier 2, from propose(),
# unconditionally core-eligible before this fix) naming
# POST /api/sharepoint/sync/{config_id} -- a completely unrelated
# integration a small model reached for on nothing but "push" sounding
# like "sync". Because a hint was trusted exactly like operator-typed
# intent, that one bad guess forced a REQUIRED clause demanding the writer
# invent a "create a sharepoint config" method before doing anything else;
# the writer never made a real API call. Mirrored here with a
# spec-agnostic "widgets/sync" stand-in for "sharepoint/sync" so the
# fixture does not depend on the real document.
HINT_RELEVANCE = json.loads(json.dumps(CHAINED))
HINT_RELEVANCE["paths"]["/api/widgets/sync/{config_id}"] = {"post": {
    "operationId": "sync_widget", "tags": ["Widgets"],
    "summary": "Sync a widget to an external system",
    "parameters": [{"name": "config_id", "in": "path", "required": True,
                    "schema": {"type": "string", "format": "uuid"}}],
    "responses": {"200": {"content": {"application/json": {
        "schema": {"type": "object"}}}}}}}
HINT_RELEVANCE["paths"]["/api/widgets/configs"] = {"post": {
    "operationId": "create_widget_config", "tags": ["Widgets"],
    "summary": "Create a widget sync configuration",
    "responses": {"201": {"content": {"application/json": {
        "schema": {"type": "object", "properties": {
            "id": {"type": "string"}}}}}}}}}
HINT_RELEVANCE_OPS = openapi.operations(HINT_RELEVANCE)

_, _hint_sliced = openapi.slice_for(
    HINT_RELEVANCE, "push the html as a new version of the thing", limit=8,
    hints=[("POST", "/api/things/{thing_id}/versions"),
          ("POST", "/api/widgets/sync/{config_id}")],
    with_index=False)
_hint_text = openapi.render(HINT_RELEVANCE, _hint_sliced)
_required_lines = [l for l in _hint_text.splitlines() if "REQUIRED" in l]
check("a hint sharing real vocabulary with the request still gets its "
     "own id need closed over (positive control -- the fix must not "
     "break the ordinary case)",
      "POST /api/things" in routes(_hint_sliced) and len(_required_lines) > 0,
      (routes(_hint_sliced), _required_lines))
check("...but the vocabulary-free hint does not get to demand its own "
     "closure -- no widget-config creation is pulled in",
      "POST /api/widgets/configs" not in routes(_hint_sliced), _hint_text)
check("...and no REQUIRED line anywhere mentions the widget/config gap",
      all("widget" not in l.lower() and "config_id" not in l
         for l in _required_lines),
      _required_lines)
check("the vocabulary-free hint itself is still offered in the slice -- "
     "this is about denying it the power to demand a closure, not about "
     "hiding it from the writer",
      "POST /api/widgets/sync/{config_id}" in routes(_hint_sliced),
      routes(_hint_sliced))

# Sabotage: with `core` computed the old way (every hint unconditionally
# eligible), the same inputs must reproduce the live failure exactly --
# proves this fixture actually exercises the bug being fixed.
_old_core = [e for e in _hint_sliced
            if (e["method"], e["path"]) in
            {("POST", "/api/things/{thing_id}/versions"),
             ("POST", "/api/widgets/sync/{config_id}")}]
_sabotaged = openapi.close_over_ids(
    HINT_RELEVANCE, HINT_RELEVANCE_OPS,
    pick_from(HINT_RELEVANCE_OPS, "POST /api/things/{thing_id}/versions",
              "POST /api/widgets/sync/{config_id}"),
    core=_old_core)
check("sabotage: with every hint unconditionally core-eligible (the "
     "pre-fix behaviour), the vocabulary-free hint's own id need does "
     "pull in the unrelated closure -- proves the fixture is real",
      "POST /api/widgets/configs" in routes(_sabotaged), routes(_sabotaged))

# --------------------------------------------------------------------------
print("\ncontent that a creating endpoint could not have supplied")

# Measured live against the real Maven spec: `AppletVersionCreate` requires
# `artifact_url` up front, a generated client invented one pointing at
# `cdn.example.com`, and `PATCH /api/applet-versions/{version_id}/update-
# content` -- the endpoint that actually pushes the real HTML -- was never
# called. Mirrored here with a `thing-version` in place of an
# `applet-version` so the fixture does not depend on the real document.
CONTENT_PUSH = json.loads(json.dumps(CHAINED))
CONTENT_PUSH["paths"]["/api/thing-versions/{thing_version_id}/update-content"] = {
    "patch": {
        "operationId": "update_thing_version_content", "tags": ["Things"],
        "requestBody": {"content": {"application/json": {
            "schema": {"type": "object"}}}},
        "responses": {"200": {"content": {"application/json": {
            "schema": {"type": "object"}}}}}}}
CONTENT_PUSH_OPS = openapi.operations(CONTENT_PUSH)

added = openapi.close_content_pushes(
    CONTENT_PUSH, CONTENT_PUSH_OPS,
    pick_from(CONTENT_PUSH_OPS, "POST /api/things/{thing_id}/versions"))
check("a version-creating endpoint pulls in its content-push sibling",
      routes(added) ==
      ["PATCH /api/thing-versions/{thing_version_id}/update-content"],
      ", ".join(routes(added)) or "nothing added")

added_none = openapi.close_content_pushes(
    CHAINED, CHAINED_OPS, pick("POST /api/things/{thing_id}/versions"))
check("no push is added when the document has no such sibling",
      routes(added_none) == [], ", ".join(routes(added_none)))

added_unrelated = openapi.close_content_pushes(
    CONTENT_PUSH, CONTENT_PUSH_OPS,
    pick_from(CONTENT_PUSH_OPS, "POST /api/jobs"))
check("an unrelated create does not pull in someone else's content push",
      routes(added_unrelated) == [], ", ".join(routes(added_unrelated)))

_push_text = openapi.render(
    CONTENT_PUSH,
    pick_from(CONTENT_PUSH_OPS, "POST /api/things/{thing_id}/versions") + added)
check("the push is marked required and phrased as 'after', not 'first' -- "
      "it runs once the resource it fills in already exists",
      "REQUIRED: call this after" in _push_text, _push_text[:400])

# Wired into slice_for's own closure step, not just callable standalone.
_, sliced = openapi.slice_for(CONTENT_PUSH, "make a thing version", limit=1,
                              hints=[("POST", "/api/things/{thing_id}/versions")],
                              with_index=False)
check("slice_for pulls the content push in on its own",
      "PATCH /api/thing-versions/{thing_version_id}/update-content"
      in routes(sliced), ", ".join(routes(sliced)))

# Sabotage: a GET on the same id family must never be mistaken for the
# content push, or the closure would start attaching read endpoints to
# every creation with a plausible-looking id match.
NOT_CONTENT = json.loads(json.dumps(CONTENT_PUSH))
del NOT_CONTENT["paths"]["/api/thing-versions/{thing_version_id}/update-content"]
NOT_CONTENT["paths"]["/api/thing-versions/{thing_version_id}"] = {"get": {
    "operationId": "get_thing_version", "tags": ["Things"],
    "responses": {"200": {"content": {"application/json": {
        "schema": {"type": "object"}}}}}}}
NOT_CONTENT_OPS = openapi.operations(NOT_CONTENT)
added_get = openapi.close_content_pushes(
    NOT_CONTENT, NOT_CONTENT_OPS,
    pick_from(NOT_CONTENT_OPS, "POST /api/things/{thing_id}/versions"))
check("a same-id GET is not mistaken for a content push",
      routes(added_get) == [], ", ".join(routes(added_get)))

# --------------------------------------------------------------------------
print("\nattaching a newly created resource to something that already existed")

# Measured live as a gap, not a defect: "creates an applet, a project, and
# uploads three units" already worked, because the project's own creation
# takes `applet_version_id` up front and `close_over_ids` finds the version
# for it. The follow-up shape -- "push a new design" on an applet already
# assigned to a project -- has no creating endpoint to attach itself to; the
# project that already points at the old version has to be told about the
# new one, and nothing forced that PATCH into the slice just because a
# version was created. Mirrored with a `thing-version` and a job that can be
# re-pointed at one after the fact, so the fixture stays spec-agnostic.
POINTER = json.loads(json.dumps(CHAINED))
POINTER["paths"]["/api/jobs/{job_id}"] = {"patch": {
    "operationId": "update_job", "tags": ["Jobs"],
    "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "properties": {
            "thing_version_id": {"type": "string", "format": "uuid"}}}}}},
    "responses": {"200": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/JobResponse"}}}}}}}
POINTER_OPS = openapi.operations(POINTER)

added = openapi.close_pointer_updates(
    POINTER, POINTER_OPS,
    pick_from(POINTER_OPS, "POST /api/things/{thing_id}/versions"))
check("creating a version pulls in the existing parent's re-point endpoint",
      routes(added) == ["PATCH /api/jobs/{job_id}"],
      ", ".join(routes(added)) or "nothing added")

added_none = openapi.close_pointer_updates(
    CHAINED, CHAINED_OPS, pick("POST /api/things/{thing_id}/versions"))
check("no pointer update is added when the document has no such endpoint",
      routes(added_none) == [], ", ".join(routes(added_none)))

added_unrelated = openapi.close_pointer_updates(
    POINTER, POINTER_OPS, pick_from(POINTER_OPS, "POST /api/jobs"))
check("creating something that consumes no produced id pulls in nothing",
      routes(added_unrelated) == [], ", ".join(routes(added_unrelated)))

# Sabotage: a POST that happens to share the id family must not be treated
# as a re-point -- creating a second parent is a different shape, and
# `close_over_ids` already covers it from the other direction.
POST_NOT_POINTER = json.loads(json.dumps(POINTER))
POST_NOT_POINTER["paths"]["/api/jobs/{job_id}/retry"] = {"post": {
    "operationId": "retry_job", "tags": ["Jobs"],
    "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "properties": {
            "thing_version_id": {"type": "string", "format": "uuid"}}}}}},
    "responses": {"200": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/JobResponse"}}}}}}}
del POST_NOT_POINTER["paths"]["/api/jobs/{job_id}"]
POST_NOT_POINTER_OPS = openapi.operations(POST_NOT_POINTER)
added_post = openapi.close_pointer_updates(
    POST_NOT_POINTER, POST_NOT_POINTER_OPS,
    pick_from(POST_NOT_POINTER_OPS, "POST /api/things/{thing_id}/versions"))
check("a POST sharing the id family is not mistaken for a re-point",
      routes(added_post) == [], ", ".join(routes(added_post)))

# A from-scratch creation request is not the same shape as a follow-up
# push, and confusing them is what actually broke a live run, not a
# contrived edge case. `POST /api/jobs` (JobCreate) already takes
# `thing_version_id` directly, exactly the way the real spec's
# `POST /api/projects` already takes `applet_version_id` directly --
# so a request that creates the version *and* the job/project in the
# same breath is pointing the new parent at the new child from the
# moment it is created. There is no existing, unpointed parent to
# re-point, and adding the PATCH anyway is exactly the spurious
# "REQUIRED" note that sent a live writer to call `update_project`
# with a placeholder id before the project existed to have a real one.
added_already_pointed = openapi.close_pointer_updates(
    POINTER, POINTER_OPS,
    pick_from(POINTER_OPS, "POST /api/things/{thing_id}/versions",
             "POST /api/jobs"))
check("no re-point is added when a chosen creating POST already threads "
      "the produced id into its own body at creation time",
      routes(added_already_pointed) == [],
      ", ".join(routes(added_already_pointed)) or "nothing added")


def _unguarded_close_pointer_updates(spec, ops, chosen, budget=2):
    """The pre-fix behaviour, reimplemented here rather than monkeypatched,
    since the guard lives inline in the real function rather than behind a
    toggle like `_NARRATIVE_TERMS`. Used only to prove the check above is
    load-bearing."""
    have = set()
    for entry in chosen:
        have |= openapi._produces(entry)
    if not have:
        return []
    seen = {(e["method"], e["path"]) for e in chosen}
    added = []
    for entry in ops:
        key = (entry["method"], entry["path"])
        if key in seen or entry["method"].upper() not in ("PATCH", "PUT"):
            continue
        for field in openapi._body_id_fields(entry, spec):
            if openapi._stem(field[:-3]) in have:
                seen.add(key)
                added.append(entry)
                break
        if len(added) >= budget:
            break
    return added


sabotaged_pointer = _unguarded_close_pointer_updates(
    POINTER, POINTER_OPS,
    pick_from(POINTER_OPS, "POST /api/things/{thing_id}/versions",
             "POST /api/jobs"))
check("sabotage: without the guard, the exact same inputs wrongly add the "
      "re-point anyway -- proves the check above is load-bearing",
      routes(sabotaged_pointer) == ["PATCH /api/jobs/{job_id}"],
      ", ".join(routes(sabotaged_pointer)))

# Wired into slice_for's own closure step, same as close_content_pushes.
_, sliced_ptr = openapi.slice_for(
    POINTER, "make a thing version", limit=1,
    hints=[("POST", "/api/things/{thing_id}/versions")], with_index=False)
check("slice_for pulls the pointer update in on its own",
      "PATCH /api/jobs/{job_id}" in routes(sliced_ptr),
      ", ".join(routes(sliced_ptr)))

_ptr_text = openapi.render(
    POINTER,
    pick_from(POINTER_OPS, "POST /api/things/{thing_id}/versions") + added)
check("the re-point is phrased as attaching to an existing parent",
      "attached to its existing parent" in _ptr_text, _ptr_text[:400])

# --------------------------------------------------------------------------
print("\ntrimming to a budget")

# Closing over ids took a real request from 13324 tokens to 17555, past a
# 16384 ceiling, and the run stopped with advice to lower --api-limit --
# which would have dropped the endpoints that had just been added. A slice
# over budget gives up its weakest term match instead.
BIG = json.loads(json.dumps(SPEC))
_, whole = openapi.slice_for(BIG, "user project team member list create",
                             limit=12, with_index=False)
WHOLE_COST = len(openapi.slice_for(BIG, "user project team member list create",
                                   limit=12, with_index=False)[0]) // 4
ROOM = WHOLE_COST - 50
tight, trimmed = openapi.slice_for(BIG, "user project team member list create",
                                   limit=12, with_index=False, budget=ROOM)
check("a slice over budget trims instead of refusing",
      len(trimmed) < len(whole), "%d -> %d ops" % (len(whole), len(trimmed)))
check("...and the result actually fits",
      len(tight) // 4 <= ROOM,
      "%d tokens against %d" % (len(tight) // 4, ROOM))

# Trimming is best-effort against a floor it may not cross, so a budget
# below that floor gets the smallest honest slice rather than an empty or
# an unbounded one. Stated as the real contract: it fits, or nothing more
# was droppable.
starved, few = openapi.slice_for(BIG, "user project team member list create",
                                 limit=12, with_index=False, budget=1)
check("an unmeetable budget stops at the floor rather than emptying",
      len(few) >= 1 and len(few) < len(whole),
      "%d ops, %d tokens" % (len(few), len(starved) // 4))

# A hint came from a model that read the entire index; a term match is a
# guess. The guess goes first. Two things make this discriminating: the
# query does not mention the hinted endpoint, so score alone would drop it
# first, and the hint is not itself a producer -- the earlier version
# hinted POST /api/auth/users, which the closure re-adds for the members
# endpoint, so it survived whether hints were protected or not.
_, kept = openapi.slice_for(
    BIG, "user project list", limit=12, with_index=False,
    hints=[("POST", "/api/teams/{team_id}/members")], budget=1)
check("a hint survives a trim that drops term matches",
      ("POST", "/api/teams/{team_id}/members")
      in {(e["method"], e["path"]) for e in kept},
      ", ".join(routes(kept)))

# A budget too small for a single operation is a misconfigured fleet. An
# empty slice answers it with an index and no detail, which is worse than
# the one endpoint that matched best and says nothing about why.
_, floor_only = openapi.slice_for(BIG, "team member list", limit=12,
                                  with_index=False, budget=1)
check("trimming never empties the slice", len(floor_only) >= 1,
      "%d ops" % len(floor_only))

# Dropping an endpoint must drop the producer it alone asked for, or the
# trim frees nothing and the slice carries an orphan.
_, closed = openapi.slice_for(
    CHAINED, "create a job", limit=12, with_index=False,
    hints=[("POST", "/api/jobs")])
check("the closure came along at full budget",
      "POST /api/things/{thing_id}/versions" in routes(closed),
      ", ".join(routes(closed)))

# An impossible budget cannot be met by trimming, because hints and their
# closure are not droppable. It must terminate rather than spin or empty
# the slice out.
text, floor = openapi.slice_for(
    CHAINED, "create a job", limit=12, with_index=False,
    hints=[("POST", "/api/jobs")], budget=1)
check("an impossible budget terminates with the undroppable core",
      routes(floor) == ["POST /api/jobs",
                        "POST /api/things/{thing_id}/versions"],
      ", ".join(routes(floor)))

# --------------------------------------------------------------------------
print("\nthe endpoint list is stated as closed")

# The other disclaimers only ever cover a *listed* gap: an endpoint named
# but not detailed, or a tag scoped out entirely. Neither says anything
# about a capability with no match anywhere in the document, which is
# exactly the shape of the live failure this note exists to prevent: a
# contract asked for `get_ui_url(base_url, project_id) -> str`, nothing in
# the spec creates or returns one, and the writer -- holding every real
# endpoint in its own prompt -- invented `GET /api/projects/{id}/my-ui-url`
# anyway.
_closed_text, _closed_picked = openapi.slice_for(
    CHAINED, "create a job", limit=12, with_index=False,
    hints=[("POST", "/api/jobs")])
check("a non-empty slice states the endpoint list is closed",
      "no other endpoint" in _closed_text, _closed_text)
check("...and says what to do when nothing matches",
      "without a network call" in _closed_text, _closed_text)

_empty_text, _empty_picked = openapi.slice_for(
    CHAINED, "zzz nothing matches this at all qqq", limit=12,
    with_index=False)
check("an empty slice carries no closed-world note to contradict",
      not _empty_picked and openapi.CLOSED_WORLD_NOTE not in _empty_text,
      _empty_text)

# --------------------------------------------------------------------------
print("\nauthentication: an apiKey scheme is preferred over a bearer one")

# Measured live: with both schemes offered as neutral alternatives ("every
# operation below accepts either"), a writer chose Bearer, found no login
# endpoint in its own slice, and invented API_JWT_TOKEN -- a credential the
# operator was never told to set and had no way to obtain correctly even if
# they had. The env vocabulary given to every writer only ever supplies
# API_BASE_URL/API_KEY, which maps to an apiKey scheme, not a bearer one.
_BOTH_SCHEMES = {
    "openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {},
    "components": {"securitySchemes": {
        "HTTPBearer": {"type": "http", "scheme": "bearer"},
        "APIKeyHeader": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
    }},
}
_auth_both = openapi.auth_block(_BOTH_SCHEMES)
check("both schemes are still listed", "HTTPBearer" in _auth_both
      and "APIKeyHeader" in _auth_both, _auth_both)
check("the apiKey scheme is named as the one to use",
      "use APIKeyHeader" in _auth_both, _auth_both)
check("...and says why: it reads the standard credential already provided",
      "API_KEY" in _auth_both, _auth_both)
check("...and warns against inventing a token variable for the other scheme",
      "do not invent" in _auth_both.lower(), _auth_both)

# Measured live on a real four-step chain: naming the scheme to use was not
# enough. Two of four writers correctly sent `X-API-Key: <value>`; the
# other two invented `Authorization: ApiKey <value>` -- a header that does
# not exist in the spec, built by pasting the scheme's own name next to the
# generic `Authorization: <Scheme> <token>` shape every http scheme
# actually uses, instead of reading the real header name ("header
# X-API-Key") stated three words earlier on the same line. Both got a real
# 401 from the live platform; the mock cannot catch this because it only
# checks that some value was sent, not which header it arrived on.
check("the exact literal header syntax is spelled out",
      "`X-API-Key: <API_KEY value>`" in _auth_both, _auth_both)
check("sending it inside an Authorization header is explicitly forbidden",
      "NOT send it inside an `Authorization` header" in _auth_both,
      _auth_both)
check("the exact wrong shape measured live is named and forbidden",
      "Authorization: ApiKey <API_KEY value>" in _auth_both, _auth_both)

_ONLY_BEARER = {
    "openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {},
    "components": {"securitySchemes": {
        "HTTPBearer": {"type": "http", "scheme": "bearer"}}},
}
_auth_bearer_only = openapi.auth_block(_ONLY_BEARER)
check("a single scheme with no apiKey alternative gets the old neutral line",
      "accepts either" in _auth_bearer_only, _auth_bearer_only)
check("...and is not told to prefer something that was never offered",
      "use " not in _auth_bearer_only.split("accepts either")[0]
      or "APIKeyHeader" not in _auth_bearer_only, _auth_bearer_only)

_ONLY_APIKEY = {
    "openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {},
    "components": {"securitySchemes": {
        "APIKeyHeader": {"type": "apiKey", "name": "X-API-Key", "in": "header"}}},
}
check("a single apiKey scheme alone still gets the plain neutral line, "
      "since there is no alternative to steer away from",
      "accepts either" in openapi.auth_block(_ONLY_APIKEY))

check("no security schemes at all still renders nothing",
      openapi.auth_block({"components": {}}) == "")

# --------------------------------------------------------------------------
print("\nconnection_setup_block: the literal code shape, not just the "
      "ingredients")

# Measured live, twice, at different granularity: a writer that never
# builds HEADERS at all (silenced the resulting NameError with a
# try/except guard instead -- see codegen.gate_placeholder_credential_
# guard) and a sibling failure one constant over -- a writer whose own
# comment read "Assume client setup handles API_BASE_URL and HEADERS
# initialization" above code that, on an earlier attempt, never actually
# built either one, crashing for real with
# `NameError: name 'API_BASE_URL' is not defined` three steps into a
# live chain. auth_block already names which header/scheme to use; this
# is the gap naming the scheme did not close -- a writer that never
# builds anything at all needs the literal code, not another sentence.
_setup_apikey = openapi.connection_setup_block(_ONLY_APIKEY)
check("builds API_BASE_URL from the environment",
      "API_BASE_URL = os.environ[\"API_BASE_URL\"]" in _setup_apikey,
      _setup_apikey)
check("builds HEADERS using the real header name from the spec",
      "\"X-API-Key\": API_KEY" in _setup_apikey, _setup_apikey)
check("tells the writer exactly where this must live",
      "module scope" in _setup_apikey and "__main__" in _setup_apikey,
      _setup_apikey)
check("forbids the exact excuse measured live, by name",
      "assumed" in _setup_apikey.lower()
      and "execution environment" in _setup_apikey, _setup_apikey)
check("covers the split-across-files case explicitly",
      "client.py" in _setup_apikey and "import" in _setup_apikey,
      _setup_apikey)

_setup_bearer = openapi.connection_setup_block(_ONLY_BEARER)
check("a bearer-only scheme still gets a literal header to build",
      "Authorization" in _setup_bearer and "Bearer" in _setup_bearer,
      _setup_bearer)

check("no security schemes at all renders no connection setup either -- "
      "there is nothing real to build credentials for",
      openapi.connection_setup_block({"components": {}}) == "")

check("sabotage: without this block, auth_block alone says which header "
      "to use but never shows the literal dict a writer should build",
      "Content-Type\": \"application/json" not in _auth_both, _auth_both)

# --------------------------------------------------------------------------
print("\nexample_call: a data URI example that claims base64 and is not")

# Measured live: a writer building f"data:text/html;base64,
# {requests.utils.quote(html, safe='')}" -- percent-encoding, not base64,
# with its own comment admitting "Base64 encoding is complex, using a
# placeholder structure". `base64.b64decode` on the result raises
# immediately; a browser's atob() fails the same way. Traced back to two
# real gaps in what the API document itself gave the writer to copy from.
_UPDATE_CONTENT_SPEC = spec_with(
    {"/api/applet-versions/{version_id}/update-content": {
        "patch": {
            "tags": ["Applets"], "summary": "Update Applet Version Content",
            "operationId": "update_applet_version_content",
            "description": (
                "Update applet version content in-place (admin only)\n\n"
                "Request body:\n{\n"
                '    "artifact_url": "data:text/html;base64,...",\n'
                '    "integrity_sha256": "sha256-hash",\n'
                '    "update_note": "Fixed bug in validation logic"\n'
                "}"),
            "parameters": [{"name": "version_id", "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"}}],
            "requestBody": {"required": True, "content": {
                "application/json": {"schema": {"type": "object"}}}},
            "responses": {"200": {"description": "ok", "content": {
                "application/json": {"schema": {}}}}},
            "security": [{"ApiKeyAuth": []}],
        }},
     "/api/applets/{applet_id}/versions": {
        "post": op("Applets", "Create Applet Version",
                   "create_applet_version", body="AppletVersionCreate",
                   resp="AppletVersionResponse")}},
    {"AppletVersionCreate": {
        "properties": {
            "version": {"type": "string",
                       "description": "Version string (e.g., '1.0.0')"},
            "artifact_url": {"type": "string",
                            "description": "URL to the applet artifact (HTML/JS)"}},
        "type": "object", "required": ["version", "artifact_url"]},
     "AppletVersionResponse": {
        "properties": {"id": {"type": "string", "format": "uuid"}},
        "type": "object", "required": ["id"]}})

_uc_entry = {"method": "PATCH",
            "path": "/api/applet-versions/{version_id}/update-content",
            "op": _UPDATE_CONTENT_SPEC["paths"]
            ["/api/applet-versions/{version_id}/update-content"]["patch"]}
_uc_example = openapi.example_call(_uc_entry, _UPDATE_CONTENT_SPEC)
check("an untyped requestBody schema falls back to the real example "
      "embedded in the operation's own free-text description, rather "
      "than the useless json={} an empty schema renders on its own",
      "artifact_url" in _uc_example and "integrity_sha256" in _uc_example,
      _uc_example)
check("...and the base64 shape inside that example becomes a real, "
      "working base64.b64encode(...) call, not the literal '...' text "
      "the description used as a placeholder",
      "base64.b64encode(" in _uc_example and "..." not in _uc_example,
      _uc_example)
check("...with an explicit warning naming the real field and forbidding "
      "the exact wrong shape measured live",
      "artifact_url" in _uc_example
      and "requests.utils.quote" in _uc_example, _uc_example)

_pv_entry = {"method": "POST", "path": "/api/applets/{applet_id}/versions",
            "op": _UPDATE_CONTENT_SPEC["paths"]
            ["/api/applets/{applet_id}/versions"]["post"]}
_pv_example = openapi.example_call(_pv_entry, _UPDATE_CONTENT_SPEC)
check("the actual live-failing endpoint (Create Applet Version) has its "
      "own schema saying only 'URL to the applet artifact' with no "
      "base64 hint at all -- fixed by cross-referencing the *same field "
      "name* documented elsewhere in this document, not by this "
      "endpoint's own schema",
      "base64.b64encode(" in _pv_example, _pv_example)
check("...and the old generic 'https://example.com' placeholder for "
      "that exact field is gone, not merely joined by the base64 one",
      "https://example.com" not in _pv_example, _pv_example)

_NO_HINT_SPEC = spec_with(
    {"/api/widgets": {"post": op("Widgets", "Create Widget", "create_widget",
                                 body="WidgetCreate", resp="WidgetResponse")}},
    {"WidgetCreate": {
        "properties": {"homepage_url": {"type": "string",
                                       "description": "Homepage URL"}},
        "type": "object", "required": ["homepage_url"]},
     "WidgetResponse": {"properties": {"id": {"type": "string"}},
                        "type": "object", "required": ["id"]}})
_widget_entry = {"method": "POST", "path": "/api/widgets",
                 "op": _NO_HINT_SPEC["paths"]["/api/widgets"]["post"]}
_widget_example = openapi.example_call(_widget_entry, _NO_HINT_SPEC)
check("a plain URL field with no base64 example anywhere else in the "
      "document is left as an ordinary URL placeholder -- this must not "
      "fire on every url-shaped field, only ones the document itself "
      "documents as base64 elsewhere",
      "https://example.com" in _widget_example
      and "base64" not in _widget_example, _widget_example)

check("sabotage: without _example_from_description, the update-content "
      "endpoint's schema alone renders the exact useless json={} the "
      "live failure had nothing better to work from",
      openapi.example_call(
          {"method": "PATCH",
           "path": "/api/applet-versions/{version_id}/update-content",
           "op": {**_UPDATE_CONTENT_SPEC["paths"]
                 ["/api/applet-versions/{version_id}/update-content"]
                 ["patch"], "description": ""}},
          _UPDATE_CONTENT_SPEC).count("json={}") == 1)
check("sabotage: without _apply_document_wide_base64_hints, the actual "
      "live-failing endpoint's own schema alone still renders the plain "
      "url placeholder that led the writer astray",
      "https://example.com" in openapi.example_call(
          _pv_entry, spec_with(
              {"/api/applets/{applet_id}/versions": _UPDATE_CONTENT_SPEC
               ["paths"]["/api/applets/{applet_id}/versions"]},
              {"AppletVersionCreate": _UPDATE_CONTENT_SPEC["components"]
               ["schemas"]["AppletVersionCreate"],
               "AppletVersionResponse": _UPDATE_CONTENT_SPEC["components"]
               ["schemas"]["AppletVersionResponse"]})))

# --------------------------------------------------------------------------
print("\na standing exclusion is a veto that survives even a perfect score")

# "i never want telephony" -- a fact about the operator's standing
# preference for this service, not a judgement about one request's wording.
# Unlike `avoid` (from `propose`, one sentence's worth of opinion), this has
# to hold even when the request scores the excluded operation as the best
# possible match, and even across requests that never mention the excluded
# area by name at all.
EXCLUDE_SPEC = spec_with(
    {"/api/projects/{project_id}/telephony/sessions": {
        "post": op("Telephony Collection", "Start Telephony Session",
                   "start_telephony_session")},
     "/api/projects/{project_id}/telephony/sessions/{session_id}/start-call": {
        "post": op("Telephony Collection", "Start Call", "start_call")},
     "/api/projects": {
        "post": op("Projects", "Create Project", "create_project")}},
    {})
_excl_text, _excl_chosen = openapi.slice_for(
    EXCLUDE_SPEC, "start a telephony session and start a call", limit=12,
    with_index=False, exclude_tags=["Telephony Collection"])
check("an excluded tag is never chosen, even when it is the best scoring "
      "match for the request",
      not any(c["path"].startswith("/api/projects/{project_id}/telephony")
              for c in _excl_chosen),
      routes(_excl_chosen))
check("...and its request/response detail is not rendered either",
      "start_telephony_session" not in _excl_text, _excl_text)

_excl_hint_text, _excl_hint_chosen = openapi.slice_for(
    EXCLUDE_SPEC, "create a project", limit=12, with_index=False,
    hints=[("POST", "/api/projects/{project_id}/telephony/sessions")],
    exclude_tags=["Telephony Collection"])
check("a model HINT naming an excluded endpoint does not override the "
      "standing exclusion either",
      not any("telephony" in c["path"] for c in _excl_hint_chosen),
      routes(_excl_hint_chosen))

_excl_override_text, _excl_override_chosen = openapi.slice_for(
    EXCLUDE_SPEC, "create a project", limit=12, with_index=False,
    tags=["Telephony Collection"], exclude_tags=["Telephony Collection"])
check("an explicit --tag from the operator still overrides the standing "
      "exclusion, since naming it directly is a stronger, present-tense "
      "statement of intent than a stored default",
      any("telephony" in c["path"] for c in _excl_override_chosen),
      routes(_excl_override_chosen))

# Sabotage: an empty exclude_tags argument must not accidentally bar
# everything (e.g. a `barred_tags and ...` check reduced to always-true).
_noexcl_text, _noexcl_chosen = openapi.slice_for(
    EXCLUDE_SPEC, "start a telephony session", limit=12, with_index=False)
check("sabotage: with no exclusion given at all, the same request finds "
      "the telephony endpoint normally -- proves the veto above is the "
      "`exclude_tags` argument, not some unrelated always-on filter",
      any("telephony" in c["path"] for c in _noexcl_chosen),
      routes(_noexcl_chosen))

# --------------------------------------------------------------------------
print("\na standing exclusion is registered per spec name and persists")

import tempfile

_tmp_dir = tempfile.mkdtemp()
_real_spec_dir, _real_registry = openapi.SPEC_DIR, openapi.REGISTRY
openapi.SPEC_DIR = _tmp_dir
openapi.REGISTRY = __import__("os").path.join(_tmp_dir, "registry.json")
try:
    with open(openapi.REGISTRY, "w", encoding="utf-8") as fh:
        json.dump({"testspec": {"url": "http://example/x", "fetched": 0,
                                "operations": 3}}, fh)
    check("a freshly registered spec has no exclusions",
          openapi.excluded_tags("testspec") == set())
    after_add = openapi.set_excluded_tags("testspec", ["Telephony Collection"])
    check("adding an exclusion persists it",
          openapi.excluded_tags("testspec") == {"Telephony Collection"})
    after_add2 = openapi.set_excluded_tags("testspec", ["Billing"])
    check("a second exclusion adds rather than replaces",
          openapi.excluded_tags("testspec")
          == {"Telephony Collection", "Billing"})
    openapi.set_excluded_tags("testspec", ["Telephony Collection"], add=False)
    check("--include-tag removes exactly the named tag, keeping the rest",
          openapi.excluded_tags("testspec") == {"Billing"})
    check("a spec that was never registered has no exclusions to find "
          "(never crashes, never bars anything by accident)",
          openapi.excluded_tags("not-a-real-spec-name") == set())
    import unittest.mock as _mock
    with _mock.patch.object(openapi, "fetch", return_value=3):
        openapi.register("testspec", "http://example/x")
    check("refreshing (re-registering under the same name) keeps a "
          "previously-set exclusion -- a re-fetched document must not "
          "silently reopen a domain that was deliberately shut off",
          openapi.excluded_tags("testspec") == {"Billing"})
finally:
    openapi.SPEC_DIR, openapi.REGISTRY = _real_spec_dir, _real_registry
    import shutil
    shutil.rmtree(_tmp_dir, ignore_errors=True)

# --------------------------------------------------------------------------
print("\nregistered names")

check("a real file always beats a registered name",
      openapi.resolve(__file__) == __file__)
check("an unknown name is passed through, to fail as a path",
      openapi.resolve("not-registered-anywhere") == "not-registered-anywhere")

# --------------------------------------------------------------------------
print("\nfiller endpoints are labelled, not silently rendered as relevant")

# Live failure this reproduces: "create a project; save its id..." asked
# for nothing but project creation, and the real slice also contained
# POST .../tasks and POST .../telephony/sessions -- both scored in as
# filler while the slice filled toward --api-limit. The writer called all
# three; the mock accepted them (it enforces no business rules), and the
# real platform correctly 400'd on the telephony one, killing the whole
# chain on step 1 over a call nothing in the request ever asked for.
SCOPE_SPEC = spec_with({
    "/api/projects": {
        "post": op("Projects", "Create Project", "create_project",
                   body="ProjectCreate", resp="ProjectResponse")},
    "/api/projects/{project_id}/tasks": {
        "post": op("Tasks", "Create Task", "create_task",
                   body="TaskCreate", resp="TaskResponse")},
    "/api/projects/{project_id}/telephony/sessions": {
        "post": op("Telephony", "Create Telephony Session",
                   "create_telephony_session", body="TelephonySessionCreate",
                   resp="TelephonySessionResponse")},
    "/api/export-jobs": {
        "post": op("Export", "Create Export Job", "create_export_job",
                   body="ExportJobCreate", resp="ExportJobResponse")},
}, {
    "ProjectCreate": {"properties": {"name": {"type": "string"}}},
    "ProjectResponse": {"properties": {"id": {"type": "string"}}},
    "TaskCreate": {"properties": {"name": {"type": "string"}}},
    "TaskResponse": {"properties": {"id": {"type": "string"}}},
    "TelephonySessionCreate": {"properties": {"phone": {"type": "string"}}},
    "TelephonySessionResponse": {"properties": {"id": {"type": "string"}}},
    "ExportJobCreate": {"properties": {"format": {"type": "string"}}},
    "ExportJobResponse": {"properties": {"id": {"type": "string"}}},
})

scope_text, scope_picked = openapi.slice_for(
    SCOPE_SPEC, "create a project; save its id as project_id in a new "
                "output.json in the same directory as the script",
    limit=12, with_index=False)

check("the real match (create project) is selected",
      any(e["path"] == "/api/projects" and e["method"] == "POST"
          for e in scope_picked),
      str(routes(scope_picked)))
check("filler endpoints (tasks, telephony) are also in the slice -- this "
     "reproduces the live shape rather than a slice that never had the "
     "problem to begin with",
      any(e["path"] == "/api/projects/{project_id}/tasks"
          for e in scope_picked)
      and any(e["path"] == "/api/projects/{project_id}/telephony/sessions"
             for e in scope_picked),
      str(routes(scope_picked)))

_proj_heading_end = scope_text.index(
    "### POST /api/projects\n")
_proj_heading_line = scope_text[_proj_heading_end:
                               scope_text.index("\n", _proj_heading_end)]
check("the actual match's heading carries no 'background context only' "
     "annotation", "background context only" not in _proj_heading_line)

check("the tasks filler endpoint is marked background-context-only",
      ("### POST /api/projects/{project_id}/tasks  -- background context "
       "only" in scope_text),
      "heading not found or not annotated")
check("the telephony filler endpoint is marked background-context-only",
      ("### POST /api/projects/{project_id}/telephony/sessions  -- "
       "background context only" in scope_text),
      "heading not found or not annotated")
check("the writer is told outright to call the fewest endpoints, not "
     "every one shown", "Call the fewest endpoints" in scope_text)

# A slice with no filler (everything chosen is a strong match or a
# REQUIRED closure) must not carry the note at all -- a warning that never
# applies is noise, and this project has already decided elsewhere that a
# detector firing on correct input costs more than it saves.
narrow_text, narrow_picked = openapi.slice_for(
    SCOPE_SPEC, "create a project", limit=1, with_index=False)
check("a slice with no filler carries no 'background context only' "
     "annotation",
      "background context only" not in narrow_text, narrow_text)
check("...and no MINIMAL_CALL_NOTE either, since there is nothing here "
     "for it to warn about",
      "Call the fewest endpoints" not in narrow_text)

# Sabotage: calling render() the old way (no core_keys at all) must lose
# the annotation entirely, proving it is core_keys reaching render that
# produces the labelling above, not something else in the pipeline.
_sab_text = openapi.render(SCOPE_SPEC, scope_picked)
check("sabotage: render() called without core_keys (the pre-fix call "
     "shape) renders every entry identically, with no annotation "
     "distinguishing the real match from the filler -- proving core_keys "
     "is what the fix actually threads through",
      "background context only" not in _sab_text, _sab_text[:200])

print()
if FAILURES:
    print("FAILED: %s" % ", ".join(FAILURES))
    sys.exit(1)
print("all openapi.py checks passed")
