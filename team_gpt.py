#!/usr/bin/env python3
"""team.py with every model call routed to the ChatGPT browser server.

Same arguments as team.py:

    ./team_gpt.py do --check "python3 t.py" "write a thing"
    RALPH_TEAM=./team_gpt.py ./ralph.sh ...      # blossom loop in ChatGPT mode

Calls under the size limit go to ChatGPT; larger ones, and any ChatGPT failure,
use the normal nodes. See chatgpt_route.py. `./team.py` itself is untouched.
"""
import sys

import chatgpt_route

chatgpt_route.install()

import team  # noqa: E402  (must import after the patch so it sees the same swarm module)

if __name__ == "__main__":
    sys.exit(team.main())
