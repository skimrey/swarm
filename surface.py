"""Surfaces: repair the part of a value the check blames, keep the rest.

A value is a set of parts (platforms in a level, sentences in a paragraph,
rows in a table). A check failure usually names where it hurts. A surface
knows how to split a value into parts, find the parts a failure line names,
and apply a small edit (REPLACE / ADD / REMOVE) that touches only those parts.

The loop in team.py asks the model for the edit instead of a whole new value,
so everything the check did not blame stays exactly as it was.

Nothing here knows about platformers or prose. The project says what its parts
are in `surface.json` (project root or `.ralph/`). Two kinds:

  json-list   the value holds one JSON object with a list of parts
              (e.g. {"platforms": [[row, c0, c1], ...], "start": 0, "goal": 12}).
              "refs" are keys holding positions in that list; they are kept in
              step when a part is added or removed.
  text        the value is prose. A part is a sentence, paragraph or line.

Locators say how a failure line points at parts. Each is a regex with named
groups; see _locate_list / _locate_text. A failure no locator can place is
global (it is about the whole value) and the loop falls back to a whole-value
rewrite for it.

Edit grammar a reply may use, one edit per line:
    REPLACE <n> <value>      change part n
    ADD <value>              add a part (json-list: end of list; text: end)
    REMOVE <n>               drop part n
A reply that is only a value, with exactly one part in play, replaces it.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# There is no cap on the number of edits: a paragraph or a level can need many, and an edit that
# makes things worse is discarded by the check, not by a limit.


class SurfaceError(Exception):
    """The reply cannot be applied. The message is shown to the model."""


# --------------------------------------------------------------------------
# loading

def load(root: str) -> Optional["Surface"]:
    for rel in ("surface.json", os.path.join(".ralph", "surface.json")):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return from_config(json.load(fh))
            except (OSError, ValueError, SurfaceError):
                return None
    return None


def from_config(cfg: Dict[str, Any]) -> "Surface":
    kind = cfg.get("kind")
    if kind == "json-list":
        return ListSurface(cfg)
    if kind == "text":
        return TextSurface(cfg)
    raise SurfaceError("unknown surface kind %r" % (kind,))


# --------------------------------------------------------------------------
# shared

def _lines_only(lines: Sequence[str]) -> List[str]:
    return [re.sub(r"^CHECK FAILED:\s*", "", l or "", flags=re.I) for l in lines]


def _matches(rx, loc: Dict[str, Any], line: str):
    """Matches of a locator in a failure line.

    "when" and "unless" are regexes the whole line must / must not match.
    "scope" is a regex whose first group is the only text searched, so a list
    inside one sentence of the line can be read without reading the rest.
    """
    if loc.get("when") and not re.search(loc["when"], line, re.I):
        return []
    if loc.get("unless") and re.search(loc["unless"], line, re.I):
        return []
    haystack = line
    if loc.get("scope"):
        m = re.search(loc["scope"], line, re.I | re.S)
        if not m:
            return []
        haystack = m.group(1)
    return list(rx.finditer(haystack))


_MD_ESCAPE = re.compile(r"\\([\[\]()_*#`])")
_EDIT_JOIN = re.compile(r"(?<=[\]\)])[ \t]+(?=(?:ADD|REPLACE\s+\d+|REMOVE\s+\d+)\b)"
                        r"|[ \t]+(?=(?:REPLACE\s+\d+|REMOVE\s+\d+)\b)")


def _unpad(raw: str) -> str:
    """Zero-padded whole numbers ([5, 04, 10]) are not JSON; a model writes them to line columns up. `04` is 4.
    Decimals (0.5) and numbers with zeros inside (100) are left alone."""
    return re.sub(r"(?<![\d.\w])0+(?=\d)", "", raw)


def _tidy_reply(reply: str) -> str:
    """A reply that went through a markdown page (a browser model) arrives with
    `\\[` and `\\]` for brackets and several edits on one line. Undo both.
    Splitting only happens on the capitalised keywords, after a closing bracket
    (ADD) or before `REPLACE n` / `REMOVE n`, so prose that says "add" is left."""
    text = _MD_ESCAPE.sub(r"\1", reply or "")
    text = re.sub(r"(?im)^[ \t]*(ADD|REPLACE[ \t]+\d+)[ \t]*:?[ \t]*\n+[ \t]*(?=\S)", r"\1 ", text)
    return "\n".join(_EDIT_JOIN.sub("\n", line) for line in text.splitlines())


def _parse_edits(reply: str) -> List[Tuple[str, Optional[int], str]]:
    edits = []
    for raw in _tidy_reply(reply).splitlines():
        line = raw.strip()
        m = re.match(r"(?i)^REPLACE\s+(\d+):?\s+(\S.*)$", line)
        if m:
            edits.append(("REPLACE", int(m.group(1)), m.group(2).strip()))
            continue
        m = re.match(r"(?i)^REMOVE\s+(\d+)\s*$", line)
        if m:
            edits.append(("REMOVE", int(m.group(1)), ""))
            continue
        m = re.match(r"(?i)^ADD:?\s+(\S.*)$", line)
        if m:
            edits.append(("ADD", None, m.group(1).strip()))
    return edits


class Plan:
    """What the loop needs to repair one failure through its parts."""

    def __init__(self, line: str, ids: List[int], prompt: str, allowed: List[int],
                 max_edits: int = 0):
        self.line = line          # the failure line this repair targets
        self.ids = ids            # part numbers (in the surface's own numbering)
        self.prompt = prompt      # the instructions shown to the model
        self.allowed = allowed    # parts the reply may REPLACE or REMOVE


class Surface:
    kind = ""

    def plan(self, value_text: str, lines: Sequence[str]) -> Optional[Plan]:
        """A part-level repair for the first failure line that can be placed."""
        raise NotImplementedError

    def plan_global(self, value_text: str, lines: Sequence[str]) -> Optional[Plan]:
        """A repair for a line no locator can place: any part may change, but
        only by a few edits, so the rest of the value is kept."""
        return None

    def apply(self, value_text: str, plan: Plan, reply: str) -> str:
        """The value with the reply's edits applied. Raises SurfaceError."""
        raise NotImplementedError

    def changed(self, before: str, after: str) -> List[int]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# json-list

class ListSurface(Surface):
    kind = "json-list"

    def __init__(self, cfg: Dict[str, Any]):
        self.key = cfg["key"]
        self.refs = list(cfg.get("refs", []))
        self.fields = dict(cfg.get("fields", {}))     # name -> index inside an item
        self.base = int(cfg.get("base", 0))
        self.max_parts = int(cfg.get("max_parts", 3))
        self.name = cfg.get("part_name", "part")
        self.item_format = cfg.get("item_format", "the same shape as the others")
        self.locators = []
        for loc in cfg.get("locators", []):
            self.locators.append((re.compile(loc["regex"], re.I), loc))

    # -- splitting ---------------------------------------------------------
    def _split(self, text: str):
        """(prefix, obj, suffix) for the first JSON object holding the list."""
        text = text or ""
        start = text.find("{")
        while start >= 0:
            depth = 0
            for end in range(start, len(text)):
                ch = text[end]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start:end + 1])
                        except ValueError:
                            break
                        if isinstance(obj, dict) and isinstance(obj.get(self.key), list):
                            return text[:start], obj, text[end + 1:]
                        break
            start = text.find("{", start + 1)
        return None

    def _dump(self, prefix: str, obj: Dict[str, Any], suffix: str) -> str:
        return prefix + json.dumps(obj) + suffix

    # -- locating ----------------------------------------------------------
    def _locate(self, items: List[Any], line: str) -> List[int]:
        found: List[int] = []

        def add(i):
            if 0 <= i < len(items) and i not in found:
                found.append(i)

        for rx, loc in self.locators:
            for m in _matches(rx, loc, line):
                g = m.groupdict()
                if g.get("i") is not None:
                    add(int(g["i"]) - self.base)
                    continue
                if loc.get("ref"):
                    ref = loc["ref"]
                    return_obj = getattr(self, "_obj", {})
                    if isinstance(return_obj.get(ref), int):
                        add(return_obj[ref])
                    continue
                if g.get("row") is None or g.get("c0") is None:
                    continue
                row = int(g["row"]) + int(loc.get("row_offset", 0))
                a = int(g["c0"])
                b = int(g["c1"]) if g.get("c1") is not None else a
                if a > b:
                    a, b = b, a
                fr, f0, f1 = (self.fields.get(k) for k in ("row", "c0", "c1"))
                for i, it in enumerate(items):
                    try:
                        r, lo, hi = int(it[fr]), int(it[f0]), int(it[f1])
                    except (TypeError, ValueError, IndexError, KeyError):
                        continue
                    if lo > hi:
                        lo, hi = hi, lo
                    if r == row and lo <= b and a <= hi:
                        add(i)
        return found

    def plan(self, value_text: str, lines: Sequence[str]) -> Optional[Plan]:
        parts = self._split(value_text)
        if parts is None:
            return None
        _, obj, _ = parts
        self._obj = obj
        items = obj[self.key]
        for line in _lines_only(lines):
            ids = self._locate(items, line)[: self.max_parts]
            if not ids:
                continue
            shown = "\n".join(
                "  %s %d: %s" % (self.name, i + self.base, json.dumps(items[i]))
                for i in ids)
            prompt = (
                "The check says:\n%s\n\n"
                "That line is about these %ss and nothing else in the value:\n%s\n\n"
                "Every other %s stays exactly as it is. Change only what the line "
                "needs, using one edit per line:\n"
                "  REPLACE <n> <%s>   change %s n\n"
                "  ADD <%s>           add a %s\n"
                "  REMOVE <n>         remove %s n\n"
                "Only %s %s may be replaced or removed. A %s is %s. "
                "Reply with the edits and nothing else.\n"
                % (line, self.name, shown, self.name, self.name, self.name,
                   self.name, self.name, self.name, self.name,
                   ", ".join(str(i + self.base) for i in ids),
                   self.name, self.item_format))
            return Plan(line, [i + self.base for i in ids], prompt,
                        [i + self.base for i in ids])
        return None

    def plan_global(self, value_text: str, lines: Sequence[str]) -> Optional[Plan]:
        parts = self._split(value_text)
        lines = _lines_only(lines)
        if parts is None or not lines:
            return None
        _, obj, _ = parts
        self._obj = obj
        items = obj[self.key]
        if not items:
            return None
        line = lines[0]
        ids = list(range(len(items)))
        shown = "\n".join("  %s %d: %s" % (self.name, i + self.base, json.dumps(items[i]))
                          for i in ids)
        prompt = (
            "The check says:\n%s\n\n"
            "That rule is about the whole value, not one %s. The %ss now:\n%s\n\n"
            "Make the smallest change that clears that rule and keep everything "
            "else exactly as it is: every %s you do not mention stays. As many "
            "edits as it takes, one per line:\n"
            "  REPLACE <n> <%s>   change %s n\n"
            "  ADD <%s>           add a %s\n"
            "  REMOVE <n>         remove %s n\n"
            "A %s is %s. Reply with the edits and nothing else.\n"
            % (line, self.name, self.name, shown, self.name,
               self.name, self.name, self.name, self.name, self.name,
               self.name, self.item_format))
        return Plan(line, [i + self.base for i in ids], prompt,
                    [i + self.base for i in ids])

    # -- applying ----------------------------------------------------------
    def apply(self, value_text: str, plan: Plan, reply: str) -> str:
        parts = self._split(value_text)
        if parts is None:
            raise SurfaceError("the held value has no %s list" % self.key)
        prefix, obj, suffix = parts
        items = list(obj[self.key])
        edits = _parse_edits(reply)
        if not edits and len(plan.ids) == 1:
            bare = _bare_json(reply)
            if bare is not None:
                edits = [("REPLACE", plan.ids[0], json.dumps(bare))]
        if not edits:
            raise SurfaceError(
                "the reply holds no edit. Write lines like: REPLACE %d <%s>"
                % (plan.ids[0], self.name))
        # resolve removals and replacements against the ORIGINAL numbering
        removed: List[int] = []
        for op, n, raw in edits:
            if op in ("REPLACE", "REMOVE"):
                if n not in plan.allowed:
                    raise SurfaceError(
                        "%s %s is not one the check line names; only %s may be "
                        "%s" % (self.name, n, plan.allowed,
                                "replaced or removed"))
            if op == "REPLACE":
                try:
                    value = json.loads(_unpad(raw))
                except ValueError:
                    raise SurfaceError("REPLACE %d: %r is not JSON" % (n, raw[:60]))
                items[n - self.base] = value
            elif op == "REMOVE":
                for ref in self.refs:
                    if obj.get(ref) == n - self.base:
                        raise SurfaceError(
                            "%s %d is the %s %s and cannot be removed; replace it"
                            % (self.name, n, ref, self.name))
                removed.append(n - self.base)
        added = []
        for op, n, raw in edits:
            if op == "ADD":
                try:
                    added.append(json.loads(_unpad(raw)))
                except ValueError:
                    raise SurfaceError("ADD: %r is not JSON" % raw[:60])
        for ref in self.refs:
            at = obj.get(ref)
            if isinstance(at, int):
                obj[ref] = at - sum(1 for r in removed if r < at)
        items = [it for i, it in enumerate(items) if i not in set(removed)]
        items.extend(added)
        obj[self.key] = items
        return self._dump(prefix, obj, suffix)

    def changed(self, before: str, after: str) -> List[int]:
        a, b = self._split(before), self._split(after)
        if not a or not b:
            return []
        x, y = a[1][self.key], b[1][self.key]
        out = [i + self.base for i in range(min(len(x), len(y))) if x[i] != y[i]]
        out += [i + self.base for i in range(min(len(x), len(y)), max(len(x), len(y)))]
        return out


def _bare_json(reply: str):
    text = (reply or "").strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    if text.endswith(";"):
        text = text[:-1]
    try:
        return json.loads(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# text

_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+[\"')\]]*|\n|$)\s*")


class TextSurface(Surface):
    """Prose, split into sentences, paragraphs or lines."""

    kind = "text"

    def __init__(self, cfg: Dict[str, Any]):
        self.unit = cfg.get("unit", "sentence")
        if self.unit not in ("sentence", "paragraph", "line"):
            raise SurfaceError("unit must be sentence, paragraph or line")
        self.base = int(cfg.get("base", 1))
        self.max_parts = int(cfg.get("max_parts", 3))
        self.locators = []
        for loc in cfg.get("locators", []):
            self.locators.append((re.compile(loc["regex"], re.I), loc))

    # -- splitting: each part keeps its trailing whitespace so a join is exact
    def _split(self, text: str) -> List[Tuple[str, str]]:
        text = text or ""
        if self.unit == "sentence":
            raw = [m.group(0) for m in _SENTENCE.finditer(text) if m.group(0)]
            if "".join(raw) != text:
                raw = [text]
        elif self.unit == "paragraph":
            raw = re.findall(r".*?(?:\n\s*\n|\Z)", text, flags=re.S)
            raw = [r for r in raw if r]
        else:
            raw = text.splitlines(keepends=True) or [text]
        out = []
        for r in raw:
            body = r.rstrip()
            out.append((body, r[len(body):]))
        return [p for p in out if p[0] or p[1]]

    def _join(self, parts: List[Tuple[str, str]]) -> str:
        return "".join(b + s for b, s in parts)

    # -- locating ------------------------------------------------------------
    def _locate(self, parts: List[Tuple[str, str]], line: str) -> List[int]:
        found: List[int] = []

        def add(i):
            if 0 <= i < len(parts) and i not in found:
                found.append(i)

        word = {"sentence": "sentence", "paragraph": "paragraph", "line": "line"}[self.unit]
        for m in re.finditer(r"(?i)\b%s\s+#?(\d+)\b" % word, line):
            add(int(m.group(1)) - self.base)
        for rx, loc in self.locators:
            for m in _matches(rx, loc, line):
                g = m.groupdict()
                if g.get("i") is not None:
                    add(int(g["i"]) - self.base)
        # a quoted piece of the text, found in exactly one part
        for quoted in re.findall(r"[\"“‘']([^\"”’']{3,})[\"”’']", line):
            hits = [i for i, (b, _) in enumerate(parts) if quoted.lower() in b.lower()]
            if len(hits) == 1:
                add(hits[0])
        return found

    def plan(self, value_text: str, lines: Sequence[str]) -> Optional[Plan]:
        parts = self._split(value_text)
        if not parts:
            return None
        for line in _lines_only(lines):
            ids = self._locate(parts, line)[: self.max_parts]
            if not ids:
                continue
            shown = "\n".join("  %s %d: %s" % (self.unit, i + self.base, parts[i][0])
                              for i in ids)
            numbers = ", ".join(str(i + self.base) for i in ids)
            prompt = (
                "The check says:\n%s\n\n"
                "That line is about these %ss and nothing else in the text:\n%s\n\n"
                "Every other %s stays exactly as written. Reply with one edit "
                "per line:\n"
                "  REPLACE <n> <new text for that %s, on one line>\n"
                "  ADD <new %s to put at the end>\n"
                "  REMOVE <n>\n"
                "Only %s %s may be replaced or removed. Reply with the edits "
                "and nothing else.\n"
                % (line, self.unit, shown, self.unit, self.unit, self.unit,
                   self.unit, numbers))
            return Plan(line, [i + self.base for i in ids], prompt,
                        [i + self.base for i in ids])
        return None

    def plan_global(self, value_text: str, lines: Sequence[str]) -> Optional[Plan]:
        parts = self._split(value_text)
        lines = _lines_only(lines)
        if not parts or not lines:
            return None
        line = lines[0]
        shown = "\n".join("  %s %d: %s" % (self.unit, i + self.base, b)
                          for i, (b, _) in enumerate(parts))
        prompt = (
            "The check says:\n%s\n\n"
            "That rule is about the whole text, not one %s. The %ss now:\n%s\n\n"
            "Make the smallest change that clears that rule and keep everything "
            "else exactly as written. As many edits as it takes, one per line:\n"
            "  REPLACE <n> <new text for that %s, on one line>\n"
            "  ADD <new %s to put at the end>\n"
            "  REMOVE <n>\n"
            "Reply with the edits and nothing else.\n"
            % (line, self.unit, self.unit, shown, self.unit, self.unit))
        ids = [i + self.base for i in range(len(parts))]
        return Plan(line, ids, prompt, ids)

    # -- applying ------------------------------------------------------------
    def apply(self, value_text: str, plan: Plan, reply: str) -> str:
        parts = self._split(value_text)
        edits = _parse_edits(reply)
        # Prose has no strict syntax, so a reply without an edit line is never
        # taken for a replacement: chat about the text would be pasted in.
        if not edits:
            raise SurfaceError(
                "the reply holds no edit. Write lines like: REPLACE %d <new text>"
                % plan.ids[0])
        removed = set()
        added: List[Tuple[str, str]] = []
        for op, n, raw in edits:
            if op in ("REPLACE", "REMOVE") and n not in plan.allowed:
                raise SurfaceError(
                    "%s %s is not one the check line names; only %s may be "
                    "replaced or removed" % (self.unit, n, plan.allowed))
            if op == "REPLACE":
                at = n - self.base
                text = raw
                if len(text) >= 2 and text[0] == text[-1] == '"':
                    try:
                        text = json.loads(text)
                    except ValueError:
                        pass
                parts[at] = (text.strip(), parts[at][1])
            elif op == "REMOVE":
                removed.add(n - self.base)
            else:
                text = raw
                if len(text) >= 2 and text[0] == text[-1] == '"':
                    try:
                        text = json.loads(text)
                    except ValueError:
                        pass
                sep = " " if self.unit == "sentence" else "\n"
                added.append((text.strip(), sep))
        kept = [p for i, p in enumerate(parts) if i not in removed]
        if kept and added:
            body, sep = kept[-1]
            kept[-1] = (body, sep or (" " if self.unit == "sentence" else "\n"))
        # The text ends the way it began: no trailing space invented by an ADD.
        tail = value_text[len(value_text.rstrip()):]
        return self._join(kept + added).rstrip() + tail

    def changed(self, before: str, after: str) -> List[int]:
        a, b = self._split(before), self._split(after)
        out = [i + self.base for i in range(min(len(a), len(b))) if a[i][0] != b[i][0]]
        out += [i + self.base for i in range(min(len(a), len(b)), max(len(a), len(b)))]
        return out
