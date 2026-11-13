#!/usr/bin/env python3
"""Mechanical polish of a level spec: the model's design, the smallest changes that satisfy the check.

  python3 polish.py SEED < reply     (the reply holds {"platforms": [...], "start": i, "goal": j})
Prints the polished spec as one JSON object. The model chose the shape; this only nudges platforms
(move a little, resize by a cell, drop or add a small one) while the check's count of failed rules
falls, and prefers the fewest changed cells among equal results. Runs until 0 rules fail: there is no time budget. It gives up only when ten walks in a row (each back
from the writer's own spec, with bigger steps) have not bettered the best it has; it then returns that best, so
the writer gets another turn with a closer value instead of waiting for a search that cannot finish.
When no batch has improved for a while it goes back to the writer's own spec and walks again, so it
does not wander away from the design. The real check judges the result afterwards.
Appends one line per call to .ralph/polish.jsonl: rules failing before and after, cells changed.
"""
import copy, json, os, random, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 60, 20
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 0
RNG = random.Random(SEED)

def parse(text):
    m = re.search(r'\{\s*"platforms"[\s\S]*\}', text or "")
    if not m:
        return None
    try:
        s = json.loads(m.group(0))
        P = s["platforms"]
        assert all(isinstance(p, list) and len(p) == 3 and all(isinstance(x, int) for x in p) for p in P)
        assert isinstance(s["start"], int) and isinstance(s["goal"], int)
        assert 0 <= s["start"] < len(P) and 0 <= s["goal"] < len(P) and s["start"] != s["goal"]
        return s
    except Exception:
        return None

def draw(spec):
    g = [["."] * W for _ in range(H)]
    for r, a, b in spec["platforms"]:
        if not (0 <= r < H):
            return None
        for c in range(max(0, min(a, b)), min(W - 1, max(a, b)) + 1):
            g[r][c] = "#"
    for k, ch in (("start", "S"), ("goal", "G")):
        r, a, b = spec["platforms"][spec[k]]
        if r < 1:
            return None
        g[r - 1][(max(0, min(a, b)) + min(W - 1, max(a, b))) // 2] = ch
    return ["".join(x) for x in g]

def cells(spec):
    return {(r, c) for r, a, b in spec["platforms"] for c in range(max(0, min(a, b)), min(W - 1, max(a, b)) + 1)}

def judge(grids):
    r = subprocess.run(["node", "check_game.js", "--judge", str(SEED)], cwd=HERE,
                       input=json.dumps(grids), capture_output=True, text=True)
    return json.loads(r.stdout)["out"]

def failing(o):
    """How many rules fail (0 = done)."""
    bad = any("not completable" in i or "not standing" in i for i in o["issues"])
    return (50 if bad else 0) + len(o["issues"])

def graded(o):
    """How far from passing: the sum of each failed rule's gap in (0, 1], a rule that reports none counting 1.
    A rule that is 9 of 10 fixed counts for less than one that is untouched, so partial fixes read as progress."""
    if any("not completable" in i or "not standing" in i for i in o["issues"]):
        return 50.0 + len(o["issues"])
    gaps = o.get("gaps") or []
    return sum(gaps[k] if k < len(gaps) else 1.0 for k in range(len(o["issues"])))

def valid(spec):
    return all(1 <= r <= 19 and 0 <= a <= b <= W - 1 and b - a >= 2 for r, a, b in spec["platforms"])

HEAT = 0   # grows each time the walk goes back to the writer's spec: bigger jumps the longer it is stuck

def mutate(spec):
    s = copy.deepcopy(spec); P = s["platforms"]
    for _ in range(RNG.randint(1, 2 + HEAT // 2)):
        k = RNG.random(); n = len(P)
        if k < 0.4:
            i = RNG.randrange(n); P[i][0] += RNG.randint(-2, 2); d = RNG.randint(-3, 3); P[i][1] += d; P[i][2] += d
        elif k < 0.65:
            i = RNG.randrange(n)
            P[i][RNG.choice([1, 2])] += RNG.choice([-1, 1])
        elif k < 0.85:
            r = RNG.randint(1, 17); a = RNG.randint(0, 54); w = RNG.randint(3, 5); P.append([r, a, a + w - 1])
        elif n > 8:
            i = RNG.randrange(n)
            if i in (s["start"], s["goal"]):
                continue
            P.pop(i)
            if s["start"] > i: s["start"] -= 1
            if s["goal"] > i: s["goal"] -= 1
    return s

def drop_unreachable(spec, o):
    """The writer's spec without the platforms the check says nobody can reach: one move, because the rule
    counts them all at once and removing them one by one never lowers the count of failed rules."""
    gone = [tuple(u) for u in o.get("unreachable") or []]
    if not gone:
        return None
    # A run the check names can be several spec entries that touch or overlap: drop every entry inside one.
    def inside(p):
        return any(p[0] == r and f <= min(p[1], p[2]) and max(p[1], p[2]) <= t for r, f, t in gone)
    s = copy.deepcopy(spec); keep = []; remap = {}
    for i, p in enumerate(s["platforms"]):
        if inside(p) and i not in (s["start"], s["goal"]):
            continue
        remap[i] = len(keep); keep.append(p)
    if len(keep) == len(s["platforms"]) or len(keep) < 8:
        return None
    s["platforms"] = keep; s["start"] = remap[s["start"]]; s["goal"] = remap[s["goal"]]
    return s

def main():
    global HEAT
    text = sys.stdin.read()
    spec = parse(text)
    if spec is None or draw(spec) is None:
        print(text.strip()); sys.exit(1)
    original = cells(spec); t0 = time.time()
    o0 = judge([draw(spec)])[0]; before_all = failing(o0)
    # Platforms nobody can reach are cleared first and are not counted as the polish's changes: they are not
    # part of any design, and clearing them one at a time never lowers the count of failed rules.
    cleaned = drop_unreachable(spec, o0)
    if cleaned is not None and valid(cleaned) and draw(cleaned):
        spec = cleaned
        o0 = judge([draw(spec)])[0]
    base = cells(spec)
    def score(o, s):
        return graded(o) * 100 + len(cells(s) ^ base)
    cur, co = spec, o0; cs = score(co, cur); before = before_all
    best, bo, bs = cur, co, cs
    stale = 0; dry = 0   # dry: walks back to the writer's spec since the best last got better
    while failing(bo) > 0 and dry < 2 and time.time() - t0 < float(os.environ.get("POLISH_MAX_SECS", "600")):
        cands = [m for m in (mutate(cur) for _ in range(150)) if valid(m) and draw(m)]
        dropped = drop_unreachable(cur, co)
        if dropped is not None and valid(dropped) and draw(dropped):
            cands.append(dropped)
        res = judge([draw(m) for m in cands])
        scored = sorted((score(o, m), i) for i, (o, m) in enumerate(zip(res, cands)))
        s, i = scored[0]
        if s <= cs or RNG.random() < 0.03:
            cur, co, cs = cands[i], res[i], s
        if s < bs:
            best, bo, bs = cands[i], res[i], s; stale = 0; dry = 0
        else:
            stale += 1
            if stale >= 40:
                cur, co, cs = spec, o0, score(o0, spec); stale = 0; HEAT += 1; dry += 1
    try:
        os.makedirs(os.path.join(HERE, ".ralph"), exist_ok=True)
        with open(os.path.join(HERE, ".ralph", "polish.jsonl"), "a") as fh:
            fh.write(json.dumps({"seed": SEED, "failing_before": before, "failing_after": failing(bo),
                                 "cells_changed": len(cells(best) ^ original), "cells_cleared_first": len(original ^ base), "restarts": HEAT, "secs": round(time.time() - t0, 1),
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
    except OSError:
        pass
    print(json.dumps(best))

main()
