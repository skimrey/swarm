# Working with the swarm

You are the operator, not the tool. `swarm.py`/`team.py`/`openapi.py` already exist and run. "Create a project, upload units, build an applet" means: run a shell command, like `ls` means run `ls`, not reimplement it. Don't open `team.py`/`swarm.py`/`openapi.py` and duplicate what a command already does — drive it via `./team.py do "<request>"`, read the output, act on that.

This project drives four local Ollama boxes as one team.

If you're a Gemma model on this network, read `LOCAL.md` instead (cheap tokens, different rules). Read it too, even as a frontier model, before writing a multi-step `steps.json`/`orchestrate.sh` chain.

## Purpose

Three LAN machines run different-sized Gemma models. `swarm.py` routes one prompt by difficulty; `team.py` runs many at once and recombines. Spend your context on judgement; let local models handle volume. Delegation still costs your own tokens to specify and verify (~1.4x direct-write cost) — the pool wins on breadth, not single errands.

## When to delegate

Hand off wide work: same operation over many files, many independent summaries, first-pass review.
Keep deep/short work yourself: a tricky function, a design call, anything where wrong is expensive and the answer is short.

Rule of thumb: if the check needed to trust the output is bigger than the code, write it yourself. If one check covers several parallel files, delegate.

## Start here

```bash
./team.py do --check "python3 your_test.py" "whatever you want"
```

Blossom is the data-generation framework: a high-level objective and guidelines in, a large set of varied outputs out, each following the theme and sortable along axes of quality. A dataset — one function, many inputs, and a collection whose members and set-level properties are the work — is its shape. The check is the spec, sizes are floors, and the loop is `ralph.sh`. Read `BLOSSOM.md` before writing that check or starting the run. `ralph.md` is the loop itself.

Always write a `--check`, unless writing one costs more than the code itself. Without one, "N/N gates passed" only proves it runs, not that it's right.

`--audit` spends pool time for opinions (sometimes wrong). `--check` spends your tokens once for ground truth. Default to `--check`.

A small model triages `code`/`ask`/`audit`; override with `--kind`.

`ask` needs a human watching stdout. If `sys.stdout.isatty()` is false (any orchestrated call) or `--api` is set, an auto-triaged `ask` becomes `code` — printed when it happens. An explicit `--kind ask` is never overridden.

For `code`: a planner splits work into parallel-writable files per model tier, feeds each writer its dependencies' real generated source (not a description). Nothing is kept unless it parses, imports, and passes `--check`.

## Commands

Stdlib Python 3.9+, no install step.

```bash
./swarm.py "some question"          # one prompt, routed by difficulty
./swarm.py nodes                    # health/speed of each box
./team.py warm                      # preload models
./team.py calibrate                 # measure predicted vs actual timing
./team.py split "Summarise this." src/*.py --reduce --out plan.json
./team.py run plan.json --dry-run   # Gantt chart + predicted makespan
./team.py run plan.json
./team.py audit src/ --json         # find bugs, every claim checked against real source
./team.py crosscheck "does this migration plan drop rows?"
```

`crosscheck`: both hard-tier models live on one box, so above floor 2 it drops the floor and says so. A weaker model disagreeing is worth checking; agreeing settles nothing. For anything executable, running it beats any number of agreeing models. Use `crosscheck` only where there's no oracle; use `--check` everywhere else.

## `--audit` is opt-in

Don't pass it unless asked for a review — it triples wall clock and has produced false findings. `do` always runs the free half (parse + AST detectors, `patterns.py`); `--audit` adds the model review.

`audit --audit` steps: parse check → structured claims (file/line/symbol, prose rejected) → mechanical refutation of claims citing nonexistent file/line/symbol → survivors get real source + every named symbol's real definition anywhere in the tree → adjudicated by `team.judge_model` → re-checked by arguing the opposite (a false confirm is the expensive error).

Model review has missed real bugs (dict mutated during iteration) and confirmed false ones with confident wrong reasoning (claimed `KeyError` where Python raises none). Verdicts are unstable run-to-run. A claim predicting a named exception: run it, don't debate it.

## `codegen.gate_interfaces`

Runs `crossref.scan` inside `generate_file`'s own gate chain, right after writing to disk, against only already-built dependencies — catches a dependent calling a not-yet-existing method on its own dependency on attempt one instead of many. Doesn't replace `interface_gate` (the whole-package pass). Doesn't fix a CONTRACT that never named the method — `DECOMPOSE_PROMPT` requires the CONTRACT list every cross-file method any component needs.

`crossref.Finding.text()` joins message+detail onto one line — `why_it_failed` used to read only the detail line, dropping the actual finding.

A CONTRACT clause naming a shared function's signature must not sit under a class's `with` clause (implies a bound method when it's a plain function) — checked mechanically in `sandbox_check.py`.

`check_attributes` only flags `ast.Load`, never `ast.Store`/`ast.Del` — `client.token = value` is a legal assignment, not a call.

## `crossref.py`: do the parts fit

When several models write one program, the seams break, not the logic inside one file. All measured defects were tree-decidable: importing a class from a module with none, a module ending `Enemy = EnemyWave(...)` (an instance, not the class), a method call that doesn't exist, an invented API (`surface.draw_circle`). `interface_gate` finds these, rewrites only the flagged files, re-checks, two rounds, then `--check` runs. The finding IS the repair instruction — read straight off the cited file, so it can't be wrong.

Also catches: reading `self.attr` the class never assigns. Never guesses on a class inheriting outside the project or using `__getattr__`/`setattr` — unknowable surface, left alone (a false positive is worse than none).

## Recall is set by the reviewer, not the judge

`patterns.py` decides everything tree-decidable before any model runs, entered as CONFIRMED directly: container mutated during iteration, mutable default args, `is` against a literal. Model review misses real bugs and confirms false ones. Test executable claims by running them.

## The check is the writer's guideline

On a blossom run (and on any `--check` that guards a stored set), every writer ask carries the whole check script, between `=== CHECK ===` and `=== END CHECK ===`, ahead of the failing lines. Failing lines say what broke; the check says what the rules are. A writer told only the failing line learns each rule by tripping it, never learns one it doesn't trip, and exploits any hole (measured: a route check that let a jump cross solid cells passed 40 levels of pure `#`).

What you do as operator:

- Name the check script as a token of `--check`, relative to the project directory (`--check "node check.js"`). Only tokens that are files are read. `python3 -m pkg.test`, `bash -c`, or rules held in an unnamed `require`d file reach the writer as nothing.
- Write the rules in the script as plain-language comments, and wrap the rules for one value in `WRITER-CONTEXT-BEGIN` / `WRITER-CONTEXT-END` comment lines so the writer is not shown a simulation, a page test, or diagnostics code. No markers means the whole script is shown, cut at 24000 characters from the end.
- Make every failing line say the input, what was measured, and what to change.
- A new gate and its guideline go in together: the rule appears in the writer's context (marked region, or `--describe` in compose mode) in the same edit that adds the check line, with the same numbers.
- Before the run, feed the check the laziest degenerate value (all-solid, empty, a copy). If it passes, fix the check first.
- A check edit applies on the next ask. Never edit a printed measurement into a different fact mid-run unless you mean to invalidate kept values; they are saved to `.ralph/removed.jsonl`, not lost.

Details and the measurements behind this: `BLOSSOM.md`, "Check context".

## Context reaches the pool

`context/` holds tagged cards; `team.project_context` attaches relevant ones. Off-topic request → nothing attached (a card matching everything is worse than none).

A relevant card must arrive whole — a long card degrading to a one-line summary is the worst failure (looks attached, isn't). Budget: 2400 tokens, sized against the largest real card.

## Planner truncation

Writers grow their answer cap on `done_reason: length`; the planner does too (1600→6400) — a short plan silently produces components for only part of the request, with no error anywhere. A CONTRACT naming a file no COMPONENT writes is flagged immediately, by basename.

**Bare filenames**: `intent.unqualified_sibling_asset_refs` flags a bare filename (no `../step/` qualifier) with a produced-artifact extension — it resolves *successfully* against the wrong directory forever, unlike a not-yet-run sibling. Advisory only.

**Ids only printed, never saved**: each chain step is its own process — stdout isn't a file. Three layers: (1) say the exact key name in both requests (see `LOCAL.md`), (2) `context/policy-output-json.md`, an always-on card, (3) `team lint-chain steps.json` (`intent.autofix_chain_output_json`) fixes what's provable, exits 1 on what it can't. Rewrites only the `request` field.

**Required CLI args on the entry point**: `codegen.gate_required_cli_args` fails a file with a required positional/flag arg, but only for the plan's own entry point (`intent.plan_entry_point`) in a real `--api` chain run — never a dependency or a genuine standalone CLI tool.

**Single-component plans have an obvious entry point**: `plan_entry_point` now compares paths, not names, and returns a lone component's own path outright — fixed a bug where `--retry --run` silently did nothing on the most common real case.

**Credential leakage in checks**: `usage_check.py`'s placeholder assertions run inside `patch.dict(os.environ, {}, clear=True)` so an operator's real exported `API_KEY` doesn't shadow the test.

## A contract must name an owner

CONTRACT broadcasts shared types to every writer; unqualified, a complete class signature reads as "build this" (measured: a constants file handed a full class signature "for reference" wrote the whole app around it). Each component is told which names are its own vs. a neighbour's. A wrongly-owned symbol is forbidden everywhere else — a false claim costs more than a missing one. Members after `with` are the class's own methods only; a type inside an annotation is owned by nobody.

A stale CONTRACT clause is still followed even when the rest of the plan moved on — `intent.reconcile_contract_assets` rewrites a `.py` clause into a note when a same-stem non-`.py` sibling was actually built, before any writer's prompt is built.

## Scheduling: the tail matters most

The last component is usually the join — its duration is the run's duration. Idle workers defer to faster idle ones; the fastest never defers (avoids deadlock). Deferring only ever targets a worker that can actually take the component (a floor-4-only worker deferring to a floor-2-only worker deadlocked both once). `model_bias` scales the compared rate for node-specific reasons (win a claim race; discourage grabbing floor-2 work while floor-4 work may still arrive) — but at the true tail (no floor-4 work left), the fallback ignores bias so the fastest real box wins.

A contract giving `main()` to two files (an ambiguous second-entry-point mistake) is detected mechanically and warned on.

## `max_ctx` is nearly free to raise

`pick_num_ctx` sizes each call to what the prompt actually needs, clamped to `max_ctx` — most calls never hit the ceiling. Set per-box from measurement (131072 where free, lower on boxes that cliff or CPU-spill at that size).

## The one performance rule

Ollama reloads the model whenever `num_ctx` changes (~35s vs ~3s compute) — changing it every loop iteration pays that every time. `team.py run` pins one context size per node per run and warms at it. `state.resident_ctx` persists a per-(node, model) high-water mark so single `do`/`code` calls don't reload-shrink-then-reload-grow across requests.

## Writing code on the pool

`./team.py code --out f.py --check "..." "spec"` for one file, `--batch jobs.json` for many. `do` uses this underneath.

- Check command must actually pass: the project root is put on `PYTHONPATH` for the check (otherwise `python3 pkg/test.py` rejects correct code with `ModuleNotFoundError`).
- Specify both floor and ceiling (`max_over`) — a floor alone silently also enrolls the slow/expensive top-tier model.
- Generation cap starts tight, grows only on `done_reason: length` (proof of real truncation) — grows unbounded otherwise (630→7701 tokens on one small module).
- `num_predict` bounds thinking + content together — a model reasoning past the cap emits nothing, burning a whole retry on a file never written. `codegen.generate_file` defaults `think=False` (measured: with reasoning on, some models never emit an answer at all on real problems; off, correct in one pass).
- Repair prompts must carry the original `{spec}`/`{extra}` too, not just the error — otherwise a repair is exactly when the model most needs reminding and gets the least context.

## When a node goes quiet

Timeout: 600s (a hang throws nothing; slowest real generation seen is ~90s).

All nodes down at once, sandboxed → check your own egress first, not three coincidental node failures.

Give a node 240s+ to load before calling it wedged — a short probe just restarts the load loop.

```bash
curl -s -m 240 http://NODE:11434/api/chat -d '{"model":"M","messages":[{"role":"user","content":"say ok"}],"stream":false,"options":{"num_predict":4}}' | python3 -c 'import sys,json;r=json.load(sys.stdin);print("load %.1fs total %.1fs"%(r["load_duration"]/1e9,r["total_duration"]/1e9))'
```

`/api/ps` with `expires_at` tracking the clock = actively generating, not stuck. Check for your own orphaned client before blaming the box:

```bash
ps -eo pid,etime,command | grep -E "team\.py|swarm\.py" | grep -v grep
```

Kill the orphan by PID — restarting Ollama just reloads the model again, doesn't touch the orphan.

## Continuity across `do` calls

`session.py` keeps a per-project append-only transcript. Every non-retry `do` folds in the last 20 turns plus the project's current files with real public names read off the syntax tree — lets a follow-up revise a file in place instead of duplicating it. Nothing trimmed from disk, only the render window.

Each turn keeps the planner's own reason per file (not just pass/fail), shown next to the file it explains.

An addition ≠ an edit: a genuinely new script named after an existing entry point overwrites working code; an edit/fix to existing behavior should reuse the same path. Non-Python files are listed too (previously invisible, causing duplicate `survey.html`). `[replaces existing]` is a printed fact, not a veto.

Entry-point naming is contextual: `main.py` unless one already exists with a different recorded purpose, then a descriptive name (`upload_unit.py`).

`ground.entry_point_conflicts` + `intent.rescue_entry_points` mechanically ask, per existing named entry point, "does this request revise it or is it unrelated work?" and rename on DIFFERENT — overrides the planner if needed. Unparseable answer → treated as DIFFERENT (a spurious rename costs a filename; a wrong reuse costs the file).

`intent.enforce_requested_entry_point`: a request's literal "the entry point is X" is checked and enforced after planning. `intent.default_api_entry_point`: an unnamed single-component `--api` plan defaults to `main.py`.

Edits need old content, not just the path: `run_components` snapshots existing `.py` files and folds the old content into the prompt with "keep what the change doesn't mention." Skipped on `--retry` (that leftover file is the broken attempt, not prior work).

**One request, one workflow.** A sentence naming 6 sequential capabilities collapses into one `client.py` + one `main.py`, never 6 folders (each step needs the previous step's id). For 6 reusable files, ask one at a time, smallest/most-depended-upon first:

```bash
team do --api maven "write client.py and create_project.py exposing create_project(client, name) -> project_id"
team do --api maven "add create_task.py exposing create_task(client, project_id, name) -> task_id, reusing the existing client.py"
```

Reusable within one project directory only — no cross-project registry (copy the file by hand or `--attach` it).

## Vague requests: read, don't guess

`ground.py` (on `mac`, `gemma4:e4b`) reads files exhaustively, split at top-level def/class boundaries, asked for a citable verdict (quoted symbol/line, never paraphrase) — a quote that doesn't actually appear is dropped. Best-effort: any failure just skips grounding. `--no-ground` skips it explicitly. Helps requests naming a fact with a real line to point at; helps little on "propose something new."

## Baked-in reference: `maven_applet_boilerplate`

`context/maven-applet-boilerplate.html` — a full reference applet, kept as `.html` deliberately (never enters relevance scoring, never degrades to a summary). Appended to `extra` only when the component's own path ends `.html`/`.htm` and `--api maven`, naming which parts to keep vs. replace.

## `attach.py`: reference material, not pasted

```bash
./team.py do --attach design.md --attach schema.json "implement the encoder"
./team.py do "now add the decoder"   # design.md still attached
./team.py do --attach                # no files: detach everything
```

Paths stored, contents re-read every run (never frozen at attach time).

Oversize refused up front against the narrowest window in the fleet — a per-worker check would silently produce a mixed package. A rejection rolls back to the prior attach set.

An attachment is labelled reference, never work — a document describing a class otherwise invites reproducing it.

A `.env` supplies variable names only — values discarded before anything touches them. Credential-shaped filenames (`.env*`, `*.pem`, `*.key`, `credentials`, `id_rsa`) refused outright by filename match. `context/policy-secrets.md` is an always-on card.

Project identity uses `os.path.realpath` (macOS's `/tmp`→`/private/tmp` symlink used to split one directory into two histories).

## `openapi.py`: an API spec is sliced, not pasted

```bash
./openapi.py --add maven https://host/openapi.json
./openapi.py --list
./openapi.py maven --stats
./openapi.py maven --refresh
./team.py do --api maven "write a client that creates users"
```

Specs live in `.swarm/specs/`, not the checkout. Key mechanisms:

- `$ref` resolved transitively, cycle-safe — a writer given only a `$ref` invents fields and 422s for real.
- **`close_over_ids`**: an endpoint creating an id another needs is pulled in automatically — else a writer substitutes something id-shaped (`"task-123"`) and 500s for real. Resolved via field name (70%), path segment (+4%), POST-to-plural-collection convention (+11%); exact stem match only, never prefix. Scoped to `core` (hinted/explicit + near-best-score entries) so weak filler can't demand its own closure.
- A closure entry is marked `REQUIRED: call this first`, only if nothing else in the slice already produces that id.
- Term weights are IDF-measured per doc, not equal (`/api/` in 98% of paths would otherwise inflate every query).
- `../folder/` sibling references stripped before intent-matching, never before the text a writer sees.
- Request-body field names count in scoring (through `$ref`), gated to 2+ word matches.
- A model proposes endpoints by reading the whole index once (catches synonyms scoring misses); every suggestion verified against the real table; also names `AVOID` endpoints and splits compound requests before scoring. Skips any action containing `output.json`/`main.py` literally — asking a model what endpoint a save-to-file convention needs gets a wrong answer, not "none."
- Index shown to a writer is scoped to only the tags actually chosen.
- Over-budget slice trims the weakest term match first, recomputing closures each drop — never trims to zero.
- **`close_pointer_updates`**: for an id a chosen op *produces*, finds a PATCH/PUT elsewhere whose body takes that id — "push a new version" also pulls in "point the project at it," unasked. Restricted to PATCH/PUT.

`intent.is_pure_static_file_request` blocks `--api` from running at all on a request that's obviously just "create a static file" (opens with create/write/make/generate, one client-side-extension filename, nothing else) — otherwise 2 unrelated scored endpoints bias the plan into 3 invented Python files around a static form. Never blocks `.py`/`.json`/`.csv`/`.sql`.

## Scheduling correctness

**`max_parallel` at claim time**: `try_claim_node` enforces the per-node cap inside the same atomic transaction that decides same-model-vs-different-model — 6 parallel callers used to independently "simultaneously" claim one `max_parallel: 1` node, Ollama serialized anyway, queued components burned 5-10x expected time.

**Failure logging**: a FAILED component's log line appends `component.error` — it used to say only "failed," never why. A succeeded component also prints its repair `history_trail` if non-empty.

**Fabricated clients**: `codegen.gate_fabricated_client` fails a file importing `requests` that never calls a verb on it — scoped to leave the legitimate split-file case (main.py only importing sibling client.py) alone.

**Swallowed API errors**: `codegen.gate_swallowed_api_failure` requires every `except` naming a real network/API error to `raise`/`sys.exit`/`os._exit` — one escape valve: any `sys.exit` inside `if __name__ == "__main__":` clears every handler.

**Invented platform facts**: `codegen.gate_platform_facts` checks source text against one known-correct literal; `gate_smoke` re-checks the same against captured stdout (catches two correct pieces concatenated into something wrong that no static scan of either half would see).

**Fresh roll on stuck repair**: `run_components` restarts a component once from the plain spec (no repair history) if the whole first pass failed — skipped only if the budget's genuinely spent or there's no `--api` to test against.

**Replanning**: `team.replan_component` fires once, after both a repair-budget failure and a restart failure, grounded in every other file's real current source. Only outputs: no change, a corrected spec for the failing file, or that plus one named sibling's described addition — never a new decomposition.

**Escalating the writer**: `team.pick_stronger_writer` fires once, between restart and replan — never below floor, only for a strictly higher quality tier (a tie isn't an escalation).

**"Node stopped answering" gets the same ladder** as a wrong-answer failure (restart, then `pick_fallback_writer` — any genuinely different node at/above floor, no quality requirement, idle-before-busy).

**Racing idle nodes**: `pick_idle_race_candidates` + `race_generate_file` run generation against every genuinely-idle node at once via scratch paths, take the first success, don't wait for the rest.

**`.env` reaches gate subprocesses**: `gate_import` probes files standalone via `importlib`, bypassing the real entry point's `.env` loader — `codegen.run_command` now parses `.env` directly and `setdefault`s it into the gate's environment. Real exports and the mock's own `API_BASE_URL` still win.

**Reconsidering the API slice on retry**: `openapi.reconsider_slice` fires once between failed decompose attempts — STARVED (nothing scored as a real match) re-proposes with a doubled limit; BLOATED (mostly filler vs. its own best match) narrows to core. Usually returns "no change."

**`--retry` needs a saved plan even on total failure**: `save_plan(decompose_failed=True)` now fires even when decomposition produces zero components, so `--retry` can redo it from the saved request instead of reporting nothing to retry.

## Running generated code is confined, not skipped

`gate_import`/`gate_smoke` are the only gates that observe real behavior, so for an API client that means real network access — confined three ways, not removed:

- Network confined to loopback via a `sitecustomize.py` on `PYTHONPATH`; `getaddrinfo` itself is blocked, not just `connect`. Proxy env vars emptied in the gate environment.
- Every non-loopback `*_URL`/`*_URI` rewritten to the mock (or a dead address with no mock) in the gate's environment.
- Non-`.py` files get a type-appropriate gate: HTML must nest correctly *and* (`gate_html_placeholder`) contain visible text or a substance element — nesting and non-emptiness are different checks. JS runs `node --check` when available (no brace-counting fallback). Inline `<script>`/`<style>` parsed as code/CSS; skipped when templated (`{{`/`{%`).

`mockapi.py` synthesizes real responses from the real spec (via `$ref`), not `{}` — an unknown route 404s naming near misses; a bad required field 422s naming it. Stateless by design. No traffic logged isn't a failure (legitimately may call nothing), but a call the mock *rejected* is checked after every gate that can see one — catches an unchecked response (`except: pass`) passing cleanly with a 404 sitting unread.

## Miscellaneous single-issue fixes

- A component depending on a page (not a module) isn't a missing import — `check_missing_imports` is Python-only; don't demand an `import` of an `.html` file.
- A second entry point is a serial-stage tail, not parallel work — detected mechanically when a contract gives `main()` to two files.
- Whole-file rewrites must refuse rather than silently truncate — prior-content budget is computed from real `max_ctx` against the real prompt; a file that can't fit is refused, naming cost and limit.
- Backups kept forever, even on success, outside the tree, never pruned — "passed all gates" doesn't mean nothing was silently dropped.
- A tool the model has is a policy whether you meant it that way or not — `tools.py`'s registry is narrow specific tools, not one broad shell tool. New tools default to CONFIRM. Paths confined to the project root in one place. `run_check` takes no command argument (reads `.swarm-check`). Validation → confinement → confirmation → run, in that order. Non-interactive stdin declines.

## Known gaps

- Default hard-tier writer rests on one measured hard component.
- No end-to-end regression test — `run_tests.py` (23 offline tests, pool stubbed) never touches the live pipeline. Don't glob-run generated packages at the project root; one might open a window or dial the LAN.
- Tokens spent vs. avoided isn't instrumented (measured by hand once).
- Only speed is remembered (`perf` table) — which model succeeds at which *kind* of task isn't, so routing can't learn from outcomes.
- Generated code executes for real, confined but not sandboxed as a security boundary.

## Timing and machines: read `config.json`, don't memorize it here

The node list, which models run where, per-node/per-model bias, and per-model `decode_tps_hint`/`thinking_tokens`/`quality`/`max_ctx` all live in `config.json`, each with its own `notes` explaining *why* (hardware, known bugs, fallback status) — the actual current state, not a copy that goes stale the moment a node is added, disabled, or re-enabled. Read it directly rather than trusting a table here; this doc already drifted once (a disabled node got re-enabled, a second machine got added, and a hardcoded table said neither).

`team.py calibrate` measures warm predictions against actual (last checked: 1.04x). One model per machine at a time — no VRAM pooling; strength comes from cross-checking, not sharing. Gemma-family only — a cross-family critic once flagged correct code as broken with confident wrong reasoning (`team.restrict_to_family` enforces this).

## Specify, then draw

If the value is easier to give as coordinates than as characters (grids, layouts), put `compose.json` in the project root and a command that turns the writer's spec into the literal. See `BLOSSOM.md`, "Specify, then draw". The writer then never has to transcribe a grid.

## A judge inside a check

A rule no script can measure can be judged by `judge.py` from the check: cached verdicts, quoted evidence a second question confirms, runs on `mac`, passes when unreachable. See `BLOSSOM.md`, "A judge inside the check". Calibrate it on a known good and a known bad text before the run, and put the rule in the writer's context in the same edit.

## ChatGPT mode: an opt-in parallel path

`./team_gpt.py` is `team.py` with every model call sent to the ChatGPT browser server (`10.0.0.178:11436`, a Chrome window on the p40 box driven by `pyautogui`) when the prompt fits, and to the normal nodes when it doesn't. `./team.py` is untouched; nothing changes unless you run `team_gpt.py` (or `RALPH_TEAM=./team_gpt.py ./ralph.sh`). It replaces `swarm.chat` from outside (`chatgpt_route.install()`), so it is deliberately outside `restrict_to_family`: it is not a Gemma node and the family rule does not apply to it. Prompts leave the LAN for OpenAI.

Rules, each measured on the real server (`chatgpt_limit_test.py`, results in `chatgpt_limit_results.jsonl`):

- **Size limit: under 24,000 tokens and at most 96,000 characters.** 96k chars passed (twice, ~75s); 128k failed 4 of 4 (connection dropped, server went down at least twice). Both limits are enforced because characters are what was measured and code tokenises worse than the 4-chars/token estimate. Env overrides: `CHATGPT_ROUTE_MAX_TOKENS`, `CHATGPT_ROUTE_MAX_CHARS`, `CHATGPT_ROUTE_URL`, `CHATGPT_ROUTE_TIMEOUT`, `CHATGPT_ROUTE_DIR`.
- **One request at a time.** The server has no lock; two requests paste into the same chat box and both come back wrong (this wrecked several early test runs). Every routed call takes a cross-process file lock (`.swarm/chatgpt.lock`) and leaves a 4s idle gap. The lock only covers requests made through this router; anything else touching that Chrome window still collides.
- **Up to 3 attempts per call** (`CHATGPT_ROUTE_ATTEMPTS`, first try included), made while holding the lock. A retry must not overlap a failed run still going in Chrome (a dropped connection does not stop it), so it waits first: 45s after no reply (dropped, timed out, HTTP error), 10s when the server was unreachable, 4s after the server reported its own failure (an `Error:` reply, which it sends as a 200, e.g. the copy step failing; its handler has finished). A short reply matching a usage-limit phrase is not retried. That phrase list is a guess.
- **When every attempt fails**, the call falls back to the normal nodes and ChatGPT is skipped for 90s (15 min on a usage-limit reply), shared across processes via `.swarm/chatgpt-breaker.json`. Worst case before falling back is about 3 failed requests plus two waits; with the server dead that is ~25s, with a request that never returns it is the full reply timeout x 3 plus 90s of waiting.
- **Warm-up pings (`num_predict=1`) stay on Ollama.** Each ChatGPT call opens a browser window and takes over the machine's keyboard.
- **System prompts and earlier turns are folded into one prompt** (the server types only the last user message).
- **No speed numbers are returned**, so `state.record_perf` never files a browser round trip under an Ollama node.
- Every decision is appended to `.swarm/chatgpt-route.jsonl` (route, reason, sizes, seconds).

Known limits: calls become serial (parallel writers queue behind one window, ~10-75s each); `num_predict` is ignored (the server cannot cap a reply); the reply is rendered markdown, so a writer or parser expecting raw code or strict JSON may see fences/headings (not yet measured in a full `team_gpt.py do` run); `judge.py`, `harness.py` and `tools.py` call `/api/chat` directly and bypass the router. Tests: `python3 test_chatgpt_route.py` (13, offline, fake server).

The server itself (`chatgpt_browser_server.py`) lives on the p40 box, not in this repo; the copy in `hush-main.zip` is the old version (OCR, 120s timeout, typing instead of pasting).
