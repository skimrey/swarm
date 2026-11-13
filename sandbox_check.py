#!/usr/bin/env python3
"""Hand-written check for the three pieces that make running generated code safe.

The gates execute what the pool writes -- `gate_import` runs module level,
`gate_smoke` runs `main`. That is the only automatic gate that observes
behaviour rather than shape, and it is also the one that can POST to a live
service. Three things make it safe, and each is checked here:

1. `codegen`'s network guard confines gate subprocesses to loopback.
2. `intent.WRITABLE` and `codegen.gate_syntax` allow non-Python files while
   refusing types nothing can check -- so "passed" never covers a file that
   was never examined.
3. `mockapi` answers on loopback from the real spec, and rejects what the
   real service would reject.

Offline throughout: no pool, no LAN, no registered spec required.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

import codegen
import intent
import mockapi
import swarm

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- " + str(detail)) if detail else ""))
        FAILURES.append(name)


def write(root, name, body):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


# ---------------------------------------------------------------------------
print("the network guard, which is what makes gate_smoke safe for a client")

ROOT = tempfile.mkdtemp(prefix="sandbox-check-")
try:
    # A real remote address. Nothing needs to be listening: the guard has to
    # refuse before the connection is even attempted, so this cannot hang
    # and cannot depend on what is up on the LAN.
    write(ROOT, "reach_out.py",
          "import socket\n"
          "socket.create_connection(('10.0.0.190', 11434), timeout=3)\n"
          "print('CONNECTED')\n")
    ok, out = codegen.run_command("%s reach_out.py" % sys.executable, ROOT,
                                  timeout=30)
    check("a gate cannot reach a remote host",
          "network blocked while testing" in out, out[-160:])
    check("...and the refusal names the address it stopped",
          "10.0.0.190" in out, out[-160:])
    check("...and it is not reported as success", not ok)

    # Loopback has to keep working or the mock below is unreachable from a
    # gate, which would make the whole arrangement pointless.
    write(ROOT, "reach_local.py",
          "import socket\n"
          "s = socket.socket()\n"
          "try:\n"
          "    s.connect(('127.0.0.1', 1))\n"
          "except ConnectionRefusedError:\n"
          "    print('REACHED THE STACK')\n")
    ok, out = codegen.run_command("%s reach_local.py" % sys.executable, ROOT,
                                  timeout=30)
    check("loopback is still allowed, so a local stand-in is reachable",
          "REACHED THE STACK" in out, out[-160:])

    # The escape hatch has to work, or someone who genuinely wants a live
    # call has no way to say so and will disable the guard permanently.
    ok, out = codegen.run_command("%s reach_out.py" % sys.executable, ROOT,
                                  timeout=30,
                                  env_extra={"SWARM_ALLOW_NETWORK": "1"})
    check("SWARM_ALLOW_NETWORK lifts the block",
          "network blocked while testing" not in out, out[-160:])

    # Refusing at connect() only ever sees what the resolver returned, so
    # the message named an anonymous CDN address and any client reaching
    # the network by a route not ending in socket.socket.connect went
    # straight through. Refusing the lookup is earlier and still knows the
    # name. `.invalid` is reserved by RFC 2606 and resolves nowhere, so a
    # failure here is the guard and never DNS.
    write(ROOT, "resolve_out.py",
          "import socket\n"
          "socket.getaddrinfo('api.example.invalid', 443)\n"
          "print('RESOLVED')\n")
    ok, out = codegen.run_command("%s resolve_out.py" % sys.executable, ROOT,
                                  timeout=30)
    check("a hostname is refused before it is resolved",
          "network blocked while testing" in out, out[-200:])
    check("...and the refusal names the host, not an address",
          "api.example.invalid" in out, out[-200:])

    # Loopback by what the address means. A set of literal strings answers
    # "is this 127.0.0.1" and reads the rest of 127/8 as remote; harmless
    # here, but the same spelling assumption is what made the guard depend
    # on which form a resolver handed back.
    write(ROOT, "reach_other_loopback.py",
          "import socket\n"
          "for host in ('127.0.0.2', '::1', 'localhost'):\n"
          "    s = socket.socket(socket.AF_INET6 if ':' in host\n"
          "                      else socket.AF_INET)\n"
          "    try:\n"
          "        s.connect((host, 1))\n"
          "    except ConnectionRefusedError:\n"
          "        print('REACHED %s' % host)\n"
          "    except OSError as e:\n"
          "        print('BLOCKED %s: %s' % (host, e))\n")
    ok, out = codegen.run_command(
        "%s reach_other_loopback.py" % sys.executable, ROOT, timeout=30)
    check("the whole loopback range is allowed, not one spelling of it",
          "network blocked while testing" not in out, out[-200:])

    # ----------------------------------------------------------------------
    print("\nthe environment a gate runs in names no live host")

    real = "https://dztfdhkmj6wqi.cloudfront.net"
    moved = codegen.local_urls({"API_BASE_URL": real, "API_KEY": "secret"})
    check("a real base URL does not survive into a gate",
          moved.get("API_BASE_URL") == "http://127.0.0.1:1", moved)
    check("only URLs are touched, not the key beside them",
          "API_KEY" not in moved, moved)

    mock = "http://127.0.0.1:54321"
    moved = codegen.local_urls({"API_BASE_URL": mock, "MAVEN_UI_URL": real})
    check("a loopback base is left alone, so the mock still works",
          "API_BASE_URL" not in moved, moved)
    check("...and becomes the target for the other URLs",
          moved.get("MAVEN_UI_URL") == mock, moved)

    moved = codegen.local_urls({"DATA_URI": "s3://bucket/key",
                                "EMPTY_URL": "",
                                "PAGE_URL": "http://localhost:8080/x"})
    check("a scheme with no host to confine is untouched",
          moved == {}, moved)

    # End to end: the parent process has the operator's real value, and the
    # child must not. This is the case that actually happened -- no --api,
    # so nothing overrode API_BASE_URL and a smoke run was handed the live
    # platform along with the key to talk to it.
    write(ROOT, "show_base.py",
          "import os\n"
          "print('BASE=' + os.environ.get('API_BASE_URL', '<unset>'))\n")
    ok, out = codegen.run_command("%s show_base.py" % sys.executable, ROOT,
                                  timeout=30,
                                  env_extra={"API_BASE_URL": real})
    check("a gate subprocess never sees the live base URL",
          real not in out, out[-160:])
    check("...it sees loopback instead", "BASE=http://127.0.0.1" in out,
          out[-160:])

    ok, out = codegen.run_command(
        "%s show_base.py" % sys.executable, ROOT, timeout=30,
        env_extra={"API_BASE_URL": real, "SWARM_ALLOW_NETWORK": "1"})
    check("the escape hatch lifts this too, or it is not an escape hatch",
          real in out, out[-160:])

    # ----------------------------------------------------------------------
    print("\nfile types: checked, or openly not generated")

    CASES = [
        (".py  valid", "m.py", "def f():\n    return 1\n", True),
        (".py  broken", "m.py", "def f(\n", False),
        (".json valid", "d.json", '{"a": 1}', True),
        (".json broken", "d.json", "{'a': 1}", False),
        (".html valid", "p.html", "<html><body><p>x</p></body></html>", True),
        (".html unclosed", "p.html", "<html><body><p>x</body></html>", False),
        (".html void tags", "p.html", "<div><br><img src=x></div>", True),
        (".css  anything", "s.css", "body{color:red}", True),
        (".md   anything", "n.md", "# notes", True),
        ("empty is never ok", "m.py", "   ", False),
    ]
    for label, name, body, want_ok in CASES:
        got = codegen.gate_syntax(body, name)
        check("%-18s %s" % (label, "accepted" if want_ok else "rejected"),
              (got is None) == want_ok, got or "accepted")

    # The one that would quietly lie: `<` inside a script is a comparison,
    # not a tag. A detector firing on correct input costs more than it saves.
    check("a comparison inside <script> is not read as markup",
          codegen.gate_syntax(
              "<html><script>if (a < b && c > d) { go(); }</script></html>",
              "p.html") is None)

    # JavaScript, which a survey page is mostly made of. Checked with node
    # when node exists, and not at all when it does not -- so everything
    # below is conditional on that, or it would fail on a machine without it.
    HAVE_NODE = codegen._node() is not None
    print("  (node %s)" % ("found: JavaScript is syntax-checked"
                           if HAVE_NODE else "absent: JS checks skipped"))
    if HAVE_NODE:
        JS = [
            ("a valid script", "app.js",
             "const a=1;\nfunction f(){return `${a}`;}\n", True),
            ("a truncated script", "app.js", "function broken( {\n", False),
            ("module syntax", "app.js",
             "import x from 'y';\nexport const a=1;\n", True),
            # A hand-rolled brace counter fails this, which is why there is
            # no fallback when node is missing.
            ("a regex literal containing a brace", "app.js",
             "const ok = /\\}/.test('x');\n", True),
            ("broken JS inside an HTML page", "p.html",
             "<html><body>\n<script>\nfunction submit( {\n</script></body>"
             "</html>", False),
            ("good JS inside an HTML page", "p.html",
             "<html><body>\n<script>\nconst q=['age'];\n"
             "q.forEach(n=>{ if (n.length < 5) console.log(n); });\n"
             "</script></body></html>", True),
            # Three ways a <script> holds something that is not JavaScript.
            # Each would be a false positive, the expensive kind.
            ("JSON parked in a script tag", "p.html",
             '<html><script type="application/json">{"a":1}</script></html>',
             True),
            ("a script loaded from src has no body to check", "p.html",
             '<html><script src="app.js"></script></html>', True),
            ("a templated page is left alone", "p.html",
             "<html><script>const n = {{ count }};</script></html>", True),
        ]
        for label, name, body, want_ok in JS:
            got = codegen.gate_syntax(body, name)
            check("%-42s %s" % (label, "accepted" if want_ok else "rejected"),
                  (got is None) == want_ok, got or "accepted")
        check(".js may be planned", ".js" in intent.WRITABLE)

    # Allowing a file type is only half of it. The writer has to be asked for
    # that language and its reply has to survive extraction -- a pattern that
    # only knew ```python read a correct ```html answer as no code block.
    print("\nthe reason a request was refused reaches the repair loop")
    # The gap: a traceback says `HTTP Error 422: Unprocessable Entity` and
    # the mock knows it was `missing required field 'version'`. Measured
    # live, three repair attempts on one file were each told only the status
    # line, against a mock that had already printed the field name on screen.
    import tempfile as _tf
    work = _tf.mkdtemp(prefix="reject-")
    client = os.path.join(work, "m.py")
    with open(client, "w", encoding="utf-8") as fh:
        fh.write('import os, json, urllib.request\n'
                 'def main():\n'
                 '    b = os.environ["API_BASE_URL"]\n'
                 '    r = urllib.request.Request(b + "/api/applets/a/versions",\n'
                 '        method="POST", data=json.dumps({"name": "x"}).encode(),\n'
                 '        headers={"Content-Type": "application/json"})\n'
                 '    urllib.request.urlopen(r)\n'
                 'if __name__ == "__main__":\n    main()\n')
    # A spec of its own, so this section does not depend on ordering
    # elsewhere in the file. One endpoint, one required field.
    REJECT_SPEC = {
        "openapi": "3.1.0",
        "paths": {
            "/api/applets/{applet_id}/versions": {
                "post": {
                    "operationId": "create_version",
                    "requestBody": {"content": {"application/json": {
                        "schema": {"type": "object",
                                   "required": ["version"],
                                   "properties": {
                                       "name": {"type": "string"},
                                       "version": {"type": "string"}}}}}},
                    "responses": {"201": {"content": {"application/json": {
                        "schema": {"type": "object", "properties": {
                            "id": {"type": "string"}}}}}}},
                }
            },
            "/api/projects": {
                "post": {"operationId": "create_project",
                         "responses": {"201": {"content": {
                             "application/json": {"schema": {
                                 "type": "object"}}}}}}
            },
        },
    }
    rej_server, mock, base = mockapi.serve(REJECT_SPEC)
    mark = len(mock.calls)
    smoke_err = codegen.gate_smoke(client, work,
                                   env_extra={"API_BASE_URL": base,
                                              "SWARM_COMPONENT": "m.py"})
    check("the gate still fails on a rejected request", smoke_err is not None)
    check("the traceback alone does not name the field",
          "version" not in (smoke_err or ""), smoke_err)
    refused = mock.rejected_for("m.py", mark)
    check("the mock supplies the field name",
          any("version" in line for line in refused), refused)
    check("the call is attributed to the component that sent it",
          all(c.get("component") == "m.py" for c in mock.calls[mark:]),
          [c.get("component") for c in mock.calls[mark:]])

    # The isolation that makes this safe to feed a writer. Components are
    # generated in parallel against one mock, and quoting another file's
    # rejection at this one is a confident wrong repair instruction.
    other = os.path.join(work, "other.py")
    with open(other, "w", encoding="utf-8") as fh:
        fh.write(open(client, encoding="utf-8").read()
                 .replace("/api/applets/a/versions", "/api/projects/create"))
    codegen.gate_smoke(other, work, env_extra={"API_BASE_URL": base,
                                               "SWARM_COMPONENT": "other.py"})
    check("one component is not told about another's failure",
          all("projects/create" not in line
              for line in mock.rejected_for("m.py", mark)),
          mock.rejected_for("m.py", mark))
    check("the other component is told about its own",
          any("projects/create" in line
              for line in mock.rejected_for("other.py", mark)),
          mock.rejected_for("other.py", mark))
    # The pieces above are necessary and not sufficient: what matters is that
    # generate_file folds the reason into the error the *next attempt* reads.
    # Stubbed pool, so this stays offline.
    BAD = ("```python\n" + open(client, encoding="utf-8").read() + "```")
    real_chat = swarm.chat_complete
    swarm.chat_complete = lambda *a, **k: {
        "text": BAD, "raw": {"done_reason": "stop"},
        "sample": {"prompt_tokens": 10, "eval_tokens": 10,
                   "total_seconds": 0.1}}
    try:
        out = os.path.join(work, "written.py")
        cfg = swarm.Config.load()
        state = swarm.State(swarm.STATE_PATH)
        node = cfg.node("mac")
        out = os.path.join(work, "written.py")
        res = codegen.generate_file(
            "a client", out, node, "stub-model", cfg, state,
            attempts=1, verbose=False, time_budget=None,
            env_extra={"API_BASE_URL": base},
            rejected=lambda since: mock.rejected_for("written.py", since),
            sent_count=lambda: len(mock.calls))
    finally:
        swarm.chat_complete = real_chat
    told = (res["history"][-1].get("error") or "") if res["history"] else ""
    check("the repair loop is handed the reason, not just the status",
          "missing required field" in told, told[-160:])
    check("it is labelled as coming from the API, not invented",
          "the real service would have said" in told, told[-160:])

    # A rejection is not always a crash. `BAD` above raises because urlopen
    # throws on a non-2xx response; code that checks status leniently, or
    # simply never calls `raise_for_status`, can reach the same rejected
    # endpoint and still exit 0 -- and that used to read as a clean pass,
    # with the defect sitting only in a log line nothing was reading.
    # Measured live: a generated `get_ui_url` called `GET /api/projects/
    # {id}/my-ui-url`, which is nowhere in the real spec, `main` did not
    # crash, and the run reported "3/3 passed every gate".
    QUIET = ("```python\n"
             "import urllib.request\n"
             "def main():\n"
             "    try:\n"
             "        urllib.request.urlopen('%s/api/projects/x/my-ui-url')\n"
             "    except Exception:\n"
             "        pass\n"
             "    print('done')\n"
             "if __name__ == '__main__':\n"
             "    main()\n"
             "```") % base
    swarm.chat_complete = lambda *a, **k: {
        "text": QUIET, "raw": {"done_reason": "stop"},
        "sample": {"prompt_tokens": 10, "eval_tokens": 10,
                   "total_seconds": 0.1}}
    try:
        quiet_out = os.path.join(work, "quiet.py")
        quiet_res = codegen.generate_file(
            "a client", quiet_out, node, "stub-model", cfg, state,
            attempts=1, verbose=False, time_budget=None,
            env_extra={"API_BASE_URL": base},
            rejected=lambda since: mock.rejected_for("quiet.py", since),
            sent_count=lambda: len(mock.calls))
    finally:
        swarm.chat_complete = real_chat
    check("a rejected call that does not crash still fails the file",
          not quiet_res["ok"], quiet_res)
    quiet_told = ((quiet_res["history"][-1].get("error") or "")
                 if quiet_res["history"] else "")
    check("...naming what was rejected, not just that something was",
          "my-ui-url" in quiet_told and "404" in quiet_told, quiet_told)

    # The other half of the same distinction: no traffic at all is still a
    # pass, because a client legitimately calling nothing from `main` is not
    # a defect -- only a call the mock actually refused is.
    EMPTY = ("```python\n"
             "def main():\n"
             "    print('done')\n"
             "if __name__ == '__main__':\n"
             "    main()\n"
             "```")
    swarm.chat_complete = lambda *a, **k: {
        "text": EMPTY, "raw": {"done_reason": "stop"},
        "sample": {"prompt_tokens": 10, "eval_tokens": 10,
                   "total_seconds": 0.1}}
    try:
        empty_out = os.path.join(work, "empty.py")
        empty_res = codegen.generate_file(
            "a client", empty_out, node, "stub-model", cfg, state,
            attempts=1, verbose=False, time_budget=None,
            env_extra={"API_BASE_URL": base},
            rejected=lambda since: mock.rejected_for("empty.py", since),
            sent_count=lambda: len(mock.calls))
    finally:
        swarm.chat_complete = real_chat
    check("calling nothing from main is still a pass", empty_res["ok"],
          empty_res)

    rej_server.shutdown()

    print("\nnon-Python components reach the writer and come back")
    # The gap this closed: the parser accepted .html for a week while the
    # plan format still said "relative path ending in .py", so the planner
    # never asked for one and a survey page came back as a Python string.
    check("the planner is told which types it may ask for",
          ".html" in intent.DECOMPOSE_PROMPT
          and "ending in .py" not in intent.DECOMPOSE_PROMPT)
    # The gap this closed: a contract asked for `get_ui_url(base_url,
    # project_id) -> str` when nothing in the real API creates or returns
    # one, and the planner had no instruction telling it a CONTRACT method
    # implying a network call must correspond to a real listed operation.
    check("the planner is told not to invent a contract method with no "
          "matching endpoint",
          "must correspond to a real listed" in intent.DECOMPOSE_PROMPT
          and "get_ui_url" in intent.DECOMPOSE_PROMPT)
    # The gap this closed: the invented-endpoint fix above made get_ui_url a
    # correct local-computation spec, and the CONTRACT still nested it
    # under `Client(...) with`, so one writer read the grouping and made it
    # a bound method while the other read the self-less signature and
    # called it as a module-level function -- a real interface mismatch
    # `gate_interfaces` caught every attempt, on a fix that was never in
    # the failing file to make.
    check("the planner is told a self-less shared function does not "
          "belong inside a class's `with` clause",
          "does not belong inside" in intent.DECOMPOSE_PROMPT
          and "no\n`self`" in intent.DECOMPOSE_PROMPT)
    check("a file is asked for in its own language",
          codegen.language_for("survey.html") == ("HTML", "html")
          and codegen.language_for("a.py") == ("Python", "python"))
    check("an unknown extension falls back to Python",
          codegen.language_for("x.weird") == ("Python", "python"))
    EXTRACT = [
        ("an html reply in an html fence",
         "```html\n<html><body>hi</body></html>\n```", "html", "<html>"),
        # Matching every tag is what makes html work, and is also what makes
        # a how-to-run block a rival for the answer. The tag decides.
        ("a bash block does not beat the html",
         "```bash\nopen survey.html\n```\n```html\n<html><p>x</p></html>\n```",
         "html", "<html>"),
        ("a bash block does not beat the python",
         "```bash\npython3 go.py\n```\n```python\nimport os\n```",
         "python", "import os"),
        ("an untagged fence still works for python",
         "```\nx = 1\n```", "python", "x = 1"),
        # Prose saved as .html would pass the nesting gate and ship. One
        # wasted repair attempt is much cheaper than that.
        ("prose is not saved as an html file",
         "Sure! Here is your survey page.", "html", ""),
    ]
    for label, reply, fence, expect in EXTRACT:
        got = codegen.extract_code(reply, fence)
        check(label, (expect in got) if expect else got == "", got[:80])

    PLAN = ("COMPONENT: page\nFILE: survey.html\nFLOOR: 2\nDEPS: none\n"
            "SPEC: the survey page\n\n"
            "COMPONENT: style\nFILE: survey.css\nFLOOR: 2\nDEPS: none\n"
            "SPEC: styling\n\n"
            "COMPONENT: cfg\nFILE: settings.yaml\nFLOOR: 2\nDEPS: none\n"
            "SPEC: config\n\n"
            "COMPONENT: client\nFILE: client.py\nFLOOR: 4\nDEPS: none\n"
            "SPEC: push it\n")
    kept = {c.path for c in intent.parse_components(PLAN)}
    check("a plan may now contain .html and .css",
          {"survey.html", "survey.css"} <= kept, sorted(kept))
    check("a type with no gate is refused, not written unchecked",
          "settings.yaml" not in kept, sorted(kept))
    check("Python is unaffected", "client.py" in kept, sorted(kept))
    check("every writable type has a gate decision",
          all(isinstance(e, str) and e.startswith(".")
              for e in intent.WRITABLE))

    # ----------------------------------------------------------------------
    print("\nthe mock API rejects what the real one would reject")

    SPEC = {
        "openapi": "3.1.0",
        "info": {"title": "Probe", "version": "1"},
        "paths": {
            "/api/applets": {"post": {
                "operationId": "create_applet", "tags": ["Applets"],
                "summary": "Create Applet",
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {
                        "$ref": "#/components/schemas/AppletCreate"}}}},
                "responses": {"201": {"description": "ok", "content": {
                    "application/json": {"schema": {
                        "$ref": "#/components/schemas/Applet"}}}}}}},
            "/api/projects/{project_id}": {"get": {
                "operationId": "get_project", "tags": ["Projects"],
                "summary": "Get Project",
                "responses": {"200": {"description": "ok", "content": {
                    "application/json": {"schema": {
                        "$ref": "#/components/schemas/Applet"}}}}}}},
        },
        "components": {"schemas": {
            "AppletCreate": {"type": "object",
                             "required": ["name", "html"],
                             "properties": {
                                 "name": {"type": "string"},
                                 "html": {"type": "string"},
                                 "version": {"type": "integer"}}},
            "Applet": {"type": "object", "properties": {
                "id": {"type": "string", "format": "uuid"},
                "name": {"type": "string"},
                "version": {"type": "integer"}}},
        }},
    }

    server, mock, url = mockapi.serve(SPEC)

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    try:
        status, body = call("POST", "/api/applets",
                            {"name": "survey", "html": "<p>x</p>"})
        check("a well-formed request is accepted", status == 201, status)
        check("...and the reply has the shape the spec promises",
              isinstance(body, dict) and set(body) == {"id", "name", "version"},
              body)
        check("...typed as the spec declares",
              isinstance(body.get("version"), int)
              and isinstance(body.get("name"), str), body)

        status, body = call("POST", "/api/applets/create", {"name": "x"})
        check("an invented endpoint is refused", status == 404, status)
        check("...and the refusal names real endpoints to use instead",
              any("/api/applets" in s for s in body.get("did_you_mean", [])),
              body.get("did_you_mean"))

        status, body = call("POST", "/api/applets", {"name": "survey"})
        check("a missing required field is refused", status == 422, status)
        check("...and it names the field",
              any("html" in d for d in body.get("detail", [])), body)

        status, body = call("POST", "/api/applets",
                            {"name": "survey", "html": "<p>x</p>",
                             "version": "one"})
        check("a wrongly typed field is refused", status == 422, status)
        check("...and it names the field and the expected type",
              any("version" in d and "integer" in d
                  for d in body.get("detail", [])), body)

        status, _ = call("GET", "/api/projects/abc-123")
        check("a templated path matches any value for the parameter",
              status == 200, status)

        status, _ = call("GET", "/api/applets")
        check("the right path with the wrong method is refused",
              status == 404, status)

        # The log is the evidence the operator reads. A call that happened
        # and was not recorded is worse than no log at all.
        check("every call was recorded", len(mock.calls) == 6,
              len(mock.calls))
        check("the log distinguishes accepted from rejected",
              [c["ok"] for c in mock.calls].count(True) == 2,
              [(c["status"], c["ok"]) for c in mock.calls])
        summary = mockapi.summarise(mock)
        check("the summary names the failures", "404" in summary
              and "422" in summary)

        # It must bind loopback only. Binding 0.0.0.0 would expose a fake
        # copy of a real API to the LAN, which is a worse outcome than any
        # this file is guarding against.
        check("it listens on loopback only",
              server.server_address[0] == "127.0.0.1", server.server_address)
    finally:
        server.shutdown()

    # ----------------------------------------------------------------------
    print("\na malformed body says why, not just that")

    # "body was not valid JSON" alone repeats the status line the traceback
    # already gave the repair loop three attempts in a row on a live run,
    # never once fixing the actual bug: `requests.post(url, data=a_dict)`
    # instead of `json=a_dict`. `data=` on a dict form-encodes it and sends
    # no `application/json` header, so the mock is handed
    # `name=Foo&applet_id=...` and no amount of retrying produces valid JSON.
    server, mock, url = mockapi.serve(SPEC)
    try:
        import urllib.parse
        form = urllib.parse.urlencode({"name": "survey", "html": "<p>x</p>"})
        req = urllib.request.Request(
            url + "/api/applets", data=form.encode(), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            check("a form-encoded body is still refused as bad JSON",
                  exc.code == 400, exc.code)

        detail = mock.calls[-1].get("detail", "")
        check("...and the detail names the actual content-type received",
              "x-www-form-urlencoded" in detail, detail)
        check("...and quotes what was actually sent",
              "name=survey" in detail, detail)
        check("...and diagnoses the likely cause: data= instead of json=",
              "data=" in detail and "json=" in detail, detail)

        # Sabotage in the other direction: genuinely broken JSON (not
        # form-encoded) must not be misdiagnosed as the data=/json= mistake,
        # since that would send a repair loop chasing a fix that cannot
        # apply to code that never touched `requests` at all.
        mark = len(mock.calls)
        req = urllib.request.Request(
            url + "/api/applets", data=b'{"name": "survey",}', method="POST",
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            check("genuinely broken JSON is still refused", exc.code == 400,
                  exc.code)
        detail2 = mock.calls[mark].get("detail", "")
        check("...but is not misdiagnosed as the data=/json= mistake",
              "data=" not in detail2, detail2)
        check("...and still quotes the actual bytes received for context",
              '"name": "survey"' in detail2, detail2)
    finally:
        server.shutdown()

    # ----------------------------------------------------------------------
    print("\nthe two halves fit: a gate can reach the mock and nothing else")

    server, mock, url = mockapi.serve(SPEC)
    try:
        write(ROOT, "client.py",
              "import json, os, urllib.request\n"
              "# 192.0.2.0/24 is TEST-NET-1: reserved, never routable, and an IP\n"
              "# literal so no DNS lookup can fail first and mask the guard.\n"
              "BASE = os.environ.get('API_BASE_URL', 'http://192.0.2.1')\n"
              "def main():\n"
              "    body = json.dumps({'name': 'survey', 'html': '<p>x</p>'})\n"
              "    req = urllib.request.Request(BASE + '/api/applets',\n"
              "        data=body.encode(), method='POST',\n"
              "        headers={'Content-Type': 'application/json'})\n"
              "    print(urllib.request.urlopen(req, timeout=5).status)\n"
              "if __name__ == '__main__':\n"
              "    main()\n")
        ok, out = codegen.run_command("%s client.py" % sys.executable, ROOT,
                                      timeout=30,
                                      env_extra={"API_BASE_URL": url})
        check("generated code reaches the mock through the guard",
              "201" in out, out[-200:])
        check("...and the mock saw the call",
              any(c["path"] == "/api/applets" and c["ok"] for c in mock.calls),
              mock.calls)

        # Same file, no API_BASE_URL: it falls back to the real host and must
        # be stopped. This is the exact accident the guard exists for.
        ok, out = codegen.run_command("%s client.py" % sys.executable, ROOT,
                                      timeout=30)
        check("the same code aimed at the real host is blocked",
              "network blocked while testing" in out, out[-200:])
    finally:
        server.shutdown()

finally:
    shutil.rmtree(ROOT, ignore_errors=True)

print()
if FAILURES:
    print("FAILED: %s" % ", ".join(FAILURES))
    sys.exit(1)
print("all sandbox checks passed")
