#!/usr/bin/env python3
"""Fire a prompt straight at gemma4:e2b, bypassing routing.

    ./ask_e2b.py is the build server up
    echo "classify this" | ./ask_e2b.py
"""

import sys

import swarm

MODEL = "gemma4:e2b"


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("usage: ask_e2b.py <prompt>", file=sys.stderr)
        return 2

    cfg = swarm.Config.load()
    state = swarm.State()
    try:
        result = swarm.run_prompt(
            cfg, state, prompt,
            force=MODEL,
            use_context=False,
            allow_escalate=False,
            refine=False,
            stream_to=lambda piece: (sys.stdout.write(piece), sys.stdout.flush()),
        )
    finally:
        state.close()

    print()
    print("[%s | %s | %.0f tok/s]"
          % (result["node"], swarm.human(result["seconds"]), result["decode_tps"]),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
