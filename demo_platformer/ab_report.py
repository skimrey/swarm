#!/usr/bin/env python3
"""A/B of the two Ralph runs forked at 26 kept (2026-11-05 18:39).
A = v3_tiers (26b on the P40)   B = v3_tiers_gpt (team_gpt.py, ChatGPT under 24k tokens)
Prints kept count, rounds, attempts, and for B how many calls went to ChatGPT vs fell back to the 26b."""
import json, os, re, collections
H = os.path.dirname(os.path.abspath(__file__))
def side(d):
    p = os.path.join(H, d)
    kept = len(json.load(open(os.path.join(p, ".ralph", "stored.json")))["elements"])
    log = open(os.path.join(p, "ralph.log"), errors="replace").read() if os.path.exists(os.path.join(p, "ralph.log")) else ""
    return kept, len(re.findall(r"== ralph round", log)), len(re.findall(r"part repair:|stored value repair", log)), len(re.findall(r"reply is not further", log))
for name, d in (("A 26b", "v3_tiers"), ("B chatgpt", "v3_tiers_gpt")):
    k, r, a, nf = side(d)
    print("%-10s kept %d (+%d since fork, which was 26)  rounds %d  repair asks %d  not-further %d" % (name, k, k - 26, r, a, nf))
rj = os.path.join(H, "v3_tiers_gpt", ".route", "chatgpt-route.jsonl")
if os.path.exists(rj):
    c = collections.Counter(); secs = []
    for l in open(rj):
        o = json.loads(l); c[o["route"] + (":" + o["why"].split(" (")[0].split(":")[0] if o["route"] == "standard" else "")] += 1
        if o["route"] == "chatgpt": secs.append(o["secs"])
    print("B routes:", dict(c), "mean chatgpt secs %.1f" % (sum(secs) / max(1, len(secs))))
