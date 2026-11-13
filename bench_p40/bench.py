#!/usr/bin/env python3
"""Compare Strata (Qwen3.8-Flash-Next Coder) and Ollama (gemma4 26b-a4b mtp) on the P40 box.

Every task is checked by running code (coding tasks: hidden asserts; puzzles: an
integer computed here by brute force), never by a model's opinion.

  python3 bench.py selftest                 # run each test against its reference solution
  STRATA_KEY=... python3 bench.py run strata
  python3 bench.py run ollama
  python3 bench.py compare

Strata and Ollama cannot both hold the card, so run one backend at a time.
The Strata key is read from the STRATA_KEY environment variable only.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STRATA_URL = "http://10.0.0.178:8080"
OLLAMA_URL = "http://10.0.0.178:11434"
OLLAMA_MODEL = "gemma4:26b-a4b-it-mtp-q4_K_M"
MAX_TOKENS = 12000          # thinking + answer together
CALL_TIMEOUT = 1800

CODE_PREFIX = ("Write Python 3 code (standard library only) for the spec below. Reply with ONE ```python "
               "code block containing only the code: no tests, no prints, no example usage.\n\nSpec:\n")

# ---------------------------------------------------------------- coding tasks
TASKS = []


def task(tid, spec, test, ref):
    TASKS.append({"id": tid, "kind": "code", "prompt": CODE_PREFIX + spec, "test": test, "ref": ref})


task("lru", """class LRUCache(capacity) with get(key) -> value, or -1 if the key is absent, and put(key, value).
Both get (on a hit) and put (insert or update) make the key the most recently used. When a put would exceed
capacity, evict the least recently used key. Capacity 0 stores nothing. Both operations O(1).""",
"""
c = LRUCache(2); c.put(1, 1); c.put(2, 2)
assert c.get(1) == 1
c.put(3, 3)
assert c.get(2) == -1
c.put(4, 4)
assert c.get(1) == -1 and c.get(3) == 3 and c.get(4) == 4
z = LRUCache(0); z.put(1, 1)
assert z.get(1) == -1
u = LRUCache(2); u.put(1, 1); u.put(2, 2); u.put(1, 10); u.put(3, 3)
assert u.get(2) == -1 and u.get(1) == 10 and u.get(3) == 3
""",
"""
from collections import OrderedDict
class LRUCache:
    def __init__(self, capacity):
        self.cap = capacity
        self.d = OrderedDict()
    def get(self, key):
        if key not in self.d:
            return -1
        self.d.move_to_end(key)
        return self.d[key]
    def put(self, key, value):
        if self.cap <= 0:
            return
        if key in self.d:
            self.d.move_to_end(key)
        self.d[key] = value
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
""")

task("duration", """def parse_duration(s) -> int: total seconds for strings like "1d2h3m4s".
Units are d (86400), h (3600), m (60), s (1). Each unit may appear at most once, and units must appear in
that descending order (d, h, m, s); any subset is allowed. Each unit has a non-negative integer of ASCII digits
directly before it. No spaces, no signs, no decimals, no bare numbers. Raise ValueError for an empty string or
anything else that does not match.""",
"""
def _bad(s):
    try:
        parse_duration(s)
    except ValueError:
        return True
    return False
assert parse_duration("1d2h3m4s") == 93784
assert parse_duration("90s") == 90 and parse_duration("1h") == 3600 and parse_duration("0s") == 0
assert parse_duration("2d") == 172800 and parse_duration("5m10s") == 310
for b in ["", "5", "h", "1m1h", "1h1h", "1.5h", " 1h", "-1h", "1h ", "1x", "1h2", "s1"]:
    assert _bad(b), b
""",
"""
import re
def parse_duration(s):
    m = re.fullmatch(r"(?:([0-9]+)d)?(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", s)
    if not s or m is None:
        raise ValueError(s)
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se
""")

task("intervals", """def merge_intervals(intervals) -> list: intervals is a list of (start, end) pairs with start <= end.
Return the merged intervals as a list of tuples sorted by start. Intervals that overlap OR merely touch
(one's end equals the next's start) are merged. Do not modify the input list. An empty input returns [].""",
"""
a = [(1, 3), (2, 6), (8, 10), (15, 18)]
a0 = list(a)
assert merge_intervals(a) == [(1, 6), (8, 10), (15, 18)]
assert a == a0
assert merge_intervals([(1, 4), (4, 5)]) == [(1, 5)]
assert merge_intervals([]) == []
assert merge_intervals([(5, 5), (1, 2)]) == [(1, 2), (5, 5)]
assert merge_intervals([(1, 10), (2, 3)]) == [(1, 10)]
assert merge_intervals([(3, 4), (1, 2), (2, 3)]) == [(1, 4)]
assert all(isinstance(t, tuple) for t in merge_intervals([[1, 2], [5, 6]]))
""",
"""
def merge_intervals(intervals):
    out = []
    for a, b in sorted((tuple(i) for i in intervals)):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
""")

task("calc", """def calc(expr: str) -> number: evaluate an arithmetic expression. Support decimal numbers, + - * /, parentheses,
and unary minus (unary plus is NOT supported). Standard precedence, left-associative; division is true
division. Whitespace between tokens is ignored. Do not use eval/exec/ast. Raise ZeroDivisionError on division by
zero. Raise ValueError for any malformed input: empty, unbalanced parentheses, a trailing operator, two numbers
in a row ("1 2"), unknown characters, a malformed number like "1.2.3", or unary plus.""",
"""
def _raises(exc, e):
    try:
        calc(e)
    except exc:
        return True
    except Exception:
        return False
    return False
assert calc("1+2*3") == 7 and calc("(1+2)*3") == 9 and calc("-3+5") == 2
assert calc("2*-3") == -6 and calc("--4") == 4 and calc("10/4") == 2.5
assert calc("1 - 2 - 3") == -4 and calc("8/2/2") == 2 and calc("2*(3+4)*5") == 70
assert calc("1.5*2") == 3 and calc("-(2+3)") == -5 and calc(" 7 ") == 7
assert _raises(ZeroDivisionError, "1/0") and _raises(ZeroDivisionError, "5/(3-3)")
for b in ["", "1+", "(1", "1)", "1 2", "1.2.3", "+1", "2**3", "a", "()"]:
    assert _raises(ValueError, b), b
""",
"""
def calc(expr):
    toks = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == ".":
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            toks.append(float(expr[i:j]))
            i = j
        elif c in "+-*/()":
            toks.append(c)
            i += 1
        else:
            raise ValueError(c)
    pos = [0]
    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None
    def eat():
        t = peek()
        pos[0] += 1
        return t
    def add():
        v = mul()
        while peek() in ("+", "-"):
            op = eat()
            r = mul()
            v = v + r if op == "+" else v - r
        return v
    def mul():
        v = unary()
        while peek() in ("*", "/"):
            op = eat()
            r = unary()
            v = v * r if op == "*" else v / r
        return v
    def unary():
        if peek() == "-":
            eat()
            return -unary()
        return atom()
    def atom():
        t = eat()
        if t == "(":
            v = add()
            if eat() != ")":
                raise ValueError("paren")
            return v
        if isinstance(t, float):
            return t
        raise ValueError("atom")
    r = add()
    if pos[0] != len(toks):
        raise ValueError("trailing")
    return r
""")

task("toposort", """def topo_order(nodes, edges) -> list: nodes is an iterable of strings; edges is an iterable of (a, b) pairs
meaning a must come before b. Return a list of all distinct nodes in a valid topological order; among all valid
orders return the lexicographically smallest list (always take the smallest available node next). Duplicate edges
are allowed and mean the same as one. Raise ValueError if there is a cycle (a self-loop is a cycle) or an edge
names a node not in nodes. Empty input returns [].""",
"""
def _bad(n, e):
    try:
        topo_order(n, e)
    except ValueError:
        return True
    return False
assert topo_order(["a", "b", "c", "d"], [("d", "a"), ("b", "c")]) == ["b", "c", "d", "a"]
assert topo_order(["c", "b", "a"], []) == ["a", "b", "c"]
assert topo_order(["a", "b"], [("a", "b"), ("a", "b")]) == ["a", "b"]
assert topo_order([], []) == []
assert topo_order(["x", "y", "z"], [("z", "x"), ("z", "y")]) == ["z", "x", "y"]
assert topo_order(["a", "b", "c", "d", "e"], [("e", "a"), ("e", "b"), ("c", "d")]) == ["c", "d", "e", "a", "b"]
assert _bad(["a", "b"], [("a", "b"), ("b", "a")])
assert _bad(["a"], [("a", "a")])
assert _bad(["a"], [("a", "q")])
assert _bad(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])
""",
"""
import heapq
def topo_order(nodes, edges):
    ns = set(nodes)
    es = set(edges)
    for a, b in es:
        if a not in ns or b not in ns or a == b:
            raise ValueError
    indeg = {n: 0 for n in ns}
    adj = {n: [] for n in ns}
    for a, b in es:
        adj[a].append(b)
        indeg[b] += 1
    h = [n for n in ns if indeg[n] == 0]
    heapq.heapify(h)
    out = []
    while h:
        n = heapq.heappop(h)
        out.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(h, m)
    if len(out) != len(ns):
        raise ValueError
    return out
""")

task("osa", """def osa_distance(a, b) -> int: the optimal string alignment distance between two strings: the minimum number of
single-character insertions, deletions, substitutions, and transpositions of two ADJACENT characters, where no
substring may be edited more than once (the restricted Damerau-Levenshtein distance, not the unrestricted one).
So osa_distance("ca", "abc") is 3.""",
"""
assert osa_distance("ca", "abc") == 3
assert osa_distance("abcd", "acbd") == 1 and osa_distance("ab", "ba") == 1
assert osa_distance("", "abc") == 3 and osa_distance("abc", "") == 3
assert osa_distance("kitten", "sitting") == 3 and osa_distance("a", "a") == 0 and osa_distance("", "") == 0
assert osa_distance("abcdef", "badcfe") == 3
assert osa_distance("sunday", "saturday") == 3
""",
"""
def osa_distance(a, b):
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]
""")

task("wrap", """def wrap_text(text, width) -> str: greedy word wrap. Split text on whitespace (runs of whitespace collapse; the
result has no leading, trailing, or doubled spaces). Lines are joined with "\\n". Pack words onto a line
separated by single spaces while the line stays <= width characters. A word longer than width always starts a NEW
line (unless the current line is empty), is cut into chunks of exactly width characters, and its final shorter
chunk stays open so later words may still join it if they fit. Raise ValueError if width < 1. Text with no words
returns "".""",
"""
assert wrap_text("the quick brown fox", 10) == "the quick\\nbrown fox"
assert wrap_text("aaaaaaaaaaaa bb", 5) == "aaaaa\\naaaaa\\naa bb"
assert wrap_text("a b c", 1) == "a\\nb\\nc"
assert wrap_text("  hello   world ", 11) == "hello world"
assert wrap_text("", 5) == "" and wrap_text("   \\n ", 5) == ""
assert wrap_text("abcdefgh", 3) == "abc\\ndef\\ngh"
assert wrap_text("ab cdefg", 3) == "ab\\ncde\\nfg"
assert wrap_text("one two", 7) == "one two" and wrap_text("one two", 6) == "one\\ntwo"
try:
    wrap_text("x", 0)
    raise SystemExit("no ValueError")
except ValueError:
    pass
""",
"""
def wrap_text(text, width):
    if width < 1:
        raise ValueError
    lines, cur = [], ""
    for w in text.split():
        if len(w) > width:
            if cur:
                lines.append(cur)
                cur = ""
            chunks = [w[i:i + width] for i in range(0, len(w), width)]
            lines.extend(chunks[:-1])
            cur = chunks[-1]
        elif not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\\n".join(lines)
""")

task("cron", """def cron_match(expr, dt) -> bool: does the datetime.datetime dt match a 5-field cron expression
"minute hour day-of-month month day-of-week"? Ranges: minute 0-59, hour 0-23, day-of-month 1-31, month 1-12,
day-of-week 0-6 where 0 is Sunday (note Python's weekday() has Monday as 0). Each field is a comma-separated list
of parts; a part is "*", "a", "a-b", "*/n", or "a-b/n" (a step n >= 1 starts at the range start). Day rule: a
field is unrestricted only when it is exactly "*". If both day-of-month and day-of-week are restricted, the date
matches when EITHER matches; if only one is restricted, that one must match; minute, hour and month must always
match. Raise ValueError for a wrong field count, out-of-range values, a>b, or a bad step.""",
"""
import datetime as _dt
def _bad(e):
    try:
        cron_match(e, _dt.datetime(2026, 10, 12, 9, 30))
    except ValueError:
        return True
    return False
D = _dt.datetime
assert cron_match("*/15 9-17 * * 1-5", D(2026, 10, 12, 9, 30)) is True
assert cron_match("*/15 9-17 * * 1-5", D(2026, 10, 12, 9, 31)) is False
assert cron_match("*/15 9-17 * * 1-5", D(2026, 10, 11, 9, 30)) is False
assert cron_match("*/15 9-17 * * 1-5", D(2026, 10, 12, 18, 0)) is False
assert cron_match("0 0 1 1 *", D(2026, 1, 1, 0, 0)) is True
assert cron_match("0 0 1 1 *", D(2026, 1, 2, 0, 0)) is False
assert cron_match("0 12 13 * 5", D(2026, 10, 13, 12, 0)) is True
assert cron_match("0 12 13 * 5", D(2026, 10, 16, 12, 0)) is True
assert cron_match("0 12 13 * 5", D(2026, 10, 14, 12, 0)) is False
assert cron_match("1-10/3 * * * *", D(2026, 10, 12, 5, 7)) is True
assert cron_match("1-10/3 * * * *", D(2026, 10, 12, 5, 8)) is False
assert cron_match("1-10/3 * * * *", D(2026, 10, 12, 5, 10)) is True
assert cron_match("* * * * 0", D(2026, 10, 11, 3, 3)) is True
assert cron_match("* * * * 0", D(2026, 10, 12, 3, 3)) is False
assert cron_match("5,10 * * * *", D(2026, 10, 12, 5, 10)) is True
for b in ["* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 7", "5-2 * * * *", "*/0 * * * *"]:
    assert _bad(b), b
""",
"""
def cron_match(expr, dt):
    f = expr.split()
    if len(f) != 5:
        raise ValueError
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    sets = []
    for field, (lo, hi) in zip(f, ranges):
        s = set()
        for part in field.split(","):
            step, base = 1, part
            if "/" in part:
                base, st = part.split("/", 1)
                if not st.isdigit() or int(st) <= 0:
                    raise ValueError
                step = int(st)
            if base == "*":
                a, b = lo, hi
            elif "-" in base:
                x, y = base.split("-", 1)
                if not (x.isdigit() and y.isdigit()):
                    raise ValueError
                a, b = int(x), int(y)
            elif base.isdigit() and "/" not in part:
                a = b = int(base)
            else:
                raise ValueError
            if a < lo or b > hi or a > b:
                raise ValueError
            s.update(range(a, b + 1, step))
        sets.append(s)
    minute, hour, dom, mon, dow = sets
    dv = (dt.weekday() + 1) % 7
    if dt.minute not in minute or dt.hour not in hour or dt.month not in mon:
        return False
    ds, ws = f[2] == "*", f[4] == "*"
    if ds and ws:
        return True
    if ds:
        return dv in dow
    if ws:
        return dt.day in dom
    return dt.day in dom or dv in dow
""")

task("ratelimit", """class SlidingWindowLimiter(limit, window, clock) with allow(key) -> bool. clock is a zero-argument callable
returning the current time in seconds (float); call it once per allow(). A key may be allowed at most `limit`
times in any sliding window of `window` seconds. A recorded allowed event at time t stops counting once
now - t >= window (so exactly `window` seconds later it no longer counts). A denied request is NOT recorded.
Keys are independent. limit 0 always denies.""",
"""
class _C:
    t = 0.0
    def __call__(self):
        return self.t
c = _C()
L = SlidingWindowLimiter(2, 10, c)
c.t = 0; assert L.allow("a") is True
c.t = 1; assert L.allow("a") is True
c.t = 2; assert L.allow("a") is False
c.t = 10; assert L.allow("a") is True
c.t = 10.5; assert L.allow("a") is False
c.t = 11; assert L.allow("a") is True
c2 = _C()
M = SlidingWindowLimiter(1, 10, c2)
c2.t = 0; assert M.allow("a") is True
c2.t = 5; assert M.allow("a") is False
c2.t = 9.99; assert M.allow("a") is False
c2.t = 10; assert M.allow("a") is True
c3 = _C()
K = SlidingWindowLimiter(1, 10, c3)
assert K.allow("a") is True and K.allow("b") is True and K.allow("a") is False
Z = SlidingWindowLimiter(0, 10, c3)
assert Z.allow("a") is False
""",
"""
import collections
class SlidingWindowLimiter:
    def __init__(self, limit, window, clock):
        self.limit, self.window, self.clock = limit, window, clock
        self.ev = {}
    def allow(self, key):
        now = self.clock()
        q = self.ev.setdefault(key, collections.deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) < self.limit:
            q.append(now)
            return True
        return False
""")


# ------------------------------------------------------------- reasoning puzzles
def _puzzles():
    ways = [1] + [0] * 100
    for c in (1, 5, 10, 25, 50):
        for v in range(c, 101):
            ways[v] += ways[v - c]
    digit15 = sum(1 for n in range(1, 1001) if sum(map(int, str(n))) == 15)
    blocked = {(1, 1), (2, 3), (4, 2)}
    g = [[0] * 6 for _ in range(6)]
    for r in range(6):
        for c in range(6):
            if (r, c) in blocked:
                continue
            g[r][c] = 1 if (r, c) == (0, 0) else (g[r - 1][c] if r else 0) + (g[r][c - 1] if c else 0)
    surv = 0
    for i in range(2, 42):
        surv = (surv + 3) % i
    suffix = "\n\nThink it through, then finish with a final line exactly of the form `ANSWER: <integer>`."
    return [
        ("puz_change", "In how many different ways can you make exactly 100 cents using any number of coins "
         "of 1, 5, 10, 25 and 50 cents? Order does not matter." + suffix, ways[100]),
        ("puz_digits", "How many integers from 1 to 1000 inclusive have digits that sum to exactly 15?" + suffix, digit15),
        ("puz_grid", "A 6x6 grid has rows and columns numbered 0 to 5. A walker starts at (row 0, col 0) and must reach "
         "(row 5, col 5), moving only one step down (row+1) or one step right (col+1) at a time. The cells (1,1), (2,3) and "
         "(4,2) are blocked and cannot be entered. How many different paths are there?" + suffix, g[5][5]),
        ("puz_josephus", "41 people stand in a circle numbered 1 to 41. Starting at person 1 and counting around the circle, "
         "every 3rd person still in the circle is eliminated (so 3 goes first, then 6, then 9, and so on, continuing the count "
         "around the circle). Which numbered person is the last one remaining?" + suffix, surv + 1),
    ]


for _id, _prompt, _ans in _puzzles():
    TASKS.append({"id": _id, "kind": "puzzle", "prompt": _prompt, "answer": _ans})


# ---------------------------------------------------------------------- grading
def extract_code(text):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    blocks = [b for b in blocks if "def " in b or "class " in b]
    return blocks[-1] if blocks else None


def run_py(source, timeout=15):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.py")
        with open(p, "w") as fh:
            fh.write(source)
        try:
            r = subprocess.run([sys.executable, "-I", p], cwd=d, capture_output=True, text=True,
                               timeout=timeout, env={"PATH": os.environ.get("PATH", "")})
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return r.returncode == 0, (r.stderr or r.stdout)[-300:]


def grade(t, text):
    if t["kind"] == "code":
        code = extract_code(text or "")
        if code is None:
            return False, "no code block"
        return run_py(code + "\n\n" + t["test"])
    m = re.findall(r"ANSWER:\s*\**\s*(-?\d+)", text or "")
    if not m:
        return False, "no ANSWER line"
    got = int(m[-1])
    return got == t["answer"], "got %d want %d" % (got, t["answer"])


def selftest():
    bad = 0
    for t in TASKS:
        if t["kind"] == "code":
            ok, why = run_py(t["ref"] + "\n\n" + t["test"])
            print("%-12s reference %s %s" % (t["id"], "PASS" if ok else "FAIL", "" if ok else why))
            bad += (not ok)
            # a test that accepts a stub is useless
            ok2, _ = run_py("pass\n\n" + t["test"])
            if ok2:
                print("  !! test passes with no solution")
                bad += 1
        else:
            print("%-12s answer %s" % (t["id"], t["answer"]))
    print("selftest:", "OK" if not bad else "%d problem(s)" % bad)
    return bad


# ---------------------------------------------------------------------- calling
def post(url, payload, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as r:
        return json.loads(r.read())


def call(backend, prompt):
    t0 = time.time()
    if backend == "strata":
        key = os.environ.get("STRATA_KEY")
        if not key:
            sys.exit("set STRATA_KEY")
        model = json.loads(urllib.request.urlopen(urllib.request.Request(
            STRATA_URL + "/v1/models", headers={"Authorization": "Bearer " + key}), timeout=20).read())["data"][0]["id"]
        r = post(STRATA_URL + "/v1/chat/completions",
                 {"model": model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": MAX_TOKENS, "stream": False},
                 {"Authorization": "Bearer " + key})
        ch = r["choices"][0]
        msg = ch["message"]
        text = msg.get("content") or ""
        think = msg.get("reasoning_content") or msg.get("reasoning") or ""
        toks = (r.get("usage") or {}).get("completion_tokens")
        done = ch.get("finish_reason")
    else:
        r = post(OLLAMA_URL + "/api/chat",
                 {"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "stream": False, "think": True, "options": {"num_predict": MAX_TOKENS}})
        msg = r["message"]
        text = msg.get("content") or ""
        think = msg.get("thinking") or ""
        toks = r.get("eval_count")
        done = r.get("done_reason")
    return {"text": text, "think": think, "tokens": toks, "finish": done, "secs": time.time() - t0}


def run(backend, only=None):
    rawdir = os.path.join(HERE, "raw_" + backend)
    os.makedirs(rawdir, exist_ok=True)
    out_path = os.path.join(HERE, "results_%s.json" % backend)
    results = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for t in TASKS:
        if only and t["id"] not in only:
            continue
        if t["id"] in results:
            print("skip (done)", t["id"], flush=True)
            continue
        print("running", t["id"], flush=True)
        try:
            r = call(backend, t["prompt"])
        except Exception as e:  # network/timeouts are a result, not a crash
            results[t["id"]] = {"pass": False, "why": "call failed: %s" % e, "secs": None, "tokens": None, "finish": "error"}
            json.dump(results, open(out_path, "w"), indent=1)
            print("  call failed:", e, flush=True)
            continue
        ok, why = grade(t, r["text"])
        with open(os.path.join(rawdir, t["id"] + ".txt"), "w") as fh:
            fh.write("## thinking\n%s\n\n## answer\n%s\n" % (r["think"], r["text"]))
        results[t["id"]] = {"pass": ok, "why": why, "secs": round(r["secs"], 1), "tokens": r["tokens"],
                            "finish": r["finish"], "think_chars": len(r["think"])}
        json.dump(results, open(out_path, "w"), indent=1)
        print("  %s  %.0fs  %s tok  finish=%s  %s" % ("PASS" if ok else "FAIL", r["secs"], r["tokens"], r["finish"],
                                                       "" if ok else why), flush=True)


def compare():
    res = {}
    for b in ("strata", "ollama"):
        p = os.path.join(HERE, "results_%s.json" % b)
        res[b] = json.load(open(p)) if os.path.exists(p) else {}
    print("%-13s | %-34s | %-34s" % ("task", "strata (Qwen Coder IQ1_M)", "ollama (gemma 26b-a4b)"))
    tot = {"strata": [0, 0, 0.0, 0], "ollama": [0, 0, 0.0, 0]}
    for t in TASKS:
        cells = []
        for b in ("strata", "ollama"):
            r = res[b].get(t["id"])
            if not r:
                cells.append("-")
                continue
            tot[b][1] += 1
            tot[b][0] += bool(r["pass"])
            tot[b][2] += r["secs"] or 0
            tot[b][3] += r["tokens"] or 0
            cells.append("%s %5ss %6s tok %s" % ("PASS" if r["pass"] else "FAIL", r["secs"], r["tokens"],
                                                  "(cut)" if r["finish"] in ("length",) else ""))
        print("%-13s | %-34s | %-34s" % (t["id"], cells[0], cells[1]))
    for b in tot:
        p, n, s, k = tot[b]
        if n:
            print("%-7s passed %d/%d, %.0fs total, %d tokens, %.1f tok/s overall" % (b, p, n, s, k, k / s if s else 0))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        sys.exit(1 if selftest() else 0)
    if cmd == "run":
        run(sys.argv[2], set(sys.argv[3:]) or None)
    elif cmd == "compare":
        compare()
    else:
        sys.exit(__doc__)
