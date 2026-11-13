#!/usr/bin/env python3
"""A stand-in for the real API, generated from its own OpenAPI document.

`gate_smoke` runs a generated file's `main`, which is the only automatic
gate that observes behaviour rather than shape -- and for an API client that
is exactly the gate you cannot afford, because the behaviour it observes is
a live POST. The network guard in `codegen.run_command` stops that reaching
production. This is what fills the hole it leaves: something on loopback
that answers, so the code can run all the way through and be judged on what
it actually sent.

The point is that it judges. A stub returning `{}` to everything proves only
that the client did not crash, which is the same thing `gate_import` already
proved. Every fact needed to do better is in the spec:

* **The endpoint must exist.** An unknown method-and-path is 404 with the
  near misses listed. This catches an invented route by execution, which
  `openapi.py`'s index only discourages by being readable -- and a writer
  that invents `POST /api/users/create` fails here in milliseconds.
* **Required fields must be present.** Resolved through `$ref`, so a missing
  `email` on `UserCreate` is a 422 naming the field, not a shrug.
* **Types must be right where the schema says so.** A string where an
  integer belongs is the defect that survives every other gate and 422s
  against the real service.

What it deliberately does *not* do is invent behaviour. Responses are
synthesised from the response schema -- the shape the spec promises, with
plausible values -- and no state is kept between calls. A mock that
pretended to be a database would start being wrong in ways nobody could
predict, and the client would be written against fiction. Anything needing
real semantics needs the real service, and that is the operator's call to
make deliberately, not a gate's to make by accident.

    ./mockapi.py maven                      # serve until interrupted
    ./mockapi.py maven --port 8931 --log calls.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import openapi

# Values used to fill a synthesised response. Recognisable on sight, so a
# value that leaks into a log or an assertion is obviously from the mock and
# not from anything real.
_SAMPLE = {
    "string": "mock",
    "integer": 1,
    "number": 1.0,
    "boolean": True,
    "array": [],
    "object": {},
}


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$")

_BOUNDARY_RE = re.compile(r'boundary="?([^";]+)"?')


def parse_multipart_fields(raw: bytes, content_type: str
                           ) -> Optional[Dict[str, Any]]:
    """Every form part in a `multipart/form-data` body, by name, as
    `{"filename": str-or-None, "value": bytes}`.

    Written against the raw bytes and the `Content-Type` header's own
    `boundary` parameter rather than the deprecated (3.11+) and
    soon-removed (3.13) stdlib `cgi.FieldStorage`, so this has no
    dependency this project would otherwise have to drop later.
    """
    m = _BOUNDARY_RE.search(content_type)
    if not m:
        return None
    boundary = ("--" + m.group(1)).encode("utf-8")
    fields: Dict[str, Any] = {}
    for chunk in raw.split(boundary)[1:-1]:
        # Strip exactly the two-byte CRLF sequence at each end, not the
        # *set* of bytes {\r, \n} -- `bytes.strip(b"\r\n")` would also eat
        # a real uploaded file's own legitimate trailing newline along
        # with the surrounding multipart framing.
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        if not chunk:
            continue
        header_block, sep, value = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers_text = header_block.decode("utf-8", errors="replace")
        name_m = re.search(r'name="([^"]*)"', headers_text)
        if not name_m:
            continue
        filename_m = re.search(r'filename="([^"]*)"', headers_text)
        fields[name_m.group(1)] = {
            "filename": filename_m.group(1) if filename_m else None,
            "value": value,
        }
    return fields


def validate_multipart_body(schema: Optional[Dict[str, Any]],
                            fields: Optional[Dict[str, Any]],
                            spec: Dict[str, Any]) -> List[str]:
    """The same shallow, spec-only checking `validate_body` applies to a
    JSON body, applied to a parsed multipart form instead: every required
    field present, and any field the schema declares `format: binary`
    must actually be an uploaded file (a form part carrying a filename),
    not a plain value under the same name.
    """
    if schema is None:
        return []
    schema = _deref(schema, spec)
    if not isinstance(schema, dict):
        return []
    if fields is None:
        return ["body was not valid multipart/form-data"]
    problems: List[str] = []
    props = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in fields:
            problems.append("missing required field %r" % name)
            continue
        sub = _deref(props.get(name), spec)
        if isinstance(sub, dict) and sub.get("format") == "binary" \
                and not fields[name]["filename"]:
            problems.append(
                "field %r must be an uploaded file (form part carried no "
                "filename)" % name)
    return problems


def _template(path: str) -> re.Pattern:
    """`/api/tasks/{task_id}` as a regex that accepts any single segment,
    capturing each one under its own (sanitised) group name so the actual
    value sent can be checked against what the spec says belongs there.
    """
    parts = []
    for chunk in re.split(r"(\{[^}]+\})", path):
        if chunk.startswith("{") and chunk.endswith("}"):
            group = re.sub(r"\W", "_", chunk[1:-1])
            parts.append(r"(?P<%s>[^/]+)" % group)
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "/?$")


class Route:
    def __init__(self, entry: Dict[str, Any], spec: Dict[str, Any]) -> None:
        self.method = entry["method"].upper()
        self.path = entry["path"]
        self.op = entry["op"]
        self.pattern = _template(entry["path"])
        self.spec = spec
        # Path parameters the spec actually types, e.g. `format: uuid` --
        # kept as (real name, sanitised group name, schema) so a captured
        # value can be checked against the real name's declared shape.
        self.path_params: List[Tuple[str, str, Dict[str, Any]]] = []
        for param in self.op.get("parameters") or []:
            param = _deref(param, spec)
            if param.get("in") != "path":
                continue
            name = param.get("name") or ""
            schema = _deref(param.get("schema") or {}, spec)
            if name:
                self.path_params.append(
                    (name, re.sub(r"\W", "_", name), schema))

    def path_errors(self, match: "re.Match") -> List[Dict[str, Any]]:
        """The real API's own error shape for a path segment that does not
        match its declared type -- currently just `format: uuid`, which is
        the one that broke a live run: a leftover placeholder literal
        (`"dummy_project_id_for_update"`) sailed through the mock's routing,
        which accepts any non-empty segment, and only failed once it
        reached the real service's strict UUID parsing. `validate_body`
        already refuses a body that does not match its schema; a path
        parameter is typed the same way in the same document and was the
        one place nothing was checked at all.

        Deliberately narrow: `format: uuid` is unambiguous (a value is a
        UUID or it is not), unlike a body field's looser type coercion,
        so there is no correct client this can reject.
        """
        problems = []
        for name, group, schema in self.path_params:
            if schema.get("format") != "uuid":
                continue
            value = match.group(group)
            if not _UUID_RE.match(value):
                problems.append({
                    "type": "uuid_parsing", "loc": ["path", name],
                    "msg": "Input should be a valid UUID, invalid character: "
                          "expected an optional prefix of `urn:uuid:` "
                          "followed by [0-9a-fA-F-]",
                    "input": value})
        return problems

    def body_schema(self) -> Optional[Dict[str, Any]]:
        content = ((self.op.get("requestBody") or {}).get("content") or {})
        for kind, holder in content.items():
            if "json" in kind:
                return _deref(holder.get("schema"), self.spec)
        return None

    def multipart_schema(self) -> Optional[Dict[str, Any]]:
        """The request body schema for a route declared as
        `multipart/form-data`, if this is one. A route can have this
        instead of (never alongside, in this spec's real usage) a JSON
        `body_schema` -- a file upload has no JSON to parse at all, and
        treating it as one is the exact bug this method exists to let
        `_handle` route around.
        """
        content = ((self.op.get("requestBody") or {}).get("content") or {})
        for kind, holder in content.items():
            if "multipart" in kind:
                return _deref(holder.get("schema"), self.spec)
        return None

    def response_schema(self) -> Tuple[int, Optional[Dict[str, Any]]]:
        responses = self.op.get("responses") or {}
        for code in sorted(responses, key=lambda c: (not c.startswith("2"), c)):
            if not code.startswith("2"):
                continue
            content = (responses[code].get("content") or {})
            for kind, holder in content.items():
                if "json" in kind:
                    return int(code), _deref(holder.get("schema"), self.spec)
            return int(code), None
        return 200, None


def _deref(node: Any, spec: Dict[str, Any], depth: int = 0) -> Any:
    """Follow a `$ref` to the thing it names. Depth-capped, since specs cycle."""
    if depth > 12 or not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = openapi._resolve(ref, spec)
        return _deref(target, spec, depth + 1) if target is not None else {}
    return node


def synthesize(schema: Any, spec: Dict[str, Any], depth: int = 0) -> Any:
    """A value of the shape the spec promises. No cleverness, no state."""
    schema = _deref(schema, spec, depth)
    if not isinstance(schema, dict) or depth > 6:
        return None
    for key in ("anyOf", "oneOf", "allOf"):
        if schema.get(key):
            return synthesize(schema[key][0], spec, depth + 1)
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "object" or "properties" in schema:
        out = {}
        for name, sub in (schema.get("properties") or {}).items():
            out[name] = synthesize(sub, spec, depth + 1)
        return out
    if kind == "array":
        return [synthesize(schema.get("items") or {}, spec, depth + 1)]
    if schema.get("format") == "uuid":
        return "00000000-0000-4000-8000-000000000000"
    if kind in _SAMPLE:
        return _SAMPLE[kind]
    return "mock"


_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_body(schema: Optional[Dict[str, Any]], body: Any,
                  spec: Dict[str, Any]) -> List[str]:
    """Required fields present, and typed as declared. Nothing further.

    Deliberately shallow on everything the spec does not state outright.
    A mock that enforced invented constraints would reject correct clients,
    which is the expensive direction of error here -- the repair loop would
    dutifully rewrite working code to satisfy a rule the real service does
    not have.
    """
    if schema is None:
        return []
    schema = _deref(schema, spec)
    if not isinstance(schema, dict):
        return []
    problems: List[str] = []
    if schema.get("type") == "object" or "properties" in schema:
        if not isinstance(body, dict):
            return ["body must be a JSON object, got %s"
                    % type(body).__name__]
        for name in schema.get("required") or []:
            if name not in body:
                problems.append("missing required field %r" % name)
        props = schema.get("properties") or {}
        for name, value in body.items():
            sub = _deref(props.get(name), spec)
            if not isinstance(sub, dict) or value is None:
                continue
            kind = sub.get("type")
            if isinstance(kind, list):
                continue
            want = _TYPES.get(kind)
            # bool is an int in Python; the spec means them as distinct.
            if want and (not isinstance(value, want)
                         or (kind != "boolean" and isinstance(value, bool))):
                problems.append("field %r should be %s, got %s"
                                % (name, kind, type(value).__name__))
    return problems


def _stem(word: str) -> str:
    """A crude singular, good enough for a substring match against a path.

    Matching `openapi.py`'s own de-pluralising rule would be overkill here
    -- this only ever has to line up "units" (from the request) against
    "units" (already sitting in a path segment almost verbatim), not
    resolve an ambiguous REST convention.
    """
    w = word.lower()
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("ses"):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _count_in_body(body: Any) -> Optional[int]:
    """How many items a bulk-write request body actually carried.

    Deliberately shallow and best-effort: an import/create-many payload in
    this API is either a bare JSON array, or an object with exactly one
    array-valued field (`{"units": [...]}`). A body with more than one
    array field is ambiguous about which one is "the items", so nothing is
    guessed -- reporting a count from the wrong field would be a worse
    failure than reporting none at all.
    """
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict):
        lists = [v for v in body.values() if isinstance(v, list)]
        if len(lists) == 1:
            return len(lists[0])
    return None


class Mock:
    """The routing table and the call log. Shared by every request thread."""

    def __init__(self, spec: Dict[str, Any]) -> None:
        self.spec = spec
        self.routes = [Route(e, spec) for e in openapi.operations(spec)]
        self.calls: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def match(self, method: str, path: str
              ) -> Tuple[Optional[Route], Optional["re.Match"]]:
        for route in self.routes:
            if route.method == method:
                m = route.pattern.match(path)
                if m:
                    return route, m
        return None, None

    def near(self, path: str, limit: int = 4) -> List[str]:
        """Real endpoints resembling one that does not exist.

        A 404 saying only "not found" sends the repair loop guessing. Naming
        the routes that share a prefix turns it into an instruction, the same
        way `crossref` reports what a module actually defines.
        """
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        scored = []
        for route in self.routes:
            shared = sum(1 for s in segments if s in route.path)
            if shared:
                scored.append((shared, "%s %s" % (route.method, route.path)))
        scored.sort(key=lambda t: -t[0])
        seen: List[str] = []
        for _, label in scored:
            if label not in seen:
                seen.append(label)
            if len(seen) >= limit:
                break
        return seen

    def record(self, **kw: Any) -> None:
        with self.lock:
            kw.setdefault("component", getattr(self, "current", "") or "")
            self.calls.append(kw)

    def rejected_for(self, component: str, since: int = 0) -> List[str]:
        """This component's refused requests, as repair instructions.

        The mock knows exactly why it said no -- which field was missing,
        which type was wrong, which endpoint does not exist -- and the
        traceback the gate sees says only `HTTP Error 422: Unprocessable
        Entity`. Measured live: three repair attempts on one file, each told
        nothing but the status line, against a mock that had already written
        `missing required field 'version'` on screen.
        """
        with self.lock:
            recent = self.calls[since:]
        out: List[str] = []
        for call in recent:
            if call.get("ok") or (component and call.get("component")
                                  and call["component"] != component):
                continue
            line = "%s %s -> %s %s" % (call.get("method"), call.get("path"),
                                       call.get("status"),
                                       call.get("detail") or "")
            if line not in out:
                out.append(line.rstrip())
        return out

    def _touched_totals(self, component: str, since: int,
                        resources: Iterable[str]
                        ) -> Tuple[Dict[str, bool], Dict[str, int], Dict[str, str]]:
        """Shared scan behind `count_problems_for` and `zero_problems_for`:
        which of `resources` this component's own traffic actually named in
        a path, and how many items its successful calls carried.

        A resource this component's own traffic never came near at all is
        not either check's business -- `count_check` is wired for every
        component the same way `rejected_for` is, and most of them will
        legitimately never call an import endpoint during their own
        isolated smoke run. Only a resource *touched* (whether the call
        succeeded or not) is judged, so silence here means "not
        applicable", not "satisfied".
        """
        with self.lock:
            recent = self.calls[since:]
        touched: Dict[str, bool] = {}
        totals: Dict[str, int] = {}
        via: Dict[str, str] = {}
        for call in recent:
            if (component and call.get("component")
                    and call["component"] != component):
                continue
            path = (call.get("path") or "").lower()
            for resource in resources:
                stem = _stem(resource)
                if not (stem and stem in path):
                    continue
                touched[resource] = True
                via.setdefault(resource, call.get("path") or "")
                if call.get("ok"):
                    count = _count_in_body(call.get("body"))
                    if count is not None:
                        totals[resource] = totals.get(resource, 0) + count
        return touched, totals, via

    def count_problems_for(self, component: str, since: int,
                           expected: Dict[str, int]) -> List[str]:
        """Did the code send as many items as the request asked for.

        `rejected_for` catches a call the real service would refuse
        outright. This catches the call it would happily accept, at the
        wrong size -- a 200 from an import endpoint says nothing about how
        many rows earned it. Measured live: "uploads 3 units to it"
        produced a task holding 2, and every gate passed clean, because
        nothing was reading the body of a call that had already succeeded.

        A resource with no `expected` count, or one whose name never
        matches any call this component made, is silently skipped -- this
        only ever reports a mismatch against a call it can actually see,
        never invents a requirement the request did not connect to
        anything real.
        """
        if not expected:
            return []
        touched, totals, via = self._touched_totals(component, since, expected)
        problems = []
        for resource, needed in expected.items():
            if not touched.get(resource):
                continue
            got = totals.get(resource, 0)
            if got == needed:
                continue
            problems.append(
                "the request asked for %d %s; %s actually sent %d"
                % (needed, resource, via[resource], got))
        return problems

    def zero_problems_for(self, component: str, since: int,
                          resources: Iterable[str]) -> List[str]:
        """Did a bulk call the request implied would carry items carry none.

        `count_problems_for` only fires when the request pins an exact
        number to a resource ("3 units"), because `required_counts` has
        nothing to check without one. "upload units to the task" pins no
        number at all, and that gap let a real, live-run file post
        `{"units": []}` -- a literal placeholder, commented as one -- to
        the real import endpoint. The call succeeded (an empty array is a
        valid payload), `gate_smoke` saw no crash, and the task was left
        holding zero units while every gate reported clean.

        Deliberately a floor, not a target: this only ever fires when the
        total sent is exactly zero, so it can never reject a real payload
        no matter how many items it actually carried, and it never invents
        a number to compare against the way a false `expected` entry
        would. `resources` is expected to already exclude anything
        `required_counts` pinned a real number to -- that check is
        stricter and already covers those.
        """
        resources = list(resources)
        if not resources:
            return []
        touched, totals, via = self._touched_totals(component, since, resources)
        problems = []
        for resource in resources:
            if not touched.get(resource):
                continue
            if totals.get(resource, 0) > 0:
                continue
            problems.append(
                "the request asks to send %s, but %s sent a payload with "
                "zero of them -- an empty list satisfies the mock without "
                "creating anything; synthesize at least one real item "
                "matching the schema instead of an empty placeholder"
                % (resource, via[resource]))
        return problems


class Handler(BaseHTTPRequestHandler):
    mock: Mock = None                      # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass                               # quiet; the call log is the record

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        raw_path = self.path.split("?", 1)[0]
        # Set by the gate's sitecustomize. Components are generated in
        # parallel against one mock, so without this the log cannot say who
        # sent a request -- and a rejection attributed to the wrong file is
        # a repair instruction that sends a writer chasing someone else's bug.
        self.mock.current = self.headers.get("X-Swarm-Component") or ""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        route, path_match = self.mock.match(method, raw_path)
        if route is None:
            near = self.mock.near(raw_path)
            self.mock.record(method=method, path=raw_path, status=404,
                             ok=False, detail="no such endpoint")
            self._send(404, {
                "detail": "no such endpoint: %s %s" % (method, raw_path),
                "did_you_mean": near})
            return

        # Checked before the body, matching the real service: a malformed
        # path segment (a UUID field holding a literal like
        # "dummy_project_id_for_update") is rejected before anything about
        # the body is even looked at.
        path_problems = route.path_errors(path_match)
        if path_problems:
            self.mock.record(
                method=method, path=route.path, status=422, ok=False,
                detail="; ".join("%s: %s" % (p["loc"][-1], p["msg"])
                                 for p in path_problems))
            self._send(422, {"detail": path_problems})
            return

        # A route declared as `multipart/form-data` has no JSON body at
        # all -- a genuinely correct file upload is never going to parse
        # as JSON, no matter how many times a repair loop retries it, so
        # this has to branch before any `json.loads` is even attempted.
        # The unconditional parse attempt that used to sit here rejected
        # every correct multipart upload as "body was not valid JSON",
        # indistinguishable from the real `data=`-vs-`json=` misuse case
        # below, which sent a repair loop chasing a fix that could never
        # apply to code that never touched JSON in the first place.
        mp_schema = route.multipart_schema()
        if mp_schema is not None:
            fields = parse_multipart_fields(raw, content_type) if raw else None
            mp_problems = validate_multipart_body(mp_schema, fields,
                                                  self.mock.spec)
            if mp_problems:
                self.mock.record(method=method, path=route.path, status=422,
                                 ok=False, detail="; ".join(mp_problems))
                self._send(422, {"detail": mp_problems})
                return
            code, schema = route.response_schema()
            self.mock.record(method=method, path=route.path, status=code,
                             ok=True, detail="", body=fields)
            self._send(code, synthesize(schema, self.mock.spec)
                      if schema is not None else {"ok": True})
            return

        body: Any = None
        bad_json = False
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                bad_json = True

        if bad_json:
            # "body was not valid JSON" alone repeats the traceback's own
            # status line and tells a repair attempt nothing it didn't
            # already know. The one recurring cause here is `requests.post(
            # url, data=a_dict)` instead of `json=a_dict` -- `data=` on a
            # dict form-encodes it (`name=Foo&applet_id=...`) and sends no
            # `application/json` content-type, so the bytes that arrive are
            # never going to parse as JSON no matter how many times the loop
            # retries. That shape is decidable from the request actually
            # received, which is a fact the mock has and the model does not:
            # it never sees its own serialized bytes, only the exception
            # `requests.raise_for_status()` raises three calls later.
            content_type = self.headers.get("Content-Type", "")
            preview = raw[:200].decode("utf-8", errors="replace")
            looks_form_encoded = (
                "form-urlencoded" in content_type.lower()
                or (raw[:1] not in (b"{", b"[") and b"=" in raw))
            hint = ""
            if looks_form_encoded:
                hint = (" -- this looks like form-encoded data (e.g. "
                        "'name=Foo&applet_id=...'), not JSON. If this came "
                        "from the `requests` library, the payload was almost "
                        "certainly passed as `data=a_dict` instead of "
                        "`json=a_dict`: `data=` on a dict form-encodes it and "
                        "sends no `application/json` content-type, while "
                        "`json=` serializes it correctly and sets the header.")
            detail = ("body was not valid JSON (content-type: %r, first 200 "
                      "bytes received: %r)%s" % (content_type, preview, hint))
            self.mock.record(method=method, path=route.path, status=400,
                             ok=False, detail=detail)
            self._send(400, {"detail": "request body was not valid JSON"})
            return

        problems = validate_body(route.body_schema(), body, self.mock.spec)
        if problems:
            self.mock.record(method=method, path=route.path, status=422,
                             ok=False, detail="; ".join(problems))
            self._send(422, {"detail": problems})
            return

        code, schema = route.response_schema()
        # The body is kept on a successful call, not just a rejected one --
        # `rejected_for` never needed it because a refusal's *reason* is the
        # whole story, but `count_problems_for` (below) has to know how many
        # items a 200 was actually earned by, and "it returned 200" cannot
        # say that on its own.
        self.mock.record(method=method, path=route.path, status=code, ok=True,
                         detail="", body=body)
        self._send(code, synthesize(schema, self.mock.spec)
                   if schema is not None else {"ok": True})

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_HEAD(self) -> None:
        self._handle("HEAD")


def serve(spec: Dict[str, Any], port: int = 0
          ) -> Tuple[ThreadingHTTPServer, Mock, str]:
    """Start on loopback in a background thread. Returns (server, mock, url).

    Loopback specifically, not 0.0.0.0: this exists to be reachable from a
    gate on this machine and from nowhere else. Port 0 lets the OS choose,
    so concurrent runs never collide on a fixed number.
    """
    mock = Mock(spec)
    handler = type("BoundHandler", (Handler,), {"mock": mock})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, mock, "http://127.0.0.1:%d" % server.server_address[1]


def summarise(mock: Mock) -> str:
    """What the code under test actually sent, and what came back."""
    if not mock.calls:
        return "  the code made no API calls"
    lines = []
    for call in mock.calls:
        mark = "ok  " if call["ok"] else "FAIL"
        lines.append("    %s %-6s %-46s %s%s"
                     % (mark, call["method"], call["path"], call["status"],
                        ("  " + call["detail"]) if call["detail"] else ""))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve a fake API built from an OpenAPI document.")
    parser.add_argument("spec", help="a registered name, or a path")
    parser.add_argument("--port", type=int, default=0,
                        help="0 lets the OS pick (default)")
    parser.add_argument("--log", help="write the call log here on exit")
    args = parser.parse_args(argv)

    try:
        spec = openapi.load(openapi.resolve(args.spec))
    except (OSError, ValueError) as exc:
        sys.stderr.write("cannot read %s: %s\n" % (args.spec, exc))
        return 1

    server, mock, url = serve(spec, args.port)
    sys.stderr.write("%s serving %d endpoints from %s\n  point your client "
                     "at it with API_BASE_URL=%s\n  ctrl-c to stop\n"
                     % (url, len(mock.routes), args.spec, url))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        sys.stderr.write("\n%s\n" % summarise(mock))
        if args.log:
            with open(args.log, "w", encoding="utf-8") as fh:
                json.dump(mock.calls, fh, indent=2)
            sys.stderr.write("wrote %s\n" % args.log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
