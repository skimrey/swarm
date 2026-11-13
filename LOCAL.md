# Running the swarm from a local model

Read this instead of `AGENTS.md` if you're a Gemma model on this network. Most of `AGENTS.md` still applies; its central "should I delegate" cost calculus does not.

A dataset run is blossom. Read `BLOSSOM.md`. The check is the spec. Run `ralph.sh` as `ralph.md` describes. Do not write the stored values yourself.

**You are the operator.** `team.py`/`swarm.py`/`openapi.py` already work. "Create a project and upload 3 units" = run a shell command, not a spec to implement. Never reimplement `create_applet`/`create_project`/`upload_units` — they exist in generated `client.py` files.

**Call as `team`/`swarm`/`openapi`**, already on `PATH`: no `./`, no `.py`, no `cd` first.

```bash
team do "the request, in the user's own words"
```

Add `--api maven` for anything touching the platform (applet/project/task/unit):

```bash
team do --api maven "the request, in the user's own words"
```

`--api maven` pulls the real spec into the call so the writer sees real endpoints/fields, not guesses. Omit it for requests that never touch the platform. Never name specific endpoints yourself — hand the whole request to `--api maven` and let it slice the spec.

Don't ask for a project name, task name, or other value the request omitted before running the call — a name is just a placeholder string the generated code will pick or accept as an argument. If something is genuinely missing, the tool's own output says so.

Then read what it printed and report that back.

## `team do` writes code. It does not, by itself, do the thing.

`team do --api maven "..."` writes `client.py`/`main.py`; it does not create anything real. Run the program to perform the action:

```bash
python3 main.py
```

(Check the run's own output for the real entry-point filename and required env vars — "main.py" is common, not guaranteed.) Two steps, always, in order: generate, then run. Never inspect/rewrite/"finish" a file that already passed its gates.

**Never check, export, or ask about `API_KEY`/`API_BASE_URL` yourself. Never run `env | grep API_`.**

- While `team do --api maven` generates/gates a file, it talks to a local mock it starts itself — no real credential needed. "N/N passed" means it worked.
- When the generated script runs for real (`python3 main.py`), its entry point loads real credentials automatically from `/Users/skimrey/Documents/Local LLMs/begin/.env`, baked in at write time. It only fills a variable that isn't already set — a real export always wins.

Never pause a plan to check for these two vars, never claim they're missing, never guess a value. If a *real* run prints `environment variable X is not set` for some *other* name, report that verbatim — don't guess a value, don't write a real credential to disk or print it.

**Never fabricate a result.** Writing wrapper scripts that "contain" `team do` calls and then reporting invented output without ever running them is a real failure mode measured live (34 minutes, hundreds of thousands of tokens, on a workflow that never executed). Two real steps, nothing invented in between — point at real files on disk and real printed output, never a composed summary.

## Never simulate a tool's result. Say what actually happened.

If a command aborts, hangs, times out, or you're unsure what it did, say exactly that and show the literal output. Never write what a successful run *would* look like and present it as what happened. A command with no output produced no result — no partial credit for a plausible guess.

This is the one failure a gate can't catch after the fact. If you catch yourself writing "simulating," "the expected output would be," or narrating a result before the step actually ran, stop and run it, or say plainly you haven't.

Check ground truth before theorizing: `ls` the directory a real `team do` call should have populated; read the terminal's real output and real exit code. Don't guess at causes you can't confirm ("maybe a rate limit").

## Do not wrap `team do` in a script to run "later"

Type `team do "..."` (then `python3 main.py`) directly with the shell tool. Don't write a `.sh` file whose contents are the command and then execute that instead — it adds a place to write a hand-authored imitation, a pointless `chmod +x`, nothing typing the line directly doesn't already give you.

Several steps → several direct shell calls in sequence (`mkdir`, `cd`, `team do`, repeat). No todo list needed for a short linear sequence.

**Exception, decided by count, not guesswork: 2+ `team do` calls in a chain is always the `nohup`'d-script case** (below) — a single call alone can run several minutes; there's no version of "probably quick" this pool satisfies. One call, typed directly, is the only case this prohibition covers.

**Read on for reasoning, or skip straight to `team orchestrate-sh` below for the actual work.** `team orchestrate-sh steps.json > orchestrate.sh` emits one finished script with both phases; write `steps.json`, run that one command, run the file it prints.

## Any request that touches the swarm runs under `nohup`. Immediately. Then you stop watching it.

**Covers indirect contact too** — any script that contains a `team`/`swarm`/`openapi` call anywhere in its body, however many layers deep, however it's named (`orchestrate.sh`, `run.sh`, whatever). If you can't quickly tell whether running something eventually shells out to one of these, default to `nohup`.

**Decide before the first command runs, always — no "this one's probably quick."** Floor-4 generation alone can take minutes; a repair loop can retry 6x before any output; a 7-step chain compounds several of those. A call reading as "aborted"/hanging is very often just your tool call's own wait timing out, not the work failing — it's likely still running or already done underneath.

So: the first `team`/`swarm`/`openapi` call goes out backgrounded already. Write the real `mkdir`/`cd`/`team do` sequence into one script, launch it detached, output to a file:

```bash
cd /path/to/the/project/directory
nohup bash orchestrate.sh > orchestrate.log 2>&1 &
```

**`cd` to the script's own directory first** — the script uses relative paths on purpose, so running from elsewhere fails (`No such file or directory`) or — worse — silently creates subfolders in the wrong place if a same-named script exists there.

**`killswarm`, run from anywhere, no args, stops everything** — every `team.py`/`swarm.py`/`openapi.py` process plus every generated script and everything it spawned, by walking the real process tree. `killswarm --dry-run` previews it. Doesn't cancel an Ollama request already in flight remotely, but frees the node the moment nobody's waiting on it.

**Then stop. Don't loop `tail`/`sleep`/`tail`/`sleep`** — that's the synchronous wait `nohup` exists to avoid, hand-reimplemented. Launch it, say it's running and where the log is, end your turn. Read the log later:

```bash
tail -n 40 orchestrate.log
```

"Never simulate a result" still applies. Until you've read real content from the log or confirmed the process exited, you know nothing new.

**The log must contain what `team do` actually printed, not your own narration.** Redirecting the whole script's output already captures every real byte. Your own `echo "Step 1: ..."` markers are fine *in addition to* the real command's output — never in place of it. If you write the orchestrator in Python, `subprocess.run` without `capture_output=True` inherits stdout automatically; the moment you *do* capture it, you must also print what you captured.

**`nohup` covers generation, not real execution of a multi-step chain — never fold `python3 main.py` against a real API into the same unattended script.** `team do` only touches a local pool + mock, so a bad attempt just gets repaired/restarted, unseen. `python3 main.py` against the real platform creates real, mostly-non-deletable resources, and each step's success is the next step's real input. Chaining real runs unattended means a step that "passed gates" but misbehaves against the real API (wrong field, unexpected shape) runs to completion — and possibly feeds bad output forward — before anyone reads the log. `set -e` only stops the chain after the damage is done, and does nothing if the step exit-codes 0 while doing the wrong thing.

So: both phases live in one `orchestrate.sh`, nohup'd as a whole (generated by `team orchestrate-sh`, never typed by hand). Inside it, the run phase executes each step's `python3 main.py` one at a time, in the script's own foreground, with `&&` stopping the chain on failure before the next step starts. "Foreground" here means internal sequencing — the whole script is still captured to a log read back later.

A step that crashes stops that script's own execution there; its traceback is sitting in `orchestrate.log`, readable immediately, fixable via `--retry` before anything downstream ever sees bad output.

**Use `wait "$GENERATE_PID"`, never a guessed `sleep`.** A fixed sleep either starts the run phase too early (files don't exist yet) or wastes time waiting past when generation actually finished. `wait` blocks exactly as long as needed and exposes the real exit status via `$?`:

```bash
nohup bash generate.sh > generate.log 2>&1 &
GENERATE_PID=$!
wait "$GENERATE_PID"
if [ $? -ne 0 ]; then
    echo "generate.sh failed -- see generate.log" >&2
    exit 1
fi
```

This doesn't reintroduce a synchronous wait on the agent's own tool call — `wait` blocks the *script*, not you. `GENERATE_PID`/`wait` is plain bash inside a file on disk; it is never an instruction about your own tool-calling harness's background-job mechanism (a real, measured confusion once: an agent invented a fake job ID and polled its own tool for it instead of writing this into a script). How *you* launch the whole script is the same `nohup ... &` rule as any other, applied once, at the outer layer — the inner `wait` and the outer `nohup &` are two different waits at two different layers and neither substitutes for the other.

**If you already wrote a combined script (generation + real execution) before reading this, split it — don't deliberate about running it "just this once."** Delete every `python3 main.py` line from the `nohup`'d script; run those individually afterward. That's the whole fix.

## The exact shape, copied rather than reasoned toward

Use `team orchestrate-sh steps.json > orchestrate.sh` (below) rather than reconstructing this from the rules above per request — reasoning it out from scratch has repeatedly produced a script with `python3 main.py` inlined into the nohup'd part anyway.

**Every write-phase step runs in parallel, not serially** — a `team do` call touches nothing real, so there's no execution-order dependency to protect, only a resource one `node_claim` already handles safely across processes. A strictly-serial write phase (no `&` anywhere) takes as long as all steps' `team do` calls added together, with idle workers the whole time. A step's own *planning* call still defaults to `g26b` and briefly queues against siblings; the parallelism payoff is in the writing that follows.

**Because every step starts within moments of every other, a promise-to-write check can't look at a sibling's real file — check the request *text* instead, before any of the fan-out starts.** That's `team lint-chain` (`intent.chain_output_json_problems`): it's the literal first thing `generate.sh` does, reading the same `steps.json` that builds the fan-out, so a chain that will silently default an id fails in seconds rather than three real API calls later. `--fix` (the plain, no-flag default) repairs whatever it can prove from the chain's own text — a step reading a named key from a sibling whose own request never promised to write one — and rewrites `steps.json` in place, touching only the `request` field (never `api`/`flags`). What it can't fix (a reference to a directory not in the chain, a read too vague to name a real key) stays a hard exit-1 stop, printed by name.

```bash
# generate.sh -- nohup this. No python3 main.py anywhere in this file.
#!/bin/bash
cat > steps.json <<'EOF'
[
  {"dir": "01_create_widget", "request": "create a widget; save its id as widget_id in a new output.json in the same directory as the script", "api": "maven"},
  {"dir": "02_use_widget", "request": "read widget_id from ../01_create_widget/output.json; do the second thing; save its own id as gadget_id in a new output.json in the same directory as the script", "api": "maven"},
  {"dir": "03_static_step", "request": "create a static asset"}
]
EOF
# "widget"/"gadget" are stand-ins -- a real chain names each key after its
# actual resource (project_id, task_id, applet_id), never a generic or
# positional placeholder like "first_id"/"second_id"/"result_id": a
# non-descriptive key gets silently overridden by a writer more often
# than a descriptive one does.
if ! team lint-chain steps.json; then
    echo "generate.sh: the chain's own request text is inconsistent -- see the problems printed above, fix by hand, rerun" >&2
    exit 1
fi
eval "$(team steps-to-bash steps.json)"

PIDS=(); NAMES=()
for i in "${!STEP_DIRS[@]}"; do
    dir="${STEP_DIRS[$i]}"; req="${STEP_REQS[$i]}"
    api="${STEP_APIS[$i]}"; flags="${STEP_FLAGS[$i]}"
    echo "launched $dir"
    if [ -n "$api" ]; then
        (mkdir -p "$dir" && cd "$dir" && team do --api "$api" $flags "$req" > generate.log 2>&1) &
    else
        (mkdir -p "$dir" && cd "$dir" && team do $flags "$req" > generate.log 2>&1) &
    fi
    PIDS+=("$!"); NAMES+=("$dir")
done

FAILED=0
for i in "${!PIDS[@]}"; do
    if wait "${PIDS[$i]}"; then
        echo "${NAMES[$i]} succeeded"
    else
        echo "generation failed: ${NAMES[$i]}" >&2
        FAILED=1
    fi
done
exit "$FAILED"
```

`team steps-to-bash steps.json` — never a hand-written `eval "$(python3 -c '...')"` — is what turns `steps.json` into the four `STEP_*` arrays. The inline version required balancing three nested closing quote/paren delimiters across a 7-line embedded program and has been dropped, half-copied, or quote-mangled by hand every time it was attempted; the shape now lives once in `team.py`, never reconstructed from memory.

**A step that depends on another step's file (not its real output) is still safe to write in parallel** — the missing-sibling skip (`codegen._missing_sibling_error`) treats a smoke gate that can't find a sibling's not-yet-written file as skipped, not failed.

**Each step also redirects its own output to its own `NN_step/generate.log`, not only the merged outer one** — six parallel steps writing to one stream interleave unreadably. (Redirect target is exactly `generate.log`, never `NN_step_name/generate.log` — the subshell has already `cd`'d in, so repeating the dirname resolves to a nonexistent nested path and every step fails identically before running.) The outer log still catches anything `generate.sh` itself prints and exists if the script dies before any subfolder is created.

**If both phases live in one `orchestrate.sh` (the default), the whole file is nohup'd once.** If you keep them as genuinely separate commands, only the `generate.sh` line is nohup'd; each run-phase line is a normal, unbackgrounded call:

```bash
nohup bash generate.sh > generate.log 2>&1 &
# come back later, confirm every step wrote its files, THEN:
```

```bash
(cd 01_create_widget && python3 main.py)
(cd 02_use_widget && python3 main.py)
```

**Always a subshell, `( cd DIR && python3 main.py )`, never `cd DIR && python3 main.py && cd ..`.** The trailing `cd ..` never runs when `main.py` fails (short-circuited by `&&`), leaving the shell stuck in the failed step's directory — every subsequent `cd`/step then fails with "No such file or directory," resolved relative to the wrong place. A subshell's `cd` never leaks out, successful or not, so there's no `cd ..` to get wrong.

**A step with no `main.py` is skipped, never attempted** — a static-asset step (HTML page, JSON fixture) writes a file and stops; a downstream step reads it off disk directly, never imports it.

```bash
if [ -f 04_create_survey/main.py ]; then
    (cd 04_create_survey && python3 main.py)
else
    echo "04_create_survey: no main.py -- static content, nothing to run"
fi
```

**A run-phase failure gets one real repair attempt (`team do --retry --run`, from inside that step's own directory) against the exact real traceback, then — if that alone doesn't fix it — one more against each named dependency** (a producer can exit 0 and still be the actual defect: it called the real API, printed a real success line, and still saved a wrong id — the failure only surfaces in the *consumer*). Bounded, not a loop: self-repair once, dependency-repair once, then report failed. `run_step`'s stdin is explicitly redirected from `/dev/null` (a `--retry --run` call with an inherited, non-tty stdin can hang indefinitely otherwise).

**Re-running the script after fixing one step must not redo steps that already succeeded** — each real API call is not idempotent (a second `01_create_project` run creates a *second* real project). `run_step` touches `$dir/.orchestrate_done` only on a genuine success; both phases check for the marker first (write phase skips regeneration, run phase skips re-execution) and `rm "$dir/.orchestrate_done"` forces a real rerun. This is also what makes "keep what worked, retry what failed, continue forward" work with no separate resume logic.

**`python3 main.py` runs with the shell's *own* environment, not the file's** — a generated entry point's own `.env` loader (`codegen.maybe_inject_env_loader`) can fail to be present for any number of reasons (a repair pass, a hand edit). Fix it at the invoking script, not the generated file: `orchestrate.sh` exports the same `.env` into its own environment once, up front, so `python3 main.py` inherits real credentials regardless — a real export the generated code makes still wins (setdefault only):

```bash
_SWARM_ENV="/Users/skimrey/Documents/Local LLMs/begin/.env"
if [ -f "$_SWARM_ENV" ]; then
    while IFS='=' read -r _swarm_k _swarm_v || [ -n "$_swarm_k" ]; do
        case "$_swarm_k" in ''|'#'*) continue ;; esac
        _swarm_v="${_swarm_v%\"}"; _swarm_v="${_swarm_v#\"}"
        _swarm_v="${_swarm_v%\'}"; _swarm_v="${_swarm_v#\'}"
        [ -z "${!_swarm_k:-}" ] && export "$_swarm_k=$_swarm_v"
    done < "$_SWARM_ENV"
fi

run_step() {
    local name="$1" dir="$2"; shift 2
    local deps=("$@")
    if [ -f "$dir/.orchestrate_done" ]; then
        echo "$name already succeeded in a previous run -- skipping (rm $dir/.orchestrate_done to force a real rerun)"
        return 0
    fi
    _run_step_try() { (cd "$dir" && python3 main.py); }
    echo "running $name"
    if _run_step_try; then
        echo "$name succeeded"; touch "$dir/.orchestrate_done"; return 0
    fi
    echo "$name failed -- asking team do --retry --run to repair it" >&2
    (cd "$dir" && team do --retry --run < /dev/null)
    if _run_step_try; then
        echo "$name succeeded after repairing itself"; touch "$dir/.orchestrate_done"; return 0
    fi
    if [ "${#deps[@]}" -gt 0 ]; then
        echo "$name still fails -- it depends on ${deps[*]}, repairing those next" >&2
        local dep
        for dep in "${deps[@]}"; do
            (cd "$dep" && team do --retry --run < /dev/null)
        done
        if _run_step_try; then
            echo "$name succeeded after repairing its dependencies"; touch "$dir/.orchestrate_done"; return 0
        fi
    fi
    echo "$name failed again after repair" >&2
    return 1
}

run_step "01_create_project" "01_create_project" || exit 1
run_step "02_create_task"    "02_create_task"    "01_create_project" || exit 1
```

**The `../DIR/output.json` convention only works if every request is actually drafted in it.** Producer: bare `"...; save the id to output.json as {\"project_id\": ...}"`. Consumer: qualified `"...; read project_id from ../01_create_project/output.json"`. `team lint-chain steps.json` checks the two agree, mechanically, as the literal first thing `generate.sh` does — not something anyone has to remember to run separately.

**The same repair, aimed one directory over, because a producer can exit 0 and still be the defect** — see `run_step`'s dependency-repair pass above; this is why a step names its own dependency directories.

Don't author a `run_full_test.sh` that re-describes the same steps instead of running the real thing — run `python3 main.py` or the real `team do` call, watched to completion, and look at the real output.

## A batch of runs is the same two-phase shape one level up

"Write me a batch test folder" = a parent directory holding several independent chain runs (`run_1/`, `run_2/`, ...), for comparison/repeatability. Same two-phase shape one recursion level up.

**Runs are created and executed one at a time, never several at once, never several `run_N/` set up in advance.** Two chains sharing the fleet queue behind each other invisibly (`pick_idle_race_candidates`/`max_parallel` serialise same-node work regardless of request count) and corrupt any timing comparison — both runs still finish, just slower, with nothing saying why.

**There is exactly one command that produces this driver — run it, don't write your own version:**

```bash
team batch-sh steps.json --n 5 > run_batch.sh
nohup bash run_batch.sh > run_batch.log 2>&1 &
```

Do not hand-write this driver, even after reading a corrected example — a fully-written example next to "don't copy this" has already lost to a model's own judgement twice, measured live, so no example is shown here at all. What it guarantees: each run created/sequenced/finished strictly before the next starts; every run's real output lands in that run's own `orchestrate.log`, checked non-empty after each run with a stderr warning if not. Not configurable by editing the generated script — change `team.py`'s own `_BATCH_RUN_TEMPLATE` instead.

`orchestrate.sh` itself also now self-redirects its own stdout/stderr to `orchestrate.log`, in its own directory, as its first executable line, regardless of how it's invoked — so a caller (hand-written or generated) forgetting the redirect can no longer lose the log at all.

**Three different logs, three different scopes, none substitutes for another:** `run_batch.log` (outer driver, per-run start/finish). `run_N/0X_step/generate.log` (one step's write-phase output only, exists even if the run phase never ran). `run_N/orchestrate.log` (the whole run's real output, both phases — the only place "did run 7 actually create a project for real" can be answered from).

The whole driver is `nohup`'d exactly once, same as any chain — never launch `run_(N+1)` by hand because `run_N` "looks done"; the driver's own blocking sequencing already guarantees order.

### The single most common way this goes wrong: "in order" is not "waited for"

```bash
# WRONG -- backgrounded + detached inside the subshell; "wait $!" in the
# parent never refers to that job, so it returns instantly every time:
(cd "$DIR" && nohup bash orchestrate.sh > orchestrate.log 2>&1 &)
wait $!

# RIGHT -- no &, no nohup: this line blocks until orchestrate.sh actually
# exits, so the loop cannot reach run_(N+1) early:
(cd "$DIR" && bash orchestrate.sh > orchestrate.log 2>&1)
```

If a call inside a batch loop has `&` on it or `nohup` in front of it, it is not blocking the loop — no `wait` afterward fixes that once the job is detached. `nohup`/backgrounding belongs on exactly one line: outside and after the entire loop, launching the whole driver.

**Second, independent bug from the same live script:** a bare, unconditional `exit "$FAILED"` sitting between generation and run phase (not `if [ "$FAILED" -ne 0 ]; then exit "$FAILED"; fi`) fires on success too, so the run phase — the one that actually calls `python3 main.py` — never runs, ever, for any run, with no error and no nonzero exit.

Both bugs are why `team batch-sh` and the self-logging `orchestrate.sh` exist rather than being fixes to reproduce by hand.

## Do not try to work out the API's shape yourself

`--api maven` exists so you never have to know which endpoint does what. Describe the *outcome* in plain English and let the call resolve the path, fields, and any secretly-required extra calls. Don't open the OpenAPI document, grep it, or recall a path from a previous run — all three substitute a guess for a mechanical lookup that's already correct:

- The real spec is far larger than any context window; a fragment can't show that creating a version also requires re-pointing the project — that fact lives in two endpoints connected only by a full-document graph walk the pipeline already does.
- A wrong guessed path/field written into the request text can drag endpoint selection toward the wrong endpoint.
- The tool's own output already names what's missing (`the CONTRACT gives no method for N REQUIRED endpoint(s)`) — react to that, don't try to predict it.

**Never invent your own stand-in for the service in generated code — always call `requests` for real.** A writer defining its own `class MockClient` that prints "MOCK: ..." and returns a fake id, never calling `requests.post`, is now a blocking gate (`codegen.gate_fabricated_client`): importing `requests` and never calling one of its verbs fails the component.

**A real call's failure must change the exit code, not just print.** `except: print(...); return` inside error handling still exits 0, and `generate.sh` decides success/failure from exactly that code. Blocking gate: `codegen.gate_swallowed_api_failure` — a caught `HTTPError`/`RequestException`/`ConnectionError`/`Timeout`/sibling must itself `raise` or call `sys.exit`/`os._exit` (or propagate through `main()`'s return value into `sys.exit(...)` in the `__main__` guard).

A single non-Python file (e.g. `survey.html`, nothing else) is a complete answer to a static-content request, read off disk by whatever needs it — not a sign something was skipped.

**Never state a function signature in a request, even one that looks safe** — the parameter list, return value, and claim it's a method on `client` are all invented plumbing. Describe the outcome only ("create a project; save its id...") and let the planner/spec decide the name/params/return. This especially matters when a step could plausibly be one of several real operations (update-in-place vs. create-new; assign vs. create-and-assign) — naming a function picks one for you, and a wrong pick can drag selection toward the wrong endpoint. Don't invent local state (a fake "applet repository") to paper over not knowing the real shape — trust the network; run the generated code and read what comes back.

**"Push a new applet version and point a project at it" is one request, not several** — `openapi.py`'s own closures (`close_content_pushes`, `close_pointer_updates`) already pull in both the create-version and the re-point endpoints in one `--api maven` slice:

```bash
mkdir -p 06_push_applet_version && cd 06_push_applet_version
team do --api maven "push survey.html (at ../04_create_survey/survey.html) as a new version of the applet whose applet_id is read from ../05_create_applet/output.json, using a version string guaranteed to be different from any that already exist (a uuid, not a fixed literal like '1.0.0'), and point the project whose project_id is read from ../01_create_project/output.json at that new version; save the new version's id as applet_version_id in a new output.json in the same directory as the script; the runnable entry point is main.py"
python3 main.py
cd ..
```

**Always say "a version guaranteed to be different from any that already exist" when pushing/creating an applet version** — a hardcoded `"1.0.0"` (copied from the schema example) 400s on a second real run. (`team do --retry --run`'s repair ladder now catches this mechanically at runtime with no model call — `team.run_with_version_conflict_retry` — but saying it up front avoids paying for the discovery.)

If the request never says an applet already exists, that's a real ambiguity — ask, or default to adding a `create_applet` step and say plainly you did.

## "Split this into sub-requests" means run `team do` more than once

Run `team do` several times, once per distinct capability, each in its own directory — not a hand-built workflow engine or `parallel()` tool. `team.py` already parallelizes across the whole LAN inside a single call.

A numbered/sequential list of distinct actions is the same shape as an explicit "split this" request, whether or not it uses that word — read it as that many separate `mkdir && cd && team do` calls, smallest/most-depended-upon first, not one bundled `main.py`.

**Not every numbered item earns its own directory — the test is reuse, not sequence.** Ask: would I ever want to run *just* this half again, independently, later? If yes, its own directory. "Push a new version" / "point the project at it" fails this test (meaningless independently, one outcome via two calls `openapi.py` already connects) — treat as one request. "Create a project" / "create a task in it" passes (each independently useful) — separate directories.

**"An applet" bare, with no other content named, means the HTML page is its content** — "create survey.html ... assign it to an applet," "push a new version," and "update the applet with html" are the same operation phrased three ways, not three steps. There is no separate "assign" operation the real API exposes — pushing a version *is* the assignment.

**Every request that creates something saves its id to `output.json`, always** — "...and save whatever id the response contains in this directory's own output.json" on every single-capability creating request, whether or not this exact request names a next step. Costs nothing unused; saves a real duplicate-resource re-run when something later needs it.

**Save the id under the API's own literal field name, stated explicitly in the request text — never "the same key the API uses" as an abstract instruction.** Two independently-written sides (producer/consumer) can each faithfully follow "use the API's own name" and still land on different spellings (`applet_version_id` vs `version_id`) since each is only guessing at the same abstraction separately. Instead: once you know the concrete name (`project_id`, `task_id`, `applet_id`, `applet_version_id`), put that exact literal string into *both* requests' text.

**The name itself must describe the resource, not its position — never `first_id`/`second_id`/`result_id`/`item_id`.** A generic placeholder name gets silently overridden by a writer reaching for the resource's own obvious name instead — measured live even when both ends of a chain explicitly agreed on the generic name.

**The write half is never phrased as bare `"output.json"`** — say "save its id as `X` in a new output.json in the same directory as the script" every time, even though it's redundant. A request mentioning `output.json` twice in one sentence (once as read target, once as write target) has been conflated by a writer, overwriting a sibling's file. Blocking gate: `codegen.gate_own_output_path` fails any file opening a `../`-prefixed `output.json` in write mode.

**"Save its id as X" still doesn't guarantee a JSON object comes out** — a producer can write a bare string (`"94b3c0dc-..."` with no `{"project_id": ...}` wrapper) and satisfy the letter of that sentence. Blocking gate: `codegen.gate_output_json_shape` fails a file whose `output.json` write is provably a bare scalar/list rather than a dict.

**Three non-redundant layers guard this, each only firing when the one above it didn't hold:** (1) request text phrased right the first time (above); (2) `context/policy-output-json.md`, an `always: true` standing-policy card every writer sees regardless of request wording; (3) `team lint-chain steps.json` — the literal first thing every generated `generate.sh` does, fixing whatever it can prove from the chain's own text (default behavior, no flag needed; `--check-only` opts out of the mutation) and hard-exiting on what it can't (a reference to a step not in the chain, a read too vague to name a key).

Point a later step at the real file an earlier one wrote ("...read from `../02_create_task/output.json`, written by the previous step") rather than leaving it to guess, and name `main.py` explicitly right after.

**`team do` caps the repair budget (and skips the automatic restart) for a single-component, sibling-reading step when the fleet has no real floor-4 writer available** — a weaker model failing once at a task it can't do is evidence about the fleet, not a reason to retry 5 more times or replan (which would need the same underpowered fleet to write a new CONTRACT). This only fires when there genuinely is no better writer — a step with a real writer, or a multi-component plan, still gets the full budget. If you see this note, the honest move is to finish the file by hand or enable a real floor-4 node, not re-run and expect a different fleet.

**Use `--single-file` for a genuinely one-capability step** instead of waiting for the planner to (usually) split into `client.py` + `main.py` anyway when nothing else will ever import that client. Loses nothing about `--api` handling — slicing/closures/mock/credential-naming are all built from the request text before the planner is ever consulted. `team do` now applies this automatically for a request that reads a sibling's `output.json` and doesn't itself name a second file.

```bash
mkdir -p 02_create_task && cd 02_create_task
team do --api maven --single-file --out main.py \
  "create a task in the project whose project_id is read from \
   ../01_create_project/output.json; save its id as task_id in a new \
   output.json in the same directory as the script"
```

**Generate every step (chained or not) in the background first; run nothing real until generation is fully finished.** A chained step's smoke gate can't find a not-yet-real sibling `output.json` during generation — `codegen._missing_sibling_error`/`gate_smoke` treats that as skipped, not failed, which is what makes generating a whole chain up front safe regardless of real execution order. Then run each step's `python3 main.py` afterward, one at a time, foreground, real dependency order. This is exactly what `team orchestrate-sh steps.json > orchestrate.sh` emits as one file — generate the real one with that command; don't hand-assemble it (a hand-assembled version has measurably dropped the retry ladder, the missing-sibling logic, or split across two files that only one of which gets run).

```bash
team orchestrate-sh steps.json > orchestrate.sh
```

is the only command needed. Both phases land in this one file; dependencies for each `run_step` call are computed automatically from `../DIR/output.json` references in each step's own request text — nothing typed by hand, nothing split across two generated files (an earlier revision split into `generate.sh` + `orchestrate.sh`; if you see a template calling out to a separate `generate.sh`, that's the pre-fix shape — regenerate instead of patching).

The pool's own cross-process node locking makes concurrent `team do` processes safe — only real execution (`python3 main.py` against the live platform) must never run concurrently or unattended.

**Planning itself does not parallelize across the fleet — it always resolves to one box (`g26b`), locked, so parallel `team do` calls queue their planning one at a time** (fixed: `build_components`/`replan_component` claim the node for the call's duration, so they queue visibly rather than racing into a timeout). The parallelism gain is in writing/repair after each plan comes back, which does spread across all nodes.

**Ask for the entry point by its exact expected name (`main.py`) in the request text if you intend to run it right after — never assume `main.py` out of habit.** A request naming `client.py`/`create_project.py` and nothing else produces exactly those files; `python3 main.py` right after fails on a file nothing created. Say "the runnable entry point is main.py" explicitly. This is now also enforced mechanically: `team.py` reads a request's own literal "the runnable entry point is X" back after planning and renames whichever file the plan produced to match, before anything is written to disk.

**Skip the entry-point instruction entirely for a purely static-file capability** (e.g. `survey.html`, nothing else) — forcing one produces a pointless script whose only job is printing "file created." Also skip `--api NAME` on a purely static line — a request like "create a survey.html" run under `--api maven` anyway still runs the full slicing/CONTRACT pipeline and can invent an unrelated pipeline of Python files around a static form. (`intent.is_pure_static_file_request` now refuses to run the `--api` pipeline for this shape regardless.)

**A single-component `--api` plan with no explicit entry-point phrase in the request now defaults to `main.py` automatically** (every `--api` component is meant to run for real, so there's no legitimate case for a non-`main.py`-named single-file `--api` script) — this doesn't cover multi-component plans or non-`--api` requests, where a descriptive filename is still correct.

```bash
mkdir -p create_project && cd create_project
team do --api maven "create a project; save its id in a new output.json in the same directory as the script; the runnable entry point is main.py"
python3 main.py
cd ..
```

## Do not spawn subagents to do this. You have one of each model.

Run the several `team do` calls yourself, one after another, in one context — never fan out to subagents "in parallel." This hardware holds exactly one instance of each model at a time; every subagent's `team.py do` call queues for the same three boxes regardless, so N subagents finish in about the time of N sequential calls plus coordination overhead — and can be worse if a subagent's own reasoning calls back to whichever model is acting as your brain and it happens to share a box with a `team.py` worker (reload-thrash).

## What changes

`AGENTS.md` weighs whether delegating is worth the token overhead for a paying frontier model. That arithmetic doesn't apply to you — your tokens and the pool's are both free. **Delegate freely and stop calculating.** The only cost is wall-clock time.

## What gets harder

You lose the thing that made a frontier model's involvement worth its cost: knowing what "correct" means well enough to write a gate a plausible-looking wrong answer fails. Models on this network are measurably bad at inventing gates — six of six model-written invariant tests rejected code that was correct in one measured batch; one demanded a two-arg function be callable with one argument and a list-returner return a string, and a worker burned nine minutes trying to satisfy an unsatisfiable test. Assume any test you write yourself is more likely wrong than the code it's judging.

## Gates you can trust, in order of preference

**1. Run the program.** No insight required:

```bash
team do --check "python3 thing.py --help" "build thing.py that ..."
```

Executing code observes behavior, not shape — a dict mutated during iteration crashes on the 4th insert and no amount of reading catches it; running does, instantly. Applied automatically to anything with a `__main__` block even with no `--check` given.

**2. Compare against something that already exists** (a reimplementation, a port, an optimization target):

```bash
team do --check "python3 -c \"import new, old; assert all(new.f(x) == old.f(x) for x in range(200))\"" "..."
```

**3. Properties the request stated outright** — turn "must never return duplicates"/"output must be sorted" directly into an assertion. Transcription, not invention, so it's safe.

**4. Invent invariants yourself. Last resort.** Validate against a stub returning `None` everywhere and confirm it fails — a test passing an empty implementation tests nothing. Never validate by writing correct code and checking it passes — that's doing the task twice.

## When the code comes back rejected

If two separately generated candidates fail the same assertion, the assertion is the more likely problem — drop it, fall back to running the file. This is the measured failure mode of this network, not a rule of thumb.

## What to do about hard problems

You are not the strongest model here (`g26b`'s 12b runs ~42 tok/s, its 26b ~13 tok/s, both more capable than the light boxes). Hand anything genuinely difficult to the pool at floor 4:

```bash
team do --check "..." "the hard thing"
```

State the problem clearly, supply a gate, read the result.

## Everything else

`AGENTS.md`'s operational rules still hold and aren't repeated here: keep `num_ctx` constant or pay a ~35s reload per call, reasoning off for code generation, check for orphaned clients before blaming a quiet node, `mac` orchestrates rather than works. Read that file for those.
