# Running this without a frontier model

Commands only. `AGENTS.md` explains why any of it is shaped this way; this is what to type. Everything is standard-library Python 3.9+, no install step.

The one thing no local model does for you is decide what "correct" means. That
is the `--check`, and it is the whole of your job here. Everything else --
planning, writing, repairing, picking tools -- runs on the pool for free.

---

## 0. Once, so the commands work anywhere

The point of this is to run it *in the project you are working on*, which is
never this repo. Link the four entry points onto your `PATH`:

```bash
REPO=~/Documents/"Local LLMs"/begin
mkdir -p ~/.local/bin
for f in team swarm openapi tools; do
  chmod +x "$REPO/$f.py"
  ln -sf "$REPO/$f.py" ~/.local/bin/$f
done
```

Check it took, from somewhere else entirely:

```bash
cd /tmp && swarm nodes
```

Symlinks rather than aliases, because an alias only exists in an interactive
shell -- scripts, `cron` and anything else calling `team` would not see it.
The scripts resolve their own real location, so config, sessions, backups and
registered API specs all keep pointing at the repo no matter where you run
from. Every command below assumes this is done.

---

## 1. Is the pool alive

```bash
swarm nodes          # health, models, and measured speed per box
team warm            # preload every node in parallel
```

Three boxes reporting DOWN at once is your network, not three failures.

---



## 2. The daily loop

```bash
cd ~/projects/thing                              # one directory per project
team do "build a CSV summariser with a --json flag"
```

Then look at what happened, then ask for the next thing. Follow-up requests in
the **same directory** automatically get the last 20 requests, the current
files and what they define, and — for any file being edited — that file's real
content. You do not need to re-explain the project.

```bash
team do "add a --sort flag to the summariser"
team do "the --json output should include row counts"
```

Useful flags on `do`:

```bash
--check "python3 check.py"   # what correctness means (see §3)
--attach design.md           # reference files to build against (see below)
--dry-run                    # show the plan and predicted timing, run nothing
--paths a.py b.py            # name the files if you already know them
--kind code|audit|ask        # override the triage if it guessed wrong
--no-ground                  # don't read your files locally to place a vague
                             #   request ("make it faster") on a real line
--retry                      # redo only what failed last time (see §5)
--retry --file f.py --error "..."   # fix f.py using an error YOU hit
                             #   running it, not one a gate caught (§5)
```



### Attaching reference files

Instead of pasting a design into the request, attach it. Any file type — a
design note, a schema, a wire format, an example of the output you want.

```bash
team do --attach design.md "implement the encoder"
team do --attach design.md schema.json notes.txt "..."   # several at once
team do "now add the decoder"       # still attached, nothing to retype
team do --attach                    # no files given: detach everything
```

It sticks to the directory until you change it, and every run prints what it
is carrying, so it never acts on you invisibly:

```
attached:
  design.md                                ~10 tokens
```

Two things worth knowing. The files are **re-read every run**, so editing the
design takes effect on the next request without re-attaching. And `--attach`
**states the whole set** rather than adding to it — listing one file replaces
whatever was there.

If the set is too big it is refused before anything runs, naming the cost and
the limit, and your previous attachments stay. Every writer carries the
attachment, so it cannot be given to only some of them; the ceiling is half of
the narrowest window in the fleet.

### Attaching a big API spec

An OpenAPI document is the one thing that reliably blows that ceiling. The
spec this was built against is 291 operations and 111,653 tokens against a
16k budget, so attaching it whole just gets refused.

Register it once, by name, and it works from any directory:

```bash
openapi --add maven https://host/openapi.json   # fetch and remember
openapi --list                                  # what is registered
openapi maven --refresh                         # re-pull, same URL
```

Then just name it on the request. No separate slicing step:

```bash
team do --api maven "write a python client that creates users"
```

It prints what it picked, and you should read that line:

```
api maven: 12 of 291 operations, 6739 tokens
    POST   /api/teams/{team_id}/members
    POST   /api/auth/users
```

The slice carries **full detail for the endpoints that matched**, with every
schema they reference resolved and pasted in, plus a **listing of the other
endpoints in the same areas** so the pool knows what else is there and asks
rather than inventing. It is not the whole catalogue — that costs 5,701
tokens on this spec, a third of the budget, mostly listing things the request
will never touch — and the listing says outright that other areas exist.

Selection is term matching plus a read of the full index by the local
`gemma4:e4b`, which takes a few seconds and catches what term matching
cannot. "Sign in with an email and password" shares no word with
`/api/auth/login`, so scoring alone missed it entirely and matched
`forgot-password` instead. Anything the model suggests is checked against
the real endpoint list first, so it cannot invent one. `--no-ground` turns
that off.

If it picks wrong, say so directly instead of rewording the request:

```bash
team do --api maven --api-limit 20 "..."      # more endpoints in full
openapi maven "..." --tag Teams --path /api/auth/api-key --out api.md
openapi maven "..." --whole-index --out api.md   # list every endpoint
openapi maven "..." --no-index --out api.md      # drop the listing entirely
```

Those last ones write a file you can read before use, then attach by hand
with `--attach api.md`. Worth doing the first time against a new spec.

If nothing matches it warns and still hands over the listing, so the run is
thin rather than silently wrong.

#### Generated code is run, but it cannot reach your platform

The gates execute what the pool writes — importing it and running `main()`
is the only automatic check that catches a real bug rather than a shape. For
an API client that would mean POSTing to Maven while merely being tested, so
the gates are confined to loopback. A stray call fails with:

```
OSError: network blocked while testing: tried to reach 'dztfdhkmj6wqi.cloudfront.net'.
```

With `--api`, a **stand-in for your API is started automatically**, built
from the spec itself, and the writers are told to read `API_BASE_URL` from
the environment. So the code runs all the way through and you get told what
it sent:

```
what the code sent to the mock API:
    ok   POST   /api/applets                    201
    FAIL POST   /api/projects/create            404  no such endpoint
    FAIL POST   /api/projects                   422  missing required field 'name'
```

That is worth more than "3/3 passed": a 404 means it invented an endpoint, a
422 means the real API would have rejected the body. It does not fail the
run — a client need not call anything from `main()` — so read the lines.

The mock keeps no state and invents no behaviour; it answers with the shape
the spec promises. Nothing proves the real thing works except running it
yourself, which is the intended last step:

```bash
python3 push_survey.py        # your terminal, your credentials, real API
```

`--no-mock` turns the stand-in off. `SWARM_ALLOW_NETWORK=1` lifts the block
entirely, which you should not need.

#### It tells you how to run what it wrote

Every run ends with the invocation, read off the generated file itself:

```
how to run this:
  export PROJECT_ID=...        # required, not set
  # already set: API_KEY
  # optional: API_BASE_URL (defaults to https://api.example)
  python3 create_quiz.py --project ... [--dry-run] OUTFILE
```

Anything marked "required, not set" is a variable the script reads and your
shell does not have. Export it in the terminal you will run from — and note
that `export API_KEY="$MAVEN_API_KEY"` only works if it comes *after* the
line defining `MAVEN_API_KEY` in your `~/.zshrc`, since the file runs top to
bottom.

#### Other file types

HTML, CSS, JavaScript, JSON, Markdown, CSV and SQL can be generated
alongside Python. Each gets the check that suits it — JSON is parsed, HTML
must nest correctly, JavaScript goes through `node --check`, including
inline `<script>` inside a page. CSS and prose are only checked for being
non-empty.

Anything else is refused out loud rather than written unchecked:

```
note: settings.yaml not generated -- no gate exists for that file type.
      Writable: .cjs .css .csv .htm .html .js .json .md .mjs .py .sql .txt
```

#### When a lot of endpoints are relevant

Full detail is expensive — parameters, request body, responses and every
schema they reach. Measured on the 291-operation spec, roughly **20 endpoints
is the ceiling**; past that the slice is refused rather than truncated:

```
api slice refused: attachments total ~21305 tokens, over the 16384-token limit
  try a smaller --api-limit (currently 30)
```

Raising `--api-limit` is the wrong first move. Splitting the request is
better, and not just because it fits — measured on "build a full client for
projects, tasks, units and submissions":

| | endpoints covered | largest call |
|---|---|---|
| one call, `--api-limit 20` | 20 | 15,191 tokens, near the ceiling |
| three focused calls, default limit | **34** | 9,385 tokens |

More coverage, no refusals, and each slice is actually about the thing being
built rather than a broad average of four areas.

```bash
team do --api maven "a client for creating and listing projects"
team do --api maven "add tasks to a project and import their units"
team do --api maven "claim a unit, submit it, and read the submission back"
```

Consecutive calls in the same directory are not starting over. The history
carries the earlier requests and the files they produced, so the later ones
extend `client.py` instead of inventing `client2.py`. That is the same
mechanism behind "now make it faster": every run in a directory records
what it did, and the next one reads it.

If it really must be one call, `--api-limit 20` plus `--no-index` on a
hand-made slice buys about 3,800 more tokens:

```bash
openapi maven "..." --limit 25 --no-index --out api.md
team do --attach api.md "write the client"
```

That drops the listing of neighbouring endpoints, so the pool loses its
ability to notice an endpoint it needs but did not get. Worth it only when
you already know the full set.

### API keys: `.env`

Put a `.env` in the project. The pool is told the variable **names** and never
the values.

```bash
cat .env
# STRIPE_API_KEY=sk_live_...
# AWS_ACCESS_KEY_ID=AKIA...

team do "upload the report to S3"
```

```
env vars (names only, values never read): STRIPE_API_KEY, AWS_ACCESS_KEY_ID
```

Generated code then reads `os.environ["STRIPE_API_KEY"]` by the correct name
instead of inventing `STRIPE_KEY` or writing a fake literal. The values are
never read off disk, so they cannot reach a prompt, a log, or the session
history.

Nothing to configure, and nothing you can get wrong by accident: `.env`,
`.env.*`, `*.pem`, `*.key`, `credentials` and `id_rsa` are all refused if you
try to `--attach` them. You still want `.env` in `.gitignore`.

---



## 3. Checks — your half of the work

Pick the **cheapest** rung that would catch the mistake you actually fear. If
you cannot name the mistake, you are on too high a rung.

```bash
# Rung 1: just run it. A __main__ block already runs automatically and an
# unhandled traceback fails the file, so this is often enough.
team do --check "python3 thing.py --help" "..."

# Rung 2: compare against something that already works (a rewrite or a port).
team do --check "python3 compare_old_new.py" "..."

# Rung 3: assert what the request already said. Transcription, not invention.
team do --check "python3 check.py" "output must be sorted, no duplicates"
```

A check does not require solving the problem. To judge a minimal edit script,
replay it and compare, then compute an LCS *length* — that skips the
backtracking that makes producing one hard. Write the easy half to judge the
hard half.

Sanity-check your check before trusting it:

```bash
# point it at a stub whose functions return None; it MUST fail, and fail with
# an assertion about the code, not a TypeError about your harness
python3 check.py
```

If you cannot write a check without solving the problem first, your request
probably does not pin the answer down. Better to learn that now.

**Do not use** `--tests`**.** It has a model write the tests, measured six for six
wrong across two runs while consuming 41% of the pool's compute.

---



## 4. Getting a file back

Every replaced file is archived forever, outside the project, and never
pruned. No commit required.

```bash
ls .swarm/backups/                                   # find your project
ls .swarm/backups/thing-a1b2c3d4/                    # newest last
cp .swarm/backups/thing-a1b2c3d4/20260913-185237.thing.py thing.py
```

Worth knowing: "passed all gates" means the new file parses, imports and
satisfies your check. It does **not** mean the rewrite kept everything the old
file did. Files are replaced whole, so if you care, diff against the backup.

```bash
diff .swarm/backups/thing-a1b2c3d4/20260913-185237.thing.py thing.py
```

---



## 5. When something fails

```bash
team do --retry                # rewrite only the failed components,
                                    # feeding each the error it died on;
                                    # keeps everything that already passed
```

If a component is **refused** as too large, the file cannot round-trip (it has
to fit in the prompt and come back as the answer). Split it, or target a
smaller file. That refusal is deliberate — the alternative was silently
dropping everything past the cutoff.

**A gate passing does not mean the file is right against the real world.**
The gates only ever ran against a mock and typed/placeholder input — an
error from the real API, or from real input you actually typed, is
something the gates structurally cannot see, so a plain `--retry` reports
"nothing failed" and does nothing. Hand that error over directly instead:

```bash
team do --retry --file main.py --error "AttributeError: 'NoneType' object has no attribute 'id'"
team do --retry --file main.py --error @traceback.txt   # @path reads a file --
                                                          # paste a whole log without quoting it
```

This puts `main.py` back through the repair loop with that exact error and
the file's current content (so it fixes the one thing that was wrong
instead of rewriting from scratch), while everything else stays as it was.

---



## 6. Running actions instead of writing code

The network proposes a tool call; your machine validates and runs it. The model
never holds credentials and cannot execute anything.

```bash
tools --list                                  # what exists, and its policy
tools --dry-run "show me what changed"        # propose, print, never run
tools "find every call to parse_row in python files"
```

There is deliberately no shell tool. Anything with side effects prints the
exact call and waits for you; `--yes` skips that prompt but never skips
validation. To let it run your check, put the command in `.swarm-check` in the
project root:

```bash
echo "python3 check.py" > .swarm-check
tools "run the check and show me the output"
```

---



## 7. One-off questions and second opinions

```bash
swarm ask "what does a bare except do to KeyboardInterrupt?"
swarm route "..."               # show which box would take it, run nothing
team crosscheck "does this migration plan drop rows?"
```

Use `crosscheck` only where running the code cannot settle it — design calls,
"will this lose data", one service or two. For anything executable the
interpreter beats any number of agreeing models.

`audit` and `review` exist but are off the daily path on purpose: measured at
three quarters of a run's wall clock, confirming two findings that were both
wrong. The free static detectors run either way. If you want more assurance,
write a better check.

---



## 8. When a box goes quiet

In order, cheapest first:

```bash
swarm nodes                                     # read the actual error
ping -c 2 10.0.0.130                                 # is the machine there
curl -s http://10.0.0.130:11434/api/tags             # is ollama answering
curl -s http://10.0.0.130:11434/api/ps               # is it busy generating
```

Give it time to load before calling it dead — a cold load is 30-40s, and a
short timeout makes a healthy box look wedged because Ollama abandons an
interrupted load and restarts from scratch:

```bash
curl -s -m 240 http://10.0.0.130:11434/api/chat \
  -d '{"model":"gemma4-e4b-fixed:latest","messages":[{"role":"user","content":"say ok"}],"stream":false,"options":{"num_predict":4}}' \
  | python3 -c 'import sys,json;r=json.load(sys.stdin);print("load %.1fs total %.1fs"%(r["load_duration"]/1e9,r["total_duration"]/1e9))'
```

If `/api/tags` is instant but `/api/chat` never returns *after* being given
time, it is occupied, not slow. Check for an orphan of your own first — killing
a backgrounded shell kills the wrapper, not the Python underneath:

```bash
ps -eo pid,etime,command | grep -E "team\.py|swarm\.py" | grep -v grep
```

An orphan and a fresh request evict each other's model on every call, which
looks exactly like a hung GPU and is not. Kill the orphan by PID; restarting
Ollama does not help, because the orphan just loads its model again.

On the box itself:

```bash
sudo systemctl restart ollama        # linux; do NOT also run `ollama serve`
```

`ollama serve` reporting "address already in use" means it is already running.
That is not an error to fix.

---



## 9. Traps that have cost real hours

**Make sure your check can actually pass.** `python3 pkg/test.py` puts `pkg/`
on `sys.path`, not the working directory, so `from pkg.mod import x` fails no
matter what was generated. An unsatisfiable gate rejects correct code and sends
the repair loop chasing an error that was never in the file.

**Do not run models on this Mac.** It has 16GB of unified memory shared with
your editor, and a resident model competes with it directly. Measured: a hard
freeze requiring a power cycle. `mac` is `"enabled": false` for this reason.
The only thing that runs there is the grounding pass (`--no-ground` disables
it), which asks for a small window on purpose.

**Keep** `num_ctx` **constant in your own loops.** Ollama reloads the model
whenever it changes, at 30-40s a reload against ~3s of actual compute. `team.py run` pins one size per node for a whole run; fixing this alone took an
identical plan from 4m47s to 1m34s.

**More output tokens is not a quality dial.** It bounds emission, not thinking,
and reasoning shares that budget with the answer — a model that reasons past it
emits nothing at all and looks incapable. This is why writers run with
reasoning off by default.

---



## 10. Checking the pool's own code still works

```bash
python3 run_tests.py              # all 23, about 8 seconds
python3 run_tests.py attach       # just the ones matching "attach"
```

Entirely offline — the pool is stubbed out, so this works with every box
asleep and a failure always means the code changed rather than the network
moved.

Use the runner, not `for t in *_test.py`. Generated packages land at the
project root, and some of them open a pygame window or dial the LAN, so a
glob eventually blocks forever on one of them. The runner has an explicit list
and a per-test timeout, so a hang is one `HUNG` line instead of a dead shell.

---



## 11. Timing and calibration

```bash
team calibrate                  # predicted vs actual on every node
team run plan.json --dry-run    # Gantt chart and predicted makespan
swarm bench                     # re-measure throughput, feed the router
```

Predictions land at about 1.04x actual when warm. If they drift badly, suspect
model reloads before suspecting the cost model.