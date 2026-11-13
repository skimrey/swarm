"""The planner's own instructions used to hardcode main.py as the entry
point filename, unconditionally. "Write a script that uploads a dummy
unit" -- unrelated to the applet/project/task flow main.py already ran --
was planned as an edit to main.py for that reason: the prompt told it to,
regardless of what the existing-files context said main.py already did.

ground.entry_point_conflicts + intent.rescue_entry_points catch this after
the fact. This is the fix that stops it happening: the instruction itself
is now conditional on what the context block already contains.
"""
import intent


def render(request="write a script that does something"):
    return intent.DECOMPOSE_PROMPT.format(
        request=request, context="", workers=3, strong=1, light=2,
        idle=2, is_are="is")


def _flat(text):
    """Line wraps inside the triple-quoted prompt would otherwise break a
    substring check on a phrase that happens to cross one."""
    return " ".join(text.lower().split())


def test_the_instruction_is_conditional_on_existing_purpose():
    text = _flat(render())
    assert "name it main.py, unless" in text, text
    assert "different task from this one" in text, text
    print("  the entry point rule reads the existing-files context first")


def test_a_purpose_derived_name_is_the_example_given():
    """A concrete example is what makes an instruction followable. The
    old text said "FILE main.py" with no room for another answer."""
    assert "upload_unit.py" in intent.DECOMPOSE_PROMPT
    print("  a purpose-derived alternative name is modelled, not just allowed")


def test_this_is_called_normal_not_an_error():
    """Framing a second unrelated script as a fallback or exception invites
    a planner to avoid it. It is the ordinary case for a project that
    already has one script and gets asked for an unrelated second one."""
    low = _flat(intent.DECOMPOSE_PROMPT)
    assert "normal case" in low or "not a fallback" in low, low
    print("  a second entry point is framed as ordinary, not exceptional")


def test_the_prompt_still_formats_with_every_placeholder():
    """The rewrite must not have broken the format string itself."""
    text = render("write a bytecode VM")
    assert "write a bytecode VM" in text
    print("  the prompt still formats correctly")


def main():
    test_the_instruction_is_conditional_on_existing_purpose()
    test_a_purpose_derived_name_is_the_example_given()
    test_this_is_called_normal_not_an_error()
    test_the_prompt_still_formats_with_every_placeholder()
    print("OK")


if __name__ == "__main__":
    main()
