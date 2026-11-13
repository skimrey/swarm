# Editing this repo

`AGENTS.md` and `LOCAL.md` tell a model how to *use* `team.py`/`swarm.py`/
`openapi.py` as a black box. This file is the other direction: you are about
to open that box and change what is inside it. Read `AGENTS.md` first anyway
-- it is not background reading, it is the design history, and most of the
mistakes it documents are ones a well-meaning edit will reintroduce if you
have not read why the current code avoids them.

## What actually lives here

A four-machine local Ollama pool, driven as one tool from a single operator
machine (`mac`, disabled as a worker, orchestrates only).

| file | owns |
|---|---|
| `swarm.py` | Node config, `chat_complete`, cross-process node claiming/locking, context-size planning, model routing by capability floor. |
| `team.py` | The orchestrator CLI (`do`, `split`, `run`, `audit`, `crosscheck`, `calibrate`, `code`). Scheduling, the repair loop's outer shell, replanning, session continuity wiring. |
| `intent.py` | Parses the planner's CONTRACT/COMPONENT text, ownership rules, entry-point detection, dependency-graph fixups (`critical_depth`, cycle breaking, hoisting shared functions). |
| `codegen.py` | `generate_file`: the actual per-file repair loop -- prompt construction, gates in order, backup/restore, retry budget, restart-from-scratch escalation. |
| `crossref.py` | Mechanical, AST-based cross-file checks: does an import resolve, does a called method exist, is an attribute ever assigned. No model involved. |
| `patterns.py` | Mechanical, single-file AST checks: mutate-while-iterate, mutable default args, `is` against a literal. |
| `openapi.py` | Registers/slices/renders OpenAPI specs: `$ref` resolution, id-closure (`close_over_ids`, `close_content_pushes`, `close_pointer_updates`), term-weighted endpoint scoring, model-assisted `propose()`, example-call generation. |
| `mockapi.py` | A stateless mock server synthesized from a registered spec, used so `gate_smoke` has something real to talk to. |
| `usage.py` | Reads a generated file's own syntax tree to print what env vars/flags it needs, without asking a model. |
| `session.py` | Per-project append-only turn history + attachment/entry-point tracking, keyed by realpath so continuity works across a `do`/`do`/`do` sequence in one directory. |
| `ground.py` | Local-model-assisted relevance grounding for vague requests (reads real file bodies on `mac`'s own e4b, cheap because it's free capacity). |
| `attach.py` | `--attach` file handling: budget-checked, refused for credential-shaped filenames, framed as reference not work. |
| `tools.py` | The action layer (`propose` + `execute`): a registry of narrow, typed tools a model can request by name; no shell tool exists, so nothing catastrophic is representable. |
| `checks.py` | Shared plumbing for `--check`/`--tests`: running a command, join-component detection. |
| `killswarm.py` | The kill button: finds every `team.py`/`swarm.py`/`openapi.py` process and everything spawned under it (nohup'd scripts, gate subprocesses) by walking the real process tree, and stops them. Installed on `PATH` as `killswarm`, same as `team`/`swarm`/`openapi`. |
| `config.json` | Node list, per-node/per-model `max_ctx`, `enabled`, biases, `env_names`, `judge_model`. |
| `.env` | Real API credentials for whatever `--api` service is configured (gitignored). Names only, never values, get baked into generated prompts -- see "Secrets" below. |

Every other `*_test.py`/`scratch_*.py` at the root is a test. Nothing else
should be. Generated packages from real `do` runs land in their own
subdirectories (or wherever the operator `cd`'d to), never at the root.

## The one rule everything else follows from

**Whatever can be decided mechanically must be decided mechanically, never
by asking a model.** A parse check, an AST walk, a schema `$ref` graph, a
regex over an operation's path -- all of these have a right answer, and a
model asked the same question can be textually confident and simply wrong
(see `AGENTS.md`'s `KeyError`/`ZeroDivisionError`/dict-mutation examples).
Every mechanical checker in this repo (`crossref.py`, `patterns.py`, the
`gate_*` functions in `codegen.py`, `openapi.py`'s closures) exists because
a model was tried there first and produced a wrong, confident answer that
looked identical to a right one from the outside.

The corollary: when you add a new check, ask first whether it is decidable
from the syntax tree / the document / the response. If yes, it belongs next
to `crossref.py`/`patterns.py`/`gate_*`, not behind a model call. If a model
is genuinely required (judgement, not fact-lookup -- "does this migration
drop rows", "which endpoint does this vague request mean"), that is what
`crosscheck`/`openapi.propose`/`ground.py` are for, and those are already
designed around the fact that a model's answer there is the *best available*
evidence, not ground truth -- verify what a model returns against the real
source before trusting it (see how `propose()`'s suggestions are checked
against the real operation table, never used unverified).

## Every change gets validated by sabotage, not by writing a happy-path test

The pattern used everywhere in this repo, and the one to follow for a new
change: write a test that (a) proves the fix handles the real, specific
input that motivated it, and (b) proves the *old* code would have failed
that same input -- either by reverting the change (`git stash`) and
rerunning, or by constructing the pre-fix behavior inline in the test and
asserting it differs from the new behavior. A test that only exercises the
happy path can pass against a no-op change; the sabotage half is what makes
it a test of the fix rather than a test that the file imports.

Existing examples to copy the shape of: `scratch_duplicate_component_name.py`
(RecursionError fix -- constructs the exact duplicate-name/cycle shape that
crashed, asserts it no longer does, and separately asserts the *unpatched*
logic would have), `scratch_html_placeholder.py`, `scratch_replan.py`,
`openapi_check.py`, `sandbox_check.py`. Nearly every "Measured live" /
"Validated by sabotage" paragraph in `AGENTS.md` is describing exactly one
of these tests; read the paragraph to understand what the test needs to
prove before you look at the test itself.

## Adding or changing a test

1. Name it `scratch_<thing>.py` (or `<thing>_test.py` for the handful of
   larger suites like `openapi_check.py`, `usage_check.py`, `sandbox_check.py`,
   `tools_check.py`). Both patterns exist; match whichever the file you're
   touching already uses.
2. It must be **offline** -- no real node contact, no real network. Stub
   `swarm.chat_complete`/`node_claim` if the code under test would otherwise
   call out. The whole suite (`run_tests.py`) has to stay safe to run with
   every box asleep.
3. Add it to `run_tests.py`'s `TESTS` list, ideally near tests of related
   code. **Never run `for t in *_test.py`** or a bare glob -- generated
   packages land at the repo root during manual testing and some of them
   open a pygame window or dial the LAN; the explicit list plus
   subprocess-with-timeout is what makes the suite hang-proof.
4. Run `python3 run_tests.py` (or `python3 run_tests.py <substring>` to
   scope it) before considering a change done. It's ~23 tests in ~8 seconds;
   there is no excuse to skip it.

`run_tests.py` covers the machinery -- scheduler, contract parsing, gates,
continuity, local tool execution. It does **not** cover a live end-to-end
`team do` run against real nodes; that has only ever been validated by a
human reading a live run's output. If you change something in the actual
generation/repair loop, run a real `do` against the pool at least once and
read the transcript, not just the exit code.

## Documentation convention: write the change up the way `AGENTS.md` does

Every substantive fix in `AGENTS.md` follows the same shape: what broke (a
quoted, real symptom -- an actual traceback line, an actual generated file's
actual wrong content), why the previous code couldn't see it, the fix, and
how it was validated (usually "Validated by sabotage in `X.py`"). This is
not decoration. It is what lets a future editor tell a real, measured
constraint apart from a plausible-sounding guess -- a rule stated without a
measurement behind it in this file is a rule that hasn't been checked yet.

When you land a change worth remembering (i.e., anything that wasn't purely
mechanical or purely local), add a paragraph to `AGENTS.md` in the same
voice: concrete symptom, root cause, fix, validation. Do not add a paragraph
for something you couldn't measure -- "this should probably help with X" is
exactly the sentence this convention exists to avoid.

## Things that are load-bearing and easy to break by accident

- **`num_ctx` changes reload the model (30+ seconds).** Any code path that
  calls `chat_complete` in a loop must pin one context size for the whole
  loop (`plan_context`/`pick_num_ctx`), never recompute it per call.
- **Backups are never deleted, including on success.** `codegen.py`'s
  whole-file-replace model means a successful rewrite can silently drop
  something the old file did that nothing else imports; the only recovery
  path is the backup in `.swarm/backups/`. Don't "clean up" old backups.
- **The sandbox guard (`sitecustomize.py`, loopback-only network) runs for
  every `gate_import`/`gate_smoke` subprocess.** If you add a new gate that
  spawns a subprocess to run generated code, it must go through the same
  environment setup (`run_command` in `codegen.py`), not a bare
  `subprocess.run`, or generated code gets a live network again.
- **A detector that fires on correct code is worse than no detector.** Every
  mechanical check in `crossref.py`/`patterns.py` is deliberately
  conservative (skips classes with `__getattr__`, skips unresolvable base
  classes, requires two-word overlap instead of one in `openapi.py`'s field
  matching) for this reason. If your new check can't prove a case is wrong,
  it must say nothing rather than guess.
- **`--audit`'s model-review half is opt-in, not a thoroughness dial.** Don't
  wire anything to call it by default; it triples wall clock and its
  findings have measured out wrong more than once. The free half (parse +
  AST detectors) always runs; that's the one to extend.
- **The judge model (`team.judge_model`) is fixed independently of the
  default writer.** Don't let a change to the default writer model silently
  become the judge too -- they're different jobs with different failure
  costs.

## Secrets

`.env` is read for **variable names only** -- the loader that feeds prompt
context strips everything right of `=` before it touches anything else.
Never add a code path that puts a `.env` *value* into a prompt, a log, or a
generated file. Credential-shaped filenames (`.env`, `*.pem`, `*.key`,
`id_rsa`, etc.) are refused by `attach.py` on filename alone, not content --
keep it that way; sniffing content for "looks like a secret" both under- and
over-fires.

## Before you call a change done

1. `python3 run_tests.py` passes.
2. If you touched `openapi.py`: `python3 openapi_check.py` passes.
3. If you touched the sandbox/gate machinery: `python3 sandbox_check.py`
   passes.
4. New behavior has a sabotage-validated test, not just a happy-path one.
5. If it's worth remembering, it's written into `AGENTS.md` in the
   measured-symptom-fix-validation shape above.
6. If it changes anything a model reading `LOCAL.md` relies on (an example
   `team do` invocation, a claim about what `--api maven` does), update
   `LOCAL.md` too -- it is prescriptive instructions for a model driving
   this tool, not just for a human, and it goes stale the same way code
   comments do.
