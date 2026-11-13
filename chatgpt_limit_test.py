#!/usr/bin/env python3
"""Find how much prompt text the ChatGPT browser server can carry intact.

Each probe is N characters of random-word filler with a unique token at the
start, middle and end, plus an instruction to repeat the three tokens. A wrong
or missing token means the text was truncated or dropped somewhere, which a
plain "did it answer" test would miss.

    python3 chatgpt_limit_test.py --url http://10.0.0.178:11436
    python3 chatgpt_limit_test.py --url ... --sizes 2000 8000 32000

Doubles through --sizes until a probe fails, then bisects between the last pass
and the first fail. Results append to chatgpt_limit_results.jsonl.
"""
import argparse
import json
import random
import secrets
import time

import chatgpt_client

WORDS = ("amber basil cedar delta ember fjord grove harbor indigo juniper kelp lunar "
         "meadow nectar orchid pebble quartz ridge saffron thistle umber velvet willow "
         "yarrow zephyr anchor beacon canyon dune estuary forest glacier hollow island").split()
RESULTS = "chatgpt_limit_results.jsonl"


def build(n_chars, rng):
    toks = ["TOK-" + secrets.token_hex(4).upper() for _ in range(3)]
    instr = ("Below is a long block of filler text with three secret tokens (TOK-XXXXXXXX), "
             "one at the very start, one in the middle and one at the very end. Ignore the "
             "filler. Reply with ONLY the three tokens in order of appearance, separated by "
             "single spaces, nothing else.\n\n")
    budget = max(n_chars - len(instr) - 80, 100)
    half = budget // 2

    def filler(k):
        out, size = [], 0
        while size < k:
            w = rng.choice(WORDS)
            out.append(w)
            size += len(w) + 1
        return " ".join(out)

    body = "%s\n%s\n%s\n%s\n%s" % (toks[0], filler(half), toks[1], filler(budget - half), toks[2])
    return instr + body, toks


def probe(url, n_chars, rng):
    prompt, toks = build(n_chars, rng)
    t0 = time.time()
    try:
        reply, _ = chatgpt_client.chat(url, [{"role": "user", "content": prompt}])
        err = None
    except SystemExit as e:
        reply, err = "", str(e)
    echoed = reply.strip() == prompt.strip()  # server handed back our own paste
    found = [] if echoed else [t for t in toks if t in reply]
    ordered = reply.split() and [w.strip(".,*`") for w in reply.split()][:3] == toks
    res = {"chars": len(prompt), "ok": ordered, "found": len(found), "echoed": echoed, "secs": round(time.time() - t0, 1),
           "reply": reply[:200], "error": err, "ts": time.strftime("%F %T")}
    with open(RESULTS, "a") as f:
        f.write(json.dumps(res) + "\n")
    print("%7d chars: %s  found %d/3  %.0fs  %s" % (
        res["chars"], "PASS" if ordered else "FAIL", len(found), res["secs"],
        (err or reply[:80]).replace("\n", " ")), flush=True)
    return ordered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=chatgpt_client.DEFAULT_URL)
    ap.add_argument("--sizes", type=int, nargs="+", default=[2000, 5000, 10000, 20000, 40000, 80000, 160000])
    ap.add_argument("--resolution", type=int, default=1000, help="stop bisecting at this many chars")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    chatgpt_client.CLIENT_TIMEOUT = args.timeout
    if not chatgpt_client.server_up(args.url):
        raise SystemExit("nothing listening at %s" % args.url)
    rng = random.Random()

    last_pass, first_fail = 0, None
    for n in args.sizes:
        if probe(args.url, n, rng):
            last_pass = n
        else:
            first_fail = n
            break
    if first_fail is None:
        print("\nNo failure up to %d chars." % last_pass)
        return
    lo, hi = last_pass, first_fail
    while hi - lo > args.resolution:
        mid = (lo + hi) // 2
        if probe(args.url, mid, rng):
            lo = mid
        else:
            hi = mid
    print("\nLargest passing size ~%d chars; first failure ~%d chars." % (lo, hi))


if __name__ == "__main__":
    main()
