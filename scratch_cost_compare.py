"""Price two agent transcripts against each other.

Counts what the frontier model actually emitted -- assistant prose plus the
arguments it wrote into tool calls -- because that is what a delegating agent
is supposed to shrink. Reading tool *results* is not counted as output; those
are input, and they differ for a separate reason.
"""
import glob
import json
import os
import sys

import swarm

DIR = ("/Users/skimrey/.cursor/projects/"
       "Users-skimrey-Documents-Local-LLMs-begin/agent-transcripts/"
       "c3455000-c7c8-4f33-96af-cc8de60ff981/subagents")


def _text(block):
    """Everything the model emitted in one content block."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        return block.get("text") or ""
    if kind in ("tool_use", "tool_call"):
        # The arguments are authored by the model, so they are output. A file
        # written via a tool call is the model writing the file.
        return json.dumps(block.get("input") or block.get("arguments") or {})
    return ""


def measure(path):
    out_chars = 0
    in_chars = 0
    turns = 0
    tools = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [content]
        if rec.get("role") == "assistant":
            turns += 1
            for b in blocks:
                out_chars += len(_text(b))
                if isinstance(b, dict) and b.get("type") in ("tool_use",
                                                             "tool_call"):
                    name = b.get("name") or "?"
                    tools[name] = tools.get(name, 0) + 1
        elif rec.get("role") == "user":
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    in_chars += len(c if isinstance(c, str) else json.dumps(c))
                else:
                    in_chars += len(_text(b))
    return {"out_tokens": swarm.est_tokens("x" * out_chars),
            "in_tokens": swarm.est_tokens("x" * in_chars),
            "turns": turns, "tools": tools}


def main():
    arms = {}
    for label, ident in (("delegated to the pool", sys.argv[1]),
                         ("wrote it itself", sys.argv[2])):
        matches = glob.glob(os.path.join(DIR, ident + "*.jsonl"))
        if not matches:
            print("no transcript for %s" % ident)
            return 1
        arms[label] = measure(matches[0])

    print("%-24s %10s %10s %7s  %s"
          % ("arm", "out tok", "in tok", "turns", "tool calls"))
    for label, m in arms.items():
        calls = ", ".join("%s x%d" % (k, v) for k, v in
                          sorted(m["tools"].items(), key=lambda kv: -kv[1])[:5])
        print("%-24s %10d %10d %7d  %s"
              % (label, m["out_tokens"], m["in_tokens"], m["turns"], calls))

    a = arms["delegated to the pool"]
    b = arms["wrote it itself"]
    if b["out_tokens"]:
        print("\noutput tokens: delegating is %.2fx the control"
              % (a["out_tokens"] / float(b["out_tokens"])))
    if b["in_tokens"] + b["out_tokens"]:
        print("total context : delegating is %.2fx the control"
              % ((a["in_tokens"] + a["out_tokens"])
                 / float(b["in_tokens"] + b["out_tokens"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
