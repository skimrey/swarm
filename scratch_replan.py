"""Two independent rolls failing the same way is evidence about the plan.

`gate_interfaces` already catches a call to a method a dependency does not
define, one AST walk per attempt -- fast and mechanical. What it cannot fix
is the CONTRACT that never gave that method to anyone in the first place, so
the entry point guessed a name and signature for it and burned every attempt
across two full passes on the same unsatisfiable call. Measured live: exactly
that shape, on `create_applet_version`.

This validates `intent.parse_replan` and `team.replan_component` against a
stubbed planner, and is sabotage-checked in both directions: a reply that
should be read as a real fix must parse, and one that says NONE (or is
unparseable) must not be treated as one.
"""
import os
import tempfile
import types

import intent
import swarm
import team


def test_parse_replan() -> None:
    reply = """CONTRACT: api_client.py owns MavenClient(api_base_url: str, api_key: str) with create_applet(slug: str, name: str) -> AppletResponse; create_applet_version(applet_id: str, version: str, artifact_url: str, integrity_sha256: str) -> AppletVersionResponse
SPEC: The entry point that creates an applet, a version of it with the html
content, a project, a task, and imports units.
SIBLING: api_client.py ADD: a create_applet_version method that POSTs to /api/applets/{applet_id}/versions
"""
    fix = intent.parse_replan(reply, "main.py")
    assert fix is not None
    assert "create_applet_version" in fix["contract"]
    assert "entry point" in fix["spec"]
    assert fix["sibling_path"] == "api_client.py"
    assert "create_applet_version" in fix["sibling_add"]

    # A spec-only fix (no sibling needed) is just as real a fix.
    fix2 = intent.parse_replan("CONTRACT: NONE\nSPEC: be more careful about "
                               "escaping the JSON body.\n", "main.py")
    assert fix2 is not None
    assert fix2["contract"] == ""
    assert "escaping" in fix2["spec"]
    assert "sibling_path" not in fix2

    # NONE, in any casing or with surrounding whitespace, is not a fix.
    assert intent.parse_replan("NONE", "main.py") is None
    assert intent.parse_replan("  none  ", "main.py") is None
    assert intent.parse_replan("", "main.py") is None
    # Unparseable prose is not silently treated as a fix either -- no SPEC
    # field means nothing usable was said about the failing file.
    assert intent.parse_replan("I think the code was just wrong.",
                               "main.py") is None
    print("parse_replan: real fixes parse, NONE and garbage do not")


def test_replan_component() -> None:
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    calls = []

    def fake_pick_role_model(_cfg, _state, _floor, exclude_family=None):
        return node, "gemma4:12b"

    def fake_chat_complete(_node, _model, messages, **kw):
        calls.append(messages[0]["content"])
        return {"text": ("CONTRACT: api_client.py owns MavenClient(...) "
                            "with create_applet_version(...)\n"
                            "SPEC: entry point spec, corrected\n"
                            "SIBLING: api_client.py ADD: a "
                            "create_applet_version method\n"),
               "sample": {"tokens": 10, "seconds": 0.1}}

    real_pick, real_chat = team.pick_role_model, swarm.chat_complete
    team.pick_role_model = fake_pick_role_model
    swarm.chat_complete = fake_chat_complete
    try:
        with tempfile.TemporaryDirectory() as root:
            # A real `swarm.State` on a throwaway db, not a hand-rolled
            # stub -- `pick_role_model` is faked, but `replan_component`
            # still routes through the real `pick_role_candidates`/
            # `_node_load`/`rates` chain underneath `claim_first_free_role`,
            # and that chain has grown new State methods more than once
            # since this test was written. A stub that only mocks what
            # was needed on the day it was written goes stale the next
            # time it grows one, which is exactly what happened here.
            state = swarm.State(os.path.join(root, "state.db"))
            sib = intent.Component("api_client", "api_client.py", 4, [],
                                   "the api client")
            sib.ok = True
            with open(os.path.join(root, "api_client.py"), "w") as fh:
                fh.write("class MavenClient:\n    pass\n")

            main_c = intent.Component("main", "main.py", 2, ["api_client"],
                                      "the entry point")
            main_c.contract = ("api_client.py owns MavenClient(...) with "
                               "create_applet(...)")
            main_c.error = "import: AttributeError: no create_applet_version"

            done = {"api_client": sib}
            fix = team.replan_component("do the thing", main_c.contract,
                                        main_c, done, cfg, state, root)
            assert fix is not None, "a real fix must parse"
            assert fix["sibling_path"] == "api_client.py"
            assert "create_applet_version" in fix["contract"]
            prompt = calls[0]
            assert "api_client.py" in prompt
            assert "class MavenClient" in prompt, (
                "the planner must see the sibling's real, current source, "
                "not a description of it")
            assert main_c.error in prompt
    finally:
        team.pick_role_model, swarm.chat_complete = real_pick, real_chat
    print("replan_component: grounds the planner in real sibling source "
         "and returns a parsed fix")


def test_replan_component_none_when_unhelpful() -> None:
    cfg = swarm.Config.load()
    node = cfg.nodes[0]

    def fake_pick_role_model(_cfg, _state, _floor, exclude_family=None):
        return node, "gemma4:12b"

    def fake_chat_complete(_node, _model, _messages, **kw):
        return {"text": "NONE", "sample": {"tokens": 1, "seconds": 0.1}}

    real_pick, real_chat = team.pick_role_model, swarm.chat_complete
    team.pick_role_model = fake_pick_role_model
    swarm.chat_complete = fake_chat_complete
    try:
        with tempfile.TemporaryDirectory() as root:
            state = swarm.State(os.path.join(root, "state.db"))
            main_c = intent.Component("main", "main.py", 2, [], "spec")
            main_c.error = "the code just had a typo"
            fix = team.replan_component("do the thing", "", main_c, {}, cfg,
                                        state, root)
            assert fix is None, "NONE must not be read as a fix"
    finally:
        team.pick_role_model, swarm.chat_complete = real_pick, real_chat
    print("replan_component: a NONE verdict changes nothing")


def test_replan_component_no_planner_available() -> None:
    cfg = swarm.Config.load()
    # `replan_component` claims through `claim_first_free_role`, not
    # `pick_role_model` directly (that hook is only used by the two
    # tests above, which fake it *underneath* a real
    # `claim_first_free_role` call) -- so simulating "no planner
    # available" has to fake the function that is actually called.
    real_claim = team.claim_first_free_role
    team.claim_first_free_role = lambda *a, **k: (None, None, False)
    try:
        main_c = intent.Component("main", "main.py", 2, [], "spec")
        fix = team.replan_component("req", "", main_c, {}, cfg, None, ".")
        assert fix is None, "no planner available must fail soft, not raise"
    finally:
        team.claim_first_free_role = real_claim
    print("replan_component: no planner available fails soft")


if __name__ == "__main__":
    test_parse_replan()
    test_replan_component()
    test_replan_component_none_when_unhelpful()
    test_replan_component_no_planner_available()
    print("OK")
