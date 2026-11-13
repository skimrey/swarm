#!/usr/bin/env python3
"""Validates `codegen.gate_placeholder_credential_guard` against the
exact live failure that motivated it: a real `--api maven` orchestration
run where 5 of 6 API-calling components correctly built an
`X-API-Key: <API_KEY>` header, and the sixth (`02_create_task/main.py`)
never built one at all -- it referenced a global `HEADERS` in two
`requests.post(..., headers=HEADERS, ...)` calls, hit the resulting
`NameError`, and "fixed" it with a `try: HEADERS / except NameError:
HEADERS = {}` guard inside `if __name__ == "__main__":` instead of
building the header for real. Every gate passed (`gate_smoke` only fails
on an unhandled traceback, and this file never raises one), the run
reported "06 generation succeeded", and the real API answered `401
Unauthorized` on the very first real request this step sent.
"""

import codegen

LIVE_BUG = '''
import os
import json
import requests
import uuid

def main():
    with open("../01_create_project/output.json") as f:
        project_id = json.load(f)["project_id"]

    url = f"https://real.example.com/api/projects/{project_id}/tasks"
    resp = requests.post(url, headers=HEADERS, json={"name": "a task"})
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("id")

    retry_resp = requests.post(url, headers=HEADERS, json={"name": "retry"})

    with open("output.json", "w") as f:
        json.dump({"task_id": task_id}, f)


if __name__ == "__main__":
    try:
        HEADERS
    except NameError:
        HEADERS = {}

    main()
'''

REAL_HEADERS_BUILT_PROPERLY = '''
import os
import requests

API_KEY = os.environ.get("API_KEY")
HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
}

def main():
    resp = requests.post("https://real.example.com/api/tasks",
                         headers=HEADERS, json={"name": "a task"})
    resp.raise_for_status()

if __name__ == "__main__":
    main()
'''

# The same guard shape, but HEADERS is *also* built for real elsewhere in
# the file (e.g. by an earlier, working revision that a repair pass only
# partially rewrote). This must NOT be flagged: the guard is no longer
# provably a no-op, because a real assignment exists too.
GUARD_PLUS_REAL_ASSIGNMENT = '''
import os
import requests

def build_headers():
    global HEADERS
    HEADERS = {"X-API-Key": os.environ.get("API_KEY")}

def main():
    build_headers()
    resp = requests.post("https://real.example.com/api/tasks",
                         headers=HEADERS, json={"name": "a task"})
    resp.raise_for_status()

if __name__ == "__main__":
    try:
        HEADERS
    except NameError:
        HEADERS = {}
    main()
'''

# The identical guard shape, but the guarded name is never fed to a
# requests call at all -- nothing here makes a claim about credentials,
# so this must not be flagged either.
GUARD_ON_UNRELATED_NAME = '''
def main():
    print(CONFIG)

if __name__ == "__main__":
    try:
        CONFIG
    except NameError:
        CONFIG = {}
    main()
'''

# A guard whose except branch assigns a *non-trivial* value is a real,
# working fallback, not a silent no-op -- must not be flagged.
GUARD_WITH_REAL_FALLBACK = '''
import os
import requests

def main():
    resp = requests.post("https://real.example.com/api/tasks",
                         headers=HEADERS, json={"name": "a task"})
    resp.raise_for_status()

if __name__ == "__main__":
    try:
        HEADERS
    except NameError:
        HEADERS = {"X-API-Key": os.environ.get("API_KEY")}
    main()
'''


def test_live_bug_is_caught():
    result = codegen.gate_placeholder_credential_guard(LIVE_BUG)
    assert result is not None, "must catch the exact live HEADERS bug"
    assert "HEADERS" in result
    assert "headers=" in result or "auth=" in result or "cookies=" in result


def test_properly_built_headers_pass():
    assert codegen.gate_placeholder_credential_guard(
        REAL_HEADERS_BUILT_PROPERLY) is None


def test_guard_alongside_a_real_assignment_elsewhere_passes():
    assert codegen.gate_placeholder_credential_guard(
        GUARD_PLUS_REAL_ASSIGNMENT) is None


def test_guard_on_a_name_never_used_as_a_credential_kwarg_passes():
    assert codegen.gate_placeholder_credential_guard(
        GUARD_ON_UNRELATED_NAME) is None


def test_guard_with_a_real_non_trivial_fallback_passes():
    assert codegen.gate_placeholder_credential_guard(
        GUARD_WITH_REAL_FALLBACK) is None


def test_file_with_no_try_except_nameerror_at_all_passes():
    assert codegen.gate_placeholder_credential_guard(
        REAL_HEADERS_BUILT_PROPERLY) is None


def test_sabotage_without_the_gate_reproduces_the_live_failure():
    # No call to gate_placeholder_credential_guard at all -- this is
    # exactly what the pipeline did before this gate existed. Confirm
    # the fixture really does have the shape that would otherwise slip
    # through every other gate: it parses, and it contains no `raise`
    # anywhere (so gate_swallowed_api_failure has nothing to catch
    # either -- there is no except handler here at all, only a bare
    # NameError guard).
    import ast
    tree = ast.parse(LIVE_BUG)
    assert not any(isinstance(n, ast.Raise) for n in ast.walk(tree)), (
        "fixture must contain no raise, matching the live file exactly")
    # And gate_fabricated_client must stay silent on it too, proving this
    # is genuinely a new failure mode, not a duplicate of an existing one.
    assert codegen.gate_fabricated_client(LIVE_BUG) is None
    assert codegen.gate_swallowed_api_failure(LIVE_BUG) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print("%d/%d passed" % (len(tests), len(tests)))
