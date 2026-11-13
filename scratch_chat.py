#!/usr/bin/env python3
"""Offline tests for chat.py: scripted inputs and a scripted model, no network."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat  # noqa: E402
import harness  # noqa: E402


def final(text):
    return {"role": "assistant", "content": text}


def model(*replies):
    """chat_fn answering with the given texts in order; records what it was shown."""
    state = {"i": 0, "seen": []}

    def fn(messages):
        state["seen"].append(json.loads(json.dumps(messages)))
        r = replies[min(state["i"], len(replies) - 1)]
        state["i"] += 1
        if isinstance(r, BaseException):
            raise r
        return final(r) if isinstance(r, str) else r
    fn.state = state
    return fn


def drive(root, lines, chat_fn, thread=None, **kw):
    """Run the REPL over scripted input lines; returns (exit code, output lines, session)."""
    it = iter(lines)

    def inp(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    out = []
    s = chat.Session(root, thread or chat.new_thread(root, "p40", "m"), chat_fn, **kw)
    rc = chat.repl(s, lambda: chat_fn, inp=inp, out=out.append)
    return rc, out, s


def test_messages_are_answered_and_saved():
    root = tempfile.mkdtemp()
    rc, out, s = drive(root, ["hello there", "and again"], model("hi!", "yes again"))
    assert rc == 0 and "hi!" in out and "yes again" in out, out
    saved = chat.list_threads(root)
    assert len(saved) == 1 and saved[0]["title"] == "hello there", saved
    roles = [m["role"] for m in saved[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user", "assistant"], roles


def test_resume_continues_with_the_old_history():
    root = tempfile.mkdtemp()
    drive(root, ["first question"], model("first answer"))
    t = chat.list_threads(root)[0]
    m = model("second answer")
    drive(root, ["follow up"], m, thread=t)
    shown = m.state["seen"][0]
    texts = [x.get("content") for x in shown]
    assert "first question" in texts and "first answer" in texts and "follow up" in texts, texts
    assert len(chat.list_threads(root)) == 1


def test_new_makes_a_separate_thread():
    root = tempfile.mkdtemp()
    orig = chat.new_thread
    counter = {"n": 0}

    def distinct(r, n, m):          # ids are per-second; make them unique inside one test
        t = orig(r, n, m)
        counter["n"] += 1
        t["id"] = "%s-%d" % (t["id"], counter["n"])
        return t
    chat.new_thread = distinct
    try:
        drive(root, ["topic one", "/new", "topic two"], model("a1", "b1"))
    finally:
        chat.new_thread = orig
    titles = sorted(r["title"] for r in chat.list_threads(root))
    assert titles == ["topic one", "topic two"], titles


def test_undo_drops_the_last_exchange():
    root = tempfile.mkdtemp()
    rc, out, s = drive(root, ["one", "two", "/undo"], model("r1", "r2"))
    texts = [m.get("content") for m in chat.list_threads(root)[0]["messages"]]
    assert "two" not in texts and "r2" not in texts and "one" in texts and "r1" in texts, texts


def test_ctrl_c_mid_reply_drops_only_that_turn():
    root = tempfile.mkdtemp()
    rc, out, s = drive(root, ["keep me", "interrupt me", "after"],
                       model("kept", KeyboardInterrupt(), "fine"))
    texts = [m.get("content") for m in chat.list_threads(root)[0]["messages"]]
    assert "interrupt me" not in texts and "keep me" in texts and "after" in texts, texts
    assert any("stopped" in line for line in out), out


def test_backslash_continues_a_line():
    root = tempfile.mkdtemp()
    m = model("ok")
    drive(root, ["line one\\", "line two"], m)
    assert m.state["seen"][0][-1]["content"] == "line one\nline two", m.state["seen"][0][-1]


def test_commands_do_not_reach_the_model():
    root = tempfile.mkdtemp()
    m = model("x")
    rc, out, s = drive(root, ["/help", "/tools", "/where", "/steps 3", "/yes", "/nope"], m)
    assert m.state["i"] == 0 and s.steps == 3 and s.yes is True, (m.state["i"], s.steps, s.yes)
    joined = "\n".join(out)
    assert "/resume" in joined and "build" in joined and "unknown command /nope" in joined, joined


def test_resume_by_prefix_and_ambiguity():
    root = tempfile.mkdtemp()
    for tid in ("20260101-000001", "20260101-000002", "20260202-000001"):
        t = chat.new_thread(root, "p40", "m")
        t["id"], t["title"] = tid, "t" + tid
        chat.save_thread(root, t)
    assert chat.find_thread(root, "20260202")["id"] == "20260202-000001"
    assert chat.find_thread(root, "20260101") is None          # two match: refuse to guess
    assert chat.find_thread(root, "nothing") is None


def test_rename_and_list():
    root = tempfile.mkdtemp()
    rc, out, s = drive(root, ["hello", "/rename my topic", "/list"], model("hi"))
    assert chat.list_threads(root)[0]["title"] == "my topic"
    assert any("my topic" in line and "<- current" in line for line in out), out


def test_damaged_thread_file_does_not_hide_the_others():
    root = tempfile.mkdtemp()
    t = chat.new_thread(root, "p40", "m")
    t["title"] = "good"
    chat.save_thread(root, t)
    with open(os.path.join(chat.threads_dir(root), "broken.json"), "w") as fh:
        fh.write("{not json")
    assert [x["title"] for x in chat.list_threads(root)] == ["good"]


def test_save_is_atomic_no_tmp_left():
    root = tempfile.mkdtemp()
    t = chat.new_thread(root, "p40", "m")
    chat.save_thread(root, t)
    assert not [f for f in os.listdir(chat.threads_dir(root)) if f.endswith(".tmp")]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL ", name, "->", exc)
            except Exception as exc:  # a crash is a failure too
                failures += 1
                print("ERROR", name, "->", type(exc).__name__, exc)
    print("%d failure(s)" % failures)
    sys.exit(1 if failures else 0)
