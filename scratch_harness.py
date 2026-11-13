#!/usr/bin/env python3
"""Offline tests for harness.py: a scripted model, no network, no swarm."""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402
import tools  # noqa: E402


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def final(text):
    return {"role": "assistant", "content": text}


def scripted(*msgs):
    """A chat_fn that returns the given messages in order, then keeps repeating the last."""
    state = {"i": 0, "seen": []}

    def chat(messages):
        state["seen"].append(json.loads(json.dumps(messages)))
        i = min(state["i"], len(msgs) - 1)
        state["i"] += 1
        return msgs[i]
    chat.state = state
    return chat


def project():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.py"), "w") as fh:
        fh.write("print('hi')\n")
    return d


def quiet():
    out = []
    return out, out.append


def test_read_then_answer():
    root = project()
    out, say = quiet()
    chat = scripted(call("list_directory"), call("read_file", path="a.py"), final("it prints hi"))
    rc = harness.run("what does a.py do", root, chat, say=say)
    assert rc == 0 and out[-1] == "it prints hi", out
    last = chat.state["seen"][-1]
    tool_msgs = [m for m in last if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "print('hi')" in tool_msgs[1]["content"], tool_msgs


def test_identical_call_and_result_stops_the_run():
    root = project()
    out, say = quiet()
    chat = scripted(call("list_directory"))               # same call forever
    rc = harness.run("loop", root, chat, say=say, max_steps=20)
    assert rc == 1 and chat.state["i"] == harness.REPEAT_LIMIT, (rc, chat.state["i"], out)
    assert any("same call returned the same result" in line for line in out), out
    warned = [m for m in chat.state["seen"][-1] if m["role"] == "tool"][-1]["content"]
    assert "exact same" not in warned and "already made this exact call" in warned, warned


def test_same_call_with_new_result_is_progress_not_a_repeat():
    root = project()
    out, say = quiet()
    seq = [call("read_file", path="a.py"), call("read_file", path="a.py"), final("done")]
    chat = scripted(*seq)
    orig = chat

    def chat_that_edits(messages):                       # file changes between the two reads
        if orig.state["i"] == 1:
            with open(os.path.join(root, "a.py"), "w") as fh:
                fh.write("print('changed')\n")
        return orig(messages)
    rc = harness.run("x", root, chat_that_edits, say=say)
    assert rc == 0, out


def test_step_budget():
    root = project()
    out, say = quiet()
    msgs = [call("read_file", path="a.py", ) if i == 0 else call("grep", pattern="x%d" % i) for i in range(10)]
    chat = scripted(*msgs)
    rc = harness.run("x", root, chat, say=say, max_steps=3)
    assert rc == 1 and chat.state["i"] == 3 and "step budget" in out[-1], (rc, out)


def test_confirm_gate_declines_and_nothing_runs():
    root = project()
    out, say = quiet()
    ran = []
    real = subprocess.run
    subprocess.run = lambda *a, **k: ran.append(a) or real(*a, **k)
    try:
        chat = scripted(call("build", request="add a thing"), final("could not"))
        rc = harness.run("x", root, chat, confirm=lambda line: False, say=say)
    finally:
        subprocess.run = real
    assert rc == 0 and not ran, (rc, ran)
    result = [m for m in chat.state["seen"][-1] if m["role"] == "tool"][0]["content"]
    assert result.startswith("declined"), result


def test_build_command_uses_ralph_and_the_operators_check():
    root = project()
    with open(os.path.join(root, ".swarm-check"), "w") as fh:
        fh.write("python3 -c 'print(1)'")
    captured = {}

    class Done:
        returncode, stdout, stderr = 0, "ralph: ok\n", ""
    real = subprocess.run
    subprocess.run = lambda cmd, **k: captured.update(cmd=cmd, cwd=k.get("cwd")) or Done()
    try:
        text = harness.build(root, "make x")
    finally:
        subprocess.run = real
    cmd = captured["cmd"]
    assert cmd[0].endswith("ralph.sh") and "--check" in cmd and cmd[-1] == "make x", cmd
    assert cmd[cmd.index("--check") + 1] == "python3 -c 'print(1)'" and captured["cwd"] == root
    assert text.startswith("exit 0"), text


def test_bad_arguments_are_reported_back_not_fatal():
    root = project()
    out, say = quiet()
    chat = scripted(call("read_file", pth="a.py"), call("nope"), final("ok"))
    rc = harness.run("x", root, chat, say=say)
    tool_msgs = [m["content"] for m in chat.state["seen"][-1] if m["role"] == "tool"]
    assert rc == 0 and tool_msgs[0].startswith("invalid") and tool_msgs[1].startswith("invalid"), tool_msgs


def test_paths_outside_the_root_are_refused():
    root = project()
    out, say = quiet()
    chat = scripted(call("read_file", path="../../etc/passwd"), final("no"))
    harness.run("x", root, chat, say=say)
    msg = [m["content"] for m in chat.state["seen"][-1] if m["role"] == "tool"][0]
    assert msg.startswith("refused"), msg


def test_compaction_trims_old_output_but_not_the_newest():
    big = "x" * 8000
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(5):
        msgs.append({"role": "assistant", "content": "", "tool_calls": []})
        msgs.append({"role": "tool", "tool_name": "read_file", "content": big + str(i)})
    cut = harness.compact(msgs, limit=3000)
    tool = [m for m in msgs if m["role"] == "tool"]
    assert cut == 3 and all("trimmed" in m["content"] for m in tool[:3]), cut
    assert all("trimmed" not in m["content"] for m in tool[-2:])
    assert msgs[0]["content"] == "s" and msgs[1]["content"] == "u"


def test_transcript_is_written():
    root = project()
    path = os.path.join(root, ".harness", "t.jsonl")
    harness.run("x", root, scripted(call("list_directory"), final("done")), log_path=path, say=lambda s: None)
    rows = [json.loads(line) for line in open(path)]
    roles = [r["role"] for r in rows]
    assert roles == ["system", "user", "assistant", "tool", "assistant"], roles


def test_pool_error_is_reported_not_raised():
    import swarm
    root = project()
    out, say = quiet()

    def boom(messages):
        raise swarm.OllamaError("down")
    rc = harness.run("x", root, boom, say=say)
    assert rc == 1 and "could not reach" in out[-1], out


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
    print("%d failure(s)" % failures)
    sys.exit(1 if failures else 0)
