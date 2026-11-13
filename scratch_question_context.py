"""Validates session.question_context: a plain question ("why does main.py
look like that, please give guidance on how to use it") used to be answered
with nothing but the bare request text -- no history, no file names, no
code. This is the fix: the same continuity `code` gets, plus the real
content of whichever files the question is actually about.
"""
import os
import shutil
import tempfile

import session
import swarm

ok = True


def check(name, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + name)
    ok = ok and cond


def _public_names(path):
    return []


root = tempfile.mkdtemp(prefix="qctx-")
try:
    main_path = os.path.join(root, "main.py")
    with open(main_path, "w") as fh:
        fh.write("import os\n\ndef main():\n    print(os.environ['API_KEY'])\n")
    other_path = os.path.join(root, "helper.py")
    with open(other_path, "w") as fh:
        fh.write("def helper():\n    return 1\n")

    cfg = swarm.Config.load()

    # 1. No paths given, but the request names a real file by name --
    #    that file's actual content must appear, not just its filename.
    ctx = session.question_context(
        root, _public_names,
        "why does main.py look like that, please give extra guidance on "
        "how to use it", None, cfg)
    check("the question naming main.py gets main.py's real content",
          "API_KEY" in ctx and "def main" in ctx)
    check("a file the question does not mention is left out",
          "def helper" not in ctx)

    # 2. No file named anywhere -- falls back to every writable file at
    #    the project root, so a bare "why does the file look like that"
    #    with only one or two candidates still gets grounded.
    ctx2 = session.question_context(
        root, _public_names, "why does the file look like that", None, cfg)
    check("naming nothing falls back to every writable file",
          "def main" in ctx2 and "def helper" in ctx2)

    # 3. An explicit `paths` argument (from triage or --paths) wins over
    #    guessing from the request text.
    ctx3 = session.question_context(
        root, _public_names, "why does it do that", ["helper.py"], cfg)
    check("an explicit paths argument is honoured",
          "def helper" in ctx3 and "def main" not in ctx3)

    # 4. A path that does not exist on disk is not fabricated -- it is
    #    silently dropped, the same rule attach.read already follows for
    #    a stale --attach entry.
    ctx4 = session.question_context(
        root, _public_names, "why does it do that", ["nonexistent.py"], cfg)
    check("a nonexistent path falls through to the request-text guess, "
          "not a fabricated file",
          "nonexistent" not in ctx4)

    # 5. An empty project (no writable files at all) must not crash and
    #    must still carry whatever history exists.
    empty_root = tempfile.mkdtemp(prefix="qctx-empty-")
    try:
        ctx5 = session.question_context(
            empty_root, _public_names, "why does the file look like that",
            None, cfg)
        check("an empty project returns cleanly with no file section",
              "current content" not in ctx5)
    finally:
        shutil.rmtree(empty_root, ignore_errors=True)

    # 6. Sabotage: budget of zero must trim everything down to nothing
    #    rather than crash or silently exceed it -- proves the trimming
    #    loop is actually load-bearing, not a no-op that happens to pass.
    import attach as _attach
    real_budget = _attach.budget
    _attach.budget = lambda cfg: 1
    try:
        ctx6 = session.question_context(
            root, _public_names, "why does the file look like that",
            None, cfg)
        check("an impossible budget trims to no file content, not a crash",
              "current content" not in ctx6)
    finally:
        _attach.budget = real_budget

    # 7. History actually reaches the question. Record a turn, then ask
    #    about it -- the reason a file changed should be visible to a
    #    follow-up question the same way it already is to a follow-up
    #    code request.
    session.record(root, "build a login script", [], True, 1, 1)
    ctx7 = session.question_context(
        root, _public_names, "what was asked before", None, cfg)
    check("prior turn history reaches the question",
          "build a login script" in ctx7)

finally:
    shutil.rmtree(root, ignore_errors=True)

print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
