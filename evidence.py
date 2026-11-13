#!/usr/bin/env python3
"""Ground truth for claims about code.

A model reviewing a file can produce a claim that is textually accurate and
still wrong. The case that motivated this: a reviewer correctly observed that
`state.record_perf(...)` is called from several threads with no lock around
the call, and concluded it was a race. It isn't -- `State.record_perf` takes
`self.lock` internally. The reviewer never saw that method, because it lives in
a different file than the one under review, so no amount of re-reading the
draft could have caught it.

The fix is not a better prompt, it is better evidence. This module resolves a
claim into facts before anyone is asked to judge it:

  - the actual source at the cited lines, read from disk
  - the definition of every symbol the claim names, found anywhere in the tree
  - deterministic checks that need no model at all (does it parse, does that
    line exist, does that symbol exist)

Anything decidable without a model is decided here. Only the genuinely
judgement-shaped remainder gets escalated.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import ast
import io
import os
import re
import tokenize
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".git", "__pycache__", ".swarm", "node_modules", ".venv", "venv",
             ".audit_probe"}


# --------------------------------------------------------------------------
# structured claims
# --------------------------------------------------------------------------

CLAIM_FORMAT = """Report each finding in exactly this block format, and nothing else:

CLAIM: <one sentence, the specific problem>
FILE: <path as given to you>
LINES: <single line number, or a start-end range>
SYMBOLS: <comma-separated function/method/class names your claim depends on>
WHY: <one or two sentences of reasoning>

Repeat the block for each finding. If you find nothing, reply exactly: none found

SYMBOLS must name every function or method whose behaviour your claim assumes.
If you claim a call is unsafe, name the function being called -- its definition
will be checked, including definitions in other files."""

_FIELD = re.compile(r"^\s*(CLAIM|FILE|LINES|SYMBOLS|WHY)\s*:\s*(.*)$", re.IGNORECASE)


class Claim:
    def __init__(self, raw: Dict[str, str]):
        self.text: str = raw.get("claim", "").strip()
        self.file: str = raw.get("file", "").strip().strip("`")
        self.lines: str = raw.get("lines", "").strip()
        self.symbols: List[str] = [
            s.strip().strip("`()")
            for s in re.split(r"[,\s]+", raw.get("symbols", ""))
            if s.strip().strip("`()")
        ]
        self.why: str = raw.get("why", "").strip()
        self.evidence: Dict[str, Any] = {}
        self.verdict: str = "UNCHECKED"
        self.reason: str = ""

    def line_range(self) -> Optional[Tuple[int, int]]:
        nums = [int(n) for n in re.findall(r"\d+", self.lines)]
        if not nums:
            return None
        return (min(nums), max(nums))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.text, "file": self.file, "lines": self.lines,
            "symbols": self.symbols, "why": self.why,
            "verdict": self.verdict, "reason": self.reason,
            "evidence": self.evidence,
        }


def parse_claims(text: str) -> List[Claim]:
    """Pull structured claim blocks out of a model's reply."""
    if text.strip().lower().startswith("none found"):
        return []
    claims: List[Claim] = []
    current: Dict[str, str] = {}
    for line in text.splitlines():
        m = _FIELD.match(line.lstrip("*-# "))
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2)
        if key == "claim" and current.get("claim"):
            claims.append(Claim(current))
            current = {}
        current[key] = value
    if current.get("claim"):
        claims.append(Claim(current))
    return claims


# --------------------------------------------------------------------------
# deterministic checks
# --------------------------------------------------------------------------


def compile_check(path: str) -> Dict[str, Any]:
    """Does this file actually parse and compile? No model required."""
    result: Dict[str, Any] = {"path": path, "parses": False, "error": None}
    if not os.path.isfile(path):
        result["error"] = "file does not exist"
        return result
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as exc:
        result["error"] = str(exc)
        return result
    try:
        compile(src, path, "exec")
        result["parses"] = True
    except SyntaxError as exc:
        result["error"] = "%s at line %s" % (exc.msg, exc.lineno)
        result["line"] = exc.lineno
    return result


def read_lines(path: str, start: int, end: int, pad: int = 6
               ) -> Optional[List[Tuple[int, str]]]:
    """The real source at those line numbers, with a little context."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    lo = max(1, start - pad)
    hi = min(len(lines), end + pad)
    if start > len(lines):
        return []
    return [(i, lines[i - 1].rstrip("\n")) for i in range(lo, hi + 1)]


# --------------------------------------------------------------------------
# symbol index
# --------------------------------------------------------------------------


class SymbolIndex:
    """Where every function, method and class in the tree is defined.

    Built with ast rather than grep so that a method keeps its qualified name
    and its real body bounds, which is what makes cross-file checking possible.
    """

    def __init__(self, root: str = HERE):
        self.root = root
        self.table: Dict[str, List[Dict[str, Any]]] = {}
        self._build()

    def _build(self) -> None:
        for path in self._python_files():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            lines = src.splitlines()
            self._walk(tree, path, lines, prefix="")

    def _python_files(self) -> List[str]:
        found: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py"):
                    found.append(os.path.join(dirpath, name))
        return found

    def _walk(self, node: ast.AST, path: str, lines: List[str], prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = child.name
                qualified = "%s.%s" % (prefix, name) if prefix else name
                start = child.lineno
                end = getattr(child, "end_lineno", start) or start
                body = "\n".join(
                    "%6d| %s" % (i, lines[i - 1])
                    for i in range(start, min(end, len(lines)) + 1)
                )
                entry = {
                    "name": name, "qualified": qualified, "path": path,
                    "line": start, "end_line": end,
                    "kind": type(child).__name__, "source": body,
                }
                self.table.setdefault(name, []).append(entry)
                if qualified != name:
                    self.table.setdefault(qualified, []).append(entry)
                if isinstance(child, ast.ClassDef):
                    self._walk(child, path, lines, prefix=name)

    def lookup(self, symbol: str, max_chars: int = 4000) -> List[Dict[str, Any]]:
        """Definitions for a symbol, tolerating `obj.method` style references."""
        candidates: List[Dict[str, Any]] = []
        for key in (symbol, symbol.split(".")[-1]):
            if key in self.table:
                candidates = self.table[key]
                break
        out = []
        for entry in candidates[:3]:
            trimmed = dict(entry)
            if len(trimmed["source"]) > max_chars:
                trimmed["source"] = trimmed["source"][:max_chars] + "\n... (truncated)"
            out.append(trimmed)
        return out


# --------------------------------------------------------------------------
# claim resolution
# --------------------------------------------------------------------------


def resolve(claim: Claim, index: SymbolIndex, root: str = HERE) -> Claim:
    """Attach real evidence, and settle what can be settled mechanically."""
    ev: Dict[str, Any] = {}

    path = claim.file
    if path and not os.path.isabs(path):
        path = os.path.join(root, path)
    ev["resolved_path"] = path
    ev["file_exists"] = os.path.isfile(path)

    if not ev["file_exists"]:
        claim.verdict = "REFUTED"
        claim.reason = "cited file %r does not exist" % claim.file
        claim.evidence = ev
        return claim

    total = sum(1 for _ in open(path, "r", encoding="utf-8", errors="replace"))
    ev["file_lines"] = total

    span = claim.line_range()
    if span:
        start, end = span
        if start > total:
            claim.verdict = "REFUTED"
            claim.reason = ("cited line %d is past the end of %s, which has %d lines"
                            % (start, claim.file, total))
            claim.evidence = ev
            return claim
        ev["cited_source"] = read_lines(path, start, end)

    # Symbol definitions, wherever they live. This is the part that was missing
    # when a lock in another file made a "race condition" claim wrong.
    defs: Dict[str, Any] = {}
    missing: List[str] = []
    for sym in claim.symbols:
        hits = index.lookup(sym)
        if hits:
            defs[sym] = [
                {"path": os.path.relpath(h["path"], root), "line": h["line"],
                 "kind": h["kind"], "source": h["source"]}
                for h in hits
            ]
        else:
            missing.append(sym)
    ev["symbol_definitions"] = defs
    ev["symbols_not_found"] = missing

    if claim.symbols and len(missing) == len(claim.symbols):
        claim.verdict = "REFUTED"
        claim.reason = ("none of the named symbols exist in the project: %s"
                        % ", ".join(missing))
        claim.evidence = ev
        return claim

    claim.verdict = "NEEDS_JUDGEMENT"
    claim.evidence = ev
    return claim


def evidence_brief(claim: Claim, max_chars: int = 9000) -> str:
    """Render the gathered facts for a model that has to decide."""
    parts: List[str] = []
    parts.append("CLAIM: %s" % claim.text)
    parts.append("STATED REASONING: %s" % claim.why)
    parts.append("FILE: %s (%s lines)"
                 % (claim.file, claim.evidence.get("file_lines", "?")))

    cited = claim.evidence.get("cited_source")
    if cited:
        parts.append("\nACTUAL SOURCE AT THE CITED LINES:")
        parts.append("\n".join("%6d| %s" % (n, t) for n, t in cited))

    defs = claim.evidence.get("symbol_definitions") or {}
    if defs:
        parts.append("\nDEFINITIONS OF THE SYMBOLS THIS CLAIM DEPENDS ON:")
        for sym, hits in defs.items():
            for h in hits:
                parts.append("\n--- %s  (%s line %d) ---" % (sym, h["path"], h["line"]))
                parts.append(h["source"])

    missing = claim.evidence.get("symbols_not_found")
    if missing:
        parts.append("\nSYMBOLS NOT FOUND ANYWHERE IN THE PROJECT: %s"
                     % ", ".join(missing))

    text = "\n".join(parts)
    return text[:max_chars]


ADJUDICATE_PROMPT = """You are checking whether a claim about code is actually true.

You have the real source, read from disk, including the definitions of the
functions the claim depends on. Judge against this evidence only -- not against
what the code looks like it probably does.

Be specific about the mechanism. If a call looks unsafe but the function it
calls handles the problem internally, the claim is REFUTED. If the evidence
does not settle it either way, say UNPROVEN rather than guessing.

Reply in exactly this format:

VERDICT: CONFIRMED or REFUTED or UNPROVEN
REASON: <one or two sentences citing the specific evidence>

EVIDENCE:
{brief}"""


REBUT_PROMPT = """A reviewer claims the code below has a bug, and a first judge
agreed. Your job is the opposite one: find the strongest reason the claim is
WRONG.

Look for the usual reasons a plausible bug report turns out to be fine -- the
called function already handles it, a caller guarantees the condition, the
branch is unreachable, the value cannot be empty or None at that point.

Only if there is genuinely no such reason should you agree the bug is real.

Reply in exactly this format:

VERDICT: CONFIRMED if the bug is real, REFUTED if the claim is wrong, UNPROVEN if unclear
REASON: <one or two sentences citing the specific evidence>

EVIDENCE:
{brief}"""


def parse_adjudication(text: str) -> Tuple[str, str]:
    verdict, reason = "UNPROVEN", ""
    for line in text.splitlines():
        s = line.strip().lstrip("*-# ").strip()
        up = s.upper()
        if up.startswith("VERDICT"):
            for token in ("CONFIRMED", "REFUTED", "UNPROVEN"):
                if token in up:
                    verdict = token
                    break
        elif up.startswith("REASON"):
            reason = s.split(":", 1)[-1].strip()
    return verdict, reason


if __name__ == "__main__":
    import json
    import sys

    index = SymbolIndex()
    if len(sys.argv) > 1 and sys.argv[1] == "symbols":
        for name in sorted(index.table)[:200]:
            entry = index.table[name][0]
            print("%-40s %s:%d" % (name, os.path.basename(entry["path"]), entry["line"]))
    else:
        text = sys.stdin.read()
        out = []
        for claim in parse_claims(text):
            out.append(resolve(claim, index).to_dict())
        print(json.dumps(out, indent=2))
