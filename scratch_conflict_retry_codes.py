"""codegen.gate_narrowed_conflict_retry_status_codes.

Measured live: `openapi.example_call()`'s own literal, copy-pasteable
retry-loop boilerplate for a `version`-marked field checks
`resp.status_code not in (400, 409, 422)` -- all three together, on
purpose, because which code a duplicate value comes back as is a fact
about the real service, not something its own OpenAPI document
necessarily documents (the real spec for this exact endpoint documents
only 201 and 422 -- no 400 anywhere). `06_update_applet/main.py` was
handed that exact boilerplate and wrote its own retry loop checking only
`(409, 422)`. The real service's second version push came back 400, fell
straight through `resp.raise_for_status()` on the first attempt with no
retry at all, and failed with a bare "400 Client Error: Bad Request" and
no response body to explain why.

Run directly: `python3 scratch_conflict_retry_codes.py`.
"""
import codegen


LIVE_BUG_CODE = '''
import requests

def update_applet_with_survey_html(applet_id, survey_html):
    version_value = "1.0.0"
    url = f"{API_BASE_URL}/api/applets/{applet_id}/versions"
    for _attempt in range(1, 101):
        try:
            resp = requests.post(url, headers=HEADERS,
                                 json={"version": version_value})
            if resp.status_code in (201, 200):
                resp.raise_for_status()
                return resp.json()["id"]
            if resp.status_code in (409, 422):
                version_value = "1.0.0-%d" % (_attempt + 1)
                continue
            else:
                resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise e
'''

CORRECT_CODE = '''
import requests

def update_applet_with_survey_html(applet_id, survey_html):
    version_value = "1.0.0"
    url = f"{API_BASE_URL}/api/applets/{applet_id}/versions"
    for _attempt in range(1, 101):
        resp = requests.post(url, headers=HEADERS,
                             json={"version": version_value})
        if resp.status_code not in (400, 409, 422):
            break
        version_value = "1.0.0-%d" % (_attempt + 1)
    resp.raise_for_status()
'''

UNRELATED_STATUS_CHECK_CODE = '''
import requests

def fetch(url):
    resp = requests.get(url)
    if resp.status_code in (404, 500):
        raise RuntimeError("not found or server error")
    return resp.json()
'''

NO_STATUS_CODE_AT_ALL_CODE = '''
def add(a, b):
    return a + b
'''

NOT_IN_VARIANT_CODE = '''
import requests

def create_slug(name):
    resp = requests.post("http://x/api/slugs", json={"slug": name})
    if resp.status_code not in (409, 422):
        return resp.json()
    raise RuntimeError("slug taken")
'''


def test_live_bug_is_caught():
    msg = codegen.gate_narrowed_conflict_retry_status_codes(LIVE_BUG_CODE)
    assert msg is not None, "the live narrowed (409, 422) retry set was not caught"
    assert "400" in msg, msg
    assert "409" in msg and "422" in msg, msg
    print("  the live (409, 422)-only retry check, missing 400, is caught")


def test_not_in_variant_is_also_caught():
    msg = codegen.gate_narrowed_conflict_retry_status_codes(NOT_IN_VARIANT_CODE)
    assert msg is not None, "the `not in (409, 422)` variant was not caught"
    assert "400" in msg, msg
    print("  the `not in (409, 422)` variant of the same narrowing is also caught")


def test_correct_full_set_passes_clean():
    assert codegen.gate_narrowed_conflict_retry_status_codes(CORRECT_CODE) is None
    print("  a retry loop checking the full {400, 409, 422} set passes clean")


def test_unrelated_status_codes_are_left_alone():
    # (404, 500) shares nothing with {400, 409, 422} -- this has nothing
    # to do with the conflict-retry idiom and must not be flagged.
    assert codegen.gate_narrowed_conflict_retry_status_codes(
        UNRELATED_STATUS_CHECK_CODE) is None
    print("  a status-code check sharing no codes with the conflict set "
         "is left alone")


def test_no_status_code_check_at_all_is_left_alone():
    assert codegen.gate_narrowed_conflict_retry_status_codes(
        NO_STATUS_CODE_AT_ALL_CODE) is None
    print("  a file with no status_code comparison at all is untouched")


def test_sabotage_no_existing_gate_would_have_caught_this():
    # Confirms this is genuinely new coverage: nothing else in this
    # pipeline reads the *set* of status codes a retry loop checks.
    assert codegen.gate_placeholder_id_literal(LIVE_BUG_CODE) is None
    assert codegen.gate_mislabeled_base64_data_uri(LIVE_BUG_CODE) is None
    print("  sabotage: confirmed no existing gate objects to the live "
         "file's narrowed retry set")


# --- codegen.autofix_narrowed_conflict_retry_status_codes -----------------
#
# The gate above is deliberately advisory-only, printed only when
# `verbose=True` -- which `run_components` never passes for a parallel
# component, so a live run (06_update_applet, generated *after* the gate
# above already existed) produced this exact narrowed-(409, 422) file and
# the note never reached anywhere an operator or the repair loop could
# see it. Widening the set is safe regardless of whether the narrowing
# was deliberate: retrying on one more code cannot turn a real failure
# into a false success, and the loop is still bounded either way -- so
# unlike blocking the file outright, this can be applied unconditionally.

def test_autofix_widens_the_live_bug_to_the_full_set():
    fixed = codegen.autofix_narrowed_conflict_retry_status_codes(LIVE_BUG_CODE)
    assert fixed is not None, "did not widen the live (409, 422) case"
    assert codegen.gate_narrowed_conflict_retry_status_codes(fixed) is None, (
        "the widened file still trips the gate")
    # The fix must be a minimal splice, not a full rewrite -- everything
    # else in the file, including the unrelated (201, 200) check, must
    # survive untouched.
    assert "(201, 200)" in fixed, fixed
    assert "1.0.0-%d" in fixed, fixed
    print("  the live (409, 422)-only file is widened to include 400, "
          "and the rest of the file is untouched")


def test_autofix_widens_the_not_in_variant_too():
    fixed = codegen.autofix_narrowed_conflict_retry_status_codes(
        NOT_IN_VARIANT_CODE)
    assert fixed is not None, "did not widen the `not in` variant"
    assert codegen.gate_narrowed_conflict_retry_status_codes(fixed) is None
    print("  the `not in (409, 422)` variant is widened too")


def test_autofix_declines_when_the_gate_is_already_clean():
    assert codegen.autofix_narrowed_conflict_retry_status_codes(
        CORRECT_CODE) is None
    assert codegen.autofix_narrowed_conflict_retry_status_codes(
        UNRELATED_STATUS_CHECK_CODE) is None
    assert codegen.autofix_narrowed_conflict_retry_status_codes(
        NO_STATUS_CODE_AT_ALL_CODE) is None
    print("  the autofix declines on a file the gate already passes clean")


def test_autofix_actually_wired_into_generate_files_gate_chain():
    # Sabotage in the other direction: confirm generate_file's own source
    # calls the autofix right where the advisory note used to be printed
    # unconditionally, so this isn't dead code sitting beside the gate.
    import inspect
    src = inspect.getsource(codegen.generate_file)
    assert "autofix_narrowed_conflict_retry_status_codes" in src, (
        "generate_file never calls the autofix -- it is unreachable "
        "from the real gate chain")
    print("  generate_file's own gate chain actually calls the autofix")


def main():
    test_live_bug_is_caught()
    test_not_in_variant_is_also_caught()
    test_correct_full_set_passes_clean()
    test_unrelated_status_codes_are_left_alone()
    test_no_status_code_check_at_all_is_left_alone()
    test_sabotage_no_existing_gate_would_have_caught_this()
    test_autofix_widens_the_live_bug_to_the_full_set()
    test_autofix_widens_the_not_in_variant_too()
    test_autofix_declines_when_the_gate_is_already_clean()
    test_autofix_actually_wired_into_generate_files_gate_chain()
    print("OK")


if __name__ == "__main__":
    main()
