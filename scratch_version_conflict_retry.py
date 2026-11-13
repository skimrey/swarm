"""team.run_with_version_conflict_retry / looks_like_version_conflict_error.

Measured live, in `run_33/06_update_applet`: the real platform answered
`POST /api/applets/{applet_id}/versions` with a real `400 Bad Request`
whose body was `{"detail":"Version '1.0.0' already exists for this
applet"}`. The orchestrator's own repair ladder then spent three full
regeneration cycles -- a self-repair of `06_update_applet`, a
dependency-repair of `05_create_applet`, then `06_update_applet` again --
all reproducing the identical error, because none of them ever looked at
the one fact already sitting in the response: the exact literal that
collided. That fact is mechanically actionable with no model at all --
swap the literal for a fresh uuid and run the real script again -- the
same shape as `run_with_gateway_retry` (scratch_gateway_retry.py), just
one rung more expensive: mutate the source rather than rerun it
unchanged, because a version conflict is not transient the way a gateway
timeout is.

Run directly: `python3 scratch_version_conflict_retry.py`.
"""
import os
import tempfile

import team


LIVE_400_OUTPUT = (
    "Attempting to create a new version for applet "
    "56cdf891-edd1-4a17-9a2b-96e42f0da1e8...\n"
    "Received 400 error on attempt 1: "
    "{\"detail\":\"Version '1.0.0' already exists for this applet\"}. "
    "Stopping retries.\n"
    "Traceback (most recent call last):\n"
    "  File \"main.py\", line 101, in create_applet_version\n"
    "    raise RuntimeError(\"Failed to create applet version after "
    "multiple attempts.\")\n"
    "RuntimeError: Failed to create applet version after multiple "
    "attempts.\n"
)

MAIN_PY_SOURCE = (
    "import requests\n\n"
    "def create_applet_version(applet_id):\n"
    "    payload = {\n"
    "        \"version\": \"1.0.0\",\n"
    "        \"artifact_url\": \"data:text/html;base64,AAAA\",\n"
    "    }\n"
    "    resp = requests.post(\n"
    "        f\"{API_BASE_URL}/api/applets/{applet_id}/versions\",\n"
    "        json=payload,\n"
    "    )\n"
    "    resp.raise_for_status()\n"
    "    return resp.json()[\"id\"]\n"
)


def _write_main(tmpdir, source=MAIN_PY_SOURCE):
    path = os.path.join(tmpdir, "main.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    return path


def test_recognizes_the_live_error_shape():
    value = team.looks_like_version_conflict_error(LIVE_400_OUTPUT)
    assert value == "1.0.0", value
    print("  the exact live 'Version ... already exists' body is "
          "recognized and the colliding literal extracted")


def test_does_not_flag_an_unrelated_failure():
    assert team.looks_like_version_conflict_error(
        "KeyError: 'project_id'") is None
    assert team.looks_like_version_conflict_error(
        "500 Server Error: Internal Server Error for url: http://x"
    ) is None
    assert team.looks_like_version_conflict_error(
        "{\"detail\":\"applet not found\"}") is None
    print("  an unrelated failure is not mistaken for a version conflict")


def test_replace_literal_in_source_swaps_the_exact_quoted_token():
    source = 'payload = {"version": "1.0.0", "other": "1.0.0-not-this"}'
    new_source, hits = team._replace_literal_in_source(
        source, "1.0.0", "REPLACED")
    assert hits == 1, hits
    assert '"version": "REPLACED"' in new_source, new_source
    assert '"1.0.0-not-this"' in new_source, (
        "must not touch a different literal that merely contains the "
        "old value as a substring")
    print("  only the exact quoted literal is replaced, never a "
          "substring match inside a longer one")


def test_replace_literal_returns_zero_hits_when_not_a_quoted_token():
    # The value is built dynamically (an f-string, concatenation) rather
    # than sitting as its own quoted literal -- nothing safe to swap.
    source = 'payload = {"version": f"1.{minor}.0"}'
    _, hits = team._replace_literal_in_source(source, "1.0.0", "REPLACED")
    assert hits == 0, hits
    print("  a value that is not its own quoted literal is correctly "
          "left alone (0 hits), never guessed at")


def test_mechanical_retry_fixes_the_live_failure_with_no_model_call():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = _write_main(tmpdir)
        calls = []
        uuids = iter(["11111111-1111-1111-1111-111111111111"])

        def fake_run_and_capture(root, target_file):
            calls.append(1)
            with open(os.path.join(root, target_file),
                      encoding="utf-8") as fh:
                current = fh.read()
            if '"version": "1.0.0"' in current:
                return 1, LIVE_400_OUTPUT
            return 0, "Successfully created applet version.\n"

        real = team.run_and_capture
        team.run_and_capture = fake_run_and_capture
        try:
            code, output, retries = team.run_with_version_conflict_retry(
                tmpdir, "main.py", sleep=lambda s: None,
                new_uuid=lambda: next(uuids))
        finally:
            team.run_and_capture = real

        assert code == 0, (code, output)
        assert retries == 1, retries
        assert len(calls) == 2, calls

        with open(path, encoding="utf-8") as fh:
            rewritten = fh.read()
        assert '"version": "11111111-1111-1111-1111-111111111111"' \
            in rewritten, rewritten
        assert '"version": "1.0.0"' not in rewritten, (
            "the colliding literal must actually be gone from the file")
        print("  a real version-conflict failure is fixed by rewriting "
              "the colliding literal to a fresh uuid and rerunning for "
              "real -- no model call, no repair attempt")


def test_backs_up_the_file_before_mutating_it():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_main(tmpdir)
        calls = []

        def fake_run_and_capture(root, target_file):
            calls.append(1)
            if len(calls) == 1:
                return 1, LIVE_400_OUTPUT
            return 0, "ok\n"

        real = team.run_and_capture
        team.run_and_capture = fake_run_and_capture
        try:
            team.run_with_version_conflict_retry(
                tmpdir, "main.py", sleep=lambda s: None,
                new_uuid=lambda: "22222222-2222-2222-2222-222222222222")
        finally:
            team.run_and_capture = real

        backups = team.codegen.backups_for(os.path.join(tmpdir, "main.py"))
        assert backups, "expected a backup of the pre-mutation file"
        with open(backups[0], encoding="utf-8") as fh:
            saved = fh.read()
        assert '"version": "1.0.0"' in saved, (
            "the backup must hold the original, pre-mutation content")
        print("  the pre-mutation file is backed up before the literal "
              "is rewritten")


def test_gives_up_after_max_retries_if_the_conflict_recurs():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_main(tmpdir)
        calls = []
        uuids = iter(["a", "b", "c", "d", "e"])

        def always_conflicts(root, target_file):
            # The real API rejecting the *current* value each time, not
            # a fixed error text -- otherwise the second attempt (which
            # has already had its literal swapped once) would find
            # nothing left to replace and the loop would stop for the
            # wrong reason (no match) instead of the one under test
            # (a conflict that genuinely keeps recurring).
            calls.append(1)
            with open(os.path.join(root, target_file),
                      encoding="utf-8") as fh:
                current = fh.read()
            m = team.re.search(r'"version":\s*"([^"]+)"', current)
            value = m.group(1) if m else "1.0.0"
            return 1, ("{\"detail\":\"Version '%s' already exists for "
                       "this applet\"}" % value)

        real = team.run_and_capture
        team.run_and_capture = always_conflicts
        try:
            code, output, retries = team.run_with_version_conflict_retry(
                tmpdir, "main.py", max_retries=2, sleep=lambda s: None,
                new_uuid=lambda: next(uuids))
        finally:
            team.run_and_capture = real

        assert code == 1, code
        assert retries == 2, retries
        assert len(calls) == 3, (
            "expected the original attempt plus 2 retries, got %d"
            % len(calls))
        print("  a conflict that recurs even after a fresh uuid stops "
              "after max_retries rather than looping forever")


def test_a_non_conflict_failure_is_not_touched_at_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = _write_main(tmpdir)
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        calls = []

        def fails_with_a_real_bug(root, target_file):
            calls.append(1)
            return 1, "KeyError: 'project_id'"

        real = team.run_and_capture
        team.run_and_capture = fails_with_a_real_bug
        try:
            code, output, retries = team.run_with_version_conflict_retry(
                tmpdir, "main.py", sleep=lambda s: None)
        finally:
            team.run_and_capture = real

        assert code == 1 and retries == 0
        assert len(calls) == 1, (
            "a non-conflict failure was rerun anyway -- wasted an attempt")
        with open(path, encoding="utf-8") as fh:
            assert fh.read() == original, (
                "a file must never be mutated for an unrelated failure")
        print("  a genuine, unrelated code failure is left untouched and "
              "not retried")


def test_literal_not_found_stops_immediately_with_no_mutation():
    # Same fixed file, but the error names a value that does not appear
    # anywhere in the source as its own quoted literal (e.g. the field
    # really is built dynamically already, and this is a coincidental
    # collision on a value the code never wrote as a fixed string).
    with tempfile.TemporaryDirectory() as tmpdir:
        path = _write_main(tmpdir)
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        calls = []

        def conflicts_on_an_absent_literal(root, target_file):
            calls.append(1)
            return 1, (
                "{\"detail\":\"Version 'nonexistent-value' already "
                "exists for this applet\"}")

        real = team.run_and_capture
        team.run_and_capture = conflicts_on_an_absent_literal
        try:
            code, output, retries = team.run_with_version_conflict_retry(
                tmpdir, "main.py", sleep=lambda s: None)
        finally:
            team.run_and_capture = real

        assert code == 1 and retries == 0, (code, retries)
        assert len(calls) == 1, calls
        with open(path, encoding="utf-8") as fh:
            assert fh.read() == original
        print("  a colliding value that cannot be found as its own "
              "quoted literal stops immediately, with no guess and no "
              "mutation")


def test_sabotage_without_the_regex_the_live_failure_is_never_caught():
    # Prove the fixture actually exercises the real defect: with the
    # detector disabled, the exact live output must not be recognized.
    def blind(output):
        return None

    real_detector = team.looks_like_version_conflict_error
    real_run = team.run_and_capture
    team.looks_like_version_conflict_error = blind
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_main(tmpdir)
            calls = []

            def always_conflicts(root, target_file):
                calls.append(1)
                return 1, LIVE_400_OUTPUT

            team.run_and_capture = always_conflicts
            code, output, retries = team.run_with_version_conflict_retry(
                tmpdir, "main.py", sleep=lambda s: None)
    finally:
        team.looks_like_version_conflict_error = real_detector
        team.run_and_capture = real_run

    assert retries == 0, (
        "sabotage check is broken: retries should be 0 with the "
        "detector disabled")
    print("  sabotage confirmed: without the detector, the live failure "
          "is never mechanically fixed (proves the fixture is real)")


def main():
    test_recognizes_the_live_error_shape()
    test_does_not_flag_an_unrelated_failure()
    test_replace_literal_in_source_swaps_the_exact_quoted_token()
    test_replace_literal_returns_zero_hits_when_not_a_quoted_token()
    test_mechanical_retry_fixes_the_live_failure_with_no_model_call()
    test_backs_up_the_file_before_mutating_it()
    test_gives_up_after_max_retries_if_the_conflict_recurs()
    test_a_non_conflict_failure_is_not_touched_at_all()
    test_literal_not_found_stops_immediately_with_no_mutation()
    test_sabotage_without_the_regex_the_live_failure_is_never_caught()
    print("OK")


if __name__ == "__main__":
    main()
