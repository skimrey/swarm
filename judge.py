#!/usr/bin/env python3
"""A model as one gate inside a check, for rules a script cannot measure.

A check runs on a scratch copy, many times per ask, over the whole stored set,
and a kept value the check rejects later is dropped along with everything
after it. So a judge inside a check has to behave like a measurement:

- One yes/no question per rule, asked at temperature 0 with a fixed seed.
- A verdict is cached by hash of (model, rule, text) in
  `<project>/.ralph/judge-cache.jsonl`, outside the scratch copy, and is never
  asked again. Changing a rule's wording is a new rule.
- A rule is broken only when the judge says so AND quotes words that really
  appear in the text. A quote that is not in the text is a hallucinated
  finding and counts as a pass. A false failure blocks a valid value for good;
  a missed one costs less.
- The judge runs on a local node that is not the writer's (default `mac`,
  loopback, so the network guard on checks lets it through).
- If the judge cannot be reached the rule passes, the miss is printed on
  stderr, and nothing is cached. A check that failed on an outage would reject
  kept values and drop them.

From a Python check:

    import judge
    for f in judge.failures(text, ["The text never addresses the reader as 'you'."]):
        print("CHECK FAILED: text %d: %s" % (i, f))

From any other check:

    python3 "$SWARM_HOME/judge.py" --rubric rubric.txt --text-file t.txt

The rubric file holds one rule per line, each a statement that is TRUE of a
good text. Exit 0 when every rule holds; otherwise exit 1 and one line per
broken rule, naming the rule, the quoted words and what to change.
`--expect pass|fail` turns a run into a calibration step on a known text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_NODE = "mac"

PROMPT = """You are checking one text against one rule. Judge only that rule.
{context}
RULE (a statement that is true of a good text):
{rule}

TEXT:
{text}

Is the RULE broken by the TEXT? If it is broken, copy the exact words from the
TEXT that break it, character for character, and say in one sentence what to
change. If the rule holds, say so.

Reply with one JSON object and nothing else:
{{"broken": true or false, "quote": "words copied from the text, or empty", "fix": "what to change, or empty"}}"""


CONFIRM = """A rule and a short quotation from a text are below. Would a careful
reader agree that the quoted words, on their own, break the rule? Answer no
when the words are ordinary or neutral and the rule is only broken in a
stretched reading.

RULE: {rule}
QUOTED WORDS: {quote}

Reply with one JSON object and nothing else: {{"violates": true or false}}"""


def _root(root: Optional[str] = None) -> str:
    return root or os.environ.get("BLOSSOM_ROOT") or os.getcwd()


def _cache_path(root: str) -> str:
    return os.path.join(root, ".ralph", "judge-cache.jsonl")


def _load_cache(root: str) -> Dict[str, Dict[str, Any]]:
    cache: Dict[str, Dict[str, Any]] = {}
    try:
        with open(_cache_path(root), encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and "key" in row:
                    cache[row["key"]] = row
    except OSError:
        pass
    return cache


def _remember(root: str, row: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_cache_path(root)), exist_ok=True)
        with open(_cache_path(root), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _node(name: str) -> Tuple[str, str, int]:
    """(url, model, num_ctx) of the judge node, from config.json beside this file.

    The context is the node's `pin_ctx`. Ollama reloads a model whenever the
    context changes, so a judge on the writer's node has to ask for the size
    the writer already holds.
    """
    home = os.environ.get("SWARM_HOME") or HERE
    try:
        with open(os.path.join(home, "config.json"), encoding="utf-8") as fh:
            nodes = json.load(fh).get("nodes", [])
    except (OSError, json.JSONDecodeError):
        nodes = []
    for node in nodes:
        if node.get("name") == name and node.get("models"):
            return (node["url"], os.environ.get("JUDGE_MODEL") or node["models"][0],
                    int(node.get("pin_ctx") or 0))
    raise RuntimeError("judge node %r not found in config.json" % name)


def _ask(url: str, model: str, prompt: str, timeout: float = 120.0,
         num_ctx: int = 0) -> str:
    body = {"model": model, "stream": False, "think": False, "format": "json",
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, "seed": 7, "num_predict": 400}}
    if num_ctx:
        body["options"]["num_ctx"] = num_ctx
    req = urllib.request.Request(
        url.rstrip("/") + "/api/chat", json.dumps(body).encode(),
        {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["message"]["content"]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _verdict(reply: str, text: str) -> Dict[str, Any]:
    """Parse the reply. Broken only with a quote that is really in the text."""
    try:
        data = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{[\s\S]*\}", reply or "")
        try:
            data = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            data = {}
    broken = bool(data.get("broken")) if isinstance(data, dict) else False
    quote = str(data.get("quote") or "").strip() if isinstance(data, dict) else ""
    fix = str(data.get("fix") or "").strip() if isinstance(data, dict) else ""
    if broken and not (quote and _squash(quote) in _squash(text)):
        return {"broken": False, "quote": "", "fix": "",
                "note": "a finding whose quote is not in the text is dropped"}
    return {"broken": broken, "quote": quote if broken else "",
            "fix": fix if broken else ""}


def check(text: str, rule: str, root: Optional[str] = None,
          node: Optional[str] = None, context: str = "") -> Optional[Dict[str, Any]]:
    """The verdict for one rule, from cache or the judge. None when unreachable."""
    root = _root(root)
    try:
        url, model, num_ctx = _node(node or os.environ.get("JUDGE_NODE") or DEFAULT_NODE)
    except RuntimeError as exc:
        sys.stderr.write("judge: %s; rule passes\n" % exc)
        return None
    key = hashlib.sha256(
        "\0".join((model, rule.strip(), context.strip(), text)).encode("utf-8")).hexdigest()
    cached = _load_cache(root).get(key)
    if cached:
        return cached["verdict"]
    try:
        reply = _ask(url, model, PROMPT.format(
            rule=rule.strip(), text=text,
            context=("\nABOUT THE TEXT: " + context.strip() + "\n") if context.strip() else ""),
            num_ctx=num_ctx)
    except (OSError, urllib.error.URLError, KeyError, json.JSONDecodeError,
            ValueError) as exc:
        sys.stderr.write("judge: %s unreachable (%s); rule passes, nothing cached\n"
                         % (url, exc))
        return None
    verdict = _verdict(reply, text)
    if verdict["broken"]:
        # A second question in different words. A finding stands only when
        # both agree: a false failure blocks a valid value for good.
        try:
            again = json.loads(_ask(url, model, CONFIRM.format(
                rule=rule.strip(), quote=verdict["quote"]), num_ctx=num_ctx))
            if not (isinstance(again, dict) and again.get("violates") is True):
                verdict = {"broken": False, "quote": "", "fix": "",
                           "note": "the second question did not confirm it"}
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            pass
    _remember(root, {"key": key, "model": model, "rule": rule.strip(),
                     "verdict": verdict})
    return verdict


def failures(text: str, rules: Sequence[str], root: Optional[str] = None,
             node: Optional[str] = None, context: str = "") -> List[str]:
    """One line per broken rule: the rule, the quoted words, what to change."""
    out: List[str] = []
    for rule in rules:
        rule = rule.strip()
        if not rule:
            continue
        verdict = check(text, rule, root, node, context)
        if verdict and verdict.get("broken"):
            out.append("breaks the rule %r at the words %r. %s" % (
                rule, verdict["quote"],
                (verdict.get("fix") or "Change those words so the rule holds.")))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rubric", required=True, help="file, one rule per line")
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--node", default=None)
    ap.add_argument("--expect", choices=("pass", "fail"), default=None,
                    help="calibration: say whether the verdict matched")
    args = ap.parse_args(argv)
    with open(args.rubric, encoding="utf-8") as fh:
        lines = [r.strip() for r in fh if r.strip()]
    # `#context: ...` lines say what the text is, so the judge reads it right
    # and words its fix in the writer's terms. Other `#` lines are comments.
    context = " ".join(l[len("#context:"):].strip() for l in lines if l.lower().startswith("#context:"))
    rules = [l for l in lines if not l.startswith("#")]
    with open(args.text_file, encoding="utf-8") as fh:
        text = fh.read()
    found = failures(text, rules, args.root, args.node, context)
    for line in found:
        print(line)
    if args.expect:
        got = "fail" if found else "pass"
        print("calibration: expected %s, got %s%s" % (
            args.expect, got, "" if got == args.expect else "  <-- MISMATCH"))
        return 0 if got == args.expect else 1
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
