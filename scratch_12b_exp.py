"""Can the 12b plus a real oracle crack edit_script faster than the 26b did?

The 26b baseline was three attempts, ~38 minutes, no passing file -- and it was
never told what its code did wrong, because parse and import were the only
gates. This attaches a real oracle and gives the cheaper model more draws.

The generated test is validated before it is trusted. An oracle that passes
known-wrong code would make the whole result meaningless.
"""
import os
import subprocess
import sys
import time

import checks
import codegen
import intent
import swarm
import team

SPEC = ("edit_script(a: str, b: str) -> list[tuple[str, str]] returns a "
        "MINIMAL character-level edit script as a list of (op, char) pairs "
        "where op is 'keep', 'insert' or 'delete', such that applying it left "
        "to right to a produces b, and the number of non-keep operations "
        "equals len(a) + len(b) - 2 * the length of the longest common "
        "subsequence of a and b. This requires a dynamic-programming LCS "
        "table with backtracking. Define only edit_script and any helpers it "
        "needs.")

PATH = "textkit/edit_script.py"

REFERENCE = '''
def edit_script(a, b):
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append(("keep", a[i])); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            out.append(("delete", a[i])); i += 1
        else:
            out.append(("insert", b[j])); j += 1
    while i < n:
        out.append(("delete", a[i])); i += 1
    while j < m:
        out.append(("insert", b[j])); j += 1
    return out
'''

WRONG = '''
def edit_script(a, b):
    """Deletes everything then inserts everything: valid, never minimal."""
    return [("delete", c) for c in a] + [("insert", c) for c in b]
'''


def write(path, source):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(source)


def run_test(test_path):
    p = subprocess.run([sys.executable, test_path], capture_output=True,
                       text=True, cwd=swarm.HERE)
    return p.returncode, (p.stdout + p.stderr).strip()


cfg = swarm.Config.load()
state = swarm.State()
root = swarm.HERE
component = intent.Component(name="edit_script", path=PATH, floor=4, deps=[],
                             spec=SPEC)

g26b = [n for n in cfg.nodes if n.name == "g26b"][0]
oracle_node, oracle_model = (
    (g26b, "gemma4:12b") if os.environ.get("ORACLE_12B")
    else team.pick_solvers(cfg, state, 1, count=4, family="gemma",
                           max_over=1)[0])
print("writing the oracle on %s/%s..." % (oracle_node.name, oracle_model))
t0 = time.monotonic()
test_path = checks.build_test(component, oracle_node, oracle_model, cfg, state,
                              root)
print("oracle written in %.0fs -> %s" % (time.monotonic() - t0, test_path))
if not test_path:
    sys.exit("no oracle produced")

print("\nvalidating the oracle before trusting it:")
write(os.path.join(root, PATH), REFERENCE)
rc, out = run_test(os.path.join(root, test_path))
print("  correct impl -> rc=%d  %s" % (rc, out.splitlines()[-1][:90]))
good = rc == 0

write(os.path.join(root, PATH), WRONG)
rc, out = run_test(os.path.join(root, test_path))
print("  wrong impl   -> rc=%d  %s" % (rc, out.splitlines()[0][:90]))
bad = rc != 0

os.remove(os.path.join(root, PATH))
if not (good and bad):
    print("\nORACLE UNUSABLE (passes wrong code or rejects correct code).")
    print("The 12b result would be meaningless against it.")
    state.close()
    sys.exit(2)
print("  oracle is sound\n")

node = [n for n in cfg.nodes if n.name == "g26b"][0]
model = ("gemma4-fixed:latest" if os.environ.get("WRITER_26B")
         else "gemma4:12b")


def resident():
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(node.url + "/api/ps", timeout=10) as fh:
            ms = json.load(fh).get("models", [])
        return ", ".join("%s(%s)" % (m["name"],
                                     m["details"].get("parameter_size"))
                         for m in ms) or "nothing"
    except Exception as exc:
        return "unknown (%s)" % exc


print("resident on %s before: %s" % (node.name, resident()))
print("generating on %s/%s, up to 6 attempts..." % (node.name, model))
started = time.monotonic()
res = codegen.generate_file(
    SPEC, os.path.join(root, PATH), node, model, cfg, state,
    check="python3 " + test_path, attempts=6, answer_tokens=1400,
    thinking=team.thinking_tokens(cfg, model), root=root, verbose=True)
elapsed = time.monotonic() - started

print("resident on %s after:  %s" % (node.name, resident()))
print("\n%s after %d attempt(s) in %s"
      % ("PASSED" if res["ok"] else "FAILED", res["attempts"],
         swarm.human(elapsed)))
print("\nmeasured decode rate on %s, newest first:" % node.name)
for r in state.db.execute(
        "SELECT model, decode_tps, prefill_tps, load_s, output_tokens, ts "
        "FROM perf WHERE node='g26b' ORDER BY ts DESC LIMIT 8"):
    print("  %-22s decode=%5.1f prefill=%6.1f load=%5.1fs out=%-5s %.0fs ago"
          % (r["model"], r["decode_tps"] or 0, r["prefill_tps"] or 0,
             r["load_s"] or 0, r["output_tokens"], time.time() - r["ts"]))
for h in res["history"]:
    print("  attempt %d: %s -- %s"
          % (h["attempt"], h["stage"], (h.get("error") or "").splitlines()[0][:80]))

if res["ok"]:
    print("\nnow against the independent reference test:")
    rc, out = run_test(os.path.join(root, "textkit_test.py"))
    print("  textkit_test.py rc=%d (expected failures for missing modules)" % rc)
state.close()
