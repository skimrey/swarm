# begin

Drives a pool of local Ollama boxes as one team. A single prompt is routed to
whichever machine should handle it; a plan is fanned out across all of them in
parallel and recombined. Nothing generated is kept unless it parses, imports,
and passes a check you supply.

Standard-library Python 3.9+. No install step, no dependencies.

```bash
./swarm.py nodes                                  # is the pool alive
./team.py do "build a CSV summariser"             # say what you want
./team.py do --check "python3 check.py" "..."     # ...and how to judge it
./tools.py --dry-run "show me what changed"       # propose an action, run nothing
```

## Where to read next

| file | for |
|---|---|
| **`OPERATING.md`** | the commands, for driving this yourself from a terminal |
| **`AGENTS.md`** | why it is shaped this way — read before doing bulk work |
| `LOCAL.md` | you are one of the Gemma models on this network |
| `SETUP.md` | standing up the boxes in the first place |
| `BLOSSOM.md` | generating a dataset: how to write the check, how to run the loop |
| `ralph.md` | the loop blossom runs in |

## The parts

| file | does |
|---|---|
| `swarm.py` | node config, routing, the Ollama client, shared state |
| `team.py` | planning, parallel scheduling, the CLI |
| `intent.py` | triage, and who owns which name in a shared contract |
| `codegen.py` | writes one file and repairs it until the gates pass |
| `checks.py` | the free gates: parse, import, smoke-run |
| `crossref.py` | decides interface mismatches from the syntax tree, not opinion |
| `patterns.py` | static bug detectors that never consult a model |
| `evidence.py` | pulls real source behind a claim before anyone judges it |
| `ground.py` | reads existing code locally to ground a vague request |
| `session.py` | per-project history, so follow-up requests have context |
| `attach.py` | reference files the operator attaches instead of pasting |
| `openapi.py` | cuts a too-large API spec down to the endpoints a request needs |
| `tools.py` | network proposes a tool call; this machine validates and runs it |
| `run_tests.py` | the offline suite — `python3 run_tests.py`, ~8s, no pool needed |

## Machines

| node | model | notes |
|---|---|---|
| `mac` | — | orchestrates only; **disabled as a worker** (16GB shared with the editor) |
| `e4b-linux` | `gemma4-e4b-fixed:latest` | AMD 6600XT, needs the ROCm backend |
| `g26b` | `gemma4:26b-a4b-it-mtp-q4_K_M` | the 4070; default hard-tier writer |
| `e2b-2` | `gemma4:e2b` | old laptop GPU, slow, often asleep |

Gemma only — `team.restrict_to_family` enforces it. Qwen was evaluated as a
cross-family critic and dropped for flagging correct code as broken with
confident wrong reasoning.
