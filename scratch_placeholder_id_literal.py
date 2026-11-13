"""Validates `codegen.gate_placeholder_id_literal`, the mechanical,
blocking check that a generated file contains no hardcoded, all-zero-
shaped UUID literal.

This exists because a live run's own `gate_platform_facts` (advisory)
flagged a malformed link -- an invented `/applets/{id}` suffix -- and
said nothing about the fact that both ids inside that link were the
exact placeholder shape (`00000000-0000-4000-8000-000000000000`) this
project's own `placeholder_env` substitutes for a MISSING credential
during a smoke run. The mock's own call log for that same attempt showed
real `POST /api/projects` and `POST /api/applets` calls succeeding with
201s -- real ids existed -- so the file did not fail to get an id at
all; it failed to *use* the real one it already had, silently falling
back to something that merely looks like a real id. A link with the
wrong shape is cosmetic. A link built from a fabricated id is proof of
a defect that fails the exact same way, invisibly, against the real
platform.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen


def test_catches_the_exact_placeholder_this_project_injects():
    code = (
        'default_id = "00000000-0000-4000-8000-000000000000"\n'
        'def main():\n'
        '    return default_id\n'
    )
    result = codegen.gate_placeholder_id_literal(code)
    assert result is not None
    assert "00000000-0000-4000-8000-000000000000" in result


def test_catches_the_pure_all_zero_uuid_too():
    """Not only this project's own exact placeholder shape -- any UUID
    that is all-zero except the version/variant nibbles is equally
    fabricated, since no real, randomly-generated UUID lands there by
    chance."""
    code = 'x = "00000000-0000-0000-0000-000000000000"\n'
    assert codegen.gate_placeholder_id_literal(code) is not None


def test_catches_it_inside_an_f_string_built_link():
    """The exact live failure shape: the placeholder is not assigned to
    its own variable, it is interpolated directly into an f-string
    building a link."""
    code = (
        'def get_ui_url(project_id, applet_id):\n'
        '    pid = project_id or "00000000-0000-4000-8000-000000000000"\n'
        '    aid = applet_id or "00000000-0000-4000-8000-000000000000"\n'
        '    return f"https://d30ca7kumywvjf.cloudfront.net/projects/'
        '{pid}/applets/{aid}"\n'
    )
    assert codegen.gate_placeholder_id_literal(code) is not None


def test_a_real_random_uuid_is_never_flagged():
    code = 'x = "e247d65a-ec0a-4c0f-a5b3-f30492508868"\n'
    assert codegen.gate_placeholder_id_literal(code) is None


def test_a_string_that_is_not_uuid_shaped_at_all_is_never_flagged():
    code = 'x = "00000000-0000-4000-8000"  # too short, not a uuid\n'
    assert codegen.gate_placeholder_id_literal(code) is None
    code2 = 'x = "not-a-uuid-just-a-string-with-hyphens"\n'
    assert codegen.gate_placeholder_id_literal(code2) is None


def test_a_uuid_with_only_a_few_zero_digits_is_never_flagged():
    """A real UUID legitimately containing some zero digits (most do,
    somewhere) must not be flagged -- only one that is all-zero except
    the two fixed version/variant nibbles counts as fabricated."""
    code = 'x = "a0000000-1234-4abc-8def-000000000001"\n'
    assert codegen.gate_placeholder_id_literal(code) is None


def test_syntax_error_is_left_alone():
    assert codegen.gate_placeholder_id_literal("def broken(:\n") is None


def test_sabotage_a_check_that_never_scans_string_constants_misses_it():
    """Proves the AST-walk half is load-bearing: a version of this gate
    that never inspected `ast.Constant` string values at all -- the
    pre-fix state of this project's gates -- would never catch the live
    defect."""
    def sabotaged(code):
        return None  # never scans anything -- the pre-fix world

    live_code = (
        'default_id = "00000000-0000-4000-8000-000000000000"\n'
        'def main():\n    return default_id\n'
    )
    assert sabotaged(live_code) is None, (
        "sabotage fixture should never catch anything")
    real_result = codegen.gate_placeholder_id_literal(live_code)
    assert real_result is not None, (
        "the real gate must catch what the sabotaged version cannot")


def run():
    test_catches_the_exact_placeholder_this_project_injects()
    test_catches_the_pure_all_zero_uuid_too()
    test_catches_it_inside_an_f_string_built_link()
    test_a_real_random_uuid_is_never_flagged()
    test_a_string_that_is_not_uuid_shaped_at_all_is_never_flagged()
    test_a_uuid_with_only_a_few_zero_digits_is_never_flagged()
    test_syntax_error_is_left_alone()
    test_sabotage_a_check_that_never_scans_string_constants_misses_it()
    print("scratch_placeholder_id_literal: all checks passed")


if __name__ == "__main__":
    run()
