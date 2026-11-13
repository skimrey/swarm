# The chat harness

A terminal chat with your local model pool. It remembers your threads, can look
around a project, run its check, and have the swarm write code, and it asks before
doing anything that changes things.

```
you> what does cli.py do?
  step 1: read_file(path='cli.py')
It parses two flags and calls main() ...
you> add a --verbose flag
  step 1: build(request='add a --verbose flag to cli.py')
  run build(request=...) ? [y/N] y
```

## Start it

```bash
cd ~/some/project
hchat            # new thread
hchat -c         # pick up the most recent thread
hchat --list     # see this project's threads
hchat -r 2026    # resume a thread by id (a unique prefix is enough)
hchat -y         # approve every action without asking (see Safety)
```

`hchat` lives in `~/.local/bin`, so it works from any folder. The folder you start it
in is the **project**: the model can only see files inside it, and threads are saved
there. Start it from a different folder and you get a different set of threads.

Default model: `gemma4:26b-a4b-it-mtp-q4_K_M` on the `p40` node. Change with
`--node NAME --model TAG` (nodes are in `config.json`).

## Talking to it

Type and press Enter. End a line with `\` to continue on the next line (handy for
pasting). Ctrl-C stops a reply that is taking too long and drops that message from the
thread, so nothing half-finished is left behind. Ctrl-D or `/quit` leaves.

| Command | What it does |
|---|---|
| `/new` | start a fresh thread (do this when you change topic) |
| `/list` | threads in this project, newest first |
| `/resume ID` | switch to a thread (a unique prefix of the id works) |
| `/rename TEXT` | give the current thread a name |
| `/undo` | drop your last message and everything after it |
| `/steps N` | the most tool steps allowed per message (default 12) |
| `/yes` | toggle approve-everything on or off |
| `/tools` | what the model is able to do |
| `/where` | project folder, model, and the thread's file |
| `/help` | the list above |

Threads are saved after every message to `<project>/.harness/threads/<id>.json`. They
are plain JSON; you can read, copy, or delete them. A damaged file is skipped, not fatal.

## What it can and can't do

The model gets a short list of narrow tools, and nothing else:

| Tool | Asks first? | What it does |
|---|---|---|
| `list_directory`, `read_file`, `grep` | no | look at files in the project |
| `git_status`, `git_diff` | no | see what changed |
| `run_check` | yes | run the command in the project's `.swarm-check` file |
| `build` | yes | have the swarm write or fix code (runs `ralph.sh`) |

There is **no shell tool**, on purpose. A model that is allowed to run commands will
eventually run a bad one; with no such tool it can't even ask. Paths are checked
against the project folder, so `../../etc/passwd` is refused. Files are only ever
changed through `build`.

### How `build` decides it's done

`build` calls `ralph.sh`, which repeats the same request until it is done. It does
not stop because a round failed or because it has tried a few times. `-n` is an
optional fuse for when you want one; the chat does not set it. The loop is
`ralph.md`. A dataset, one function over many inputs, is blossom: `BLOSSOM.md`.
The check is the spec, and a size rule in that check is a floor.

- **If the project has a `.swarm-check` file**, that command is the judge. Put the
  command that means "correct" in it (for example `python3 -m unittest`), and `build`
  repeats until it passes. This is the strong option.
- **If not**, "done" means the swarm's own gates passed: the files parse, import, run a
  smoke test in a network-restricted sandbox, and fit together. That proves it runs,
  not that it is right. Say what you expect, then check the result yourself.

## Safety

- Anything with effects (`build`, `run_check`) shows you the exact call and waits for
  `y`. With no terminal attached it declines instead of going ahead.
- `-y` or `/yes` removes that prompt. Only use it in a project you can undo, e.g. a git
  repo with nothing uncommitted. `git diff` shows what `build` changed.
- Generated code does run: inside the swarm's gates it is confined to loopback
  networking, but it is not a security sandbox.

## When it misbehaves

| You see | Likely cause and fix |
|---|---|
| `could not reach the pool` | the node is down. Check `./swarm.py nodes`, or `curl http://10.0.0.178:11434/api/tags` |
| First reply takes a minute or more | the model is loading into the card. Later replies are fast |
| `stopping: the same call returned the same result 3 times` | it was going in circles. `/undo`, then rephrase or break the task up |
| `stopping: step budget (12) used up` | it needs more steps: `/steps 25`, or ask for a smaller piece |
| Replies get strange or repetitive in a long thread | `/new`. A warning appears past about 60k tokens. Old tool output is trimmed automatically, but your own messages and its answers stay |
| `declined: ... (no tty to confirm on)` | you piped input into it; run it in a normal terminal, or use `-y` |

Loops in long conversations are the reason this harness trims old tool output and
caps each message at 12 steps. If you hit them anyway, the thread's JSON file shows
exactly what the model saw.

## How it fits together

```
hchat  ->  chat.py       the thread list and the /commands
             harness.py   the loop: ask the model, run a tool, feed back the result,
                          step budget, repeat detection, history trimming
               tools.py   the allowed actions, argument checks, path confinement, the y/N gate
               ralph.sh   what `build` runs: team.py do, repeated until the check passes
                 team.py  the swarm that writes the code
```

Each message may take several steps; the loop ends when the model answers in words
without calling a tool.

## Notes and limits

- Replies are not streamed: you see `...` while it works, then the answer.
- One model decides the steps and the swarm writes the code, so a `build` can use the
  other boxes in the pool while the chat model sits on `p40`.
- The context size for a run is chosen once and held (for `p40`, the pinned value in
  `config.json`), so the model is not reloaded between messages.
- Threads belong to the project folder. Moving or renaming the folder takes them along;
  copying it copies them too.

## Tests

```bash
python3 scratch_harness.py   # the loop, with a scripted model (no network)
python3 scratch_chat.py      # threads and commands, with a scripted model (no network)
```
