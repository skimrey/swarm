#!/usr/bin/env python3
"""Part-level ("surface") repair: unit tests, then the stored-value loop with a
stubbed model, on prose (text surface) and on the platformer (list surface).

Offline: the model is a scripted function. The platformer part runs the real
compose.js and check_game.js from demo_platformer/v3_tiers when they exist.
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import surface  # noqa: E402
import team  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(name)


def raises(fn):
    try:
        fn()
    except surface.SurfaceError as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------- list surface
LIST_CFG = {
    "kind": "json-list", "key": "platforms", "refs": ["start", "goal"],
    "fields": {"row": 0, "c0": 1, "c1": 2}, "part_name": "platform",
    "item_format": "[row, first_col, last_col]", "base": 0, "max_parts": 3,
    "locators": [{"regex": r"row (?P<row>\d+) columns? (?P<c0>\d+)(?: to (?P<c1>\d+))?"}],
}
SPEC = json.dumps({"platforms": [[10, 0, 5], [8, 10, 15], [6, 20, 25], [4, 30, 35]],
                   "start": 0, "goal": 3})
LS = surface.from_config(LIST_CFG)
plan = LS.plan(SPEC, ["CHECK FAILED: too long on row 8 columns 10 to 15"])
check("list: plan names only the located platform", plan and plan.allowed == [1], plan and plan.allowed)
out = LS.apply(SPEC, plan, "REPLACE 1 [8, 10, 14]")
after = json.loads(out)
check("list: REPLACE changes one platform", after["platforms"][1] == [8, 10, 14]
      and after["platforms"][0] == [10, 0, 5] and after["platforms"][3] == [4, 30, 35], after)
check("list: refs untouched on REPLACE", after["start"] == 0 and after["goal"] == 3)
check("list: unnamed platform refused", "not one" in (raises(lambda: LS.apply(SPEC, plan, "REPLACE 2 [6, 1, 2]")) or ""))
check("list: empty reply refused", raises(lambda: LS.apply(SPEC, plan, "no idea")) is not None)
check("list: bare JSON accepted for one part", json.loads(LS.apply(SPEC, plan, "[8, 11, 15]"))["platforms"][1] == [8, 11, 15])
check("list: no cap on the number of edits", len(json.loads(LS.apply(SPEC, plan, "\n".join(["ADD [1, 1, 2]"] * 20)))["platforms"]) == 24)
check("list: ADD appends", len(json.loads(LS.apply(SPEC, plan, "ADD [12, 5, 9]"))["platforms"]) == 5)
plan0 = LS.plan(SPEC, ["too long on row 10 columns 0 to 5"])
check("list: REMOVE of the start platform refused", raises(lambda: LS.apply(SPEC, plan0, "REMOVE 0")) is not None)
plan1 = LS.plan(SPEC, ["too long on row 8 columns 10 to 15"])
rm = json.loads(LS.apply(SPEC, plan1, "REMOVE 1"))
check("list: REMOVE keeps goal pointing at the same platform", rm["goal"] == 2 and rm["platforms"][rm["goal"]] == [4, 30, 35], rm)
check("list: a line nothing can place gives no plan", LS.plan(SPEC, ["the level is too easy"]) is None)

# ---------------------------------------------------------------- text surface
TS = surface.from_config({"kind": "text", "unit": "sentence"})
TEXT = "The cat sat. It was a very long and winding afternoon for everyone. Then it left."
tp = TS.plan(TEXT, ["CHECK FAILED: passage(1) sentence 2 has 11 words, more than 8"])
check("text: plan names sentence 2", tp and tp.allowed == [2], tp and tp.allowed)
new = TS.apply(TEXT, tp, "REPLACE 2 It was a long afternoon.")
check("text: only the named sentence changes",
      new == "The cat sat. It was a long afternoon. Then it left.", new)
check("text: other sentence refused", raises(lambda: TS.apply(TEXT, tp, "REPLACE 1 Nope.")) is not None)
check("text: chat without an edit line is refused, never pasted in",
      raises(lambda: TS.apply(TEXT, tp, "I think it should be shorter.")) is not None)
check("text: ADD appends a sentence", TS.apply(TEXT, tp, "ADD The end.").endswith("Then it left. The end."))
check("text: REMOVE drops it", TS.apply(TEXT, tp, "REMOVE 2") == "The cat sat. Then it left.")
qp = TS.plan(TEXT, ['CHECK FAILED: the word "winding" is banned'])
check("text: a quoted word finds its sentence", qp and qp.allowed == [2], qp and qp.allowed)
PARA = surface.from_config({"kind": "text", "unit": "paragraph"})
doc = "One.\n\nTwo is bad.\n\nThree."
pp = PARA.plan(doc, ["paragraph 2 is too short"])
check("text: paragraph unit", pp and PARA.apply(doc, pp, "REPLACE 2 Two is fine now.") == "One.\n\nTwo is fine now.\n\nThree.")


# a rule about the whole value: any part may change, by a few edits, the rest is kept
gp = LS.plan_global(SPEC, ["CHECK FAILED: the level has an obvious path"])
check("global: every platform may be edited", gp and gp.allowed == [0, 1, 2, 3], gp and gp.allowed)
g_out = json.loads(LS.apply(SPEC, gp, "ADD [9, 20, 24]\nREPLACE 2 [6, 18, 25]"))
check("global: only the mentioned platforms change", g_out["platforms"][:2] == [[10, 0, 5], [8, 10, 15]]
      and g_out["platforms"][3] == [4, 30, 35] and g_out["platforms"][2] == [6, 18, 25] and len(g_out["platforms"]) == 5, g_out)
check("global: no cap on the number of edits", len(json.loads(LS.apply(SPEC, gp, "\n".join(["ADD [1, 1, 3]"] * 30)))["platforms"]) == 34)
check("global: the goal platform cannot be removed", raises(lambda: LS.apply(SPEC, gp, "REMOVE 3")) is not None)
tg = TS.plan_global(TEXT, ["CHECK FAILED: the tone is too formal"])
check("global: text, every sentence editable", tg and tg.allowed == [1, 2, 3], tg and tg.allowed)
check("global: text edit changes one sentence only",
      TS.apply(TEXT, tg, "REPLACE 3 Then it went home.") == "The cat sat. It was a very long and winding afternoon for everyone. Then it went home.")

# ------------------------------------------------------------ loop helpers
check("further: reaching more of the cells counts",
      team._further(["CHECK FAILED: not completable. The route reaches 8 of 141 cells."],
                    ["CHECK FAILED: not completable. The route reaches 60 of 141 cells."], "not completable"))
check("further: reaching fewer does not",
      not team._further(["CHECK FAILED: not completable. The route reaches 60 of 141 cells."],
                        ["CHECK FAILED: not completable. The route reaches 8 of 141 cells."], "not completable"))
check("further: a non-reach 'X of Y' is not read as progress",
      not team._further(["CHECK FAILED: share. 3 of 10 are bad."], ["CHECK FAILED: share. 9 of 10 are bad."], "share"))
# gate order: with gates.json, a completable level that fails design rules is FURTHER than one that is not completable,
# although the design rules are written higher in the check's text
gd = tempfile.mkdtemp()
with open(os.path.join(gd, "gates.json"), "w") as fh:
    json.dump({"gates": ["is not completable"]}, fh)
fake_check = "design: has an obvious path ... then later: generateLevel is not completable. A jump"
team._load_gates(gd)
old_l = ["CHECK FAILED: generateLevel(24) is not completable. A jump in this tier rises at most 3 rows"]
new_l = ["CHECK FAILED: generateLevel(24) has an obvious path: always jumping to the reachable platform nearest"]
check("gates: completable-but-boring is further than not completable (with gates.json)", team._further(old_l, new_l, fake_check))
check("gates: not completable is not further than boring", not team._further(new_l, old_l, fake_check))
team._GATE_PATTERNS.clear()
check("gates: without gates.json the text order rules (old behaviour)", not team._further(old_l, new_l, fake_check))
shutil.rmtree(gd, ignore_errors=True)
# a draft reloaded from disk is spaced differently from the value that was drawn; its spec must still be found
drawn = '["..#","...#"]'
reloaded = '[ "..#", "...#" ]'
check("specs: a reloaded draft finds its spec despite different spacing",
      team._spec_for({drawn: "SPEC"}, reloaded) == "SPEC" and team._spec_for({drawn: "SPEC"}, "[]") == "")
# no remembered spec at all: the compose command reads one back out of the drawn value
if os.path.isfile(os.path.join(HERE, "demo_platformer", "v3_tiers", "compose.js")):
    vt = os.path.join(HERE, "demo_platformer", "v3_tiers")
    fxd = json.load(open(os.path.join(HERE, "fixtures_surface", "stored.json")))["draft"]
    got = team._spec_for({}, fxd, vt, "node compose.js")
    rendered = team._compose_render(vt, "node compose.js", got)[0] if got else ""
    check("specs: a missing spec is rebuilt from the drawn value and draws the same level",
          bool(got) and json.loads(rendered) == json.loads(fxd))
d = tempfile.mkdtemp()
specs = {"a": "1", "b": "2", "c": "3"}
for i in range(30):
    specs["k%d" % i] = "x"
team._save_specs(d, specs, keep=["a"])
check("specs: the held draft survives eviction", "a" in specs and len(specs) <= 25, list(specs)[:3])
shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------- stored loop with a stub model
def run_loop(root, filename, command, request, count, replies):
    asks = []
    it = iter(replies)

    def ex(cfg, state, prompt, *a, **k):
        asks.append(prompt)
        try:
            return next(it), ""
        except StopIteration:
            return "", ""
    team._floor4_exchange = ex
    team._floor4_reply = lambda *a, **k: ""
    err, old = io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        result = team._store_outputs(root, command, request, filename, count, None, None)
    finally:
        sys.stderr = old
    return result, asks, err.getvalue()


# prose: three gates in the check's order, a held draft with two defects
PROSE_CHECK = '''import sys
from story import passage
bad = []
for i in range(3):
    text = passage(i)
    if not text:
        bad.append("CHECK FAILED: passage(%d) is empty" % i)
    sentences = [s for s in text.replace("!", ".").split(".") if s.strip()]
    for n, s in enumerate(sentences, 1):
        if len(s.split()) > 8:
            bad.append("CHECK FAILED: passage(%d) sentence %d has %d words, more than 8" % (i, n, len(s.split())))
    if "very" in text:
        bad.append("CHECK FAILED: passage(%d) contains the banned word \\"very\\"" % i)
print("\\n".join(bad) if bad else "CHECK OK")
sys.exit(1 if bad else 0)
'''
TEMPLATE = "STORED = [\n];\n\ndef passage(i):\n    return STORED[i] if 0 <= i < len(STORED) else \"\"\n"


def prose_project(draft_text):
    root = tempfile.mkdtemp(prefix="surf-prose-")
    with open(os.path.join(root, "check.py"), "w") as fh:
        fh.write(PROSE_CHECK)
    with open(os.path.join(root, "surface.json"), "w") as fh:
        json.dump({"kind": "text", "unit": "sentence"}, fh)
    good = json.dumps("Good one. Short too. Fine.")
    good2 = json.dumps("Another good one. Also short. Fine too.")
    os.makedirs(os.path.join(root, ".ralph"))
    state = {"file": "story.py", "count": 3, "index": 0, "template": TEMPLATE,
             "elements": [good, good2], "draft": json.dumps(draft_text),
             "element": "a string; count 1.",
             "briefs": {"0": "a", "1": "b", "2": "c"}}
    with open(os.path.join(root, ".ralph", "stored.json"), "w") as fh:
        json.dump(state, fh)
    with open(os.path.join(root, "story.py"), "w") as fh:
        fh.write(team._splice_stored(TEMPLATE, [good, good2, json.dumps(draft_text)]))
    return root


DRAFT = "Short one. This sentence is far too long to pass the limit here. A very tiny end."
root = prose_project(DRAFT)
result, asks, log = run_loop(root, "story.py", "python3 check.py", "Store three passages in story.py.", 3, [
    "REPLACE 1 This is not named.",                       # sentence 1 is not the failing one
    "I think the second sentence should be shorter.",     # no edit and the plan names one sentence: taken as a replacement? (see below)
    "REPLACE 2 This one is short now.",
    "REPLACE 3 A tiny end.",
])
check("prose: the first ask is a part ask naming sentence 2",
      "sentence 2" in asks[0] and "REPLACE <n>" in asks[0], asks[0][-600:])
check("prose: the ask shows the whole passage for context", DRAFT.split(".")[0] in asks[0])
check("prose: refused edit leaves the draft", "part edit not applied" in log and "not one the check line names" in log, log[-1500:])
with open(os.path.join(root, "story.py")) as fh:
    final = fh.read()
check("prose: loop finished with both passages passing", result == team.STORED_DONE, result)
check("prose: sentence 1 of the held draft stays byte for byte", "Short one." in final, final)
check("prose: the final text holds the edits", "This one is short now." in final and "A tiny end." in final, final)
check("prose: the long sentence and the banned word are gone", "far too long" not in final and "very" not in final, final)
shutil.rmtree(root, ignore_errors=True)

# a reply that is a Cancel-like empty string, and an exhausted script, change nothing
root = prose_project(DRAFT)
before = json.load(open(os.path.join(root, ".ralph", "stored.json")))
result, asks, log = run_loop(root, "story.py", "python3 check.py", "Store three passages in story.py.", 3, ["", "", ""])
after_state = json.load(open(os.path.join(root, ".ralph", "stored.json")))
check("prose: empty replies never change the held draft or the kept values",
      after_state["draft"] == before["draft"] and after_state["elements"] == before["elements"])
check("prose: empty replies are said on screen", "part edit not applied" in log and "the held value stays" in log, log[-600:])
shutil.rmtree(root, ignore_errors=True)

# a bad edit is remembered in the next ask
root = prose_project(DRAFT)
result, asks, log = run_loop(root, "story.py", "python3 check.py", "Store three passages in story.py.", 3, [
    "REPLACE 2 This sentence is still far too long to pass the limit here ok.",
    "REPLACE 2 Short now.",
])
check("prose: a rejected edit is shown back in the next ask",
      len(asks) >= 2 and "tried and not kept" in asks[1] and "still far too long" in asks[1], asks[1][-900:] if len(asks) > 1 else "")
shutil.rmtree(root, ignore_errors=True)

# a reply that went through a markdown page: escaped brackets, several edits on a line
import surface as _sf
check("markdown-escaped ADD pair is two edits", _sf._parse_edits(r"ADD \[8, 24, 27\] ADD \[5, 30, 33\]") == [("ADD", None, "[8, 24, 27]"), ("ADD", None, "[5, 30, 33]")])
check("two REPLACEs on one line are two edits", _sf._parse_edits(r"REPLACE 11 \[1, 20, 23\] REPLACE 23 \[1, 25, 28\]") == [("REPLACE", 11, "[1, 20, 23]"), ("REPLACE", 23, "[1, 25, 28]")])
check("prose saying ADD is not split", _sf._parse_edits("REPLACE 2 We should ADD more here.") == [("REPLACE", 2, "We should ADD more here.")])

check("zero-padded numbers in an edit are read as numbers", _sf._unpad("[5, 04, 10]") == "[5, 4, 10]" and _sf._unpad("[1, 100, 0.5]") == "[1, 100, 0.5]")

# a text project with its own analyzer: no compose step, the report still reaches the ask
root = prose_project(DRAFT)
with open(os.path.join(root, "words.py"), "w") as fh:
    fh.write("import sys\nfor i, s in enumerate(sys.stdin.read().split('.'), 1):\n    if s.strip(): print('sentence %d has %d words' % (i, len(s.split())))\n")
with open(os.path.join(root, "analyze.json"), "w") as fh:
    json.dump({"command": "python3 words.py"}, fh)
result, asks, log = run_loop(root, "story.py", "python3 check.py", "Store three passages in story.py.", 3, ["REPLACE 2 Short now."])
check("prose: analyze.json output reaches the ask (no compose)", asks and "sentence 2 has" in asks[0], asks[0][-700:] if asks else "")
shutil.rmtree(root, ignore_errors=True)
# no analyzer: nothing added, nothing breaks
root = prose_project(DRAFT)
check("no analyzer gives an empty report", team._compose_analysis(root, json.dumps(DRAFT), 2) == "")
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------- platformer
SRC = os.path.join(HERE, "demo_platformer", "v3_tiers")
if os.path.isfile(os.path.join(SRC, "compose.js")) and os.path.isfile(os.path.join(HERE, "fixtures_surface", "stored.json")):
    root = tempfile.mkdtemp(prefix="surf-plat-")
    shutil.rmtree(root)
    shutil.copytree(SRC, root, ignore=shutil.ignore_patterns(".ralph", ".ralph.bak*", "ralph*.log", "*hedge*"))
    os.makedirs(os.path.join(root, ".ralph"))
    for name in ("stored.json", "specs.json"):
        shutil.copy(os.path.join(HERE, "fixtures_surface", name), os.path.join(root, ".ralph", name))
    fx = json.load(open(os.path.join(HERE, "fixtures_surface", "stored.json")))
    with open(os.path.join(root, "level.js"), "w") as fh:
        fh.write(team._splice_stored(fx["template"], list(fx["elements"]) + [fx["draft"]]))
    for name in ("briefs-basis.txt", "axes.json"):
        if os.path.isfile(os.path.join(SRC, ".ralph", name)):
            shutil.copy(os.path.join(SRC, ".ralph", name), os.path.join(root, ".ralph", name))
    req = "Store the levels generateLevel(seed) returns, for every seed the check loops over, in level.js."
    result, asks, log = run_loop(root, "level.js", "node check_game.js", req, 40, [
        "REPLACE 5 [5, 1, 3]",                # not a named platform
        "here is a whole new level",          # no edit
        "REPLACE 0 [2, 5, 12]\nREMOVE 99",    # nonsense index
        "",
    ])
    check("platformer: part ask used", asks and "REPLACE <n>" in asks[0] and "Every other platform stays" in asks[0])
    check("platformer: the ask carries the route report", ("The goal cannot be reached. Platforms the start can reach" in asks[0]) or ("How the level plays now" in asks[0]), asks[0][-1500:])
    check("platformer: refused edits are said", log.count("part edit not applied") >= 3, log[-800:])
    before = json.load(open(os.path.join(root, ".ralph", "stored.json")))["draft"]
    check("platformer: refused edits leave the draft", before == json.load(open(os.path.join(HERE, "fixtures_surface", "stored.json")))["draft"])
    shutil.rmtree(root, ignore_errors=True)
else:
    print("SKIP platformer (v3_tiers not present)")

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
