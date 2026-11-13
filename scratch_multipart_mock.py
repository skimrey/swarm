#!/usr/bin/env python3
"""`mockapi.py`'s multipart/form-data support. Before this, `Handler._handle`
attempted `json.loads()` on every request body before even matching the
route, so a genuinely correct multipart file upload was always rejected as
"body was not valid JSON" -- indistinguishable from the real `data=` vs
`json=` misuse case, which sent a repair loop chasing a fix that could never
apply to code that never touched JSON at all.

Run directly: `python3 scratch_multipart_mock.py`.
"""
import sys
import threading

import requests

import mockapi

passed = 0


def ok(msg):
    global passed
    passed += 1
    print("ok:", msg)


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


UPLOAD_SPEC = {
    "paths": {
        "/api/tasks/{task_id}/units/import-csv": {
            "post": {
                "operationId": "import_units_csv",
                "parameters": [
                    {"name": "task_id", "in": "path", "required": True,
                     "schema": {"type": "string", "format": "uuid"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {"multipart/form-data": {"schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                        },
                    }}},
                },
                "responses": {"201": {"content": {"application/json": {
                    "schema": {"type": "object", "properties": {
                        "count": {"type": "integer"}}}}}}},
            },
        },
        "/api/things": {
            "post": {
                "operationId": "create_thing",
                "parameters": [],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "type": "object", "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    }}},
                },
                "responses": {"201": {"content": {"application/json": {
                    "schema": {"type": "object", "properties": {
                        "id": {"type": "string"}}}}}}},
            },
        },
    },
}


def start_server(spec):
    mock = mockapi.Mock(spec)
    handler_cls = type("H", (mockapi.Handler,), {"mock": mock})
    srv = mockapi.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, mock, "http://127.0.0.1:%d" % port


srv, mock, base = start_server(UPLOAD_SPEC)
task_id = "11111111-1111-4111-8111-111111111111"

# ---------------------------------------------------------------------
# 1. A correct multipart upload succeeds and is recorded as ok.
# ---------------------------------------------------------------------
csv_content = "name,value\nfoo,1\nbar,2\n"
r = requests.post(
    base + "/api/tasks/%s/units/import-csv" % task_id,
    files={"file": ("units.csv", csv_content.encode("utf-8"), "text/csv")})
check(r.status_code == 201,
      "a correct multipart upload must succeed, got %d: %r"
      % (r.status_code, r.text))
ok("a correct multipart upload succeeds")
check(mock.calls[-1]["ok"] is True, "the call log must record it as ok")
ok("the call log records the multipart upload as ok")

# ---------------------------------------------------------------------
# 2. A multipart body missing the required field is 422 naming it.
# ---------------------------------------------------------------------
r = requests.post(
    base + "/api/tasks/%s/units/import-csv" % task_id,
    files={"other_field": ("x.txt", b"data", "text/plain")})
check(r.status_code == 422,
      "a multipart body missing the required field must be 422, got %d"
      % r.status_code)
check("file" in str(r.json()),
      "the rejection must name the missing field, got %r" % r.json())
ok("a multipart body missing the required field is 422 naming it")

# ---------------------------------------------------------------------
# 3. A form field reusing the file's name but with no filename is still
#    rejected -- it is not an uploaded file.
# ---------------------------------------------------------------------
r = requests.post(
    base + "/api/tasks/%s/units/import-csv" % task_id,
    data={"file": "not-a-real-upload"})
check(r.status_code == 422,
      "a plain form field named 'file' with no filename must still be "
      "rejected, got %d" % r.status_code)
ok("a same-named field with no filename is rejected, not accepted as a file")

# ---------------------------------------------------------------------
# 4. A JSON-only route is completely unaffected.
# ---------------------------------------------------------------------
r = requests.post(base + "/api/things", json={"name": "widget"})
check(r.status_code == 201,
      "a normal JSON route must still succeed, got %d: %r"
      % (r.status_code, r.text))
ok("a JSON-only route is unaffected by the multipart branch")

# ---------------------------------------------------------------------
# 5. The existing data=/json= misuse detection on a JSON route is
#    untouched.
# ---------------------------------------------------------------------
r = requests.post(base + "/api/things", data={"name": "widget"})
check(r.status_code == 400,
      "data= misuse on a JSON route must still be a 400, got %d" % r.status_code)
check("not valid JSON" in r.json().get("detail", ""),
      "detail should still describe the JSON parse failure, got %r"
      % r.json())
check("data=" in mock.calls[-1]["detail"],
      "the mock's own call log must still carry the detailed data=/json= "
      "hint, got %r" % mock.calls[-1]["detail"])
ok("the existing data=/json= misuse detection on JSON routes is untouched")

srv.shutdown()

# ---------------------------------------------------------------------
# 6. parse_multipart_fields directly: recovers field name, filename, and
#    exact bytes -- including a trailing newline that a naive .strip(b"\r\n")
#    would eat.
# ---------------------------------------------------------------------
content_type = "multipart/form-data; boundary=XYZ"
csv_bytes = csv_content.encode("utf-8")
raw = (
    b"--XYZ\r\n"
    b'Content-Disposition: form-data; name="file"; filename="units.csv"\r\n'
    b"Content-Type: text/csv\r\n\r\n"
    + csv_bytes + b"\r\n"
    b"--XYZ--\r\n"
)
fields = mockapi.parse_multipart_fields(raw, content_type)
check(fields is not None and "file" in fields,
      "must recover the 'file' field")
check(fields["file"]["filename"] == "units.csv",
      "must recover the exact filename, got %r" % fields["file"]["filename"])
check(fields["file"]["value"] == csv_bytes,
      "must recover the exact bytes including the trailing newline; "
      "got %r, expected %r" % (fields["file"]["value"], csv_bytes))
ok("parse_multipart_fields recovers field name/filename/exact bytes "
   "(including a legitimate trailing newline)")

# ---------------------------------------------------------------------
# 7. A non-multipart content-type returns None.
# ---------------------------------------------------------------------
check(mockapi.parse_multipart_fields(b"{}", "application/json") is None,
      "a non-multipart content-type must return None")
ok("parse_multipart_fields returns None for a non-multipart content-type")

# ---------------------------------------------------------------------
# 8. validate_multipart_body accepts a real upload and reports missing
#    fields by name.
# ---------------------------------------------------------------------
schema = UPLOAD_SPEC["paths"]["/api/tasks/{task_id}/units/import-csv"] \
    ["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
problems = mockapi.validate_multipart_body(schema, fields, UPLOAD_SPEC)
check(problems == [], "a real upload must have no problems, got %r" % problems)
ok("validate_multipart_body accepts a real, correct upload")

problems = mockapi.validate_multipart_body(schema, {}, UPLOAD_SPEC)
check(any("file" in p for p in problems),
      "must name the missing field, got %r" % problems)
ok("validate_multipart_body reports a missing field by name")

# ---------------------------------------------------------------------
# 9. Sabotage: the real bytes of a correct multipart upload are never
#    valid JSON. Proves the fix has to be route-aware, not just "try
#    JSON first, then fall back."
# ---------------------------------------------------------------------
import json as _json
try:
    _json.loads(raw.decode("utf-8", errors="replace"))
    sabotage_parsed = True
except ValueError:
    sabotage_parsed = False
check(not sabotage_parsed,
      "sabotage check: raw multipart bytes must never parse as JSON, "
      "confirming the mock cannot tell these two cases apart by content "
      "alone -- it has to know the route's declared content-type")
ok("sabotage: confirms multipart bytes are never valid JSON, so the fix "
   "must be route-aware")

print()
print("%d/%d checks passed" % (passed, passed))
