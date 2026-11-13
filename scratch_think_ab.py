"""Does reasoning earn its cost when writing code? Measure, do not argue.

For each spec an invariant oracle is generated, then validated two ways before
it is trusted: it must pass a reference implementation written here, and it
must reject a stub that returns None for everything. An oracle that fails
either check is thrown out, because a bad oracle would silently decide the
experiment.

Usage:
    python3 scratch_think_ab.py [model] [attempts]
"""
import os
import shutil
import subprocess
import sys
import time

import checks
import codegen
import intent
import swarm
import team

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemma4:12b"
ATTEMPTS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
WORK = "scratch_ab"

SPECS = [
    ("rle",
     "encode(text: str) -> str run-length encodes a string as character "
     "followed by count for every run, for example 'aaab' becomes 'a3b1'. "
     "decode(data: str) -> str reverses it exactly. Counts may exceed 9. "
     "encode('') and decode('') both return ''. Define only encode and decode.",
     '''
def encode(text):
    if not text:
        return ""
    out, prev, n = [], text[0], 1
    for ch in text[1:]:
        if ch == prev:
            n += 1
        else:
            out.append(prev + str(n)); prev, n = ch, 1
    out.append(prev + str(n))
    return "".join(out)


def decode(data):
    out, i = [], 0
    while i < len(data):
        ch = data[i]; i += 1
        j = i
        while j < len(data) and data[j].isdigit():
            j += 1
        out.append(ch * int(data[i:j])); i = j
    return "".join(out)
'''),
    ("rotated",
     "search(nums: list[int], target: int) -> int returns the index of target "
     "in a sorted list of distinct integers that has been rotated at an "
     "unknown pivot, or -1 if absent. Must run in O(log n): no linear scan, "
     "no use of list.index. Define only search.",
     '''
def search(nums, target):
    lo, hi = 0, len(nums) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if nums[mid] == target:
            return mid
        if nums[lo] <= nums[mid]:
            if nums[lo] <= target < nums[mid]:
                hi = mid - 1
            else:
                lo = mid + 1
        else:
            if nums[mid] < target <= nums[hi]:
                lo = mid + 1
            else:
                hi = mid - 1
    return -1
'''),
    ("intervals",
     "merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]] merges "
     "all overlapping or touching closed intervals and returns them sorted by "
     "start. merge([]) returns []. Intervals may arrive in any order. "
     "Touching intervals such as (1,2) and (2,3) merge into (1,3). "
     "Define only merge.",
     '''
def merge(intervals):
    if not intervals:
        return []
    ordered = sorted(intervals)
    out = [tuple(ordered[0])]
    for start, end in ordered[1:]:
        last_start, last_end = out[-1]
        if start <= last_end:
            out[-1] = (last_start, max(last_end, end))
        else:
            out.append((start, end))
    return out
'''),
]


def run(path):
    p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                       cwd=swarm.HERE, timeout=120)
    return p.returncode, (p.stdout + p.stderr).strip()


def stub_for(reference):
    """A module defining the same names, all returning None."""
    names = [l.split("(")[0][4:] for l in reference.splitlines()
             if l.startswith("def ")]
    return "\n".join("def %s(*a, **k):\n    return None\n" % n for n in names)


cfg = swarm.Config.load()
state = swarm.State()
root = swarm.HERE
os.makedirs(os.path.join(root, WORK), exist_ok=True)
open(os.path.join(root, WORK, "__init__.py"), "w").close()

# Writing the oracle with the same model that writes the code keeps g26b on a
# single slot: it is one box, and swapping between the 26b and the 12b costs a
# ~34s reload in each direction for no benefit here.
oracle_node, oracle_model = ([n for n in cfg.nodes if n.name == "g26b"][0],
                             os.environ.get("ORACLE_MODEL", "gemma4:12b"))
node = [n for n in cfg.nodes if n.name == "g26b"][0]

print("writing and validating oracles on %s/%s\n" % (oracle_node.name,
                                                     oracle_model))
usable = []
for name, spec, reference in SPECS:
    path = os.path.join(WORK, "%s.py" % name)
    component = intent.Component(name=name, path=path, floor=4, deps=[],
                                 spec=spec)
    test_path = checks.build_test(component, oracle_node, oracle_model, cfg,
                                  state, root)
    if not test_path:
        print("  %-10s no oracle produced" % name)
        continue

    with open(os.path.join(root, path), "w") as fh:
        fh.write(reference)
    good = run(os.path.join(root, test_path))[0] == 0
    with open(os.path.join(root, path), "w") as fh:
        fh.write(stub_for(reference))
    rejects = run(os.path.join(root, test_path))[0] != 0
    os.remove(os.path.join(root, path))

    if good and rejects:
        print("  %-10s oracle sound" % name)
        usable.append((name, spec, path, test_path))
    else:
        print("  %-10s oracle REJECTED (passes reference=%s, rejects stub=%s)"
              % (name, good, rejects))

if not usable:
    print("\nno usable oracles; nothing to measure")
    state.close()
    sys.exit(1)

print("\nmeasuring %s, %d attempt(s) per arm\n" % (MODEL, ATTEMPTS))
print("%-10s %-8s %-7s %-9s %s" % ("spec", "think", "result", "time", "attempts"))
rows = []
arms = ((False,) if os.environ.get("ARMS") == "off"
        else (True,) if os.environ.get("ARMS") == "on" else (False, True))
for name, spec, path, test_path in usable:
    for think in arms:
        full = os.path.join(root, path)
        if os.path.exists(full):
            os.remove(full)
        t0 = time.monotonic()
        res = codegen.generate_file(
            spec, full, node, MODEL, cfg, state,
            check="python3 " + test_path, attempts=ATTEMPTS,
            answer_tokens=1400, thinking=team.thinking_tokens(cfg, MODEL),
            think=think, root=root, verbose=False)
        elapsed = time.monotonic() - t0
        starved = sum(1 for h in res["history"] if h["stage"] == "starved")
        print("%-10s %-8s %-7s %-9s %d%s"
              % (name, "on" if think else "off",
                 "PASS" if res["ok"] else "fail", swarm.human(elapsed),
                 res["attempts"],
                 "  (starved %dx)" % starved if starved else ""))
        rows.append((name, think, res["ok"], elapsed, res["attempts"]))

print("\nsummary")
for think in (False, True):
    arm = [r for r in rows if r[1] is think]
    if not arm:
        continue
    passed = sum(1 for r in arm if r[2])
    total_s = sum(r[3] for r in arm)
    print("  reasoning %-3s %d/%d passed, %s total"
          % ("on" if think else "off", passed, len(arm),
             swarm.human(total_s)))
shutil.rmtree(os.path.join(root, WORK), ignore_errors=True)
state.close()
