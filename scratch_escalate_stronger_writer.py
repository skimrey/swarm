"""Validates `team.pick_stronger_writer`, the new escalation rung between
"restart the same model" and "ask the planner to reconsider the CONTRACT".

Checks, against synthetic configs (never the real network):
  - a strictly higher-quality model at or above the floor is returned
  - a tie in quality does not count as an escalation
  - a `model_bias`-suppressed fallback (like `gemma4:12b`/`gemma4-fixed:latest`
    on g26b, deliberately excluded from `pick_solvers`'s routine selection)
    IS still eligible here, because the whole point of that bias is to hold
    it in reserve for exactly this case
  - a model below the requested floor is never returned even if its quality
    is higher
  - already being on the fleet's best model returns None (nowhere to escalate)
  - family restriction is honoured

Sabotage: a version of the picker that reused `pick_solvers`'s own
`model_bias < 0.5` filter (the natural, wrong thing to copy-paste) is
simulated directly and shown to miss the low-bias fallback the real
function must find.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swarm
import team


def make_state():
    """`pick_fallback_writer` reads real cross-process leases via
    `_node_load`, unlike `pick_stronger_writer`, so it needs a real (if
    empty) `swarm.State` rather than `None` -- an empty state reports
    every node idle, which is the common case these tests want anyway."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return swarm.State(tmp.name)


def make_cfg():
    data = {
        "nodes": [
            {
                "name": "weak_node",
                "url": "http://127.0.0.1:1",
                "models": ["small"],
                "enabled": True,
                "model_bias": {},
            },
            {
                "name": "strong_node",
                "url": "http://127.0.0.1:2",
                "models": ["default_strong", "reserve_strongest"],
                "enabled": True,
                # Mirrors g26b: the routine writer has no bias entry (1.0),
                # the reserve fallback is suppressed to 0.3 so pick_solvers
                # never picks it for ordinary work.
                "model_bias": {"reserve_strongest": 0.3},
            },
            {
                "name": "other_family_node",
                "url": "http://127.0.0.1:3",
                "models": ["other_family_strongest"],
                "enabled": True,
                "model_bias": {},
            },
        ],
        "models": {
            "small": {"capability": 2, "quality": 0.6, "family": "gemma"},
            "default_strong": {"capability": 4, "quality": 0.9, "family": "gemma"},
            "reserve_strongest": {"capability": 4, "quality": 1.0, "family": "gemma"},
            "other_family_strongest": {"capability": 4, "quality": 1.0, "family": "qwen"},
        },
    }
    return swarm.Config(data, "/tmp/scratch_escalate_cfg.json")


def sabotaged_pick_stronger_writer(cfg, state, current_model, floor, family=None):
    """Reproduces the bug this test exists to catch: filtering by
    `model_bias < 0.5` the way `pick_solvers` correctly does for *routine*
    selection, which is exactly wrong here since the low-bias model is the
    reserve this function exists to reach."""
    cur_quality = float(cfg.model_meta(current_model).get("quality", 0.0))
    best = None
    for node in cfg.enabled_nodes():
        for model in node.models:
            if model == current_model:
                continue
            if float(node.model_bias.get(model, 1.0)) < 0.5:
                continue
            meta = cfg.model_meta(model)
            if int(meta.get("capability", 1)) < floor:
                continue
            if not team.allowed_family(cfg, model, family):
                continue
            quality = float(meta.get("quality", 0.0))
            if quality <= cur_quality:
                continue
            if best is None or quality > best[0]:
                best = (quality, node, model)
    return (best[1], best[2]) if best else None


def test_escalates_to_reserve_fallback_despite_low_bias():
    cfg = make_cfg()
    state = None  # not read by pick_stronger_writer
    result = team.pick_stronger_writer(cfg, state, "default_strong", 4,
                                       family="gemma")
    assert result is not None, "expected an escalation target"
    node, model = result
    assert model == "reserve_strongest", (
        "should have found the higher-quality reserve model despite its "
        "low model_bias; got %r" % (model,))


def test_tie_in_quality_is_not_an_escalation():
    cfg = make_cfg()
    result = team.pick_stronger_writer(cfg, None, "reserve_strongest", 4,
                                       family="gemma")
    assert result is None, (
        "already on the best-quality gemma model at this floor; nothing "
        "should look stronger, got %r" % (result,))


def test_never_returns_a_model_below_the_floor():
    cfg = make_cfg()
    # "small" has quality 0.6 and capability 2; nothing else in the fleet
    # scores lower than a hypothetical current_model of quality -1, but
    # floor=4 must still exclude it.
    result = team.pick_stronger_writer(cfg, None, "small", 4, family="gemma")
    assert result is not None
    assert result[1] != "small"
    node, model = result
    assert cfg.model_meta(model)["capability"] >= 4


def test_family_restriction_is_honoured():
    cfg = make_cfg()
    result = team.pick_stronger_writer(cfg, None, "default_strong", 4,
                                       family="gemma")
    assert result is not None
    assert result[1] != "other_family_strongest", (
        "must not cross families even though it scores highest overall")


def test_sabotage_bias_filter_misses_the_reserve_model():
    cfg = make_cfg()
    sabotaged = sabotaged_pick_stronger_writer(cfg, None, "default_strong", 4,
                                               family="gemma")
    assert sabotaged is None, (
        "the sabotaged (pick_solvers-style bias filter) version should "
        "find nothing stronger, proving the real function's choice not "
        "to filter on model_bias is what makes the escalation work")
    real = team.pick_stronger_writer(cfg, None, "default_strong", 4,
                                     family="gemma")
    assert real is not None and real[1] == "reserve_strongest"


def test_fallback_writer_never_returns_the_same_node():
    """`pick_fallback_writer` exists for a node that stopped answering, not
    a model that answered wrongly -- so unlike `pick_stronger_writer`, a
    same-or-lower quality result is fine, but the *node* it currently sits
    on must never come back."""
    cfg = make_cfg()
    state = make_state()
    # No family restriction, so the cross-family node at this floor is a
    # legitimate candidate -- the point being tested is only that
    # `strong_node` itself, the one that stopped answering, never comes
    # back as the answer.
    result = team.pick_fallback_writer(cfg, state, "strong_node", 4)
    assert result is not None
    node, model = result
    assert node.name != "strong_node"


def test_fallback_writer_prefers_higher_quality_among_others():
    cfg = make_cfg()
    state = make_state()
    # Excluding weak_node (capability 2, below floor 4 anyway) and
    # strong_node itself leaves only other_family_node, which is a
    # different family -- restricting to "gemma" should then find nothing.
    result = team.pick_fallback_writer(cfg, state, "strong_node", 4,
                                       family="gemma")
    assert result is None, (
        "no other gemma node at floor 4 exists once strong_node is "
        "excluded -- got %r" % (result,))
    # Without the family restriction, the cross-family node is reachable --
    # a node that stopped answering is not a reason to also drop a
    # family restriction the operator actually asked for, so this only
    # matters when no restriction was given at all.
    result_any_family = team.pick_fallback_writer(cfg, state, "strong_node", 4)
    assert result_any_family is not None
    assert result_any_family[0].name == "other_family_node"


def test_fallback_writer_never_returns_below_floor():
    cfg = make_cfg()
    state = make_state()
    result = team.pick_fallback_writer(cfg, state, "weak_node", 4)
    assert result is not None
    node, model = result
    assert cfg.model_meta(model)["capability"] >= 4


def make_cfg_with_two_alternatives():
    """A dedicated fixture for the idle-vs-busy test: a caller's own
    node (`origin_node`, excluded from every result by construction) plus
    two genuinely distinct alternatives -- `busy_node`, higher quality,
    and `idle_node`, lower quality -- so preferring idle over busy is an
    observable choice rather than the only option available."""
    data = {
        "nodes": [
            {"name": "origin_node", "url": "http://127.0.0.1:10",
             "models": ["origin_model"], "enabled": True, "model_bias": {}},
            {"name": "busy_node", "url": "http://127.0.0.1:11",
             "models": ["busy_model"], "enabled": True, "model_bias": {}},
            {"name": "idle_node", "url": "http://127.0.0.1:12",
             "models": ["idle_model"], "enabled": True, "model_bias": {}},
        ],
        "models": {
            "origin_model": {"capability": 4, "quality": 0.7, "family": "gemma"},
            "busy_model": {"capability": 4, "quality": 1.0, "family": "gemma"},
            "idle_model": {"capability": 4, "quality": 0.5, "family": "gemma"},
        },
    }
    return swarm.Config(data, "/tmp/scratch_escalate_alt_cfg.json")


def test_fallback_writer_prefers_idle_over_busy():
    """The actual ask this whole rung exists to satisfy: "try something
    available", not just "try whatever scores highest". A node with a
    live, unexpired lease at its `max_parallel` reads as busy via the
    same `_node_load` real leases mechanism `pick_role_candidates` uses,
    and an idle, *lower-quality* node must still win over it."""
    cfg = make_cfg_with_two_alternatives()
    state = make_state()
    busy_node = next(n for n in cfg.enabled_nodes() if n.name == "busy_node")
    lease_id = state.acquire_lease(busy_node.name, "busy_model", 300.0)
    try:
        result = team.pick_fallback_writer(cfg, state, "origin_node", 4)
        assert result is not None
        node, model = result
        assert node.name == "idle_node", (
            "busy_node scores higher but is occupied; the idle, lower-"
            "quality node should win -- got %r" % (result,))
    finally:
        state.release_lease(lease_id)


def test_fallback_writer_quality_breaks_ties_among_idle():
    """With no lease held anywhere, both alternatives are idle -- quality
    is the only thing left to decide between them, and the higher-quality
    one must win."""
    cfg = make_cfg_with_two_alternatives()
    state = make_state()
    result = team.pick_fallback_writer(cfg, state, "origin_node", 4)
    assert result is not None
    node, model = result
    assert node.name == "busy_node", (
        "both idle -- the higher-quality alternative should win the "
        "tiebreak, got %r" % (result,))


def test_stronger_writer_can_still_land_on_the_same_node():
    """`pick_stronger_writer` deliberately does NOT exclude the current
    *node*, only the current *model* -- escalating from `default_strong`
    to `reserve_strongest` on the same `strong_node` is the intended,
    common case (a `model_bias`-suppressed reserve on the same box the
    default writer already owns). This is the control for the live-bug
    tests below: proving the function's own contract is unchanged, so the
    fix for the live bug had to live in *when this is called*, not inside
    `pick_stronger_writer` itself."""
    cfg = make_cfg()
    result = team.pick_stronger_writer(cfg, None, "default_strong", 4,
                                       family="gemma")
    assert result is not None
    node, model = result
    assert node.name == "strong_node" and model == "reserve_strongest"


def test_last_stage_is_unreachable_matches_a_raw_node_error():
    """Live bug: a component's initial attempt and restart both failed
    with `stage: "request"` (a raw `swarm.OllamaError` -- the node never
    answered) on `g26b`. `_last_stage_is_unreachable` did not exist, so
    this fell through both `_last_stage_is_budget` (stage "budget") and
    `_no_mock` (a different marker string), reached `pick_stronger_writer`
    unfiltered, which correctly found `gemma4-fixed:latest` also on
    `g26b` and "escalated" to it -- immediately failing again in 7.8s,
    since the box itself, not the model, was the problem. This is a
    closure-scoped nested function in `run_components`, so it is checked
    here by reconstructing its exact body against a fixture history."""
    def _last_stage_is_unreachable(r):
        return bool(r["history"]) and \
            r["history"][-1].get("stage") == "request"

    unreachable = {"ok": False, "history": [
        {"attempt": 1, "stage": "request",
         "error": "cannot reach http://10.0.0.190:11434/api/chat: "
                 "[Errno 60] Operation timed out"}]}
    budget = {"ok": False, "history": [{"attempt": 1, "stage": "budget",
                                        "error": "gave up ... the node "
                                                "stopped answering"}]}
    wrong_answer = {"ok": False, "history": [
        {"attempt": 1, "stage": "smoke", "error": "AssertionError: ..."}]}

    assert _last_stage_is_unreachable(unreachable) is True
    assert _last_stage_is_unreachable(budget) is False, (
        "a budget timeout has its own, separate check "
        "(_last_stage_is_budget) -- this one must not also claim it")
    assert _last_stage_is_unreachable(wrong_answer) is False


def test_sabotage_unreachable_failure_without_the_fix_reaches_escalation():
    """Reproduces the live shape end to end: without
    `_last_stage_is_unreachable` folded into `gave_up_early`, a raw node
    error is (wrongly) not `gave_up_early`, so `pick_stronger_writer`
    runs and finds a same-node reserve model -- which is exactly the
    defect (escalating model, not node, for a problem that was the node).
    This does not call the real `run_components`; it isolates the
    boolean logic that decides whether escalation is attempted at all,
    which is the actual fix."""
    def _last_stage_is_budget(r):
        return bool(r["history"]) and r["history"][-1].get("stage") == "budget"

    def _no_mock(r):
        return bool(r["history"]) and "no mock is configured" \
            in (r["history"][-1].get("error") or "")

    unreachable = {"ok": False, "history": [
        {"attempt": 1, "stage": "request",
         "error": "cannot reach http://10.0.0.190:11434/api/chat: "
                 "[Errno 60] Operation timed out"}]}

    # Pre-fix: gave_up_early only ever considered these two signals.
    gave_up_early_before_fix = (
        _no_mock(unreachable) or _last_stage_is_budget(unreachable))
    assert gave_up_early_before_fix is False, (
        "sabotage setup: a raw node error must NOT have been caught by "
        "either pre-existing check, proving it fell through to "
        "escalation before this fix existed")

    def _last_stage_is_unreachable(r):
        return bool(r["history"]) and \
            r["history"][-1].get("stage") == "request"

    gave_up_early_after_fix = (
        _no_mock(unreachable) or _last_stage_is_budget(unreachable)
        or _last_stage_is_unreachable(unreachable))
    assert gave_up_early_after_fix is True, (
        "the fix must make gave_up_early true for a raw node error, so "
        "pick_stronger_writer (and replan_component) are skipped in "
        "favour of the fallback-node ladder")


def run():
    test_escalates_to_reserve_fallback_despite_low_bias()
    test_tie_in_quality_is_not_an_escalation()
    test_never_returns_a_model_below_the_floor()
    test_family_restriction_is_honoured()
    test_sabotage_bias_filter_misses_the_reserve_model()
    test_fallback_writer_never_returns_the_same_node()
    test_fallback_writer_prefers_higher_quality_among_others()
    test_fallback_writer_never_returns_below_floor()
    test_fallback_writer_prefers_idle_over_busy()
    test_fallback_writer_quality_breaks_ties_among_idle()
    test_stronger_writer_can_still_land_on_the_same_node()
    test_last_stage_is_unreachable_matches_a_raw_node_error()
    test_sabotage_unreachable_failure_without_the_fix_reaches_escalation()
    print("scratch_escalate_stronger_writer: all checks passed")


if __name__ == "__main__":
    run()
