"""Two guarantees about replacing an existing file, neither of which a gate sees.

Generation replaces whole files rather than patching them, and that has two
consequences worth pinning down with tests, because both failure modes produce
valid code that passes every gate.

The first is silent truncation. A file handed to the writer as "its current
content" while being cut to fit the window produces a complete file missing
everything past the cut -- it parses, imports, smoke-runs, and only
`interface_gate` would notice, and only if another file imported a dropped
name. Measured at the old 8000-character cap: 16 of 63 top-level files in this
project exceeded it. So nothing is truncated now; a file that cannot round-trip
is refused instead, which is a fact rather than a corruption.

The second is that "passed all gates" does not mean "kept what the old file
did". It means the new file parses, imports and satisfies what was asked. A
rewrite can pass everything while having dropped a function nothing else
imports, so the previous content has to survive somewhere -- and since whole
files are replaced, the result cannot be used to reconstruct it.

Generation is stubbed throughout. No model is involved.
"""
import os
import shutil
import tempfile
import threading

import codegen
import intent
import swarm
import team

TIMEOUT = 20.0


def _component(name, path, spec):
    c = intent.Component(name, path, 4, [], spec)
    c.contract = ""
    return c


def _run(components, root, existing):
    """Drive run_components with generation stubbed; return captured prompts."""
    seen = {}
    calls = []

    def fake_generate(spec, out_path, node, model, cfg, state, **kw):
        calls.append(out_path)
        seen[out_path] = spec + "\n" + kw.get("extra", "")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                "target": "%s/%s" % (node.name, model), "code": ""}

    real = codegen.generate_file
    codegen.generate_file = fake_generate
    try:
        thread = threading.Thread(
            target=lambda: team.run_components(
                components, swarm.Config.load(), swarm.State(),
                root=root, existing=existing),
            daemon=True)
        thread.start()
        thread.join(TIMEOUT)
        assert not thread.is_alive(), "run_components hung"
    finally:
        codegen.generate_file = real
    return seen, calls


def test_a_file_too_large_to_round_trip_is_refused_not_truncated() -> None:
    root = tempfile.mkdtemp(prefix="toobig-")
    try:
        # Sized against the configured ceiling rather than a fixed number of
        # lines. A hardcoded size silently stops testing anything the moment
        # the window is raised -- which is exactly what happened when
        # max_ctx went from 16384 to 131072 and this file started fitting.
        cfg = swarm.Config.load()
        widest = max(node.max_ctx(m) for node in cfg.nodes for m in node.models)
        # The file is paid for twice against one window (prompt + answer), so
        # anything past half the widest window cannot round-trip anywhere.
        body = ""
        i = 0
        while swarm.est_tokens(body) * 2 <= widest:
            body += "def f%d():\n    return %d\n\n" % (i, i)
            i += 1
        assert swarm.est_tokens(body) * 2 > widest, \
            "test file is not actually oversize for a %d window" % widest
        path = os.path.join(root, "huge.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

        component = _component("huge", "huge.py", "add a docstring")
        seen, calls = _run([component], root, {"huge.py": body})

        assert not calls, \
            "generation was attempted on a file that cannot round-trip"
        assert component.ok is False, "a refused component was reported ok"
        assert "too large" in (component.error or ""), \
            "the refusal did not say why: %r" % component.error
        # The operator has to be able to act on it, so the message names the
        # file, the cost, and the limit rather than just failing.
        for fragment in ("huge.py", "window"):
            assert fragment in component.error, \
                "refusal message missing %r: %r" % (fragment, component.error)
        # And crucially: the original file is untouched.
        assert open(path, encoding="utf-8").read() == body, \
            "the file was modified despite being refused"
        print("too large         refused with a reason, original untouched")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_file_that_fits_is_passed_whole() -> None:
    """Past the old 8000-char cap, but within the window: nothing is cut."""
    root = tempfile.mkdtemp(prefix="fits-")
    try:
        filler = "\n".join("# padding line %d" % i for i in range(600))
        body = ("MARKER_START = 1\n" + filler +
                "\n\ndef tail_function():\n    return 'MARKER_END'\n")
        assert len(body) > 8000, "test needs a file past the old cap, got %d" % len(body)
        path = os.path.join(root, "medium.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

        component = _component("medium", "medium.py", "add a helper")
        seen, calls = _run([component], root, {"medium.py": body})

        prompt = seen.get(path)
        assert prompt is not None, "the component never ran"
        assert "MARKER_START" in prompt, "the start of the file is missing"
        # The point of the change: content past character 8000 is present.
        assert "MARKER_END" in prompt, \
            "content past the old 8000-char cap was truncated away"
        assert "tail_function" in prompt
        print("fits, >8000 chars whole file reached the prompt, nothing cut")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _stub_chat(reply_text):
    def fake(node, model, messages, num_ctx, answer_tokens=-1, **kw):
        return {"text": reply_text, "thinking": "", "thinking_starved": False,
                "raw": {"done_reason": "stop"},
                "sample": {"prompt_tokens": 10, "output_tokens": 10,
                           "prefill_tps": None, "decode_tps": None,
                           "load_s": 0.0, "total_s": 0.1, "ttft_s": None}}
    return fake


def _generate(root, path, reply, attempts=1):
    cfg = swarm.Config.load()
    node = cfg.nodes[0]
    state = swarm.State()
    real = swarm.chat_complete
    swarm.chat_complete = _stub_chat(reply)
    try:
        return codegen.generate_file(
            "a spec", path, node, node.models[0], cfg, state,
            attempts=attempts, root=root, smoke=False, verbose=False,
            time_budget=None)
    finally:
        swarm.chat_complete = real


def test_backup_is_kept_on_success_and_lives_outside_the_project() -> None:
    root = tempfile.mkdtemp(prefix="bak-ok-")
    try:
        path = os.path.join(root, "thing.py")
        original = "ORIGINAL = 'keep me'\n\n\ndef dropped_helper():\n    return 1\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)

        # A rewrite that passes every gate while silently losing a function --
        # exactly the case a passing run cannot distinguish from a good one.
        res = _generate(root, path, "```python\nORIGINAL = 'keep me'\n```\n")
        assert res["ok"], "the stubbed generation should have passed: %r" % res

        backup = res.get("backup")
        assert backup, "no backup was recorded on a successful run"
        assert os.path.exists(backup), "the backup was deleted on success"
        assert open(backup, encoding="utf-8").read() == original, \
            "the backup does not hold the original content"

        # Outside the project tree, so a checkout gains nothing and a
        # directory listing stays readable.
        assert not os.path.realpath(backup).startswith(
            os.path.realpath(root) + os.sep), \
            "the backup was written inside the project: %s" % backup
        assert ".swarm" in backup and "backups" in backup, backup
        leftovers = [f for f in os.listdir(root) if ".bak" in f]
        assert not leftovers, "stray .bak files in the project: %r" % leftovers

        # And it is findable without knowing the hash.
        assert backup in codegen.backups_for(path), \
            "backups_for() cannot find the backup it just made"
        # The dropped function really is gone from the live file -- which is
        # the whole reason the backup has to outlive a successful run.
        assert "dropped_helper" not in open(path, encoding="utf-8").read()
        print("backup on success kept, outside the project, findable")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_failure_restores_the_original_and_still_keeps_the_backup() -> None:
    root = tempfile.mkdtemp(prefix="bak-fail-")
    try:
        path = os.path.join(root, "thing.py")
        original = "ORIGINAL = 1\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)

        # No code block at all, so every attempt fails the parse gate.
        res = _generate(root, path, "I would rather discuss the weather.")
        assert not res["ok"], "a reply with no code should not pass"
        assert open(path, encoding="utf-8").read() == original, \
            "the original was not restored after a failed run"
        backup = res.get("backup")
        assert backup and os.path.exists(backup), \
            "the backup was consumed by the restore instead of copied"
        print("backup on failure original restored, archive survives")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_backups_accumulate_rather_than_overwrite() -> None:
    root = tempfile.mkdtemp(prefix="bak-many-")
    try:
        path = os.path.join(root, "thing.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("GEN = 0\n")
        before = len(codegen.backups_for(path))
        _generate(root, path, "```python\nGEN = 1\n```\n")
        _generate(root, path, "```python\nGEN = 2\n```\n")
        after = codegen.backups_for(path)
        assert len(after) >= before + 1, \
            "successive runs did not accumulate history: %r" % after
        print("history           successive runs accumulate, nothing pruned")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_new_file_leaves_no_backup_and_is_removed_on_failure() -> None:
    """Nothing existed, so there is nothing to archive and nothing to restore."""
    root = tempfile.mkdtemp(prefix="bak-new-")
    try:
        path = os.path.join(root, "fresh.py")
        res = _generate(root, path, "not code at all")
        assert not res["ok"]
        assert res.get("backup") is None, \
            "a backup was invented for a file that never existed"
        assert not os.path.exists(path), \
            "a failed brand-new file was left behind"
        print("brand-new failure no phantom backup, no half-written file")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_a_file_too_large_to_round_trip_is_refused_not_truncated()
    test_a_file_that_fits_is_passed_whole()
    test_backup_is_kept_on_success_and_lives_outside_the_project()
    test_failure_restores_the_original_and_still_keeps_the_backup()
    test_backups_accumulate_rather_than_overwrite()
    test_a_new_file_leaves_no_backup_and_is_removed_on_failure()
    print("OK")
