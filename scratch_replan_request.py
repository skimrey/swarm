"""Validates the request-level escalation rung above `replan_component`:
`intent.parse_request_replan` and `team.replan_request`.

`cmd_do` already restarts decomposition from scratch, once, from the same
request text, when components still fail after full per-component repair.
That catches a bad roll of the decomposition dice. It does not catch a
request that is itself the defect -- the exact live shape this project has
already hit once (a chain step's own request named `output.json` with no
path and no promise to write one, so no amount of re-decomposing that same
sentence was ever going to produce a working step). `replan_request` is the
rung one level above `replan_component`: it fires only after *both* the
per-component ladder and the existing whole-plan restart have failed, and
it is allowed to propose a corrected REQUEST rather than only a corrected
CONTRACT/SPEC for one file.
"""
import os
import tempfile
import types

import intent
import swarm
import team


def test_parse_request_replan() -> None:
    reply = ("REQUEST: create a project; save its id to output.json in "
             "this directory as project_id, matching what "
             "../02_create_task/main.py already reads\n")
    fixed = intent.parse_request_replan(reply)
    assert fixed is not None
    assert "project_id" in fixed
    assert fixed.startswith("create a project")

    # A multi-line reply is joined, mirroring parse_replan's own handling
    # of a SPEC that runs past one line.
    multiline = "REQUEST: create a project;\nsave its id as project_id.\n"
    fixed2 = intent.parse_request_replan(multiline)
    assert fixed2 is not None
    assert "project_id" in fixed2

    # NONE, in any casing, blank, or unparseable prose, is not a rewrite.
    assert intent.parse_request_replan("NONE") is None
    assert intent.parse_request_replan("  none  ") is None
    assert intent.parse_request_replan("") is None
    assert intent.parse_request_replan(
        "I think the request was fine, honestly.") is None
    print("parse_request_replan: real rewrites parse, NONE and garbage do not")


def _stub_state():
    return types.SimpleNamespace(record_perf=lambda *a: None,
                                 try_claim_node=lambda *a, **k: True,
                                 release_node=lambda *a: None,
                                 prune_dead_leases=lambda: None,
                                 load_for=lambda *a: (0, 0.0),
                                 register_waiter=lambda *a: 0,
                                 unregister_waiter=lambda *a: None)


def test_replan_request_grounds_in_full_context() -> None:
    """The prompt fed to the planner must carry: the original request, the
    CONTRACT, the failing file's own SPEC and its full attempt trail (not
    just the last rung's condensed line), and the real, current source of
    whatever already succeeded -- so a proposed rewrite cannot contradict
    working code it never saw."""
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    calls = []

    def fake_claim(_cfg, _state, _floors, family=None, exclude_family=None):
        return node, "gemma4:12b", False

    def fake_chat_complete(_node, _model, messages, **kw):
        calls.append(messages[0]["content"])
        return {"text": ("REQUEST: create a project; save its id to "
                            "output.json in this directory as "
                            "project_id\n"),
               "sample": {"tokens": 10, "seconds": 0.1}}

    real_claim, real_chat = team.claim_first_free_role, swarm.chat_complete
    team.claim_first_free_role = fake_claim
    swarm.chat_complete = fake_chat_complete
    try:
        with tempfile.TemporaryDirectory() as root:
            ok_c = intent.Component("task_creator", "task_creator.py", 4,
                                    [], "creates a task")
            ok_c.ok = True
            with open(os.path.join(root, "task_creator.py"), "w") as fh:
                fh.write("def create_task():\n    return 'real source'\n")

            failing = intent.Component("project_creator", "project_creator.py",
                                       4, [], "creates a project")
            failing.contract = "project_creator.py owns create_project(...)"
            failing.error = "KeyError: 'project_id'"
            failing.history_trail = [
                "[initial attempt(s) on g26b/gemma4:12b] smoke: "
                "KeyError: 'project_id'",
                "[fresh restart on g26b/gemma4:12b] smoke: "
                "KeyError: 'project_id'",
            ]

            components = [ok_c, failing]
            fixed = team.replan_request(
                "create a project; read the id back later somehow",
                failing.contract, components, cfg, _stub_state(), root)
            assert fixed is not None, "a real rewrite must parse"
            assert "project_id" in fixed

            prompt = calls[0]
            assert "create a project; read the id back later somehow" \
                in prompt
            assert failing.contract in prompt
            assert "creates a project" in prompt  # failing component's SPEC
            for line in failing.history_trail:
                assert line in prompt, (
                    "the full attempt trail, not just the last error, "
                    "must reach the request-level replanner")
            assert "def create_task():" in prompt, (
                "the real source of a component that already succeeded "
                "must ground the rewrite, not just its name"
            )
    finally:
        team.claim_first_free_role, swarm.chat_complete = real_claim, real_chat
    print("replan_request: grounds the planner in the full trail and real "
         "sibling source")


def test_replan_request_none_when_unhelpful() -> None:
    cfg = swarm.Config.load()
    node = cfg.nodes[0]

    def fake_claim(_cfg, _state, _floors, family=None, exclude_family=None):
        return node, "gemma4:12b", False

    def fake_chat_complete(_node, _model, _messages, **kw):
        return {"text": "NONE", "sample": {"tokens": 1, "seconds": 0.1}}

    real_claim, real_chat = team.claim_first_free_role, swarm.chat_complete
    team.claim_first_free_role = fake_claim
    swarm.chat_complete = fake_chat_complete
    try:
        with tempfile.TemporaryDirectory() as root:
            c = intent.Component("main", "main.py", 2, [], "spec")
            c.error = "the code just had a typo"
            fixed = team.replan_request("do the thing", "", [c],
                                        cfg, _stub_state(), root)
            assert fixed is None, "NONE must not be read as a rewrite"
    finally:
        team.claim_first_free_role, swarm.chat_complete = real_claim, real_chat
    print("replan_request: a NONE verdict changes nothing")


def test_replan_request_no_planner_available() -> None:
    cfg = swarm.Config.load()
    real_claim = team.claim_first_free_role
    team.claim_first_free_role = lambda *a, **k: (None, None, False)
    try:
        c = intent.Component("main", "main.py", 2, [], "spec")
        fixed = team.replan_request("req", "", [c], cfg, None, ".")
        assert fixed is None, "no planner available must fail soft, not raise"
    finally:
        team.claim_first_free_role = real_claim
    print("replan_request: no planner available fails soft")


def test_sabotage_without_history_trail_the_prompt_loses_the_real_evidence():
    """Proves `history_trail` is load-bearing: a version of `replan_request`
    that fell back to only `component.error` (the pre-fix state, before
    every rung logged its own attempt) would show the planner just the
    last rung's one line, not the sequence of independent failures the
    escalation is supposed to be evidence of."""
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    calls = []

    def fake_claim(_cfg, _state, _floors, family=None, exclude_family=None):
        return node, "gemma4:12b", False

    def fake_chat_complete(_node, _model, messages, **kw):
        calls.append(messages[0]["content"])
        return {"text": "NONE", "sample": {"tokens": 1, "seconds": 0.1}}

    real_claim, real_chat = team.claim_first_free_role, swarm.chat_complete
    team.claim_first_free_role = fake_claim
    swarm.chat_complete = fake_chat_complete
    try:
        with tempfile.TemporaryDirectory() as root:
            c = intent.Component("main", "main.py", 2, [], "spec")
            c.error = "smoke: KeyError: 'project_id'"
            c.history_trail = []  # sabotage: never populated
            team.replan_request("req", "", [c], cfg, _stub_state(), root)
            prompt = calls[0]
            # Falls back to the bare error line -- present, but the
            # multi-rung trail this feature exists to supply is not.
            assert "KeyError" in prompt
            assert "[initial attempt" not in prompt, (
                "with no history_trail there is nothing to distinguish "
                "this from the bare last-error line replan_component "
                "already had before this feature existed"
            )
    finally:
        team.claim_first_free_role, swarm.chat_complete = real_claim, real_chat
    print("sabotage: no history_trail means no multi-rung evidence, "
         "confirming the trail (not the bare error) is what this adds")


if __name__ == "__main__":
    test_parse_request_replan()
    test_replan_request_grounds_in_full_context()
    test_replan_request_none_when_unhelpful()
    test_replan_request_no_planner_available()
    test_sabotage_without_history_trail_the_prompt_loses_the_real_evidence()
    print("OK")
