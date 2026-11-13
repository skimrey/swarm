"""Validates the network-based repair for `intent.chain_dangling_version_
problems`: `intent.dangling_version_findings`, `intent.
CHAIN_VERSION_REPAIR_PROMPT`/`parse_chain_version_repair`, and
`team.repair_dangling_version_chain` end to end.

Before this, a chain step that consumed "the new version" with no named
source was reported and the run stopped, unconditionally -- the operator
had to hand-edit the request text before any real API call could happen.
This validates the alternative asked for directly: use the pool to
propose a fix, verify it mechanically against the exact same two ground-
truth checks an operator would otherwise satisfy by hand
(`chain_dangling_version_problems`, `chain_output_json_problems`), and
only ever apply a fix that passes both.
"""
import types

import intent
import swarm
import team


LIVE_STEPS = [
    ("01_create_project",
     "create a project; save its id as project_id in a new output.json "
     "in the same directory as the script"),
    ("05_create_applet",
     "create an applet; save its id as applet_id in a new output.json "
     "in the same directory as the script"),
    ("06_update_applet",
     "read applet_id from ../05_create_applet/output.json; push "
     "../04_create_survey/survey.html to the applet"),
    ("07_update_project",
     "read project_id from ../01_create_project/output.json; update "
     "the project with the new version"),
]


def _raw(steps):
    return [{"dir": d, "request": r, "api": "maven"} for d, r in steps]


def test_dangling_version_findings_matches_the_live_shape() -> None:
    findings = intent.dangling_version_findings(LIVE_STEPS)
    assert len(findings) == 1
    idx, consumer, producer = findings[0]
    assert consumer == "07_update_project"
    # The live bug: 06_update_applet never says the word "version" at
    # all, so the literal-mention scan correctly finds no producer --
    # that gap is exactly what the network repair exists to close.
    assert producer is None
    assert idx == 3
    print("dangling_version_findings: matches the live 06/07 shape, "
         "correctly finding no literal producer")


def test_parse_chain_version_repair() -> None:
    reply = ("Here is the fix.\n```json\n"
            "[{\"dir\": \"01_create_project\", \"request\": \"create a "
            "project\", \"api\": \"maven\"},\n"
            " {\"dir\": \"06_push_applet_version\", \"request\": \"push "
            "the new version\", \"api\": \"maven\"}]\n```\n")
    parsed = intent.parse_chain_version_repair(reply)
    assert parsed is not None
    assert parsed[1]["dir"] == "06_push_applet_version"
    assert intent.parse_chain_version_repair("NONE") is None
    assert intent.parse_chain_version_repair("not json at all") is None
    assert intent.parse_chain_version_repair("```json\n[1,2,3]\n```") is None
    print("parse_chain_version_repair: parses a real fenced fix, "
         "rejects garbage")


def _stub_state():
    return types.SimpleNamespace(record_perf=lambda *a: None,
                                 try_claim_node=lambda *a, **k: True,
                                 release_node=lambda *a: None,
                                 prune_dead_leases=lambda: None,
                                 load_for=lambda *a: (0, 0.0),
                                 register_waiter=lambda *a: 0,
                                 unregister_waiter=lambda *a: None)


def _patch(fake_chat):
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    real_claim, real_chat = team.claim_first_free_role, swarm.chat_complete
    team.claim_first_free_role = lambda *a, **k: (node, "gemma4:12b", False)
    swarm.chat_complete = fake_chat
    return cfg, real_claim, real_chat


def test_repair_merges_the_two_steps_when_the_pool_proposes_a_merge() -> None:
    """The pool notices 06_update_applet is the real producer (even
    though it never says "version") and proposes merging it with 07,
    the LOCAL.md-preferred fix. The merge must be validated and applied,
    replacing exactly those two steps and nothing else."""
    raw = _raw(LIVE_STEPS)
    calls = []

    def fake_chat(_node, _model, messages, **kw):
        calls.append(messages[0]["content"])
        return {"text": (
            "```json\n"
            "[{\"dir\": \"01_create_project\", \"request\": \""
            + LIVE_STEPS[0][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"05_create_applet\", \"request\": \""
            + LIVE_STEPS[1][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"06_push_applet_version\", "
            "\"request\": \"read applet_id from "
            "../05_create_applet/output.json; push "
            "../04_create_survey/survey.html as a new version of the "
            "applet; read project_id from "
            "../01_create_project/output.json; point the project at "
            "that new version\", \"api\": \"maven\"}]\n```\n"),
            "sample": {"tokens": 10, "seconds": 0.1}}

    cfg, real_claim, real_chat = _patch(fake_chat)
    try:
        new_raw, remaining, notes = team.repair_dangling_version_chain(
            raw, cfg, _stub_state())
        assert not remaining, "the merged step must satisfy both checks"
        assert notes and "pool" in notes[0]
        dirs = [item["dir"] for item in new_raw]
        assert dirs == ["01_create_project", "05_create_applet",
                        "06_push_applet_version"]
        assert new_raw[0]["request"] == LIVE_STEPS[0][1], (
            "an untouched step must survive byte-identical")
        assert new_raw[1]["request"] == LIVE_STEPS[1][1]
        # The prompt actually told the model which step was flagged and
        # showed it the whole chain, not just the two steps involved.
        assert "07_update_project" in calls[0]
        assert "06_update_applet" in calls[0]
    finally:
        team.claim_first_free_role, swarm.chat_complete = (
            real_claim, real_chat)
    print("repair_dangling_version_chain: applies a validated merge, "
         "leaves the untouched step alone")


def test_repair_rejects_a_fix_that_still_fails_the_check() -> None:
    """A reply that parses fine but does not actually resolve the
    dangling reference (still says "the new version" with nothing
    tying it to a real source) must never be applied."""
    raw = _raw(LIVE_STEPS)

    def fake_chat(_node, _model, _messages, **kw):
        return {"text": (
            "```json\n"
            "[{\"dir\": \"01_create_project\", \"request\": \""
            + LIVE_STEPS[0][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"05_create_applet\", \"request\": \""
            + LIVE_STEPS[1][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"06_update_applet\", \"request\": \""
            + LIVE_STEPS[2][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"07_update_project\", \"request\": "
            "\"read project_id from ../01_create_project/output.json; "
            "update the project with the new version\", "
            "\"api\": \"maven\"}]\n```\n"),
            "sample": {"tokens": 5, "seconds": 0.1}}

    cfg, real_claim, real_chat = _patch(fake_chat)
    try:
        new_raw, remaining, notes = team.repair_dangling_version_chain(
            raw, cfg, _stub_state())
        assert remaining, "an unresolved fix must not be reported as fixed"
        assert any("could not repair" in n for n in notes)
        assert new_raw == raw, "a rejected fix must never be applied"
    finally:
        team.claim_first_free_role, swarm.chat_complete = (
            real_claim, real_chat)
    print("repair_dangling_version_chain: refuses a fix that does not "
         "actually pass the check")


def test_repair_rejects_a_fix_that_touches_an_unrelated_step() -> None:
    """A reply that fixes the named problem but also rewords an
    unrelated step must be refused outright -- the same "touch only
    what was asked" rule this project applies to every other repair."""
    raw = _raw(LIVE_STEPS)

    def fake_chat(_node, _model, _messages, **kw):
        return {"text": (
            "```json\n[{\"dir\": \"01_create_project\", \"request\": "
            "\"create a totally different project somehow\", "
            "\"api\": \"maven\"},\n"
            " {\"dir\": \"05_create_applet\", \"request\": \""
            + LIVE_STEPS[1][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"06_push_applet_version\", \"request\": "
            "\"push survey.html as a new version and point the "
            "project at it\", \"api\": \"maven\"}]\n```\n"),
            "sample": {"tokens": 10, "seconds": 0.1}}

    cfg, real_claim, real_chat = _patch(fake_chat)
    try:
        new_raw, remaining, notes = team.repair_dangling_version_chain(
            raw, cfg, _stub_state())
        assert new_raw == raw, (
            "touching 01_create_project, which the problem never named, "
            "must void the whole fix")
        assert remaining
    finally:
        team.claim_first_free_role, swarm.chat_complete = (
            real_claim, real_chat)
    print("repair_dangling_version_chain: refuses a fix that touches a "
         "step outside the flagged window")


def test_repair_rejects_a_non_adjacent_producer() -> None:
    """The automatic repair only ever handles the adjacent-steps shape.
    A chain where the mentioned producer sits several steps earlier
    (not directly before the consumer) must be left alone rather than
    guessed through by silently reordering steps."""
    steps = [
        ("01_make_version", "create a version of the thing"),
        ("02_unrelated", "do something else entirely"),
        ("03_consume", "read x from ../01_make_version/output.json; "
         "update the record with the new version"),
    ]
    raw = _raw(steps)

    def fake_chat(_node, _model, _messages, **kw):
        return {"text": (
            "```json\n[{\"dir\": \"01_make_version\", \"request\": \""
            + steps[0][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"02_unrelated\", \"request\": \""
            + steps[1][1] + "\", \"api\": \"maven\"},\n"
            " {\"dir\": \"03_consume\", \"request\": \"read "
            "version_id from ../01_make_version/output.json; update "
            "with it\", \"api\": \"maven\"}]\n```\n"),
            "sample": {"tokens": 5, "seconds": 0.1}}

    cfg, real_claim, real_chat = _patch(fake_chat)
    try:
        new_raw, remaining, notes = team.repair_dangling_version_chain(
            raw, cfg, _stub_state())
        # A literally-mentioned producer that is not adjacent to the
        # consumer is refused outright, on purpose -- the automatic
        # repair only understands the one shape this project's chains
        # actually take (an adjacent pair, or no literal producer at
        # all), never a reorder across an unrelated step in between.
        assert new_raw == raw
        assert remaining
        assert any("could not repair" in n for n in notes)
    finally:
        team.claim_first_free_role, swarm.chat_complete = (
            real_claim, real_chat)
    print("repair_dangling_version_chain: a non-adjacent producer does "
         "not corrupt the fix window")


def test_parse_chain_version_repair_takes_the_last_fence_not_the_first() -> None:
    """Measured live: `think=False` does not stop this model from
    reasoning in prose, and a reply choosing between two named repair
    strategies routinely tries one, catches its own mistake in the text
    between fences ("Wait, if I merge 07 into 06, 07 no longer
    exists..."), and only then writes the real answer -- in a second
    fenced block. The old `re.search` grabbed the first fence
    unconditionally, so every one of these self-correcting replies was
    read as the model's abandoned first guess, not its answer, and
    `repair_dangling_version_chain` reported "could not repair" on a
    fix that was sitting one fence later in the very same reply."""
    reply = (
        "Corrected Chain:\n```json\n"
        "[{\"dir\": \"06_update_applet\", \"request\": \"draft one, "
        "self-contradictory\", \"api\": \"maven\"}]\n```\n"
        "Wait, that drops a step. Let me try the other option.\n"
        "```json\n"
        "[{\"dir\": \"06_update_applet\", \"request\": \"read applet_id "
        "from ../05_create_applet/output.json; update the applet and "
        "save the new version_id in a new output.json in the same "
        "directory as the script\", \"api\": \"maven\"},\n"
        " {\"dir\": \"07_update_project\", \"request\": \"read "
        "project_id from ../01_create_project/output.json; read "
        "version_id from ../06_update_applet/output.json; update the "
        "project with the new version\", \"api\": \"maven\"}]\n```\n")
    parsed = intent.parse_chain_version_repair(reply)
    assert parsed is not None
    assert len(parsed) == 2, (
        "must have taken the second, well-formed fence, not the "
        "one-item draft in the first")
    assert parsed[0]["dir"] == "06_update_applet"
    assert "save the new version_id" in parsed[0]["request"]
    assert parsed[1]["dir"] == "07_update_project"
    assert "version_id from ../06_update_applet" in parsed[1]["request"]
    print("parse_chain_version_repair: takes the last well-formed "
         "fence, not the model's abandoned first draft")


def test_sabotage_first_fence_extraction_reproduces_the_live_failure() -> None:
    """By sabotage: reproducing the exact pre-fix behaviour (grab only
    the first fence via `re.search`) against the identical reply above
    shows it returns the self-contradictory one-item draft instead of
    the real fix -- proving the last-fence fix is load-bearing and not
    incidentally correct."""
    import re
    reply = (
        "Corrected Chain:\n```json\n"
        "[{\"dir\": \"06_update_applet\", \"request\": \"draft one, "
        "self-contradictory\", \"api\": \"maven\"}]\n```\n"
        "Wait, that drops a step. Let me try the other option.\n"
        "```json\n"
        "[{\"dir\": \"06_update_applet\", \"request\": \"the real fix\", "
        "\"api\": \"maven\"},\n"
        " {\"dir\": \"07_update_project\", \"request\": \"also real\", "
        "\"api\": \"maven\"}]\n```\n")
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", reply, re.DOTALL)
    import json as _json
    old_behaviour = _json.loads(fence.group(1).strip())
    assert len(old_behaviour) == 1, (
        "the old first-fence extraction reproduces the exact live bug: "
        "it reads the abandoned one-item draft")
    assert old_behaviour[0]["request"] == "draft one, self-contradictory"
    print("sabotage: unpatched first-fence extraction reproduces the "
         "live failure exactly, confirming the fix is load-bearing")


def test_version_key_read_matches_the_bare_unprefixed_name() -> None:
    """Measured live: `_VERSION_KEY_READ_RE` required at least one
    character ahead of the literal "version", so a prefixed name like
    "applet_version_id" matched but the single most natural, minimal
    name a fix would ever propose -- bare "version_id", with no prefix
    at all -- could never match. Every real repair reply that correctly
    proposed "read version_id from ../06_update_applet/output.json" was
    rejected by `_validate_dangling_version_fix`'s re-check against
    `chain_dangling_version_problems`, because this regex still read
    the corrected text as still dangling."""
    fixed_text = ("read project_id from ../01_create_project/output.json; "
                 "read version_id from ../06_update_applet/output.json; "
                 "update the project with the new version")
    assert intent._VERSION_KEY_READ_RE.search(fixed_text) is not None
    assert intent.dangling_version_reference(fixed_text) is False

    # The prefixed form must keep matching too -- broadening must not
    # narrow the case that already worked.
    prefixed = ("read applet_version_id from "
               "../06_update_applet/output.json; update with the new "
               "version")
    assert intent._VERSION_KEY_READ_RE.search(prefixed) is not None
    assert intent.dangling_version_reference(prefixed) is False

    # A genuinely still-dangling request (no key read at all) must stay
    # flagged.
    still_dangling = ("read project_id from ../01/output.json; update "
                      "the project with the new version")
    assert intent.dangling_version_reference(still_dangling) is True
    print("_VERSION_KEY_READ_RE: matches the bare 'version_id' name, "
         "keeps matching the prefixed form, still flags a real gap")


def test_sabotage_unfixed_version_key_regex_rejects_the_real_fix():
    """By sabotage: the exact pre-fix regex (mandatory leading
    character before the "version" literal) is reconstructed inline
    and shown to reject the bare "version_id" key -- reproducing the
    live failure where a correct fix was never recognized as one."""
    import re
    old_re = re.compile(
        r'\b[a-z][a-z0-9_]*version[a-z0-9_]*_id\b\s+from\s+\.\./',
        re.IGNORECASE)
    fixed_text = ("read version_id from "
                 "../06_update_applet/output.json; update the project "
                 "with the new version")
    assert old_re.search(fixed_text) is None, (
        "the old regex fails to recognize the bare 'version_id' key, "
        "reproducing the exact live rejection")
    print("sabotage: unpatched _VERSION_KEY_READ_RE rejects the exact "
         "correct fix the live repair proposed, confirming the fix is "
         "load-bearing")


def test_sabotage_no_mechanical_verification_would_accept_the_bad_fix():
    """Proves the mechanical re-check is load-bearing: replaying the
    exact bad reply from the "still fails the check" test above through
    `parse_chain_version_repair` alone (skipping
    `_validate_dangling_version_fix`) shows it parses as well-formed
    JSON despite not fixing anything -- confirming that without the
    re-check, a plausible-looking non-fix would be applied."""
    reply = ("```json\n[{\"dir\": \"07_update_project\", \"request\": "
            "\"read project_id from ../01_create_project/output.json; "
            "update the project with the new version\", "
            "\"api\": \"maven\"}]\n```\n")
    parsed = intent.parse_chain_version_repair(reply)
    assert parsed is not None, (
        "the shape-only parser accepts this reply -- proving the real "
        "guardrail is the mechanical re-check in "
        "_validate_dangling_version_fix, not the parser")
    still_broken = [(item["dir"], item["request"]) for item in parsed]
    assert intent.chain_dangling_version_problems(
        [("01_create_project", LIVE_STEPS[0][1])] + still_broken), (
        "the reply's own content still trips the ground-truth check, "
        "which is exactly what the validator catches")
    print("sabotage: a parseable but non-fixing reply is only caught by "
         "the mechanical re-check, confirming it is load-bearing")


if __name__ == "__main__":
    test_dangling_version_findings_matches_the_live_shape()
    test_parse_chain_version_repair()
    test_repair_merges_the_two_steps_when_the_pool_proposes_a_merge()
    test_repair_rejects_a_fix_that_still_fails_the_check()
    test_repair_rejects_a_fix_that_touches_an_unrelated_step()
    test_repair_rejects_a_non_adjacent_producer()
    test_parse_chain_version_repair_takes_the_last_fence_not_the_first()
    test_sabotage_first_fence_extraction_reproduces_the_live_failure()
    test_version_key_read_matches_the_bare_unprefixed_name()
    test_sabotage_unfixed_version_key_regex_rejects_the_real_fix()
    test_sabotage_no_mechanical_verification_would_accept_the_bad_fix()
    print("OK")
