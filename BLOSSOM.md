# Blossom

Blossom is a data-generation framework. You give it the high-level objective of a project and guidelines for what a good output looks like, and it produces a large set of outputs that follow that theme, differ from each other in designed ways, and can be sorted along axes of quality.

It works by four ideas. The writer is told the **purpose** as well as the rules, so it aims at the goal and not at the edge of a number. Blossom works out the **axes of quality** along which outputs can differ: some climb from simple to demanding, some are different kinds. Each output gets its own position on those axes, written as a one-line **seed** (the brief) that steers the prompt, so a model asked 40 times does not give 40 copies. And because every output has a known position, the finished set is **sortable**, for example from easy to hard, by measurement and not by guess.

Every output is tested by the check. A good one is kept, a near-miss is repaired from the exact line that failed, and a bad one is discarded. One function is called on many inputs, the collection is the work, each member has to be valid, and the set has its own properties (order, variety, coverage). A local model writes the values. The check decides which ones stay.

The platformer under `demo_platformer/` is one dataset of this shape. The same loop is for any record shape.

## What you write

Two things.

1. A check program. Exit 0 when the dataset is acceptable. Exit nonzero otherwise, and print one fact per line.
2. A request, in plain language, naming the collection and the file that holds it. The request does not list the values.

You do not write the values, and you do not write a generator that invents them. Blossom writes a lookup that returns each stored value unchanged, then fills the slots until the check passes.

## Where a rule has to live

The request is repeated every round. The check output is what the writer repairs against. A rule that exists only in the request is a wish. A rule the check prints is a gate.

Put a guideline in the check the same way you put a hard rule: as a line the program prints when the guideline is missed. The writer is shown those lines, and a short description parsed out of the check:

```
CONTAINER: what one call returns
COUNT: the fewest parts that one return value may hold
PART: what one part is, and the smallest it may be
RULES: the constraints the check states, copied from the check
```

That description is cached in `.ralph/stored.json` and reused. The whole check text is also shown to the writer in every ask (see "Check context" below). When the check and the description disagree, the check is the one that keeps or drops a value.

Write the failure so a later attempt can see what moved:

```
CHECK FAILED: record 4 has 2 fields, shorter than 8
CHECK FAILED: record 4 is missing a title
CHECK FAILED: record 4 does not balance
```

Prefix the line with `CHECK FAILED:`. Ralph stores those lines in `.ralph/last-check.txt`. Any other non-empty line is still a failure the writer sees, but the prefix is what the loop files between rounds.

## Sizes are floors

Fail a size when it is under the minimum. A longer row, a longer record, or an extra member is a value you can drop later. It is not a failed value.

```
# under the floor: fail
if len(record) < 8:
    fail("record %d has %d fields, shorter than 8" % (i, len(record)))

# over the floor: leave it
```

Use the words `shorter than` when the first number is what you measured and the second is the minimum. `3, not 5` is also read as a distance. A smaller gap is progress. A larger gap is not. A sentence with no pair of numbers is judged only by whether the sentence changed.

An exact length, an exact count, or a maximum will stall a model that does not emit the same string twice. Require what the data needs in order to function: at least this many parts, at least this long, at least one of a required member.

## One line, one fact

The loop diffs the check output. A line that was there before this candidate and is still there belongs to some other value, or to the set. A line that appears with this candidate is this candidate's failure. A line that disappears was resolved.

So:

- Name the input in the line. `record 4 ...` can be told apart from `record 5 ...`.
- Keep each fact on its own line. A paragraph that mixes three failures changes as a blob, and a real improvement looks like a new failure.
- Say the number you measured and the minimum. `bad record` gives the writer nothing to move toward.

## Say why, and how far

A verdict names a rule. A good line also gives the cause the check already knows, and a distance the loop can compare.

```
CHECK FAILED: record 4 is not reachable
CHECK FAILED: record 4 is not reachable. 17 of its 40 steps can be taken, and the chain stops at step 9, which needs field `owner`. Steps left to the end: 31, not 0.
```

Only the check can write this, because only the check knows what "reachable" means. The pipeline does not read the rule and does not guess at it. When a check keeps failing with the same verdict, the fix is usually a better line, not more retries.

- Name what the check measured, in the value's own terms: where it stopped, which part was short, what was missing under what.
- End with a distance in the form `N, not M`. The loop keeps the reply with the smaller distance and drops the one with the larger. A verdict with no number cannot be told closer from farther.
- Report the cause when two parts must agree. `G is not standing` is a verdict. `row 1 has length 60, so it has no cell under column 63` is a cause.
- Keep it one line, with the input named.

The writer for the slot being filled is shown the lines that name that slot. Lines about later empty slots are left out of that prompt, so one reply is not asked to satisfy the whole suite and does not come back as a stub.

## Different items must differ

Sampling does not make two near-identical asks produce different values. A model that is shown an accepted value tends to return it. Two things keep a dataset from filling with copies:

- Fail a copy as a member line, naming the earlier item: `record 6 is the same as record 5`. A set line such as "at least 15 distinct in 20" only fires at the end, and members that were kept as copies cannot be revisited cheaply.
- Blossom asks the model once for a one-line brief per input and puts each brief in that input's ask. A brief is a direction. It is not a requirement, and the check decides. Briefs live in `.ralph/stored.json` under `briefs`.

If the dataset is the product, ask for a larger spread in the check, not in the pipeline.

## The set is its own lines

Some facts are about the collection, not about one member.

```
CHECK FAILED: fewer than 15 distinct records in the first 20
CHECK FAILED: record 3 is easier than record 2
```

A set line that is already failing before this candidate is not blamed on the candidate. Members can be kept while the set line is still failing. When the members are in place, Blossom asks for an order of the kept indexes if the remaining lines are about the set.

A member line should be satisfiable by that member alone. A second call that demands a different exact shape from the same stored value will not be fixed by resizing. The lookup returns the value it stored. Write the second call as a floor the larger value already meets, or as a property of the value rather than a second grid size.

## Missing slots

A slot that has not been written yet must make the check fail, and must not throw. The lookup returns an empty value for an absent index (an empty list, or null). Null is often reported by a check as an object. Fail that case with a normal line:

```
CHECK FAILED: record 4 returned 0 rows, shorter than 8
```

Throwing skips the rest of the suite and the diff no longer means anything.

The number of values Blossom keeps is how many times the collection loop runs. A loop `i < 40` from 0 is 40 values. That count is the loop, not a width, a height, or a field inside one record.

## The request

Say what is being collected, which function the check calls, and which file should hold the values. One collection is one run. A request that names a chain of different jobs collapses into one file.

```
Store the records generateRecord(i) returns, for every i the check loops over, in records.js.
```

The shape question is answered from the check. Stored is the right shape when one function is called on many inputs and the hard part is the collection. A single artifact, or a function that solves each input on its own with no property of the set, stays a script.

## Run it

From the project directory, so the check's own paths resolve:

```bash
env -u RALPH_TEAM "/absolute/path/to/begin/ralph.sh" \
  --check "node check.js" \
  "Store the records generateRecord(i) returns, for every i the check loops over, in records.js."
```

`RALPH_TEAM` is how tests stub the team. Leave it unset on a real run.

Ralph repeats that same request. Each round starts by running the check. Exit 0 ends the loop. A failing check is written to `ralph.log` and to `.ralph/last-check.txt`, then `team.py do` runs once. Progress is whatever that round left on disk. `team.py` exiting 0 only means the round finished. Ralph is done when a later round's opening check exits 0.

`-n 20` is an optional fuse. Without it the loop runs until the check passes.

A new Ralph truncates `ralph.log`. `.ralph/stored.json` and the output file are kept, so a new process resumes the set. One Ralph at a time. A second one shares the same file and the same model.

A change to `team.py` or `intent.py` is loaded by the next `team.py` process, which is the next round. The round already running keeps the code it started with.

## What a round is doing

The log lines that matter:

| line | meaning |
|---|---|
| `build shape: stored records.js x 40` | this file, this many values, and the shape will not be re-planned |
| `stored value is:` | the cached description of one value |
| `kept an output (4 of 40)` | this value added no failure lines |
| `closer value stays in the file` | the check moved toward passing; the value is installed and not counted yet |
| `reply is not further; the best value stays` | this reply was rejected. The draft is kept. The reply is saved in `.ralph/attempts.jsonl` |
| `failed reply (why):` | the reply, with the check lines it produced, so a miss can be read from the log |
| `asking again past the same failure` | the next ask names what the last reply missed |
| `edit was not a value (SyntaxError ...)` | the reply did not parse as one literal, so the check never saw it |
| `reply was cut off at N tokens` | the reply hit the ceiling. The next ask allows more, up to what the window leaves |
| `wrote N briefs, one per input` | the model wrote one direction per input, kept in `stored.json` |
| `kept value K is rejected by the check now` | a kept value started failing (a copy, or a rule added later). It returns to the draft and the values after it are dropped |
| `stored outputs pass the check` | the check exited 0 |
| `ralph: done after N round(s)` | the opening check of a round exited 0 |

Kept values are appended. A closer value is served by the lookup so the next check shows its real failure, and it becomes kept only when it introduces no lines. A reply is judged by the earliest gate it fails: a later gate is further, the same gate is further only when its `N, not M` distance shrank, and an earlier gate is not. The best draft stays on disk across tries and rounds. Nothing wipes it.

The model that writes values is the floor-4 model, with thinking off. Thinking on this model loops (its trace repeats one pattern until the cap) and costs minutes a try. Sampling is the node's own (`sample` in `config.json`), plus a fresh seed per call. The runner can still return the same text from the same ask, so the ask changes instead:

- It carries the check lines the draft failed, one accepted value as a passing example, and the measured shape of recent accepted values (counts and lengths).
- It lists up to three replies already tried, each as a change against the held value, with how many times it came back.
- It carries that input's own brief, written once by the model.

The reply ceiling starts at 4096 tokens, to end a reply that loops. A reply cut off at the ceiling doubles it for the next try, up to what the pinned window leaves after the prompt. The window is not raised.

## Files

| path | role |
|---|---|
| the check program | the spec |
| the output file named in the shape | the lookup plus the values kept so far |
| `.ralph/stored.json` | template, count, element description, briefs, kept values, the current draft |
| `.ralph/attempts.jsonl` | every rejected reply in full: the ask it answered, the check lines it produced, why it was rejected |
| `.ralph/thoughts.jsonl` | reasoning traces, written only on a call that has thinking on |
| `.ralph/removed.jsonl` | every kept value that left the set, with the time. Written before the drop, never pruned |
| `.ralph/last-check.txt` | the failure lines from the last round |
| `ralph.log` | the whole run, truncated when a new Ralph starts |

Deleting `.ralph/stored.json` starts the set over. Leaving it resumes. The description cache is reused until that key is gone.

## A small check

```python
import json, sys

failed = False

def fail(msg):
    global failed
    failed = True
    print("CHECK FAILED:", msg)

records = json.load(open("records.json"))
for i in range(40):
    row = records[i] if i < len(records) else None
    if not isinstance(row, dict):
        fail("record %d returned 0 fields, shorter than 8" % i)
        continue
    if len(row) < 8:
        fail("record %d has %d fields, shorter than 8" % (i, len(row)))
        continue
    if "title" not in row or not str(row["title"]).strip():
        fail("record %d is missing a title" % i)

titles = [r.get("title") for r in records[:20] if isinstance(r, dict)]
if len(set(titles)) < 15:
    fail("fewer than 15 distinct titles in the first 20")

sys.exit(1 if failed else 0)
```

The failure lines are the contract. Exit 0 only when none of them printed.

## Editing a check mid-run

A check line is compared by who it names and what it says. The input number in the first parentheses is the owner; every other number is a measurement. A set-level line whose measurement moves ("the first 20 seeds give 5 different levels") is the same line, not one a kept value introduced. Editing a message's numbers therefore never drops kept values. If a check change does make kept values fail for real, they are rewound and written to `removed.jsonl` first.

## Order is placement, not rejection

A line naming two or more inputs describes how they sit together ("seed 9 is easier than seed 8"). When a new value fails only on such lines, the pipeline tries each position from the end backwards and keeps the value at the first position where the check adds no line (`valid, but not in order ...; placed at position N`). The check is the ranker, so the pipeline needs no score of its own. A line that no position clears (a duplicate) still rejects the value.

## A duplicate rewrites the brief

When a reply is exactly a value already kept for another input, the pipeline reads the two inputs' briefs side by side and asks for a replacement brief for the second that names something none of the briefs name. The new brief replaces the old one in `stored.json`, and the next ask carries it (`reply equals input N; this input's brief now reads: ...`). The duplicate is compared by value, so it needs no wording from the check.

## Check context: what the writer is shown, and how to provide it

**What happens.** Every ask carries the whole check script between `=== CHECK ===` and `=== END CHECK ===`, ahead of the failing lines. The check is the guideline document. The failing lines say what broke; the check says what the rules are. The model-written shape description (`CONTAINER / COUNT / PART / RULES`) is a fallback line at the end of the ask, and it loses to the check wherever they disagree.

**Why it matters.** A writer told only which line failed learns the rules one rejection at a time, and it never learns a rule it does not trip over. Measured on the platformer:

- The ask carried only failing lines and "minimum shape: an array of strings." Nothing said the grid needs open air, that `S` and `G` stand on `#`, or what a step is.
- The first check let a jump pass through solid cells. The writer found that: every one of 40 levels was solid `#`, `S` on top, `G` buried inside. All 40 passed, and the game could not be played. The check is the spec, so a hole in the check becomes the dataset.
- A guideline that is only in the request is a wish; a guideline only in the check text is read; a guideline in the check that prints a line when missed is a gate. Do both of the last two.

**How to provide it.**

1. The check must be a script file named as a token in `--check`, and it must exist relative to the project directory (`--check "node check_game.js"`, `--check "python3 test_x.py"`). Tokens that are files are read, and nothing else is. `python3 -m pkg.test`, `bash -c "..."`, and a check that `require`s its rules from another file show the writer nothing of those rules. Name the other file as a token too (`--check "node check.js rules.js"` if the check ignores extra arguments), or keep the rules in the one script.
2. Write the rules the way you would for a colleague. A comment above each gate saying what it demands in plain words (`/** A level is mostly platforms with open air between them. */`) is read by the writer verbatim.
3. Mark what the writer should read. Put `WRITER-CONTEXT-BEGIN` and `WRITER-CONTEXT-END` in comment lines around the rules for one value (several regions are joined). With no markers the whole script is shown. A check also holds a simulation, a page test, a scoring routine and diagnostics code, none of which a writer can use, and they bury the rules (the platformer's check went from 19,400 to 6,800 characters). Whatever is shown is cut at 24000 characters from the end, and the cut is stated in the ask. Comments inside a region are read verbatim, so say there what the pipeline handles outside the writer (here: the order of seeds).
4. Keep gate order the order a value has to pass them. The pipeline ranks "closer" by the earliest gate, read off the same text.
5. Every printed line starts `generateLevel(N)` (the input number in the first parentheses), then what was measured, then what to change. See "Say why, and how far."
6. Test the check against the worst value you can imagine before the run: an all-`#` grid, an empty one, a copy of the example. If a degenerate value passes, the check has a hole and the model will find it.

**Editing the check mid-run.** The check is re-read for every ask and every round, so an edit applies at once and the whole new text reaches the writer. A change that makes kept values fail drops them, and they go to `.ralph/removed.jsonl` first. A moving measurement in a line is not a change (see "Editing a check mid-run").

## Ask order, and briefs that can be drawn

The task comes first, the reference last. An ask reads: format rule; `You are writing the value for input N. What it has to show: <brief>`; the failing line (once); accepted inputs; the held attempt and diffs of the rejected ones; the whole check; a closing line repeating the brief. Measured on the platformer: a 28K-character ask with the brief as one line in the middle produced the held grid with minor differences every time, because everything else in it was repair material. A failed attempt is shown as something not to return, not as a starting point.

A brief has to be drawable: a count, a placement, or a repeating pattern ("three platforms arranged as a descending staircase"), never an adjective ("a complex structure"). `BRIEFS_PROMPT` says so. To rewrite briefs, remove `briefs` from `.ralph/stored.json` with no round running; the next round writes them again.


## Specify, then draw: `compose.json`

Some values are easy to specify and hard to draw. Measured: asked for a 20x60 character grid, the writer produced plans that were sound (a staircase of 3-row rises) and grids that were not (ragged rows, solid and air swapped, a goal buried in rock, replies running past 1,000 rows). Asked for platform coordinates and nothing else, the same model answered in about 330 tokens and 2 of 4 drawn grids passed the check on the first reply.

When the project root holds `compose.json`, `{"command": "node compose.js"}`, the writer replies with a spec and the command draws the value:

- `<command> --describe` prints what the writer is told about the spec: the format, the limits, the reply form. In this mode that text is the writer's guideline, since the writer never sees the grid format.
- `<command>` reads the spec on stdin and prints the value literal on stdout. A spec it cannot draw exits non-zero with the reason on stdout or stderr; the reason is shown to the writer.
- The check judges the drawn value, as always. A rejected value is shown to the writer with the spec it wrote and the drawing, and the writer replies with a corrected spec.

The drawing step does the mechanical parts (equal row lengths, an `S` and `G` that stand on `#`) so none of them can fail. It does not repair a route or choose a design; those stay the writer's, and the check's. This is not extraction from a malformed reply: the spec is the value the writer was asked for, and an unparseable spec is rejected like any other bad reply.

`compose.json` is read at the start of each round. Remove it and the next round asks for the literal again.

### A new gate ships with its guideline

A rule added to the check is also added to what the writer reads before its first reply, in the same edit: the `WRITER-CONTEXT` region of the check, or the `--describe` text in compose mode. A gate the writer learns only by tripping it costs one rejected turn per rule, and the turn teaches nothing the first ask could not have. Put the numbers in both places and say in a comment that they change together. Measured: four design gates (platform count, run length, height spread, route jumps) were added to the check first; the first level asked after the edit failed all four before the writer had seen any of them.

### Compose mode: three things that must match the spec

- **Failing lines speak the writer's language.** A repair that names row strings or single cells is no use to a writer who gives platforms. The check reads `compose.json` and, when it exists, says "change the platform list" and names the platform, rise and gap that fail, not "replace string 17". Measured: with the grid advice left in, the writer was told to edit a string it never wrote and kept resubmitting the same spec.
- **A repair shows the spec that made the value.** `.ralph/specs.json` maps each drawn value to the spec it came from, so the writer is shown the spec of the value it is repairing, not the latest reply's.
- **Briefs follow the rules.** Briefs are written against the `--describe` text and rewritten when that text changes (`.ralph/briefs-basis.txt`). An old brief ("two parallel lines of '#'") that the new gates reject sends the writer straight into them.

## Part-level repair: what is generic and what the project supplies

A failing value is repaired one part at a time, not rewritten. A part is whatever the value is made of: the sentences of a paragraph, the lines of a text, the entries of a list. Edits are `REPLACE n value`, `ADD value`, `REMOVE n`. There is **no cap on the number of edits**; a paragraph can take dozens, one after another. Progress is what the check says: the targeted failing line is gone and no earlier gate regressed (or a "reaches X of Y" count rose).

**The framework (`team.py`, `surface.py`) names no task, no domain and no value format beyond the surface kinds below. Everything in this table is supplied by the project, and an orchestrator setting up a job has to decide each row together with the briefs and the check:**

| File in the project root | What it is | Why it belongs to the project | Without it |
|---|---|---|---|
| `surface.json` | The surface kind (`text` with a unit such as sentence or line; `json-list` for a list of parts) plus locators: regexes that map a failing check line to the part(s) it is about | Only the check's author knows which words of a failing line name a part | No part repair; whole-value rewrite |
| `gates.json` | Regexes in the order the check RUNS its rules | "Further" means a later gate was reached; only the check knows its run order | Order falls back to where the failure's wording sits in the check's text |
| `analyze.json` `{"command": "..."}` | Reads the held value on stdin and prints a report of how it behaves now; the report is added to the repair ask. `{seed}` is the slot number | A reader of one failing line cannot see a property of the whole value; what to measure is domain knowledge | The ask has the failing line and the value only |
| `compose.json` | `command` (spec to value, above), and optional `clean`, `polish` (below) and a `--describe --seed N` form of the rules text | Only for values easier to specify than to write | The writer writes the literal |

**Writing a locator.** A locator turns one failing line into the parts it concerns. A surface example: for a list of parts where each part is a row and a column range, a line that quotes "row R columns A to B" locates every part with those numbers; a line about the whole value locates none, and the repair may then change any part. For a paragraph, a line that quotes "sentence 3" locates sentence 3.

**Deciding what goes in `analyze.json`.** When the same failing line is repaired many times without progress, read what the writer was shown and ask what it could not have known. If the answer is a fact about the value as a whole, print that fact as plain sentences: counts, directions, lengths, which parts the check flagged, or a drawing of the value when the value is spatial. Facts, not advice; short (cut at 3,000 characters); exit 0 only when there is something to say.

**`gates.json` follows the order the check RUNS its rules, not the order they are written in the file.** A rule written in a helper above the function that calls it sorts first by source position and runs later. The repair then targets the later rule for hours while the earlier one keeps failing. Verify with `team._gate_at` on a real failing output, and list every rule whose written order differs from its run order.

**`clean`** (in `compose.json`): the held spec on stdin, the same spec on stdout without what only crowds it (for example exact duplicate parts that earlier `ADD` edits left behind). The repair ask shows the cleaned spec. **`--describe --seed N`**: the rules text narrowed to that slot's band, so the ask does not carry bands that do not apply.

### Mechanical polish: the model designs, a program fixes the details (`"polish"` in `compose.json`)

A model makes one careful edit per call, and a check that fails many rules at once punishes it: each fix tends to break another. A blind search that asks the check to grade many small mutations at a time can be much faster at satisfying the rules, though it chooses nothing worth keeping. Measured once: two models made over a thousand edits on one slot with no pass, while a plain hill-climb on the same check found a passing value for it in 59 seconds. So polish keeps the model in front, for the design, and uses the search for the details.

The hook: `"polish": "<command> {seed}"`. The writer's reply goes in on stdin and the spec comes out on stdout (`{seed}` is the slot number). `team.py` runs it on every writer reply before the value is drawn and checked. It has no time budget: it runs until the check's rules all pass for the value, and when a walk stalls it returns to the writer's own spec so it does not drift away from the design. The real check still judges the result; a polish that prints something wrong is just a failed value. Each call appends `{failing_before, failing_after, cells_changed, secs}` to `.ralph/polish.jsonl`: how far the writer was from passing and how much the program had to change. Compare writers by those numbers.

**What is generic and what is not.** The hook (reply in, value out, run before the check) is generic. The polish command is NOT: it is written per task. An orchestrator setting up a task has to supply three things with the briefs and the check:
1. **Mutation operators for that value's parts**, small and meaning-preserving. For a text surface: reword, swap, split, delete or add a sentence. For a list surface: nudge, resize, drop or add an entry.
2. **A fast scorer** that returns how many rules each candidate fails, for a batch of candidates in one process. The fallback is the project's own check run once per candidate with its failure lines counted: generic, but too slow to search with unless the check is quick. Without a fast scorer, do not use polish.
3. **A distance measure** so the search prefers the fewest changes to the writer's design. Score = failing rules x a large weight + distance.

Without these the writer alone repairs. Polish makes a value valid, not good: a value the search finds from scratch can pass every rule and still be poor. Judge quality on `polish.jsonl` (a small `cells_changed`-style distance means the writer's own design survived) and by reading or playing the output.

### The graded scorer: how a search sees partial progress

A scorer that only counts failed rules cannot tell "9 of 10 offenders fixed" from "none fixed": both are one failed rule, so a search rejects the partial fix and wanders. Measured: a search that never finished on one value because clearing offenders one at a time never changed the count. The scorer therefore reports, for every failed rule, a **gap**: how far the value is from passing that rule.

**Protocol (task-neutral).** For a batch of candidates, the scorer returns for each candidate: the list of failed rules (the same text the check prints), a parallel list `gaps`, and any hard gate (the value does not parse, is not valid at all) as a separate flag. A gap is a number in (0, 1]:
- 1 means not even close, or the rule is all-or-nothing (a yes/no property). This is also the default when a rule reports none, so a scorer can be graded rule by rule.
- A small positive number means almost there. Never 0: a failed rule always costs something.
A rule that passes appears in neither list.

**How to compute a gap, by kind of rule** (this is the part the orchestrator writes per task; the check already measures the numbers it prints):
| Kind of rule | Gap |
|---|---|
| A floor ("at least N of X") | (N - have) / N |
| A ceiling ("at most N of X", or each X at most L) | (have - N) / N, or (longest - L) / L |
| Offenders to clear ("none of X may exist") | offenders / total parts |
| A share ("no more than P of the parts alike") | (share - P) / (1 - P) |
| A yes/no property | 1 |
| A measured quality that has to rise above the previous slot's | (previous - value) / a scale where one step is a clearly bigger change |
For text, an example: "sentence longer than 25 words" gives (words - 25) / 25 per sentence; "uses a banned word" gives 1 per occurrence over the parts; "fewer than 3 paragraphs" gives (3 - have) / 3.

**Combining.** The search score is `sum(gaps) x W + distance`, where distance is how much the search changed the writer's design (any count of changed parts) and W is large enough that fixing a rule is worth a visible change. A value that is invalid outright takes a fixed large score so the search first becomes valid. Selecting by this score, with distance as the tie-break, makes every partial fix a step down.

**Where it lives.** The check file computes the gap at the place it already decides a rule failed: the same measure that builds the message also builds the gap, so the two cannot disagree. The batch entry point (`--judge`) is the only new surface. The polish command reads `gaps` when present and counts each failed rule as 1 otherwise, so a project can start with none and add gaps to the rules that stall. Do this per rule when a search stalls on it, not all at once.

**What stays outside it.** Rules judged by a model (`judge.py`) have no cheap gap and cost a call per candidate: leave them out of the search and let the real check decide after. A rule that is not computable on a partial value cannot be graded.

### Before blaming the model

When a slot stays unkept for a long time, work down this list before comparing writers: (1) confirm a passing value exists at all, by searching for one with the fast scorer; (2) the gate order matches the run order; (3) the ask shows how the value behaves as a whole, not only the failing line; (4) the held value is not carrying junk (duplicates, unreachable or unused parts) while the ask calls it "nearly right"; (5) the briefs do not ask for less than the check's own floors. Briefs and floors are written separately and drift apart: when a floor changes, regenerate or clamp the briefs in the same edit.

### Browser models (`team_gpt.py`) and the edit grammar

A browser-driven model returns rendered markdown: escaped brackets (`\[...\]`), several edits on one line, a keyword alone on a line with its value on the next, `ADD:` with a colon. `surface._tidy_reply` undoes this before parsing, and splits only on capitalised `ADD` / `REPLACE n` / `REMOVE n`, so prose containing "add" is untouched. Any new reply path that feeds the edit grammar goes through it.

### Comparing two writers (A/B)

Fork the project folder at the same state (copy it with its `.ralph`), run each fork with its own writer (`RALPH_TEAM=./team_gpt.py` for the browser model), and give both the same check, gates, analyzer, clean and polish. Report per fork: kept count, rounds, repair asks, "not further" replies and, for the browser fork, how many calls went to the model versus fell back to local nodes (a fallback is the local model answering inside the other arm, so read the kept count together with that share). A fix to shared code (`team.py`, `surface.py`) reaches a run only at its next round, so restart a run's current round to apply it, and count only what happens after both runs are on the same code. A worked example lives in `demo_platformer/` (a `json-list` surface: `v3_tiers`, `v3_tiers_gpt`, `ab_report.py`).

`team.py` must not name a task. If a fix needs the task, it belongs in one of the project files above.


## A judge inside the check (`judge.py`)

Use it for a rule a script cannot measure (tone, "reads as an apology", "no cliché openers"). Anything a script can count stays a script: the exclamation-mark rule in the calibration run below belongs in the check, not with the judge.

**Why a bare model call in a check goes wrong.** The check runs on a scratch copy, many times per ask, over the whole stored set, and a kept value that a later run rejects is dropped with everything after it. A judge that answers differently on two runs wipes kept values, which is how the seeds were lost once. A call per run also costs minutes per ask. So the judge has to behave like a measurement, and `judge.py` does that:

- One yes/no question per rule, at temperature 0, fixed seed.
- The verdict is cached by hash of (model, rule, text) in `<project>/.ralph/judge-cache.jsonl`, outside the scratch copy (`team.py` exports `BLOSSOM_ROOT`). Asked once, never again. Rewording a rule is a new rule.
- A finding stands only when the judge quotes words that are really in the text **and** a second question in different words confirms the quoted words break the rule. Anything else passes. A false failure blocks a valid value for good; a miss costs less.
- It runs on a node that is not the writer's: `mac` (`gemma4:e4b`) by default, set `JUDGE_NODE` to change. `mac` is loopback, so the network guard on checks lets it through. A Python check cannot reach a LAN node unless `SWARM_ALLOW_NETWORK` is set, and that lifts the guard for every gate.
- If the judge cannot be reached the rule passes, the miss goes to stderr and nothing is cached. A check that failed on an outage would reject kept values and drop them.

**Starting a project with a judged rule**

1. Write the rubric: one line per rule, each a statement that is TRUE of a good text, in observable terms ("contains no slang such as 'dude', 'gonna', 'hey'"), not adjectives ("is professional").
2. Put the same rule text in the writer's context (a `WRITER-CONTEXT` region, or `--describe` in compose mode), in the same edit. A rule the writer learns by being rejected costs a turn per rule.
3. Call it last. Mechanical gates first; only values that pass them are judged.
   ```python
   import judge
   for line in judge.failures(text, RULES):
       print("CHECK FAILED: text %d %s" % (i, line)); failed = True
   ```
   From Node or a shell: `python3 "$SWARM_HOME/judge.py" --rubric rubric.txt --text-file t.txt` (exit 0 holds, exit 1 prints one line per broken rule).
4. Calibrate before the run: `--expect pass` on a text you know is good, `--expect fail` on one you know is bad. The first calibration of a tone rule failed "Good morning." as informal; the confirmation question removed that false failure. A rule that still misjudges a known text is reworded or dropped from the check.
5. The failing line names the rule, the quoted words and what to change, so the writer's next reply is an edit, not a guess.

**Limits.** The judge is an opinion from a small model. It is stable (cached) but not correct; a rule it keeps misjudging should be demoted to ranking or rewritten as measurable proxies. The cache grows with texts times rules; delete `.ralph/judge-cache.jsonl` to start over. Tests: `python3 -m unittest test_judge` (model stubbed).

## Briefs from a library of qualities

One call asked for 40 one-line briefs gives 40 variations on one idea ("N platforms, each r rows down and c columns right"). Briefs are now built in three steps:

1. **Axes** (`intent.AXES_PROMPT`, once per rules text, kept in `.ralph/axes.json`): the model lists the ways values can differ, plus a theme line. A `RAMP` axis climbs from simple to demanding (platform count, jump gap, vertical span). A `FREE` axis holds different kinds with no order (arrangement, layout). Values are concrete (a count, a size, a pattern), and every combination must satisfy the rules' minimums and maximums.
2. **Combinations** (`intent.compile_qualities`, no model): input i takes the RAMP values at its place in the set, with a small deterministic wobble, and a rotating FREE value per axis with a different stride each, so neighbours differ on every free axis. A repeated combination moves one axis a step. 40 inputs give 40 distinct combinations; the same axes and count always give the same set.
3. **Sentences** (`intent.BRIEF_FROM_QUALITIES_PROMPT`): one call turns each line of qualities into a brief of the whole value, in the theme. A brief the model drops falls back to its own qualities line, still unique.

The ramp also lines the set up with the ranker's difficulty order, so fewer valid levels arrive out of place. To change the character of a set, edit `.ralph/axes.json` (keep `basis`) and delete `.ralph/briefs-basis.txt` so the briefs are rebuilt from it. A rules change rebuilds the axes. Tests: `python3 -m unittest test_briefs`.

### A difficulty score the sorter can use

When the set must run from easy to hard, the check needs one number per level, not a pair, and the axes need to know what moves it.

- **One scalar in the check.** `difficultyScalar(level)` in `check_game.js` is the hardest jump on the kindest route plus 0.001 per jump that hard (infinity when unfinishable). Order is a scalar comparison, and the failing line prints both numbers and the formula. Anything that sorts or places levels reads this number.
- **`<command> --difficulty` in compose mode** prints how the check measures difficulty and what does and does not raise it. It is read only when the set is planned: the axes call is told to build every climbing (RAMP) axis from what raises the measure and make the rest FREE. The writer is not shown it as a rule; difficulty order is arranged outside the writer.
- Changing that text rebuilds the axes (`.ralph/axes.json`, `.ralph/briefs-basis.txt`).

### A ramp with a start and a stop, and what a level asks beyond its hardest jump

Measured on the first 40 platformer levels (`MEASURE=1 node check_game.js` prints the table): the hardest jump ordered the set, but 13 of the 40 put the goal in the bottom five rows, 28 never made the player jump back against the route's main direction, and 24 held a platform nobody could reach (up to 13 in one level). The check measured jumps; the writer built shapes the goal never asked for.

Four route-independent measures now sit beside the hardest jump (`challenge()` in `check_game.js`). Each is the least over all routes, so no route avoids it: **climb** (rows the player must go up; drops cost nothing), **doubling back** (jumps in the less used direction), **jumps** (platform changes on the shortest route) and **fall cost** (drop off any platform on the shortest route, count the jumps back onto it, take the worst). Three gates keep the shapes in play: the goal is on row 14 or higher, every platform can be reached, and the shortest route stands on at least 40% of them (decoys stay: at least 3 off the route).

The ramp is a table of floors by band of 8 seeds (`MIN_CLIMB`, `MIN_BACK`, `MIN_FALL`, `MIN_JUMPS`), so a set starts gentle and ends demanding. Bands, not a per-seed line, because placement moves a level one slot and a floor that changes every seed would reject a level for moving. The same table is in `compose.js --describe` (what the writer reads) and the axes in `.ralph/axes.json` are written to aim above each floor after the wobble `compile_qualities` adds. `difficultyScalar` adds up to 0.04 of "depth" and at most 0.03 for the hardest-jump count, under one jump step (0.083), so the extras only order levels that share a hardest jump. Hand-written axes are used only while `basis` equals the current `--describe` + `--difficulty` text; edit that text and they are rebuilt by the model.

### Say what the thing is for, not only its limits

Measured on the platformer: a writer given only numeric limits (no run over 30 cells, 8 platforms) wrote a 60-cell slab, then a 31-cell one. It was following the numbers and had no idea what they protected. A cap is a proxy for a purpose, and a model that does not know the purpose tunes to the edge of the proxy.

In compose mode the `--describe` text now opens with the purpose, in the project's own words: what makes a value good (here: decisions, varied moves, rhythm, using the whole screen, risk against safety) and what makes it dull and rejected (a flat floor, a slab, a corridor). The limits follow as consequences ("platforms are places to stand, not walls of floor"). The writer also writes a one-line DESIGN note before the spec, so the idea comes before the coordinates; the compose command reads the spec from the first `{"platforms"` on and ignores the note.

The check measures the purpose, not just the caps: kinds of jump on the shortest route, and platforms the route never stands on (choices). Each failing line says what the measure is for ("a level is fun when the player has choices"). Write a gate as the purpose it protects, then the number. Before tightening a gate, run it against the kept set: a gate that rejects a kept value drops every value after it.

## Accepting a stalled unit (advisory failures)

A unit can stall on rules that shape quality but do not make it unusable. The orchestrator may let such a unit through so the set keeps moving. Everything runs as before: full rounds of part repairs and polish. Only when a round ends stalled, and the unit has now ended `advisory_rounds` rounds stalled, is the held value accepted, if none of its failing lines is hard.

The project supplies, in `gates.json`:
- `hard`: patterns for failures that always block (the value is invalid, cannot do its job at all, or duplicates another).
- `advisory_rounds`: how many stalled rounds before acceptance. Absent or 0 turns the mechanism off.

The framework keeps the stalled-round count in `.ralph/stuck.json` and writes `.ralph/advisory.json` (`slots` and `notes`). The project's check must read that file from `BLOSSOM_ROOT` (the check runs in a scratch copy without `.ralph`) and print the accepted slots' non-hard failures as `ADVISORY:` lines instead of failing. The notes stay as context for later improvement passes. A check that ignores the file keeps failing and the unit is never really accepted.

### Rework pass for accepted units

When every unit is filled and some were accepted with advisories, the orchestrator reworks them, lowest first, each once. A unit leaves `advisory.json`, so the check fails it like any other failure and the whole repair loop (part edits, polish) runs on it again, starting from its old value with its saved notes shown. The units after it are parked in `.ralph/rework.json` and put back when it is done. It ends when the unit passes, or when it has stalled `rework_rounds` rounds (`gates.json`, default `advisory_rounds`) and is accepted again as it is, with fresh notes. `.ralph/reworked.json` lists units already reworked.

The project's check must make the loop's own driver keep going while rework is pending: when run without `BLOSSOM_ROOT` (the driver's run, not the orchestrator's scratch-copy runs), fail if `rework.json` exists or any `advisory.json` slot is not in `reworked.json`.

`compose.json` may set `polish_runs` (a number): the most times the polish command runs for one unit. The count is kept in `.ralph/polish_runs.json`. Absent or 0 means no limit.
