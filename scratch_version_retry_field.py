#!/usr/bin/env python3
"""`openapi._VERSION_FIELD_MARKER`: a `version` field renders as a
retry-on-conflict loop (try "1.0.0", then "1.0.0-2", "1.0.0-3", ... until
accepted) rather than a single uuid-suffixed guess like
`_UNIQUE_FIELD_MARKER`. No read of existing versions first -- the operator's
own framing was "incrementing doesn't require reading existing versions, it
just requires retrying while appending a number on the end till something
works."

Run directly: `python3 scratch_version_retry_field.py`.
"""
import re
import sys

import openapi


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


passed = 0


def ok(msg):
    global passed
    passed += 1
    print("ok:", msg)


# ---------------------------------------------------------------------
# _example_scalar marker selection
# ---------------------------------------------------------------------

v = openapi._example_scalar({"type": "string", "description": "Version "
                             "string (e.g. '1.0.0')"}, "version")
check(v == openapi._VERSION_FIELD_MARKER + "version",
      "a field literally named 'version' gets the version marker, got %r" % v)
ok("_example_scalar marks a field named 'version'")

v = openapi._example_scalar({"type": "string", "description": "must be "
                             "unique across the applet"}, "slug")
check(v == openapi._UNIQUE_FIELD_MARKER + "slug",
      "a field whose description says 'unique' still gets the unique "
      "marker, not the version one, got %r" % v)
ok("_example_scalar still marks a genuinely-unique-described field")

v = openapi._example_scalar({"type": "string", "description": "e.g. 1.2"},
                            "schema_version")
check(not str(v).startswith(openapi._VERSION_FIELD_MARKER),
      "a field named schema_version (not exactly 'version') must not be "
      "marked, got %r" % v)
ok("_example_scalar leaves schema_version/api_version alone (exact match "
   "only)")

v = openapi._example_scalar({"type": "string", "description": "a country "
                             "code, e.g. 'US'"}, "country")
check(v == "example",
      "an unrelated string field with an 'e.g.' example must render "
      "plainly, got %r" % v)
ok("_example_scalar leaves an ordinary descriptive field alone")


# ---------------------------------------------------------------------
# example_call: the rendered retry loop
# ---------------------------------------------------------------------

SPEC = {
    "paths": {
        "/api/applets/{applet_id}/versions": {
            "post": {
                "operationId": "create_applet_version",
                "parameters": [
                    {"name": "applet_id", "in": "path", "required": True,
                     "schema": {"type": "string", "format": "uuid"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["version"],
                        "properties": {
                            "version": {"type": "string",
                                       "description": "Version string "
                                       "(e.g., '1.0.0')"},
                        },
                    }}},
                },
                "responses": {"201": {"content": {"application/json": {
                    "schema": {"type": "object", "properties": {
                        "id": {"type": "string"}}}}}}},
            },
        },
    },
}

entry = {
    "method": "POST",
    "path": "/api/applets/{applet_id}/versions",
    "op": SPEC["paths"]["/api/applets/{applet_id}/versions"]["post"],
}
code = openapi.example_call(entry, SPEC)
print("--- generated code ---")
print(code)
print("--- end ---")

check(openapi._VERSION_FIELD_MARKER not in code,
      "the raw marker must never leak into the rendered code")
ok("no raw marker leaks into the output")

check('"1.0.0"' in code, "the loop must seed the first value as '1.0.0'")
ok("seeds version_value with '1.0.0'")

check("for _attempt in range(1, 101):" in code,
      "the retry loop must run up to 100 attempts")
ok("loops up to 100 attempts")

check("resp.status_code not in (400, 409, 422)" in code,
      "must check the exact rejection status codes")
ok("checks for the three conflict/rejection status codes")

check("break" in code, "must break out of the loop on acceptance")
ok("breaks on acceptance")

check("1.0.0-%d" in code, "must build a numbered suffix for the retry")
ok("increments via a numbered suffix")

check("version_value" in code, "the body must reference the loop variable")
ok("body references the retry variable, not a bare literal")

check("must be unique on every call" in code,
      "the backstop warning comment must be present so "
      "gate_hardcoded_unique_field still protects a hardcoded version "
      "literal too")
ok("backstop warning comment survives for version fields")


# ---------------------------------------------------------------------
# Execute the generated code for real, against a fake rejecting server,
# and confirm it actually retries in the right order and stops on the
# right value.
# ---------------------------------------------------------------------

class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)


sent_versions = []


class FakeRequests:
    @staticmethod
    def post(url, headers=None, json=None):
        v = json["version"]
        sent_versions.append(v)
        # Accept only the 4th distinct value tried.
        if len(sent_versions) < 4:
            return FakeResp(409, {"detail": "version already exists"})
        return FakeResp(201, {"id": "new-version-id"})


namespace = {
    "requests": FakeRequests,
    "API_BASE_URL": "http://mock",
    "HEADERS": {},
    "applet_id": "11111111-1111-4111-8111-111111111111",
}
exec(compile(code, "<generated>", "exec"), namespace)

check(sent_versions == ["1.0.0", "1.0.0-2", "1.0.0-3", "1.0.0-4"],
      "expected exactly the sequence 1.0.0, 1.0.0-2, 1.0.0-3, 1.0.0-4; "
      "got %r" % sent_versions)
ok("executes correctly: retries in order and stops on the accepted value")

check(namespace["data"]["id"] == "new-version-id",
      "the final data must reflect the accepted call's response")
ok("final data reflects the accepted response")


# ---------------------------------------------------------------------
# Sabotage: confirm the pre-fix behaviour (no version marker at all)
# would have rendered a bare, unhelpful literal instead.
# ---------------------------------------------------------------------

def pre_fix_example_scalar(schema, name):
    if schema.get("enum"):
        return schema["enum"][0]
    fmt = schema.get("format", "")
    typ = schema.get("type", "string")
    lowered = name.lower()
    if typ == "integer":
        return 1
    if typ == "number":
        return 1.0
    if typ == "boolean":
        return True
    if typ == "array":
        return []
    if typ == "object":
        return {}
    if fmt == "uuid" or lowered.endswith(("_id", "id")):
        return "00000000-0000-4000-8000-000000000000"
    if fmt == "date-time":
        return "2024-01-01T00:00:00Z"
    if fmt == "date":
        return "2024-01-01"
    if "url" in lowered or "uri" in lowered:
        return "https://example.com"
    return "example"


bare = pre_fix_example_scalar({"type": "string", "description": "Version "
                               "string (e.g., '1.0.0')"}, "version")
check(bare == "example",
      "sabotage check: the pre-fix renderer must produce the bare, "
      "unhelpful 'example' placeholder with no retry behaviour at all")
ok("sabotage: confirms the pre-fix renderer had no retry mechanism")

print()
print("%d/%d checks passed" % (passed, passed))
