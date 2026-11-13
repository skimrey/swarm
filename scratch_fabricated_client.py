#!/usr/bin/env python3
"""Validates two gates against the exact live failure that motivated
them: a `--api maven` step that fabricated its own "MOCK" client instead
of calling `requests`, and a sibling step whose error handling swallowed
a real API failure and exited 0 anyway, so `generate.sh` printed
"succeeded" directly under the failure it was reporting on.
"""

import codegen

FABRICATED_CLIENT = '''
import requests
import json

class MockClient:
    def __init__(self, base_url, headers):
        self.base_url = base_url

    def create_task(self, project_id, name):
        url = f"{self.base_url}/api/projects/{project_id}/tasks"
        print(f"MOCK: POST {url} with name='{name}'")
        return {"id": "mock-task-id-456", "name": name}

client = MockClient("https://real.example.com", {})

def main():
    task = client.create_task("proj-1", "a task")
    print(task["id"])

if __name__ == "__main__":
    main()
'''

REAL_CLIENT = '''
import requests

def create_task(base_url, headers, project_id, name):
    url = f"{base_url}/api/projects/{project_id}/tasks"
    resp = requests.post(url, headers=headers, json={"name": name})
    resp.raise_for_status()
    return resp.json()

def main():
    task = create_task("https://real.example.com", {}, "proj-1", "a task")
    print(task["id"])

if __name__ == "__main__":
    main()
'''

SPLIT_ACROSS_FILES = '''
import requests
from client import Client

def main():
    c = Client("https://real.example.com", {})
    try:
        c.create_task("proj-1", "a task")
    except requests.HTTPError as e:
        print(f"failed: {e}")

if __name__ == "__main__":
    main()
'''

SWALLOWED_FAILURE = '''
import requests

def main():
    try:
        resp = requests.post("https://real.example.com/api/tasks/1/units")
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"Unit upload failed: {e}")
    except ConnectionError as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    main()
'''

RERAISES = '''
import requests

def main():
    try:
        resp = requests.post("https://real.example.com/api/tasks/1/units")
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"Unit upload failed: {e}")
        raise

if __name__ == "__main__":
    main()
'''

EXITS_EXPLICITLY = '''
import sys
import requests

def main():
    try:
        resp = requests.post("https://real.example.com/api/tasks/1/units")
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"Unit upload failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''

PROPAGATES_VIA_RETURN_VALUE = '''
import sys
import requests

def main():
    try:
        resp = requests.post("https://real.example.com/api/tasks/1/units")
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"Unit upload failed: {e}")
        return False
    return True

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
'''

NEVER_CALLS_REQUESTS_AT_ALL = '''
def main():
    print("nothing to do here")

if __name__ == "__main__":
    main()
'''


def test_fabricated_client_is_caught():
    result = codegen.gate_fabricated_client(FABRICATED_CLIENT)
    assert result is not None
    assert "requests.get/post" in result


def test_real_client_passes():
    assert codegen.gate_fabricated_client(REAL_CLIENT) is None


def test_file_importing_a_local_client_module_is_not_flagged():
    # main.py only catches requests.HTTPError raised by a sibling
    # client.py -- the real work may be happening there, so this must
    # not be flagged as fabricated.
    assert codegen.gate_fabricated_client(SPLIT_ACROSS_FILES) is None


def test_file_never_mentioning_requests_is_not_flagged():
    assert codegen.gate_fabricated_client(NEVER_CALLS_REQUESTS_AT_ALL) is None


def test_swallowed_api_failure_is_caught():
    result = codegen.gate_swallowed_api_failure(SWALLOWED_FAILURE)
    assert result is not None
    assert "HTTPError" in result and "ConnectionError" in result


def test_reraising_handler_passes():
    assert codegen.gate_swallowed_api_failure(RERAISES) is None


def test_explicit_sys_exit_in_handler_passes():
    assert codegen.gate_swallowed_api_failure(EXITS_EXPLICITLY) is None


def test_return_value_propagated_to_sys_exit_passes():
    # The one legitimate pattern the check must not reject: main()'s
    # return value feeds sys.exit in the __main__ guard, even though the
    # handler itself only returns.
    assert codegen.gate_swallowed_api_failure(PROPAGATES_VIA_RETURN_VALUE) \
        is None


def test_file_never_mentioning_requests_is_not_flagged_for_swallowing():
    assert codegen.gate_swallowed_api_failure(
        NEVER_CALLS_REQUESTS_AT_ALL) is None


def test_sabotage_without_fabricated_client_gate_reproduces_the_live_bug():
    # No call to gate_fabricated_client at all -- this is what the
    # pipeline did before the fix existed: nothing distinguishes a real
    # call from a hand-invented one, since both leave the mock's call
    # log equally empty.
    tree_ok = True
    try:
        import ast
        ast.parse(FABRICATED_CLIENT)
    except SyntaxError:
        tree_ok = False
    assert tree_ok, "fixture itself must at least be valid Python"


def test_sabotage_without_swallowed_failure_gate_reproduces_the_live_bug():
    # Confirms the fixture is exactly the shape that exits 0: no
    # gate_swallowed_api_failure call means nothing stops this file from
    # being written and later reporting false success.
    import ast
    tree = ast.parse(SWALLOWED_FAILURE)
    assert not any(isinstance(n, ast.Raise) for n in ast.walk(tree)), (
        "fixture must contain no raise, matching the live file exactly")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print("%d/%d passed" % (len(tests), len(tests)))
