#!/usr/bin/env python3
"""Checks for the run instructions and the placeholders that feed the gates.

Two things are being guarded, and they fail in opposite directions.

The printed instructions must not describe an interface the file does not
have -- an operator typing `--dry-run true` because the note said so is
worse off than one who read the source. So the assertions here are mostly
about *not* reporting: a computed variable name, a switch shown as taking a
value, a module with no entry point.

The placeholders must never fail a file that would otherwise pass. That is
the expensive error in this project, because a false gate failure sends the
repair loop rewriting correct code. `test_a_placeholder_cannot_fail_a_file`
is the one that matters most here.

Offline: no node in the pool is contacted.
"""

import os
import sys
import tempfile
import unittest.mock

import codegen
import usage

FAILURES = []


def check(label, condition, detail=""):
    print("  %-4s %s%s" % ("ok" if condition else "FAIL", label,
                           "" if condition else "  <- " + str(detail)[:200]))
    if not condition:
        FAILURES.append(label)


def scratch(name, body):
    path = os.path.join(tempfile.mkdtemp(prefix="usage-"), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


CLIENT = '''
import os, json, argparse, urllib.request
KEY = os.environ["API_KEY"]
BASE = os.getenv("API_BASE_URL", "https://real.example")
LEVEL = os.environ.get("LOG_LEVEL", "info")
TOKEN = os.getenv("SESSION_TOKEN")
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", "-p", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retries", default=3)
    ap.add_argument("outfile")
    ap.parse_args()
if __name__ == "__main__":
    main()
'''


def main():
    print("run instructions")
    found = usage.read("client.py", CLIENT)

    # A default is the file saying the variable is optional; no default is it
    # saying the opposite. Both spellings of the same call must agree.
    check("os.environ[...] and bare getenv are required",
          sorted(found.required_env) == ["API_KEY", "SESSION_TOKEN"],
          found.required_env)
    check("a default makes a variable optional",
          sorted(found.optional_env) == [("API_BASE_URL",
                                          "https://real.example"),
                                         ("LOG_LEVEL", "info")],
          found.optional_env)
    check("the long form of a flag is what gets shown",
          ("--project", True, True) in found.flags, found.flags)
    check("a switch is not reported as taking a value",
          ("--dry-run", False, False) in found.flags, found.flags)
    check("an optional flag is not marked required",
          ("--retries", False, True) in found.flags, found.flags)
    check("positionals are found", found.positionals == ["outfile"],
          found.positionals)
    check("a __main__ block makes it runnable", found.runnable)

    # The refusals. Each of these would be an instruction that is wrong.
    computed = usage.read("x.py", 'import os\nk = os.environ[prefix + "_KEY"]\n')
    check("a computed variable name is not guessed at",
          computed is None or computed.required_env == [],
          computed and computed.required_env)
    check("a module with no entry point gets no instructions",
          usage.render(["lib.py"], os.path.dirname(
              scratch("lib.py", "def helper():\n    return 1\n"))) == "")
    check("a non-Python file is not parsed as one",
          usage.read("page.html", "<html></html>") is None)

    root = os.path.dirname(scratch("client.py", CLIENT))
    block = usage.render(["client.py"], root,
                         env={"API_KEY": "sk-real-secret-value"})
    check("a variable that is set is not demanded again",
          "export API_KEY" not in block, block)
    check("a variable that is missing is demanded",
          "export SESSION_TOKEN=..." in block, block)
    # Same rule as reading `.env` for names only: whether it is set is the
    # useful fact, and the value is never the useful fact.
    check("no value is ever printed", "sk-real-secret-value" not in block,
          block)
    check("the invocation line is complete",
          "python3 client.py --project ... [--dry-run] [--retries ...] OUTFILE"
          in block, block)
    check("html says to open it, not run it",
          "open page.html in a browser" in usage.render(["page.html"], root))

    print("\nplaceholders")
    argv = usage.placeholder_args(CLIENT)
    check("required arguments are supplied",
          "--project" in argv and "out.txt" in argv, argv)
    check("optional flags are left alone",
          "--dry-run" not in argv and "--retries" not in argv, argv)
    # uuid.UUID("placeholder") raises; uuid.UUID(<a uuid>) does not, so the
    # shape decides whether the run gets past the first line that parses it.
    check("an id-shaped name gets a uuid",
          usage._placeholder_for("project_id").count("-") == 4,
          usage._placeholder_for("project_id"))

    # placeholder_env deliberately reads the real os.environ too, so a
    # value already there is never shadowed -- but that makes the check
    # depend on the operator's own shell unless it is isolated here. This
    # project's own instructions tell an operator to export API_KEY, and a
    # test that only passes in a shell where nobody did that is not a test
    # anybody can actually rely on.
    with unittest.mock.patch.dict(os.environ, {}, clear=True):
        filled = codegen.placeholder_env(
            CLIENT, {"API_BASE_URL": "http://127.0.0.1:9"})
        check("a missing credential is filled", filled.get("API_KEY"), filled)
        check("a url points at the base the gates were given",
              codegen.placeholder_env(
                  'import os\nu = os.environ["SERVICE_URL"]\n',
                  {"API_BASE_URL": "http://127.0.0.1:9"}
              )["SERVICE_URL"] == "http://127.0.0.1:9")
        real = codegen.placeholder_env(CLIENT, {"API_KEY": "the-real-one"})
        check("a supplied value is never shadowed", "API_KEY" not in real, real)

        # The case that motivated the isolation above, made explicit: a
        # value sitting in the operator's real environment -- not the one
        # handed to this call -- must still not be shadowed.
        os.environ["API_KEY"] = "from-the-operators-shell"
        untouched = codegen.placeholder_env(
            CLIENT, {"API_BASE_URL": "http://127.0.0.1:9"})
        check("a value in the ambient environment is not shadowed either",
              "API_KEY" not in untouched, untouched)

    print("\nthe placeholders may only ever help")
    # The expensive error: a guessed value that fails a correct file and
    # sends the repair loop rewriting it. The gate must fall back to the
    # plain run, which is what it did before placeholders existed.
    picky = scratch("picky.py",
                    'import os, sys\n'
                    'def main():\n'
                    '    key = os.environ["API_KEY"]\n'
                    '    if not key.startswith("apk_"):\n'
                    '        raise ValueError("bad key format")\n'
                    'if __name__ == "__main__":\n'
                    '    main() if os.environ.get("API_KEY") else sys.exit(0)\n')
    check("test_a_placeholder_cannot_fail_a_file",
          codegen.gate_smoke(picky, os.path.dirname(picky)) is None)

    broken = scratch("broken.py",
                     'def main():\n    return {}["nope"]\n'
                     'if __name__ == "__main__":\n    main()\n')
    check("a genuinely broken file still fails",
          codegen.gate_smoke(broken, os.path.dirname(broken)) is not None)

    # Without this the gate is vacuous for any API client: it exits on the
    # missing credential having sent nothing, and reports a pass.
    reaches = scratch("reaches.py",
                      'import os, json, urllib.request\n'
                      'def main():\n'
                      '    base = os.environ["API_BASE_URL"]\n'
                      '    os.environ["API_KEY"]\n'
                      '    urllib.request.urlopen(base + "/ping")\n'
                      'if __name__ == "__main__":\n    main()\n')
    import http.server
    import threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):                       # noqa: D102
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    codegen.gate_smoke(reaches, os.path.dirname(reaches),
                       env_extra={"API_BASE_URL": base})
    srv.shutdown()
    check("a client with no credential still reaches the API",
          hits == ["/ping"], hits)

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all usage checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
