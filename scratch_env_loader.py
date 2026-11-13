"""Validates codegen.maybe_inject_env_loader / env_loader_snippet.

The real failure this exists to fix: an operator running a generated
`main.py` for real, standalone, in a brand new directory `--api` work
happened to land in, gets "environment variable is not set" no matter how
many times they export it in some other shell -- a fresh directory and a
fresh shell remember nothing from the last one. The fix is a stdlib-only
loader, prepended to the entry point at generation time, that reads a
single fixed `.env` at the swarm's own root and fills in `os.environ` with
`setdefault` -- never shadowing a value the operator genuinely exported.
"""
import os
import sys
import tempfile

import codegen
import swarm

ENTRY_SOURCE = '''import os

def main():
    key = os.environ["API_KEY"]
    print("got", key)

if __name__ == "__main__":
    main()
'''

LIBRARY_SOURCE = '''def helper():
    return 1
'''


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("ok:", msg)


# --- 1. injected only when env_extra is supplied (this run's --api proxy) --

no_api = codegen.maybe_inject_env_loader(ENTRY_SOURCE, "main.py", None)
check(no_api == ENTRY_SOURCE,
      "no env_extra (no --api) -- code is returned unchanged")

with_api = codegen.maybe_inject_env_loader(ENTRY_SOURCE, "main.py",
                                           {"API_BASE_URL": "http://x"})
check(codegen.ENV_LOADER_MARKER in with_api,
      "env_extra supplied -- the loader marker is present")
check(with_api.endswith(ENTRY_SOURCE),
      "the original source survives intact after the loader")
check(with_api.index(codegen.ENV_LOADER_MARKER) < with_api.index("def main"),
      "the loader is prepended before the file's own code, not appended")


# --- 2. only a runnable entry point gets it, not a plain library file -----

lib_result = codegen.maybe_inject_env_loader(LIBRARY_SOURCE, "client.py",
                                             {"API_BASE_URL": "http://x"})
check(lib_result == LIBRARY_SOURCE,
      "a file with no __main__ guard (client.py) is left untouched")


# --- 3. idempotent -- injecting twice does not double the loader ----------

twice = codegen.maybe_inject_env_loader(with_api, "main.py",
                                        {"API_BASE_URL": "http://x"})
check(twice.count(codegen.ENV_LOADER_MARKER) == 2,  # opening + closing marker
      "re-injecting an already-injected file changes nothing (marker "
      "count stays at the opening+closing pair, %d found)"
      % twice.count(codegen.ENV_LOADER_MARKER))
check(twice == with_api, "the second call is a true no-op")


# --- 4. the injected code actually loads .env and never shadows a real ---
# --- value that was already exported --------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    fake_env_path = os.path.join(tmp, ".env")
    with open(fake_env_path, "w", encoding="utf-8") as fh:
        fh.write("API_KEY=from-dotenv\nAPI_BASE_URL=https://from-dotenv\n"
                 "# a comment\n\nMALFORMED_LINE_NO_EQUALS\n")

    old_here = swarm.HERE
    swarm.HERE = tmp
    try:
        snippet = codegen.env_loader_snippet()
    finally:
        swarm.HERE = old_here

    check(fake_env_path in snippet,
          "the snippet points at the .env path under swarm.HERE")

    # Run the snippet for real, in a clean namespace, and confirm it fills
    # os.environ via setdefault -- both the "nothing set yet" case and the
    # "already exported, must not be shadowed" case, in one execution.
    saved = dict(os.environ)
    try:
        os.environ.pop("API_KEY", None)
        os.environ.pop("API_BASE_URL", None)
        os.environ["API_KEY"] = "already-exported-by-operator"
        exec(compile(snippet, "<snippet>", "exec"), {})
        check(os.environ["API_KEY"] == "already-exported-by-operator",
              "a value already in os.environ is never shadowed by .env")
        check(os.environ["API_BASE_URL"] == "https://from-dotenv",
              "a value missing from os.environ is filled in from .env")
    finally:
        os.environ.clear()
        os.environ.update(saved)


# --- 5. a missing .env is a silent no-op, not an error --------------------

with tempfile.TemporaryDirectory() as tmp:
    old_here = swarm.HERE
    swarm.HERE = tmp
    try:
        snippet = codegen.env_loader_snippet()
    finally:
        swarm.HERE = old_here
    # Must not raise even though tmp/.env does not exist.
    exec(compile(snippet, "<snippet>", "exec"), {})
    print("ok: a missing .env is a silent no-op, not an exception")


# --- sabotage: without setdefault (plain assignment), a real export -------
# --- would be overwritten, proving check 4 is not vacuous -----------------

SABOTAGED_TEMPLATE = codegen._ENV_LOADER_TEMPLATE.replace(
    "_os.environ.setdefault(_k.strip(), _v.strip().strip('\"').strip(\"'\"))",
    "_os.environ[_k.strip()] = _v.strip().strip('\"').strip(\"'\")")
check(SABOTAGED_TEMPLATE != codegen._ENV_LOADER_TEMPLATE,
      "sabotage setup: the template was actually changed")

with tempfile.TemporaryDirectory() as tmp:
    fake_env_path = os.path.join(tmp, ".env")
    with open(fake_env_path, "w", encoding="utf-8") as fh:
        fh.write("API_KEY=from-dotenv\n")
    bad_snippet = SABOTAGED_TEMPLATE % (
        codegen.ENV_LOADER_MARKER, fake_env_path, fake_env_path,
        codegen.ENV_LOADER_MARKER)
    saved = dict(os.environ)
    try:
        os.environ["API_KEY"] = "already-exported-by-operator"
        exec(compile(bad_snippet, "<snippet>", "exec"), {})
        check(os.environ["API_KEY"] == "from-dotenv",
              "sabotage -- plain assignment really does shadow a real "
              "export (proves check 4 was not vacuous)")
    finally:
        os.environ.clear()
        os.environ.update(saved)

print("all checks passed")
