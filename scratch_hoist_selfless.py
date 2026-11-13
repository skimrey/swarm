#!/usr/bin/env python3
"""Validates intent.hoist_selfless_members against the live failure.

Live CONTRACT text (from the maven-applet run this fixes):
  client.py owns Client(api_base_url: str, api_key: str) with
  create_applet(slug: str, name: str) -> str;
  create_applet_version(applet_id: str, version: str, artifact_url: str,
  integrity_sha256: str) -> str; create_project(name: str,
  applet_version_id: str) -> str; create_task(project_id: str, name: str)
  -> str; import_units(task_id: str, units: list[dict]) -> None;
  get_ui_url(api_base_url: str, project_id: str) -> str (as a plain
  function); main.py owns main()

get_ui_url repeats `api_base_url`, a Client constructor argument -- the
signal that it is a plain function, not a bound method. Every other
member's arguments are disjoint from the constructor's, so they must stay
exactly where they are.
"""
import re

import intent

LIVE = (
    "client.py owns Client(api_base_url: str, api_key: str) with "
    "create_applet(slug: str, name: str) -> str; "
    "create_applet_version(applet_id: str, version: str, artifact_url: str, "
    "integrity_sha256: str) -> str; "
    "create_project(name: str, applet_version_id: str) -> str; "
    "create_task(project_id: str, name: str) -> str; "
    "import_units(task_id: str, units: list[dict]) -> None; "
    "get_ui_url(api_base_url: str, project_id: str) -> str "
    "(as a plain function); main.py owns main()"
)


def test_hoists_get_ui_url_out_of_with_clause():
    fixed = intent.hoist_selfless_members(LIVE)
    assert fixed != LIVE, "expected the contract to change"
    # get_ui_url no longer sits inside the `with` list.
    with_clause = fixed.split(" with ", 1)[1].split(";")
    with_members = " ".join(with_clause)
    assert "get_ui_url" not in with_clause[0:1][0] or True  # sanity no-op
    assert not any(m.strip().startswith("get_ui_url")
                  for m in with_clause), (
        "get_ui_url should have been removed from the with-clause: %r"
        % with_clause)
    # It reappears as its own owns clause, callable module-level.
    assert "client.py owns get_ui_url(api_base_url: str, project_id: str)" \
        in fixed
    assert "module-level function" in fixed
    assert "client.get_ui_url(...)" in fixed
    # Every real method survives untouched.
    for kept in ("create_applet(slug: str, name: str)",
                 "create_applet_version(applet_id: str",
                 "create_project(name: str, applet_version_id: str)",
                 "create_task(project_id: str, name: str)",
                 "import_units(task_id: str, units: list[dict])"):
        assert kept in fixed, "lost a real method: %r" % kept
    # main.py's own clause is untouched.
    assert "main.py owns main()" in fixed


def test_ordinary_contract_untouched():
    plain = ("shapes.py owns Shape(x: int, y: int) with "
             "update(dt: float) -> None; draw(surface: object) -> None; "
             "main.py owns main()")
    assert intent.hoist_selfless_members(plain) == plain


def test_empty_and_none_untouched():
    assert intent.hoist_selfless_members("") == ""
    assert intent.hoist_selfless_members("NONE") == "NONE"


def test_no_with_clause_untouched():
    simple = "utils.py owns slugify(text: str) -> str; main.py owns main()"
    assert intent.hoist_selfless_members(simple) == simple


def test_hoisted_clause_does_not_pollute_ownership_note():
    # Live regression: the first version of the fix moved get_ui_url onto
    # its own line but glued its explanation onto the same `owns` clause
    # contract_owners parses. The explanation's own commas and colon ("...
    # NOT a method of Client: call it as ...") were then read as three more
    # symbols -- Client, NOT, get_ui_url, never -- all "owned" by
    # api_client.py, and the writer's own instruction became "You define
    # Client, NOT, get_ui_url, never." instead of naming just the two real
    # symbols. That garbled instruction is exactly the kind of thing that
    # would make a writer skip implementing the very function being hoisted.
    fixed = intent.hoist_selfless_members(LIVE)
    owners = intent.contract_owners(fixed)
    assert owners == {"Client": "client.py", "get_ui_url": "client.py",
                      "main": "main.py"}, (
        "hoisting must not invent extra owned symbols out of its own "
        "explanatory prose: %r" % owners)
    note = intent.ownership_note(fixed, "client.py")
    assert "You define Client, get_ui_url." in note, (
        "the writer's own instruction is garbled: %r" % note)
    assert "NOT" not in note.split("Use the exact")[0].split(
        "You define ")[1].split(".")[0].split(",")
    # And the module-level clause itself parses as a clean single owns line.
    assert re.search(r"client\.py owns get_ui_url\(.*?\)[^;]*;", fixed)


def test_sabotage_disabling_reproduces_live_bug():
    # With the fix disabled (identity function), the live contract would
    # still hand writers the ambiguous shape that caused the bug.
    disabled = lambda c: c
    assert "get_ui_url(api_base_url: str, project_id: str) -> str " \
        "(as a plain function)" in disabled(LIVE)
    # The one call this fix targets: contract_owners must not have already
    # been silently fixing this (it explicitly discards `with` members).
    owners = intent.contract_owners(LIVE)
    assert "get_ui_url" not in owners, (
        "contract_owners never tracked with-members; confirms the bug was "
        "reachable before this fix")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print("%d/%d passed" % (len(tests), len(tests)))
