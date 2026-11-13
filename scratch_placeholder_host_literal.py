"""`gate_placeholder_host_literal` catches a hardcoded `example.com`-style
host or a "YOUR_API_TOKEN"-shaped credential string in generated source --
the sibling of `gate_placeholder_id_literal`'s zeroed-UUID check, for the
two live shapes neither `local_urls` (only rewrites `*_URL` env vars, never
a source literal) nor the socket guard (bypassed by a caught-and-re-raised
exception, which `gate_swallowed_api_failure` explicitly permits) can
catch.

Measured live in `03_upload_units/main.py`, produced by `interface_gate`'s
own cross-file repair rewrite:
    api_url = f"https://api.example.com/tasks/{task_id}/units"
    headers = {"Authorization": "Bearer YOUR_API_TOKEN"}
Both literals are reproduced here verbatim, plus the negative cases that
must stay clean, plus sabotage proving the fixture is load-bearing.
"""
import sys

import codegen

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- %s" % detail) if detail else ""))
        FAILURES.append(name)


LIVE_FAILURE_SOURCE = '''
import requests

def main():
    task_id = "abc"
    api_url = f"https://api.example.com/tasks/{task_id}/units"
    headers = {
        "Authorization": "Bearer YOUR_API_TOKEN"
    }
    requests.post(api_url, headers=headers, json={})

if __name__ == "__main__":
    main()
'''

GOOD_SOURCE = '''
import os
import requests

def main():
    base_url = os.environ.get("API_BASE_URL", "https://real.host.example-app.io")
    api_key = os.environ["API_KEY"]
    headers = {"Authorization": "Bearer " + api_key}
    requests.post(base_url + "/tasks/1/units", headers=headers, json={})

if __name__ == "__main__":
    main()
'''

CLASS_ATTR_SOURCE = '''
import requests

class Client:
    def __init__(self, base_url, api_key):
        self.base_url = base_url
        self.headers = {"Authorization": "Bearer " + api_key}

    def post(self, path, **kw):
        return requests.post(self.base_url + path, headers=self.headers, **kw)
'''

WWW_SUBDOMAIN_SOURCE = '''
import requests

def main():
    requests.get("http://www.example.org/status")

if __name__ == "__main__":
    main()
'''

BRACKETED_CREDENTIAL_SOURCE = '''
import requests

def main():
    headers = {"Authorization": "<YOUR_TOKEN_HERE>"}
    requests.get("https://real.example-app.io/status", headers=headers)

if __name__ == "__main__":
    main()
'''


print("the exact live failure is caught")
finding = codegen.gate_placeholder_host_literal(LIVE_FAILURE_SOURCE)
check("api.example.com is flagged", finding is not None
      and "example.com" in finding, finding)

finding2 = codegen.gate_placeholder_host_literal(LIVE_FAILURE_SOURCE)
check("finding mentions the host literal specifically",
      finding2 is not None and "api.example.com" in finding2, finding2)

print("\na correct file reading real env vars is left alone")
check("gate_placeholder_host_literal returns None on GOOD_SOURCE",
      codegen.gate_placeholder_host_literal(GOOD_SOURCE) is None,
      codegen.gate_placeholder_host_literal(GOOD_SOURCE))

print("\na normal client class building auth headers from a real "
      "variable is left alone")
check("gate_placeholder_host_literal returns None on CLASS_ATTR_SOURCE",
      codegen.gate_placeholder_host_literal(CLASS_ATTR_SOURCE) is None,
      codegen.gate_placeholder_host_literal(CLASS_ATTR_SOURCE))

print("\na www.example.org subdomain is caught too")
finding3 = codegen.gate_placeholder_host_literal(WWW_SUBDOMAIN_SOURCE)
check("www.example.org is flagged", finding3 is not None
      and "example.org" in finding3, finding3)

print("\na bracketed <YOUR_TOKEN_HERE>-shaped credential is caught")
finding4 = codegen.gate_placeholder_host_literal(BRACKETED_CREDENTIAL_SOURCE)
check("<YOUR_TOKEN_HERE> is flagged", finding4 is not None, finding4)

print("\nwired into the per-attempt gate chain in generate_file (via the "
      "same free, pre-subprocess stage placeholder_id_literal already "
      "uses)")
import inspect
src = inspect.getsource(codegen)
check("gate_placeholder_host_literal is actually called from "
      "generate_file's gate chain",
      "failure = gate_placeholder_host_literal(code)" in src)

print("\nsabotage: an unpatched copy of the check (before the RE was "
      "added) would have missed the live failure -- proves the fixture "
      "actually exercises new code, not something an existing gate "
      "already caught")
import re as _re
old_style_would_catch = bool(
    _re.search(r"00000000-0000-4000-8000-000000000000", LIVE_FAILURE_SOURCE))
check("gate_placeholder_id_literal (the pre-existing UUID gate) does NOT "
      "catch this on its own -- confirms this is new coverage, not a "
      "duplicate",
      codegen.gate_placeholder_id_literal(LIVE_FAILURE_SOURCE) is None
      and not old_style_would_catch)

print("\n%d failed" % len(FAILURES))
sys.exit(1 if FAILURES else 0)
