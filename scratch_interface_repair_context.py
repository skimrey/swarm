"""`interface_gate` and `repair_redefinitions` used to build their own
repair prompt from scratch and discard everything `cmd_do` had already
attached to the ordinary write: the env var names ("read API_BASE_URL
from the environment"), the mock's base-URL note ("never hardcode a
hostname"), and any `--attach`ed reference material.

Measured live: a `03_upload_units/main.py` correctly read `API_BASE_URL`
on its winning attempt, was then flagged by `interface_gate` for an
unrelated cross-file mismatch (`requests.HTTPError` on a wrapper class
named `MockClient`), and the repair rewrite that fixed the mismatch
produced a file hardcoding `https://api.example.com` and
`"Bearer YOUR_API_TOKEN"` -- because the repair prompt never told it
there was an environment variable to read at all. `repair_redefinitions`
had the identical gap, one step worse: it passed no `extra` whatsoever,
not even the dependency context `interface_gate` already carried.

This validates that both functions now thread a `boilerplate` string
(the caller's own `attached_text`) into the `extra` they hand to
`codegen.generate_file`, and reproduces the exact live failure shape by
sabotage: calling them with `boilerplate=""` (the pre-fix default)
loses the env-var note precisely the way the live run did.
"""
import os
import sys
import tempfile

import codegen
import intent
import swarm
import team


FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- %s" % detail) if detail else ""))
        FAILURES.append(name)


def _state():
    tmp = tempfile.mkdtemp()
    return swarm.State(os.path.join(tmp, "state.db"))


ENV_NOTE = (
    "\n\nBASE URL: read the API base URL from the environment variable "
    "API_BASE_URL, falling back to the spec's own server URL when it is "
    "unset. Never hardcode a hostname.\n"
)


def _component(root):
    path = "main.py"
    full = os.path.join(root, path)
    with open(full, "w") as fh:
        fh.write("import requests\ndef main():\n    pass\n")
    c = intent.Component("main", path, 4, [], "upload three units")
    c.ok = True
    c.code = open(full).read()
    return c


def test_interface_gate_carries_boilerplate_into_repair():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    captured_extra = []

    def fake_generate_file(spec, out_path, node, model, cfg, state,
                           **kw):
        captured_extra.append(kw.get("extra", ""))
        return {"ok": False, "code": None}

    real_scan = None
    import crossref
    real_scan = crossref.scan

    def fake_scan(paths, root_, deps, paths_by_component):
        # One fabricated finding, exactly once, so the repair loop makes
        # its one repair call and then reports "not resolved" rather than
        # looping forever.
        if fake_scan.calls == 0:
            fake_scan.calls += 1
            return [crossref.Finding(component.path, 1,
                                     "calls requests.HTTPError, but "
                                     "MockClient in main.py has no such "
                                     "attribute", "")]
        return []
    fake_scan.calls = 0

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    codegen.generate_file = fake_generate_file
    crossref.scan = fake_scan
    try:
        team.interface_gate([component], cfg, state, 3000, root,
                            boilerplate=ENV_NOTE)
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan

    check("interface_gate's repair call received the env-var/base-URL "
          "note", any(ENV_NOTE in e for e in captured_extra),
          captured_extra)


def test_interface_gate_sabotage_no_boilerplate_loses_the_note():
    """Same fixture, called the pre-fix way (no boilerplate) -- proves the
    fixture actually exercises the gap the fix closes."""
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    captured_extra = []

    def fake_generate_file(spec, out_path, node, model, cfg, state,
                           **kw):
        captured_extra.append(kw.get("extra", ""))
        return {"ok": False, "code": None}

    import crossref

    def fake_scan(paths, root_, deps, paths_by_component):
        if fake_scan.calls == 0:
            fake_scan.calls += 1
            return [crossref.Finding(component.path, 1,
                                     "calls requests.HTTPError, but "
                                     "MockClient in main.py has no such "
                                     "attribute", "")]
        return []
    fake_scan.calls = 0

    old_generate = codegen.generate_file
    old_scan = crossref.scan
    codegen.generate_file = fake_generate_file
    crossref.scan = fake_scan
    try:
        team.interface_gate([component], cfg, state, 3000, root)
        # no boilerplate passed -- the pre-fix call shape
    finally:
        codegen.generate_file = old_generate
        crossref.scan = old_scan

    check("sabotage: with no boilerplate argument (the pre-fix call "
          "shape), the env-var note is absent from the repair prompt -- "
          "reproducing the live api.example.com failure's actual cause",
          not any(ENV_NOTE in e for e in captured_extra), captured_extra)


def test_repair_redefinitions_carries_boilerplate():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    captured_extra = []

    def fake_generate_file(spec, out_path, node, model, cfg, state,
                           **kw):
        captured_extra.append(kw.get("extra", ""))
        return {"ok": True, "code": "import requests\n"}

    old_generate = codegen.generate_file
    codegen.generate_file = fake_generate_file
    try:
        team.repair_redefinitions(
            [component], [(component.path, "helper_fn", "other.py")],
            cfg, state, None, 3000, root, boilerplate=ENV_NOTE)
    finally:
        codegen.generate_file = old_generate

    check("repair_redefinitions's repair call received the env-var note "
          "(it previously passed no extra at all)",
          any(ENV_NOTE in e for e in captured_extra), captured_extra)


def test_repair_redefinitions_sabotage_default_has_no_boilerplate():
    root = tempfile.mkdtemp()
    cfg = swarm.Config.load()
    state = _state()
    component = _component(root)

    captured_extra = []

    def fake_generate_file(spec, out_path, node, model, cfg, state,
                           **kw):
        captured_extra.append(kw.get("extra", ""))
        return {"ok": True, "code": "import requests\n"}

    old_generate = codegen.generate_file
    codegen.generate_file = fake_generate_file
    try:
        team.repair_redefinitions(
            [component], [(component.path, "helper_fn", "other.py")],
            cfg, state, None, 3000, root)
        # no boilerplate -- the pre-fix default
    finally:
        codegen.generate_file = old_generate

    check("sabotage: with no boilerplate argument, extra is empty -- the "
          "exact pre-fix call shape (this path used to pass no extra "
          "whatsoever)",
          captured_extra and captured_extra[0] == "", captured_extra)


test_interface_gate_carries_boilerplate_into_repair()
test_interface_gate_sabotage_no_boilerplate_loses_the_note()
test_repair_redefinitions_carries_boilerplate()
test_repair_redefinitions_sabotage_default_has_no_boilerplate()

print("\n%d failed" % len(FAILURES))
sys.exit(1 if FAILURES else 0)
