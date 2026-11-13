"""Attached reference files must reach every writer, stay fresh, and refuse oversize.

An attachment is the operator saying "build against this" -- a design note, a
wire format, an example of the wanted output. Three properties decide whether
that is worth having, and none of them is checked by anything downstream:

* it reaches *every* writer, because a specification half the package can see
  produces files that disagree about what they implement;
* it is re-read rather than frozen, because a design document that silently
  stops taking effect once edited is worse than no attachment at all;
* it is refused when it does not fit, never truncated, because a half-supplied
  spec is valid code away from a wrong answer that passes every gate.

Generation is stubbed throughout. No model is involved -- the question is
only what reaches the prompt.
"""
import os
import shutil
import tempfile
import threading

import attach
import codegen
import intent
import session
import swarm
import team

TIMEOUT = 20.0


def _fake_generate(seen):
    def fake(spec, out_path, node, model, cfg, state, **kw):
        seen[os.path.basename(out_path)] = spec + "\n" + kw.get("extra", "")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                "target": "%s/%s" % (node.name, model), "code": ""}
    return fake


def _run(components, root, **kw):
    seen = {}
    real = codegen.generate_file
    codegen.generate_file = _fake_generate(seen)
    try:
        thread = threading.Thread(
            target=lambda: team.run_components(
                components, swarm.Config.load(), swarm.State(),
                root=root, **kw),
            daemon=True)
        thread.start()
        thread.join(TIMEOUT)
        assert not thread.is_alive(), "run_components hung"
    finally:
        codegen.generate_file = real
    return seen


def test_attachment_reaches_every_writer() -> None:
    """Not just the one whose path matches. This is the whole point."""
    root = tempfile.mkdtemp(prefix="attach-all-")
    try:
        doc = os.path.join(root, "design.md")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("# Wire format\nEvery frame starts with MAGIC0.\n")
        block = attach.render(attach.read(root, ["design.md"])[0])

        components = []
        for name in ("parser", "writer", "main"):
            c = intent.Component(name, name + ".py", 2, [], "do %s things" % name)
            c.contract = ""
            components.append(c)

        seen = _run(components, root, attached=block)
        assert len(seen) == 3, "only %d of 3 components ran" % len(seen)
        for name, prompt in seen.items():
            assert "Every frame starts with MAGIC0." in prompt, \
                "%s never saw the attachment:\n%s" % (name, prompt)
        print("every writer      all 3 components got the attached document")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_attachment_is_labelled_reference_not_work() -> None:
    """A doc describing a class must not read as an instruction to write it.

    The unqualified CONTRACT broadcast already produced exactly this failure:
    handed a complete `Shape(...)` signature, a constants.py wrote the class.
    """
    root = tempfile.mkdtemp(prefix="attach-label-")
    try:
        with open(os.path.join(root, "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("The Frame class has fields seq and payload.\n")
        block = attach.render(attach.read(root, ["spec.md"])[0])
        assert "Do NOT reproduce" in block, \
            "nothing tells the model this is background, not a file to write"
        assert "Reference material" in block
        print("labelled          attachment framed as reference, not as work")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fence_matches_the_file_type() -> None:
    """A markdown note must not be presented to a small model as Python."""
    root = tempfile.mkdtemp(prefix="attach-fence-")
    try:
        for name, want in (("a.md", "markdown"), ("b.json", "json"),
                           ("c.py", "python"), ("d.weird", "text")):
            with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
                fh.write("x\n")
            block = attach.render(attach.read(root, [name])[0])
            assert "```%s" % want in block, \
                "%s was fenced as something other than %s:\n%s" % (name, want, block)
        print("fencing           each file type fenced as itself, not as python")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_oversize_is_refused_not_truncated() -> None:
    """Over the budget must produce a refusal naming the cost, not a short doc."""
    cfg = swarm.Config.load()
    limit = attach.budget(cfg)
    root = tempfile.mkdtemp(prefix="attach-big-")
    try:
        # Comfortably past the ceiling, derived from the configured windows so
        # this keeps meaning the same thing if max_ctx moves again.
        huge = "lorem ipsum dolor sit amet consectetur\n" * (limit // 2)
        with open(os.path.join(root, "huge.md"), "w", encoding="utf-8") as fh:
            fh.write(huge)
        block = attach.render(attach.read(root, ["huge.md"])[0])
        why = attach.check_size(block, cfg)
        assert why is not None, \
            "a %d-token attachment passed a %d-token budget" % (
                swarm.est_tokens(block), limit)
        assert "over the" in why and str(limit) in why, \
            "the refusal does not name the limit: %s" % why
        # And the whole document is present in the block -- refusing is the
        # mechanism, so nothing anywhere may have quietly shortened it first.
        assert swarm.est_tokens(block) > limit
        print("oversize          refused with the cost named, nothing truncated")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_document_that_fits_is_passed_whole() -> None:
    root = tempfile.mkdtemp(prefix="attach-fits-")
    try:
        body = "".join("line %d of the design\n" % i for i in range(400))
        with open(os.path.join(root, "design.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
        content, _ = attach.read(root, ["design.md"])
        block = attach.render(content)
        assert attach.check_size(block, swarm.Config.load()) is None, \
            "an ordinary design document was refused"
        assert "line 0 of the design" in block and "line 399 of the design" in block, \
            "the document was cut somewhere"
        print("fits              ordinary document passed whole, start and end")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_attachments_stick_and_clear() -> None:
    root = tempfile.mkdtemp(prefix="attach-stick-")
    try:
        assert session.attachments(root) == []
        session.set_attachments(root, ["design.md"])
        assert session.attachments(root) == ["design.md"], \
            "attachment did not survive to the next call"
        # Replace, not append: the flag states the set.
        session.set_attachments(root, ["other.md"])
        assert session.attachments(root) == ["other.md"], \
            "setting appended instead of replacing"
        session.set_attachments(root, [])
        assert session.attachments(root) == [], "clearing left something behind"
        print("sticky            persists across calls, replaces, and clears")
    finally:
        session.set_attachments(root, [])
        shutil.rmtree(root, ignore_errors=True)


def test_content_is_reread_not_frozen() -> None:
    """Editing an attached document must take effect without re-attaching."""
    root = tempfile.mkdtemp(prefix="attach-fresh-")
    try:
        doc = os.path.join(root, "design.md")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("VERSION ONE\n")
        session.set_attachments(root, [attach.store(root, doc)])

        first = attach.render(attach.read(root, session.attachments(root))[0])
        assert "VERSION ONE" in first

        with open(doc, "w", encoding="utf-8") as fh:
            fh.write("VERSION TWO\n")
        second = attach.render(attach.read(root, session.attachments(root))[0])
        assert "VERSION TWO" in second, "the edit never took effect"
        assert "VERSION ONE" not in second, "stale content was served from a cache"
        print("freshness         edited document took effect with no re-attach")
    finally:
        session.set_attachments(root, [])
        shutil.rmtree(root, ignore_errors=True)


def test_missing_attachment_is_reported_not_fatal() -> None:
    """A deleted attachment is normal for a sticky list; say so and carry on."""
    root = tempfile.mkdtemp(prefix="attach-gone-")
    try:
        with open(os.path.join(root, "here.md"), "w", encoding="utf-8") as fh:
            fh.write("real content\n")
        content, problems = attach.read(root, ["here.md", "vanished.md"])
        assert "here.md" in content, "a good attachment was lost with the bad one"
        assert len(problems) == 1 and "vanished.md" in problems[0], \
            "the missing file was not reported: %r" % problems
        assert "not found" in problems[0]
        print("missing file      reported by name, the rest still attached")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_paths_inside_the_project_are_stored_relative() -> None:
    """So a project that gets moved or cloned keeps its attachments."""
    root = tempfile.mkdtemp(prefix="attach-rel-")
    outside = tempfile.mkdtemp(prefix="attach-out-")
    try:
        inside = os.path.join(root, "docs", "design.md")
        os.makedirs(os.path.dirname(inside), exist_ok=True)
        open(inside, "w").close()
        stored = attach.store(root, "docs/design.md")
        assert stored == os.path.join("docs", "design.md"), \
            "a file inside the project was stored absolutely: %s" % stored

        far = os.path.join(outside, "shared.md")
        open(far, "w").close()
        stored_far = attach.store(root, far)
        assert os.path.isabs(stored_far), \
            "a file outside the project lost its absolute path: %s" % stored_far
        assert attach.resolve(root, stored) == os.path.abspath(inside)
        print("path storage      relative inside the project, absolute outside")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


SECRET = "sk_live_51H8xQ2ZZZZtotallyrealsecretvalue"


def test_env_values_never_appear_anywhere() -> None:
    """The whole point: names reach the pool, values do not."""
    root = tempfile.mkdtemp(prefix="attach-env-")
    try:
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as fh:
            fh.write("# platform keys\n"
                     "STRIPE_API_KEY=%s\n"
                     "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
                     "\n"
                     "DATABASE_URL=postgres://user:hunter2@localhost/db\n" % SECRET)
        names = attach.env_names(root)
        assert names == ["STRIPE_API_KEY", "AWS_ACCESS_KEY_ID", "DATABASE_URL"], \
            "wrong names, or wrong order: %s" % names

        block = attach.render_env_names(names)
        for leaked in (SECRET, "AKIAIOSFODNN7EXAMPLE", "hunter2",
                       "postgres://"):
            assert leaked not in block, \
                "a secret value reached the prompt: %r in\n%s" % (leaked, block)
        for name in names:
            assert name in block, "%s never reached the prompt" % name
        print("env names         3 names reached the pool, 0 values did")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_attaching_a_dotenv_is_refused() -> None:
    """Attaching the credentials file must not be possible by accident."""
    root = tempfile.mkdtemp(prefix="attach-envref-")
    try:
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as fh:
            fh.write("STRIPE_API_KEY=%s\n" % SECRET)
        with open(os.path.join(root, "secrets.pem"), "w", encoding="utf-8") as fh:
            fh.write("-----BEGIN PRIVATE KEY-----\n%s\n" % SECRET)

        content, problems = attach.read(root, [".env", "secrets.pem"])
        assert content == {}, "a credentials file was attached: %s" % list(content)
        assert len(problems) == 2, problems
        for problem in problems:
            assert "refused" in problem, problem
            assert SECRET not in problem, \
                "the refusal message itself leaked the value: %s" % problem
        assert attach.render(content) == ""
        print("secret files      .env and .pem refused, no value in the message")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_malformed_env_lines_are_skipped_not_guessed() -> None:
    root = tempfile.mkdtemp(prefix="attach-envjunk-")
    try:
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as fh:
            fh.write("# a comment with an = sign in it\n"
                     "   \n"
                     "no_equals_here\n"
                     "9STARTS_WITH_DIGIT=x\n"
                     "HAS-A-DASH=x\n"
                     "GOOD_ONE=x\n")
        names = attach.env_names(root)
        assert names == ["GOOD_ONE"], \
            "junk was read as a variable name: %s" % names
        print("env parsing       comments and malformed lines skipped")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_env_file_contributes_nothing() -> None:
    root = tempfile.mkdtemp(prefix="attach-noenv-")
    try:
        assert attach.env_names(root) == []
        assert attach.render_env_names([]) == ""
        print("no .env           nothing added, no empty block")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_attachment_changes_nothing() -> None:
    """The default path must behave exactly as it did before this feature."""
    root = tempfile.mkdtemp(prefix="attach-none-")
    try:
        c = intent.Component("util", "util.py", 2, [], "a helper")
        c.contract = ""
        seen = _run([c], root)
        prompt = seen.get("util.py")
        assert prompt is not None, "the component never ran with no attached= arg"
        assert "Reference material" not in prompt, \
            "an attachment notice appeared with nothing attached:\n%s" % prompt
        assert attach.render({}) == ""
        print("no attachment     old behaviour unchanged, no empty notice")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_attachment_reaches_every_writer()
    test_attachment_is_labelled_reference_not_work()
    test_fence_matches_the_file_type()
    test_oversize_is_refused_not_truncated()
    test_a_document_that_fits_is_passed_whole()
    test_attachments_stick_and_clear()
    test_content_is_reread_not_frozen()
    test_missing_attachment_is_reported_not_fatal()
    test_paths_inside_the_project_are_stored_relative()
    test_env_values_never_appear_anywhere()
    test_attaching_a_dotenv_is_refused()
    test_malformed_env_lines_are_skipped_not_guessed()
    test_no_env_file_contributes_nothing()
    test_no_attachment_changes_nothing()
    print("OK")
