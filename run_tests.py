#!/usr/bin/env python3
"""Run the machinery tests. One command, no globs, no hangs.

    python3 run_tests.py            # all of them
    python3 run_tests.py attach     # only names containing "attach"

There used to be no runner, so the obvious thing was `for t in *_test.py`.
That is a trap in this repo and it cost two terminal sessions: generated
packages land at the project root, some of them import pygame and open a
window, others dial the LAN, and the loop blocks forever on the first one it
reaches. The list below is therefore explicit, and every test runs in a
subprocess under a timeout so that a single hang is one FAIL rather than a
wedged shell.

Everything here is offline. No test in this list contacts a node -- the pool
is stubbed out -- so the suite is safe to run with every box asleep, and a
failure always means the code changed rather than the network moved.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))

# Explicit, and ordered cheapest-first so a smoke failure shows up quickly.
TESTS = [
    "scratch_surface.py",
    "scratch_surface.py",
    # The scheduler: who takes what, and what happens when that goes wrong.
    "scratch_claim_order.py",
    "scratch_floor_deadlock.py",
    "scratch_tail_bias.py",
    "scratch_node_claim_scoring.py",
    "scratch_contract_asset_fix.py",
    "scratch_hoist_selfless.py",
    "scratch_entry_main_note.py",
    "scratch_entry_rename.py",
    "scratch_maven_boilerplate.py",
    "scratch_contract_missing_endpoints.py",
    "scratch_decompose_echo.py",
    "scratch_question_context.py",
    "scratch_entry_deps_backfill.py",
    "scratch_interface_gate.py",
    "scratch_finding_text.py",
    "scratch_replan.py",
    "scratch_repair_context.py",
    "scratch_html_placeholder.py",
    "scratch_parent_ref_seed.py",
    "scratch_missing_sibling.py",
    "scratch_output_json_shape.py",
    "scratch_output_json_not_json.py",
    "scratch_orchestrate_sh.py",
    "scratch_fabricated_client.py",
    "scratch_race_scratch_extension.py",
    "scratch_pure_static.py",
    "scratch_missing_output_write.py",
    "scratch_retry_stdin_hang.py",
    "scratch_failed_reason_printed.py",
    "scratch_node_claim_capacity.py",
    "scratch_planner_claim_race.py",
    "scratch_low_parallelism_note.py",
    "scratch_serial_link_routing.py",
    "scratch_single_file_mode.py",
    "scratch_why_shadow.py",
    "scratch_worker_death.py",
    "scratch_time_budget.py",
    "scratch_time_budget_queue_wait.py",
    "scratch_judge_pin.py",
    # Planning and the shared contract.
    "scratch_contract_owners.py",
    "scratch_plan_truncation.py",
    "scratch_redef_unrelated.py",
    "scratch_deshadow.py",
    "scratch_join_skip.py",
    "scratch_context_wiring.py",
    # The gates.
    "scratch_checks_test.py",
    "scratch_smoke_gate.py",
    "scratch_env_vocab.py",
    "scratch_entry_point.py",
    "scratch_new_or_edit.py",
    "scratch_cards.py",
    "scratch_entry_rescue.py",
    "scratch_entry_naming.py",
    "scratch_exercise_test.py",
    "scratch_patterns_test.py",
    "scratch_crossref_test.py",
    "scratch_selfattr_test.py",
    # Continuity, context, and what reaches a writer.
    "scratch_session_test.py",
    "scratch_existing_content_test.py",
    "scratch_attach_test.py",
    "scratch_backup_refuse_test.py",
    "scratch_ground_test.py",
    "scratch_resume.py",
    "scratch_external_deps.py",
    "scratch_operator_error.py",
    "scratch_run_capture.py",
    "scratch_ctx_floor.py",
    "scratch_platform_facts.py",
    "scratch_output_labels.py",
    "scratch_unit_count.py",
    "scratch_checkpoint_save.py",
    "scratch_26b_toggle.py",
    "scratch_floor4_fallback.py",
    "scratch_duplicate_component_name.py",
    "scratch_markdown_field_noise.py",
    "scratch_retry_decompose_failure.py",
    "scratch_escalate_stronger_writer.py",
    "scratch_race_generate_file.py",
    "scratch_plan_retry.py",
    "scratch_reconsider_slice.py",
    "scratch_gate_env_dotenv.py",
    "scratch_unqualified_asset.py",
    "scratch_chain_output_json.py",
    "scratch_output_json_key.py",
    "scratch_output_json_key_paraphrase.py",
    "scratch_output_json_overwritten.py",
    "scratch_placeholder_id_literal.py",
    "scratch_placeholder_host_literal.py",
    "scratch_interface_repair_context.py",
    "scratch_hedge_poll.py",
    "scratch_output_only_component.py",
    "scratch_waiter_distribution.py",
    "scratch_unrequested_client_split.py",
    "scratch_zero_units.py",
    "scratch_dangling_version.py",
    "scratch_version_repair.py",
    "scratch_content_source.py",
    "scratch_required_cli_args.py",
    "scratch_lone_entry_point.py",
    "scratch_replan_request.py",
    "scratch_steps_to_bash.py",
    "scratch_version_retry_field.py",
    "scratch_multipart_mock.py",
    "scratch_sibling_read_keys_second_open.py",
    "scratch_placeholder_credential_guard.py",
    "scratch_dangling_directory_reference.py",
    "scratch_output_json_boilerplate.py",
    "scratch_mislabeled_base64.py",
    "scratch_output_json_wrong_variable.py",
    "scratch_dict_by_name_resolution.py",
    "scratch_conflict_retry_codes.py",
    "scratch_pick_writer_idle.py",
    "scratch_interface_gate_mock_race.py",
    "scratch_history_trail_and_bulk_report.py",
    "scratch_write_then_read.py",
    "scratch_save_failed_attempt.py",
    "scratch_dead_entry_and_invented_env.py",
    "scratch_notes_survive_nonverbose.py",
    "scratch_gateway_retry.py",
    "scratch_repeated_widget_query_selector.py",
    "scratch_stale_precondition.py",
    "openapi_check.py",
    # Running generated code safely: the network guard, the per-type
    # gates, and the spec-built stand-in for the real API.
    "sandbox_check.py",
    # Telling the operator how to run what was written, and the
    # placeholders that stop an API client passing without calling.
    "usage_check.py",
    # Local execution of tool calls.
    "tools_check.py",
]

# Generous: the slowest here is ~3s. This exists to convert a hang into a
# failure, not to police how long a test may legitimately take.
TIMEOUT = 120


def unregistered():
    """Test-shaped files nobody listed. Usually debris, occasionally an oversight."""
    known = set(TESTS)
    found = []
    for name in sorted(os.listdir(HERE)):
        if name in known or not name.endswith(".py"):
            continue
        if name.endswith(("_test.py", "_check.py")) or name.startswith("test_"):
            found.append(name)
    return found


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [t for t in TESTS if only in t]
    if not tests:
        print("no test matches %r" % only)
        return 2

    width = max(len(t) for t in tests)
    failures = []
    started = time.monotonic()
    for name in tests:
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            print("  %-*s  MISSING" % (width, name))
            failures.append((name, "file does not exist"))
            continue
        began = time.monotonic()
        try:
            done = subprocess.run(
                [sys.executable, path], cwd=HERE, timeout=TIMEOUT,
                capture_output=True, text=True,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            took = time.monotonic() - began
            if done.returncode == 0:
                print("  %-*s  ok    %5.2fs" % (width, name, took))
            else:
                print("  %-*s  FAIL  %5.2fs" % (width, name, took))
                tail = (done.stdout + done.stderr).strip().splitlines()
                failures.append((name, "\n".join(tail[-12:])))
        except subprocess.TimeoutExpired:
            print("  %-*s  HUNG  >%ds" % (width, name, TIMEOUT))
            failures.append((name, "timed out after %ds -- it is probably "
                                   "waiting on a node or a window" % TIMEOUT))

    elapsed = time.monotonic() - started
    for name in unregistered():
        print("\n  note: %s looks like a test but is not in TESTS. Add it, or "
              "delete it if it is left over from a generation run." % name)

    print("\n%d/%d passed in %.1fs" % (len(tests) - len(failures),
                                       len(tests), elapsed))
    for name, detail in failures:
        print("\n--- %s\n%s" % (name, detail))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
