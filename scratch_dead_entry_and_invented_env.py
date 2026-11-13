#!/usr/bin/env python3
"""Validates `gate_dead_entry_point`/`autofix_dead_entry_point` and
`gate_invented_env_id` against the exact live failures they were built
from (run_10's no-op main.py, run_7's invented PROJECT_ID), plus the
negative controls each must leave alone.
"""
import sys

import codegen
import team

FAILED = []


def check(label, cond, detail=None):
    status = "ok  " if cond else "FAIL"
    print("  %s %s" % (status, label))
    if not cond:
        FAILED.append(label)
        if detail is not None:
            print("      %r" % (detail,))


# The exact live shape: a real Client class with real work, and a dead guard.
LIVE_DEAD_ENTRY = '''
import os
import requests

API_BASE_URL = os.environ["API_BASE_URL"]

class Client:
    def __init__(self, api_base_url):
        self.api_base_url = api_base_url

    def create_project(self, name):
        resp = requests.post(self.api_base_url + "/api/projects",
                             json={"name": name})
        data = resp.json()
        project_id = data.get("id")
        if project_id:
            print(project_id)
            import json
            with open("output.json", "w") as f:
                json.dump({"project_id": project_id}, f)
            return data
        raise RuntimeError("no id returned")

if __name__ == "__main__":
    # kept here for completeness if run standalone
    pass
'''

# The ordinary, correct convention this project's own generated files use.
CORRECT_MAIN_CALLS = '''
import os

def main():
    print("doing real work")
    with open("output.json", "w") as f:
        f.write("{}")

if __name__ == "__main__":
    main()
'''

# main() is defined but the guard never calls it -- autofix should splice it in.
MAIN_DEFINED_NOT_CALLED = '''
import os

def main():
    print("doing real work")

if __name__ == "__main__":
    pass
'''

# A pure-constants module with nothing to call -- must never be flagged.
PURE_CONSTANTS = '''
COLORS = ["red", "green", "blue"]

if __name__ == "__main__":
    pass
'''

# A guard that calls a class method directly (no main()) -- must not be flagged.
DIRECT_METHOD_CALL = '''
class Client:
    def run(self):
        print("working")

if __name__ == "__main__":
    Client().run()
'''

# No __main__ guard at all -- a pure library module, must not be flagged.
NO_GUARD_AT_ALL = '''
def helper(x):
    return x + 1
'''


def test_catches_the_live_dead_entry_point():
    failure = codegen.gate_dead_entry_point(LIVE_DEAD_ENTRY)
    check("the live no-op __main__ guard is caught",
         failure is not None, failure)
    check("the message names the unused definition",
         failure and "Client" in failure, failure)


def test_correct_main_convention_is_not_flagged():
    check("main() actually being called is left alone",
         codegen.gate_dead_entry_point(CORRECT_MAIN_CALLS) is None)


def test_direct_method_call_is_not_flagged():
    check("a guard calling a class method directly (no main()) is fine",
         codegen.gate_dead_entry_point(DIRECT_METHOD_CALL) is None)


def test_pure_constants_module_is_not_flagged():
    check("a module with nothing but trivial defs is never flagged",
         codegen.gate_dead_entry_point(PURE_CONSTANTS) is None)


def test_no_guard_is_not_flagged():
    check("a file with no __main__ guard at all is not this defect",
         codegen.gate_dead_entry_point(NO_GUARD_AT_ALL) is None)


def test_autofix_splices_in_main_call_when_main_is_defined():
    fixed = codegen.autofix_dead_entry_point(MAIN_DEFINED_NOT_CALLED)
    check("autofix returns a rewritten file", fixed is not None)
    check("the fixed code no longer trips the gate",
         fixed is not None and codegen.gate_dead_entry_point(fixed) is None)
    check("the fixed code still parses", fixed is not None and
         _parses(fixed))
    check("the fixed code actually calls main() and produces its output",
         fixed is not None and _runs_and_prints(fixed, "doing real work"))


def test_autofix_declines_when_there_is_no_main_to_call():
    # The live shape (a Client class, no main()) has no safe guess to make.
    check("autofix declines on the live shape (class-only, no main())",
         codegen.autofix_dead_entry_point(LIVE_DEAD_ENTRY) is None)


def _parses(code):
    import ast
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _runs_and_prints(code, needle):
    import subprocess
    import tempfile
    import os as _os
    with tempfile.TemporaryDirectory() as d:
        p = _os.path.join(d, "f.py")
        with open(p, "w") as f:
            f.write(code)
        r = subprocess.run([sys.executable, p], cwd=d,
                           capture_output=True, text=True, timeout=10)
        return needle in r.stdout


# --- gate_invented_env_id ---

LIVE_INVENTED_ENV = '''
import os
import json

API_BASE_URL = os.environ["API_BASE_URL"]
API_KEY = os.environ["API_KEY"]

def main():
    with open("../02_create_task/output.json") as f:
        task_id = json.load(f)["task_id"]
    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        raise EnvironmentError("PROJECT_ID environment variable is required")
    print(task_id, project_id)

if __name__ == "__main__":
    main()
'''

# Reading a real credential name (in `allowed`) must never be flagged, even
# though it is not id-shaped here -- this is the ordinary, correct case.
ORDINARY_CREDENTIALS_ONLY = '''
import os

API_BASE_URL = os.environ["API_BASE_URL"]
API_KEY = os.environ.get("API_KEY")
'''

# A real .env-derived name that happens to be id-shaped must be allowed
# when it is passed in `allowed` -- protects a real operator credential
# from being misread as invented plumbing.
ALLOWED_ID_SHAPED_CREDENTIAL = '''
import os

TENANT_ID = os.environ["TENANT_ID"]
'''

# getenv() spelling must be caught too, not just os.environ[...]/get(...).
GETENV_SPELLING = '''
import os

def main():
    task_id = os.getenv("TASK_ID")
    if not task_id:
        raise RuntimeError("TASK_ID required")

if __name__ == "__main__":
    main()
'''


def test_catches_the_live_invented_project_id():
    failure = codegen.gate_invented_env_id(LIVE_INVENTED_ENV)
    check("the live invented PROJECT_ID is caught",
         failure is not None, failure)
    check("the message names PROJECT_ID and the sibling-file alternative",
         failure and "PROJECT_ID" in failure and "output.json" in failure,
         failure)


def test_ordinary_credentials_are_not_flagged():
    check("API_BASE_URL/API_KEY alone are never flagged",
         codegen.gate_invented_env_id(ORDINARY_CREDENTIALS_ONLY) is None)


def test_allowed_list_protects_a_real_id_shaped_credential():
    check("an id-shaped name IS flagged with the default allowed set",
         codegen.gate_invented_env_id(ALLOWED_ID_SHAPED_CREDENTIAL)
         is not None)
    check("...but not once it is in the real pool vocabulary passed in",
         codegen.gate_invented_env_id(
             ALLOWED_ID_SHAPED_CREDENTIAL,
             allowed=["API_BASE_URL", "API_KEY", "TENANT_ID"]) is None)


def test_getenv_spelling_is_caught_too():
    check("os.getenv(...) is checked the same as os.environ[...]",
         codegen.gate_invented_env_id(GETENV_SPELLING) is not None)


def test_sabotage_disabling_the_id_shape_check_loses_the_live_case():
    # Prove the fixture is real: a version that only matched a fixed
    # literal name ("PROJECT_ID") rather than the general shape would
    # miss the getenv-spelled TASK_ID case above, showing the shape
    # match (not a hardcoded name) is what makes this general.
    import re
    old = codegen._ID_ENV_NAME_RE
    codegen._ID_ENV_NAME_RE = re.compile(r"^PROJECT_ID$")
    try:
        check("sabotage: a name-specific regex misses the getenv case",
             codegen.gate_invented_env_id(GETENV_SPELLING) is None)
    finally:
        codegen._ID_ENV_NAME_RE = old


def test_generate_file_calls_are_all_wired_with_env_names():
    """Every `codegen.generate_file(...)` call site in `team.py` passes
    `env_names` (directly, or via a `**kwargs` dict that itself sets it)
    -- a structural guarantee, checked the same way
    `scratch_save_failed_attempt.py` checks its own call-site wiring,
    since fixturing all eleven call sites end to end would be far more
    code than the property actually needs. Without this, `gate_invented_
    env_id` would fall back to its bare default (API_BASE_URL, API_KEY
    only) everywhere, silently losing every real `.env` credential name
    the pool actually knows about and flagging them as invented."""
    import ast
    tree = ast.parse(open("team.py").read())
    missing = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "generate_file"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "codegen"):
            continue
        kw_names = {k.arg for k in node.keywords if k.arg}
        has_star = any(k.arg is None for k in node.keywords)
        if "env_names" not in kw_names and not has_star:
            missing.append(node.lineno)
    check("every codegen.generate_file(...) call site sets env_names "
         "(directly or via a **kwargs spread)",
         not missing, missing)


def test_env_vocabulary_used_at_call_sites_is_the_real_pool_function():
    check("team.env_vocabulary exists and returns the configured names",
         team.env_vocabulary(team.swarm.Config.load()) == ["API_BASE_URL",
                                                            "API_KEY"]
         or "API_BASE_URL" in team.env_vocabulary(team.swarm.Config.load()))


def main():
    test_catches_the_live_dead_entry_point()
    test_correct_main_convention_is_not_flagged()
    test_direct_method_call_is_not_flagged()
    test_pure_constants_module_is_not_flagged()
    test_no_guard_is_not_flagged()
    test_autofix_splices_in_main_call_when_main_is_defined()
    test_autofix_declines_when_there_is_no_main_to_call()
    test_catches_the_live_invented_project_id()
    test_ordinary_credentials_are_not_flagged()
    test_allowed_list_protects_a_real_id_shaped_credential()
    test_getenv_spelling_is_caught_too()
    test_sabotage_disabling_the_id_shape_check_loses_the_live_case()
    test_generate_file_calls_are_all_wired_with_env_names()
    test_env_vocabulary_used_at_call_sites_is_the_real_pool_function()
    print("%d failed" % len(FAILED))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
