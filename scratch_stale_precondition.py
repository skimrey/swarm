#!/usr/bin/env python3
"""Validates `gate_stale_precondition_before_overwrite` against the exact
live failure (07_update_project's main.py, run_8) and the negative
controls that must never be flagged: a genuine state-machine guard
comparing to a specific expected value, and a genuine read-modify-write
that re-sends the same variable it just read.
"""
import sys

import codegen

FAILED = []


def check(label, cond, detail=None):
    status = "ok  " if cond else "FAIL"
    print("  %s %s" % (status, label))
    if not cond:
        FAILED.append(label)
        if detail is not None:
            print("      %r" % (detail,))


# The exact live shape from run_8/07_update_project/main.py: version_id is
# already known (read from a sibling output.json elsewhere in the real
# file), but this fetches the project, requires its OLD
# applet_version_id to already be non-empty, and then PATCHes the field
# to a completely different, already-known value.
LIVE_BUG = '''
import requests

def main():
    project_id = "abc"
    version_id = "new-version-id"

    project_url = f"https://api.example.com/api/projects/{project_id}"
    resp = requests.get(project_url, headers=HEADERS)
    project_data = resp.json()
    current_applet_version_id = project_data.get("applet_version_id")

    if not current_applet_version_id:
        raise ValueError("Could not determine current applet_version_id")

    update_payload = {
        "applet_version_id": version_id,
    }
    resp2 = requests.patch(project_url, headers=HEADERS, json=update_payload)
    resp2.raise_for_status()
    print(resp2.json())
'''

# `VAR is None` spelling of the same absence check must be caught too.
LIVE_BUG_IS_NONE_SPELLING = '''
import requests

def main():
    version_id = "new-version-id"
    resp = requests.get(project_url, headers=HEADERS)
    current = resp.json().get("applet_version_id")
    if current is None:
        raise ValueError("missing")
    resp2 = requests.patch(url, json={"applet_version_id": version_id})
'''

# A genuine state-machine precondition: the guard compares to a SPECIFIC
# expected value ("must currently be draft"), not mere presence. This
# must never be flagged -- it is a legitimate business rule, not a
# chicken-and-egg check on a field being set for the first time.
LEGITIMATE_STATE_MACHINE_GUARD = '''
import requests

def main():
    resp = requests.get(url, headers=HEADERS)
    status = resp.json().get("status")
    if status != "draft":
        raise ValueError("can only submit a draft")
    resp2 = requests.patch(url, json={"status": "submitted"})
'''

# A genuine read-modify-write: the fetched value IS the one written back
# (e.g. incrementing a counter). Must never be flagged.
LEGITIMATE_READ_MODIFY_WRITE = '''
import requests

def main():
    resp = requests.get(url, headers=HEADERS)
    view_count = resp.json().get("view_count")
    if view_count is None:
        raise ValueError("missing view_count")
    view_count = view_count + 1
    resp2 = requests.patch(url, json={"view_count": view_count})
'''

# A read and guard with no write at all afterward -- nothing to compare
# against, must never be flagged.
NO_WRITE_AT_ALL = '''
import requests

def main():
    resp = requests.get(url, headers=HEADERS)
    name = resp.json().get("name")
    if not name:
        raise ValueError("missing name")
    print(name)
'''

# A write that sets an UNRELATED field -- the guarded field and the
# written field are different keys entirely. Must never be flagged.
UNRELATED_FIELD_WRITTEN = '''
import requests

def main():
    resp = requests.get(url, headers=HEADERS)
    current = resp.json().get("applet_version_id")
    if not current:
        raise ValueError("missing")
    resp2 = requests.patch(url, json={"name": "New Name"})
'''


def test_catches_the_live_bug():
    failure = codegen.gate_stale_precondition_before_overwrite(LIVE_BUG)
    check("the live not-VAR shape is caught", failure is not None, failure)
    check("the message names the field and the line",
         failure and "applet_version_id" in failure, failure)


def test_catches_is_none_spelling():
    failure = codegen.gate_stale_precondition_before_overwrite(
        LIVE_BUG_IS_NONE_SPELLING)
    check("the `VAR is None` spelling of the same check is caught too",
         failure is not None, failure)


def test_legitimate_state_machine_guard_not_flagged():
    check("a guard comparing to a specific expected state is left alone",
         codegen.gate_stale_precondition_before_overwrite(
             LEGITIMATE_STATE_MACHINE_GUARD) is None)


def test_legitimate_read_modify_write_not_flagged():
    check("writing back the same variable that was just read is left alone",
         codegen.gate_stale_precondition_before_overwrite(
             LEGITIMATE_READ_MODIFY_WRITE) is None)


def test_no_write_at_all_not_flagged():
    check("a read+guard with no later write raises nothing",
         codegen.gate_stale_precondition_before_overwrite(
             NO_WRITE_AT_ALL) is None)


def test_unrelated_field_written_not_flagged():
    check("a write touching an unrelated field is left alone",
         codegen.gate_stale_precondition_before_overwrite(
             UNRELATED_FIELD_WRITTEN) is None)


def test_wired_into_generate_files_gate_chain():
    # This is a business-logic gate like gate_fabricated_client and
    # gate_invented_env_id -- wired directly into generate_file's own
    # per-attempt loop (never into gate_syntax, which only ever checks
    # shape: compiles, parses as JSON, nests as HTML). Checked the same
    # way scratch_conflict_retry_codes.py checks its own call-site
    # wiring: the call has to actually be reachable, not merely defined.
    import inspect
    import re
    source = inspect.getsource(codegen.generate_file)
    check("generate_file's own source calls "
         "gate_stale_precondition_before_overwrite(code)",
         re.search(r"gate_stale_precondition_before_overwrite\(\s*code\)",
                   source) is not None)


def test_sabotage_disabling_the_absence_check_loses_the_live_case():
    old = codegen._is_absence_check
    codegen._is_absence_check = lambda test, varname: False
    try:
        check("sabotage: with no absence-check detector, the live bug is missed",
             codegen.gate_stale_precondition_before_overwrite(LIVE_BUG)
             is None)
    finally:
        codegen._is_absence_check = old


# The exact live file on disk, read once for the autofix tests below.
LIVE_FILE_PATH = ("/Users/skimrey/Documents/deepseek-harness-test/"
                  "wednesdaysept26-7/bulk_testing_v4/run_8/"
                  "07_update_project/main.py")


def _live_file_code():
    try:
        with open(LIVE_FILE_PATH) as f:
            return f.read()
    except FileNotFoundError:
        return None


def test_autofix_removes_the_whole_pointless_fetch_on_the_live_file():
    code = _live_file_code()
    if code is None:
        print("  skip (live file not present on this machine)")
        return
    fixed = codegen.autofix_stale_precondition_before_overwrite(code)
    check("autofix does not decline on the live file", fixed is not None)
    if fixed is None:
        return
    check("the fixed file still parses",
         _parses(fixed))
    check("the gate no longer fires on the fixed file",
         codegen.gate_stale_precondition_before_overwrite(fixed) is None)
    check("the pointless GET to read the OLD applet_version_id is gone",
         "requests.get(" not in fixed)
    check("the write is untouched and still sets the real, known value",
         'json=update_payload' in fixed and '"applet_version_id": version_id'
         in fixed)
    check("the two genuinely required sibling-output.json reads survive",
         'json.load(f)' in fixed and fixed.count('json.load(f)') == 2)


def _parses(code):
    try:
        import ast as _ast
        _ast.parse(code)
        return True
    except SyntaxError:
        return False


def test_autofix_on_synthetic_live_bug_shape():
    fixed = codegen.autofix_stale_precondition_before_overwrite(LIVE_BUG)
    check("autofix does not decline on the synthetic live-bug fixture",
         fixed is not None)
    if fixed is None:
        return
    check("the fetch, the derived value, and the guard are all gone",
         "requests.get(" not in fixed
         and "current_applet_version_id" not in fixed
         and "raise ValueError" not in fixed)
    check("the write survives untouched",
         "resp2 = requests.patch" in fixed and '"applet_version_id": '
         'version_id' in fixed)
    check("fixed code still parses", _parses(fixed))
    check("the gate no longer fires", codegen
         .gate_stale_precondition_before_overwrite(fixed) is None)


def test_autofix_declines_on_ambiguous_multiple_matches():
    doubled = LIVE_BUG + "\n" + LIVE_BUG.replace(
        "current_applet_version_id", "other_current_id").replace(
        "project_url", "other_project_url")
    # Two independent matches in one file -- the autofix must not guess
    # which one (or both) to touch; it declines entirely.
    matches = codegen._find_stale_precondition_matches(
        __import__("ast").parse(doubled))
    check("the doubled fixture really does contain two matches",
         len(matches) == 2, len(matches))
    check("autofix declines rather than guessing across multiple matches",
         codegen.autofix_stale_precondition_before_overwrite(doubled)
         is None)


def test_autofix_declines_when_the_try_has_a_real_finally():
    # A finally block with real content means the try cannot be safely
    # absorbed wholesale -- the autofix must back out rather than
    # silently drop code that still runs.
    has_finally = '''
import requests

def main():
    version_id = "new-version-id"
    log = []
    try:
        resp = requests.get(project_url, headers=HEADERS)
        project_data = resp.json()
        current_applet_version_id = project_data.get("applet_version_id")
    finally:
        log.append("checked")

    if not current_applet_version_id:
        raise ValueError("missing")

    update_payload = {"applet_version_id": version_id}
    resp2 = requests.patch(url, headers=HEADERS, json=update_payload)
'''
    check("gate still fires on the finally-guarded fixture",
         codegen.gate_stale_precondition_before_overwrite(has_finally)
         is not None)
    check("autofix declines rather than dropping a real finally block",
         codegen.autofix_stale_precondition_before_overwrite(has_finally)
         is None)


def test_autofix_sabotage_bad_shadowing_would_wrongly_keep_dead_code():
    # Regression guard for the exact bug found while building this: a
    # purely name-based liveness check (no binding attribution) sees the
    # live file's *first*, unrelated `project_data = json.load(f)`
    # binding's use and wrongly treats the *second*, truly-dead
    # `project_data = resp.json()` binding as still live, leaving the
    # pointless GET (and its raise_for_status) in place. Reproduced
    # directly against the live shape's rebinding pattern.
    code = _live_file_code()
    if code is None:
        print("  skip (live file not present on this machine)")
        return
    fixed = codegen.autofix_stale_precondition_before_overwrite(code)
    check("sabotage-guard: the fixed file must not still contain the "
         "now-pointless GET call",
         fixed is not None and "requests.get(" not in fixed)


def test_wired_into_generate_files_gate_chain_with_autofix():
    import inspect
    source = inspect.getsource(codegen.generate_file)
    check("generate_file calls the autofix, not just the gate",
         "autofix_stale_precondition_before_overwrite(code)" in source)


# --- gate_dead_value_after_guard: advisory-only broader check ---

# A value fetched purely to gate execution, never used for anything
# else (not even under a different name in a later write). Must be
# flagged, but only as an advisory note, never blocking.
DEAD_VALUE_NEVER_USED = '''
import requests

def main():
    resp = requests.get(url, headers=HEADERS)
    data = resp.json()
    ready = data.get("ready")
    if not ready:
        raise ValueError("not ready")
    resp2 = requests.patch(url, json={"status": "submitted"})
'''

# A genuine, plausible business rule: check a permission flag before
# proceeding, and correctly never echo it back anywhere. This is
# exactly the case the docstring says a model cannot tell apart from
# dead weight -- it still matches (nothing proves it is NOT dead), but
# the point of being advisory-only is that this is fine to flag as a
# note without blocking or auto-deleting it.
PLAUSIBLE_PERMISSION_GUARD = '''
import requests

def main():
    resp = requests.get(url, headers=HEADERS)
    can_edit = resp.json().get("can_edit")
    if not can_edit:
        raise PermissionError("not allowed to edit")
    resp2 = requests.patch(url, json={"status": "submitted"})
'''

# A value read from a sibling output.json (this project's own normal,
# encouraged id-passing convention) must never be flagged, even though
# it is used only in a guard -- this is not a GET response at all.
SIBLING_OUTPUT_JSON_READ_NOT_FLAGGED = '''
import json

def main():
    with open("../01_create/output.json") as f:
        data = json.load(f)
    project_id = data.get("project_id")
    if not project_id:
        raise ValueError("missing project_id")
    print(project_id)
'''

# The value IS used elsewhere (in the write), so this is exactly the
# narrower, blocking, autofixable case above -- the advisory check must
# not ALSO fire redundantly once the value has a real external use.
VALUE_USED_ELSEWHERE_NOT_FLAGGED_BY_ADVISORY = LEGITIMATE_READ_MODIFY_WRITE


def test_dead_value_after_guard_flags_the_never_used_case():
    note = codegen.gate_dead_value_after_guard(DEAD_VALUE_NEVER_USED)
    check("a value used only in the guard, never elsewhere, is flagged",
         note is not None, note)


def test_dead_value_after_guard_is_advisory_not_blocking():
    # The whole point: this never becomes `failure` in generate_file,
    # only a `notes` entry. Checked at the call-site level, the same
    # way the wiring test above checks the blocking gate is actually
    # called.
    import inspect
    source = inspect.getsource(codegen.generate_file)
    check("gate_dead_value_after_guard's result feeds `notes`, not "
         "`failure`",
         "notes.append(dead_value_note)" in source)


def test_dead_value_after_guard_flags_plausible_guard_too_but_only_advisory():
    # Comprehensive on purpose: this really is indistinguishable from
    # the never-used case above by static analysis alone, so it is
    # flagged the same way -- the safety comes from being advisory,
    # not from trying to guess intent here.
    note = codegen.gate_dead_value_after_guard(PLAUSIBLE_PERMISSION_GUARD)
    check("a plausible real permission guard is still flagged "
         "(comprehensiveness) ...", note is not None)
    check("... but never autofixed -- there is no autofix_dead_value_"
         "after_guard function at all",
         not hasattr(codegen, "autofix_dead_value_after_guard"))


def test_dead_value_after_guard_ignores_sibling_output_json():
    check("a value read from a sibling output.json is never flagged -- "
         "it is this project's own normal id-passing convention, not a "
         "GET response",
         codegen.gate_dead_value_after_guard(
             SIBLING_OUTPUT_JSON_READ_NOT_FLAGGED) is None)


def test_dead_value_after_guard_ignores_values_used_elsewhere():
    check("a value that IS used elsewhere (read-modify-write) is not "
         "flagged by the advisory check either",
         codegen.gate_dead_value_after_guard(
             VALUE_USED_ELSEWHERE_NOT_FLAGGED_BY_ADVISORY) is None)


def test_dead_value_after_guard_sabotage_scoping_to_get_responses():
    # Sabotage: if the "must trace back to a GET response" scoping were
    # removed and the check matched any dict .get()-style read, it
    # would fire on the sibling-output.json case above, which must stay
    # silent. Confirmed by calling the real function on that fixture
    # (already asserted above) and, here, confirming a bare dict read
    # with no requests.get anywhere in the file is not enough on its
    # own to trigger it.
    no_requests_at_all = '''
def main():
    data = {"a": 1}
    value = data.get("missing_thing")
    if not value:
        raise ValueError("missing")
'''
    check("sabotage-guard: no requests.get anywhere means nothing to "
         "flag, confirming the GET-response scoping is load-bearing",
         codegen.gate_dead_value_after_guard(no_requests_at_all) is None)


def main():
    test_catches_the_live_bug()
    test_catches_is_none_spelling()
    test_legitimate_state_machine_guard_not_flagged()
    test_legitimate_read_modify_write_not_flagged()
    test_no_write_at_all_not_flagged()
    test_unrelated_field_written_not_flagged()
    test_wired_into_generate_files_gate_chain()
    test_sabotage_disabling_the_absence_check_loses_the_live_case()
    test_autofix_removes_the_whole_pointless_fetch_on_the_live_file()
    test_autofix_on_synthetic_live_bug_shape()
    test_autofix_declines_on_ambiguous_multiple_matches()
    test_autofix_declines_when_the_try_has_a_real_finally()
    test_autofix_sabotage_bad_shadowing_would_wrongly_keep_dead_code()
    test_wired_into_generate_files_gate_chain_with_autofix()
    test_dead_value_after_guard_flags_the_never_used_case()
    test_dead_value_after_guard_is_advisory_not_blocking()
    test_dead_value_after_guard_flags_plausible_guard_too_but_only_advisory()
    test_dead_value_after_guard_ignores_sibling_output_json()
    test_dead_value_after_guard_ignores_values_used_elsewhere()
    test_dead_value_after_guard_sabotage_scoping_to_get_responses()
    print("%d failed" % len(FAILED))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
