#!/usr/bin/env python3
"""Chat with the local model pool, with saved threads. Run it from the project folder.

    hchat                 new thread          hchat -c          continue the latest thread
    hchat --list          list threads        hchat -r ID       resume one (id or a prefix)
    hchat -y              approve every action without asking (use deliberately)

Inside: plain text is sent to the model; lines starting with / are commands (/help).
Each message runs the agent loop in harness.py (look, check, build), and the thread is
saved after every turn to <project>/.harness/threads/<id>.json, so closing the window
loses nothing. See HARNESS.md for the guide.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import harness
import swarm
import tools

THREAD_WARN_TOKENS = 60000   # past this a thread is long enough that /new is the better move

HELP = """\
  /new              start a fresh thread          /list           threads in this project
  /resume ID        switch to a thread (prefix ok) /rename TEXT   name this thread
  /undo             drop your last message and the reply
  /steps N          max tool steps per message     /yes            toggle approve-everything
  /tools            what the model can do          /where          project, model, thread file
  /help             this                           /quit           leave (Ctrl-D also works)
  end a line with \\ to continue it on the next line.  Ctrl-C stops the current reply."""


# ------------------------------------------------------------------ threads
def threads_dir(root: str) -> str:
    return os.path.join(root, ".harness", "threads")


def new_thread(root: str, node: str, model: str) -> Dict[str, Any]:
    now = time.time()
    return {"id": time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), "title": "",
            "created": now, "updated": now, "node": node, "model": model, "messages": []}


def save_thread(root: str, thread: Dict[str, Any]) -> str:
    d = threads_dir(root)
    os.makedirs(d, exist_ok=True)
    thread["updated"] = time.time()
    path = os.path.join(d, thread["id"] + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(thread, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)           # a crash mid-write never leaves half a thread
    return path


def list_threads(root: str) -> List[Dict[str, Any]]:
    d = threads_dir(root)
    out = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue                 # a damaged file should not hide the others
    out.sort(key=lambda t: t.get("updated", 0), reverse=True)
    return out


def find_thread(root: str, ident: str) -> Optional[Dict[str, Any]]:
    matches = [t for t in list_threads(root) if t["id"].startswith(ident)]
    return matches[0] if len(matches) == 1 else None


def summary(t: Dict[str, Any]) -> str:
    turns = sum(1 for m in t.get("messages", []) if m.get("role") == "user")
    when = time.strftime("%b %d %H:%M", time.localtime(t.get("updated", 0)))
    return "%-16s %-13s %2d msg  %s" % (t["id"], when, turns, t.get("title") or "(empty)")


# --------------------------------------------------------------------- repl
class Session:
    def __init__(self, root: str, thread: Dict[str, Any], chat_fn: Callable, yes: bool = False,
                 steps: int = harness.MAX_STEPS, confirm: Callable[[str], bool] = tools.ask_tty):
        self.root, self.thread, self.chat_fn = root, thread, chat_fn
        self.yes, self.steps, self.confirm = yes, steps, confirm


def read_message(inp: Callable[[str], str]) -> str:
    """One message; a trailing backslash continues onto the next line."""
    lines = []
    prompt = "you> "
    while True:
        line = inp(prompt)
        if line.endswith("\\"):
            lines.append(line[:-1])
            prompt = "...> "
            continue
        lines.append(line)
        return "\n".join(lines).strip()


def turn(s: Session, text: str, out: Callable[[str], None]) -> None:
    msgs = s.thread["messages"]
    if swarm.est_tokens(json.dumps(msgs)) > THREAD_WARN_TOKENS:
        out("  (this thread is long, and models get unreliable as history grows; /new is better)")
    before = len(msgs)
    if not s.thread["title"]:
        s.thread["title"] = " ".join(text.split())[:60]
    try:
        harness.run(text, s.root, s.chat_fn, assume_yes=s.yes, max_steps=s.steps,
                    confirm=s.confirm, say=out, messages=msgs)
    except KeyboardInterrupt:
        del msgs[before:]            # a half-finished turn would confuse the next one
        out("\n  stopped; that message was dropped from the thread.")
    save_thread(s.root, s.thread)


def command(s: Session, line: str, out: Callable[[str], None], mk_chat: Callable) -> bool:
    """Handle a /command. Returns False to quit."""
    word, _, rest = line[1:].partition(" ")
    rest = rest.strip()
    if word in ("quit", "exit", "q"):
        return False
    if word in ("help", "h", "?"):
        out(HELP)
    elif word == "new":
        s.thread = new_thread(s.root, s.thread["node"], s.thread["model"])
        out("  new thread " + s.thread["id"])
    elif word == "list":
        rows = list_threads(s.root)
        out("\n".join(summary(t) + ("   <- current" if t["id"] == s.thread["id"] else "")
                      for t in rows) or "  no saved threads yet")
    elif word == "resume":
        t = find_thread(s.root, rest) if rest else None
        if t is None:
            out("  no single thread matches %r (try /list)" % rest)
        else:
            # the running session's backend answers, whatever the old thread used
            t["node"], t["model"] = s.thread["node"], s.thread["model"]
            s.thread = t
            out("  resumed %s: %s" % (t["id"], t.get("title") or "(empty)"))
    elif word == "rename":
        if rest:
            s.thread["title"] = rest[:80]
            save_thread(s.root, s.thread)
        out("  title: " + (s.thread["title"] or "(empty)"))
    elif word == "undo":
        msgs = s.thread["messages"]
        idx = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
        if idx:
            del msgs[idx[-1]:]
            save_thread(s.root, s.thread)
            out("  dropped your last message and everything after it")
        else:
            out("  nothing to undo")
    elif word == "steps":
        if rest.isdigit() and int(rest) > 0:
            s.steps = int(rest)
        out("  max steps per message: %d" % s.steps)
    elif word == "yes":
        s.yes = not s.yes
        out("  approve-everything is now %s" % ("ON" if s.yes else "off"))
    elif word == "tools":
        for name in tools.registry.names():
            t = tools.registry.get(name)
            out("  %-16s %-8s %s" % (name, t.policy, t.description))
    elif word == "where":
        out("  project %s\n  model   %s/%s\n  thread  %s" % (
            s.root, s.thread["node"], s.thread["model"],
            os.path.join(threads_dir(s.root), s.thread["id"] + ".json")))
    else:
        out("  unknown command /%s (try /help)" % word)
    return True


def repl(s: Session, mk_chat: Callable, inp: Callable[[str], str] = input,
         out: Callable[[str], None] = print) -> int:
    out("  chat in %s  [%s/%s]  thread %s   (/help for commands)" % (
        s.root, s.thread["node"], s.thread["model"], s.thread["id"]))
    while True:
        try:
            text = read_message(inp)
        except (EOFError, KeyboardInterrupt):
            out("")
            return 0
        if not text:
            continue
        if text.startswith("/"):
            if not command(s, text, out, mk_chat):
                return 0
            continue
        turn(s, text, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Chat with saved threads over the local pool.")
    ap.add_argument("-c", "--continue", dest="cont", action="store_true", help="latest thread")
    ap.add_argument("-r", "--resume", metavar="ID", help="resume a thread (id or prefix)")
    ap.add_argument("--list", action="store_true", help="list threads and exit")
    ap.add_argument("--root", default=".", help="project folder (default: here)")
    ap.add_argument("--node", default=harness.DEFAULT_NODE)
    ap.add_argument("--model")
    ap.add_argument("--steps", type=int, default=harness.MAX_STEPS)
    ap.add_argument("-y", "--yes", action="store_true")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)

    if args.list:
        rows = list_threads(root)
        print("\n".join(summary(t) for t in rows) or "no saved threads in " + root)
        return 0

    cfg = swarm.Config.load()
    node = cfg.node(args.node)
    if node is None or not node.enabled:
        sys.stderr.write("node %r is missing or disabled (see config.json)\n" % args.node)
        return 2
    model = args.model or (harness.DEFAULT_MODEL if harness.DEFAULT_MODEL in node.models
                           else node.models[0])

    thread = None
    if args.resume:
        thread = find_thread(root, args.resume)
        if thread is None:
            sys.stderr.write("no single thread matches %r (try --list)\n" % args.resume)
            return 2
    elif args.cont:
        rows = list_threads(root)
        thread = rows[0] if rows else None
    if thread is None:
        thread = new_thread(root, node.name, model)
    else:
        node = cfg.node(thread.get("node", node.name)) or node
        model = thread.get("model", model)

    base = harness.make_chat(cfg, node, model, tools.registry.schemas())

    def chat_fn(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        sys.stdout.write("  ...\r")
        sys.stdout.flush()
        try:
            return base(messages)
        finally:
            sys.stdout.write("     \r")

    return repl(Session(root, thread, chat_fn, yes=args.yes, steps=args.steps), lambda: chat_fn)


if __name__ == "__main__":
    raise SystemExit(main())
