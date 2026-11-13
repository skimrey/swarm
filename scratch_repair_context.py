"""A repair attempt used to see none of what the first attempt saw.

`GENERATE_PROMPT` carries `{spec}` and `{extra}` -- the task, the CONTRACT,
dependency source, attached reference material, the maven applet
boilerplate. `REPAIR_PROMPT`, sent on every attempt after the first, carried
none of it: only the failing stage, the raw error, and the file's own
current (broken) code. Every context mechanism this project has built --
`attach.py`, `ground.py`, the CONTRACT broadcast, the deterministic
boilerplate injection -- was silently discarded the moment a component
needed even one repair.

Measured live: a `survey.html` component was handed the reference applet
boilerplate on attempt 1, failed `gate_html_placeholder` ("the page has no
visible text and none of input, form, button..."), and every one of the
remaining 5 attempts -- plus a full whole-loop restart -- repaired against
nothing but that one terse gate message, with no way to recover the
boilerplate it was supposed to be built from. Six attempts, twelve counting
the restart, and the model never saw the reference applet again after the
first.

This validates that `extra` (and `spec`) now ride along on every repair
attempt, and that the context window is sized to hold it -- sabotage-
checked in both directions.
"""
import os
import tempfile
import types

import codegen
import swarm

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


def test_repair_prompt_carries_extra_and_spec():
    cfg = swarm.Config.load()
    node = cfg.node("e4b-linux")
    model = "gemma4-e4b-fixed:latest"
    state = _state()

    prompts = []

    def fake_chat_complete(_node, _model, messages, **kw):
        prompts.append(messages[-1]["content"])
        if len(prompts) == 1:
            # Attempt 1: syntactically fine but semantically empty, so
            # gate_syntax's html-placeholder check fails it.
            text = "```html\n<html><body></body></html>\n```"
        else:
            # Attempt 2 onward: only pass if the repair prompt actually
            # told it what substance was required.
            text = ("```html\n<html><body><form><input name=\"age\">"
                   "<button>Submit</button></form></body></html>\n```")
        return {"text": text, "raw": {"done_reason": "stop"},
               "sample": {"tokens": 20, "seconds": 0.1}}

    real_chat = swarm.chat_complete
    swarm.chat_complete = fake_chat_complete
    try:
        with tempfile.TemporaryDirectory() as root:
            out_path = os.path.join(root, "survey.html")
            spec = "A THREE QUESTION SURVEY asking for AGE_UNIQUE_MARKER."
            extra = ("BOILERPLATE_UNIQUE_MARKER: keep the input and button "
                    "scaffolding from the reference applet.")
            res = codegen.generate_file(
                spec, out_path, node, model, cfg, state, attempts=3,
                answer_tokens=200, thinking=100, root=root, smoke=False,
                extra=extra, verbose=False)
    finally:
        swarm.chat_complete = real_chat
        state.close()

    check("two attempts were made (first failed, second recovered)",
         len(prompts) == 2, prompts)
    check("the component's own SPEC reaches the repair attempt",
         len(prompts) >= 2 and "AGE_UNIQUE_MARKER" in prompts[1])
    check("`extra` -- the boilerplate/context payload -- reaches the "
         "repair attempt",
         len(prompts) >= 2 and "BOILERPLATE_UNIQUE_MARKER" in prompts[1])
    check("the repair attempt still carries the failing code and error",
         len(prompts) >= 2 and "<html><body></body></html>" in prompts[1])
    check("the file ultimately passes once repaired with real context",
         res["ok"], res.get("history"))


def test_context_window_sized_for_extra():
    # A repair prompt now includes `extra`, so the window planned for the
    # whole loop has to be sized against it too, or a context-heavy repair
    # (a large boilerplate, real dependency source) would be silently cut
    # by a window that was only ever sized for `spec`.
    cfg = swarm.Config.load()
    node = cfg.node("e4b-linux")
    model = "gemma4-e4b-fixed:latest"

    small_extra_ctx = codegen.plan_context("x" * 200, cfg, node, model,
                                           500, 300)
    big_extra_ctx = codegen.plan_context("x" * 200 + "y" * 80000, cfg, node,
                                         model, 500, 300)
    check("a large `extra` payload folded into the sizing call grows the "
         "window",
         big_extra_ctx > small_extra_ctx,
         (small_extra_ctx, big_extra_ctx))


def _sabotage_repair_prompt_without_extra():
    """Confirms the test above actually exercises the fix: reverting
    REPAIR_PROMPT to drop {extra}/{spec} must fail it."""
    broken = ("The file you wrote does not work.\n\nFAILING STAGE: {stage}\n"
              "ERROR:\n{error}\n\nYOUR CURRENT FILE:\n```{fence}\n{code}\n```"
              "\n\nFix it. Output the entire corrected file in one fenced "
              "```{fence} block, and nothing else.")
    real_prompt = codegen.REPAIR_PROMPT
    codegen.REPAIR_PROMPT = broken
    try:
        prompt = codegen.REPAIR_PROMPT.format(
            stage="parse", error="e", code="c", fence="html")
        assert "BOILERPLATE_UNIQUE_MARKER" not in prompt
        print("  ok   sabotage confirmed: the old REPAIR_PROMPT shape "
             "carries no extra/spec at all")
    finally:
        codegen.REPAIR_PROMPT = real_prompt


if __name__ == "__main__":
    test_repair_prompt_carries_extra_and_spec()
    test_context_window_sized_for_extra()
    _sabotage_repair_prompt_without_extra()
    if FAILURES:
        raise SystemExit("%d failure(s): %s" % (len(FAILURES), FAILURES))
    print("OK")
