#!/usr/bin/env python3
"""A basic agent harness: one model drives `tools.py`'s narrow tools in a loop.

    ./harness.py "the tests in this folder fail; find out why and fix it"
    ./harness.py --root ~/proj --yes "add a --verbose flag to cli.py"

The model can look (read_file, list_directory, grep, git_status, git_diff), run the
project's own check (run_check, from `.swarm-check`), and change code only through
`build`, which hands the request to the swarm via `ralph.sh`. There is no shell tool:
a model that wants `rm -rf` has no way to ask for it (see tools.py for why).

Loop control, which tools.py left for real transcripts to inform:
  * step budget (--steps, default 12);
  * repeat detection on (call, result): the same call giving the same answer 2x gets a
    warning in the tool result, 3x stops the run. The same call with a NEW result (a
    check re-run after a build) is progress, not a repeat;
  * history compaction: old tool output is trimmed past ~24k tokens, so a long session
    never grows into the region where models start looping;
  * `num_ctx` is chosen once and held (a change reloads the model);
  * every message is appended to <root>/.harness/<time>.jsonl. Those are the
    transcripts tools.py said it needed.
Anything with effects asks first; with no tty it declines. `--yes` approves all.
Exit: 0 final answer given, 1 step budget / stuck / pool error, 2 bad usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import swarm
import tools

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_NODE = "p40"
DEFAULT_MODEL = "gemma4:26b-a4b-it-mtp-q4_K_M"
MAX_STEPS = 12
ANSWER_TOKENS = 1500        # per step; thinking is off, tool calls are short
COMPACT_TOKENS = 24000      # trim older tool output above this
KEEP_CHARS = 600            # what a trimmed tool result keeps
REPEAT_LIMIT = 3
TEMPERATURE = 0.3           # not 0: greedy decoding is how small models loop

SYSTEM = (
    "You are a coding assistant working in one project folder, using tools. Work in small "
    "steps: look first (list_directory, read_file, grep, git_status, git_diff), then act. "
    "Call ONE tool at a time and read its result before the next. To create or change any "
    "code, call `build` with a specific request; it writes and tests the code, so do not "
    "describe code you have not built. After a build, confirm with run_check if it exists. "
    "If a tool result says an error, change your approach instead of repeating the call. "
    "When the work is finished, or you cannot proceed, answer in plain words with no tool call.")


@tools.registry.tool(
    "build",
    "Have the local model pool write or fix code for a request. Use for any change to the "
    "project's files. Be specific; it loops until its checks pass or it gives up.",
    params={"request": {"type": "string", "description": "what to build or fix"}},
    required=["request"], policy=tools.CONFIRM)
def build(root: str, request: str) -> str:
    cmd = [os.path.join(HERE, "ralph.sh")]
    check = tools.check_command(root)
    if check:
        cmd += ["--check", check]       # the operator's own check wins when there is one
    cmd.append(request)
    try:
        done = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise tools.ToolError("build could not run: %s" % exc)
    tail = (done.stdout + done.stderr).strip().splitlines()[-30:]
    return "exit %d\n%s" % (done.returncode, "\n".join(tail))


def compact(messages: List[Dict[str, Any]], limit: int = COMPACT_TOKENS) -> int:
    """Trim the oldest tool results until the history fits. Returns how many were cut.

    The system prompt, the request, the model's own turns and the two newest tool results
    are never touched; only old bulk output is.
    """
    cut = 0
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_idx[:-2]:
        if swarm.est_tokens(json.dumps(messages)) <= limit:
            break
        text = messages[i].get("content") or ""
        if len(text) > KEEP_CHARS:
            messages[i]["content"] = text[:KEEP_CHARS] + "\n...(older output trimmed)"
            cut += 1
    return cut


def _sig(call_key: str, result: str) -> str:
    return hashlib.sha1((call_key + "\0" + result).encode("utf-8", "replace")).hexdigest()


def run(request: str, root: str, chat_fn: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
        reg: Optional[tools.Registry] = None, confirm: Callable[[str], bool] = tools.ask_tty,
        assume_yes: bool = False, max_steps: int = MAX_STEPS,
        log_path: Optional[str] = None, say: Callable[[str], None] = print,
        messages: Optional[List[Dict[str, Any]]] = None) -> int:
    """Run one request. Pass `messages` (mutated in place) to continue a conversation:
    the request is appended as a new user turn after whatever is already there."""
    reg = reg or tools.registry
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]
        fresh = list(messages)
    else:
        if not messages:
            messages.append({"role": "system", "content": SYSTEM})
        messages.append({"role": "user", "content": request})
        fresh = [messages[-1]]
    seen: Dict[str, int] = {}

    def log(entry: Dict[str, Any]) -> None:
        if not log_path:
            return
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    for m in fresh:
        log(m)
    for step in range(1, max_steps + 1):
        compact(messages)
        try:
            msg = chat_fn(messages)
        except (swarm.OllamaError, OSError, ValueError) as exc:
            say("  could not reach the pool: %s" % exc)
            return 1
        calls = msg.get("tool_calls") or []
        messages.append(msg)
        log(msg)
        if not calls:
            say((msg.get("content") or "").strip() or "(no answer)")
            return 0
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            say("  step %d: %s" % (step, tools.render(name, args if isinstance(args, dict) else {})))
            rec = tools.execute(reg, root, name, args, confirm=confirm, assume_yes=assume_yes)
            content = "%s: %s" % (rec["status"], rec["detail"]) if rec["detail"] else rec["status"]
            key = name + json.dumps(args, sort_keys=True, default=str)
            sig = _sig(key, content)
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] >= 2:
                content += ("\n(you already made this exact call and got this exact result; "
                            "it will not change. Do something different or give your answer.)")
            tool_msg = {"role": "tool", "tool_name": name, "content": content}
            messages.append(tool_msg)
            log(tool_msg)
            if seen[sig] >= REPEAT_LIMIT:
                say("  stopping: the same call returned the same result %d times." % seen[sig])
                return 1
    say("  stopping: step budget (%d) used up without a final answer." % max_steps)
    return 1


def make_chat(cfg: swarm.Config, node: swarm.Node, model: str, schemas: List[Dict[str, Any]],
              timeout: float = 600.0) -> Callable[[List[Dict[str, Any]]], Dict[str, Any]]:
    """Ollama chat with tools. `num_ctx` is fixed once so the model is never reloaded."""
    num_ctx = swarm.pick_num_ctx(cfg, node, model,
                                 COMPACT_TOKENS + swarm.est_tokens(json.dumps(schemas)),
                                 ANSWER_TOKENS, reasoning=False)

    def chat(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = {"model": model, "messages": messages, "tools": schemas, "stream": False,
                   "think": False,
                   "options": {"num_ctx": num_ctx, "num_predict": ANSWER_TOKENS,
                               "temperature": TEMPERATURE}}
        resp = swarm._request(node.url + "/api/chat", payload, timeout)
        body = json.loads(resp.read().decode("utf-8", "replace"))
        if body.get("error"):
            raise swarm.OllamaError(str(body["error"]))
        return body.get("message") or {}
    return chat


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Agent loop over the project's narrow tools.")
    ap.add_argument("request", nargs="*", help="what you want done")
    ap.add_argument("--root", default=".", help="project folder (default .)")
    ap.add_argument("--node", default=DEFAULT_NODE)
    ap.add_argument("--model", help="default: %s on the default node" % DEFAULT_MODEL)
    ap.add_argument("--steps", type=int, default=MAX_STEPS)
    ap.add_argument("--yes", action="store_true", help="approve every action (use deliberately)")
    args = ap.parse_args(argv)
    request = " ".join(args.request).strip()
    if not request:
        ap.error("say what you want done")

    cfg = swarm.Config.load()
    node = cfg.node(args.node)
    if node is None or not node.enabled:
        sys.stderr.write("node %r is missing or disabled\n" % args.node)
        return 2
    model = args.model or (DEFAULT_MODEL if DEFAULT_MODEL in node.models else node.models[0])
    root = os.path.abspath(args.root)
    log_path = os.path.join(root, ".harness", time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    sys.stderr.write("  %s/%s  root=%s  log=%s\n" % (node.name, model, root, log_path))
    chat = make_chat(cfg, node, model, tools.registry.schemas())
    return run(request, root, chat, assume_yes=args.yes, max_steps=args.steps, log_path=log_path)


if __name__ == "__main__":
    raise SystemExit(main())
