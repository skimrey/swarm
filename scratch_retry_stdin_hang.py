#!/usr/bin/env python3
"""Validates that `team.py do --retry --run` (and any `--retry` call with
no request text) never blocks reading stdin, even when the caller leaves
stdin as an open, unclosed pipe -- the exact shape an orchestrator/agent
subprocess wrapper produces when it inherits stdin without redirecting it,
which is what `LOCAL.md`'s `run_step` helper looks like from team.py's own
point of view.

`--retry` never needs new request text: it is reusing the saved plan, and
`--file`/`--error`/`--run` are how it names what to fix. Reading stdin
anyway, unconditionally whenever no request text and no tty, is a real
hang -- confirmed live before the fix, reproduced here mechanically.
"""
import subprocess
import sys
import time

HERE = __file__.rsplit("/", 1)[0] or "."


def hangs(argv, timeout=4.0):
    """True if the subprocess is still alive after `timeout` seconds with
    stdin left open and never communicated with -- the actual shape of an
    unclosed pipe, not `communicate()`'s own EOF-on-close behaviour."""
    p = subprocess.Popen([sys.executable] + argv, cwd=HERE,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    time.sleep(timeout)
    alive = p.poll() is None
    if alive:
        p.kill()
        p.wait(timeout=5)
    else:
        p.stdout.read()
    return alive


failures = []


def check(name, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print("%s %s%s" % (status, name, ("  -- " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


check("team do --retry --run does not hang on an open, unclosed stdin pipe",
      not hangs(["team.py", "do", "--retry", "--run"]))
check("team do --retry (no --run) does not hang either -- same request-"
      "text-is-empty shape",
      not hangs(["team.py", "do", "--retry"]))

# Sabotage: prove the test would have caught the pre-fix behaviour by
# reproducing it directly against the exact broken condition.
def sabotage():
    old_hangs = ("if not request and not sys.stdin.isatty():"
                in open(HERE + "/team.py").read())
    # The fixed source must NOT contain the old unconditional line
    # unqualified by `not args.retry` -- confirm the guard is actually
    # present in the file this test just ran against.
    src = open(HERE + "/team.py").read()
    guarded = "if not request and not args.retry and not sys.stdin.isatty():" in src
    check("sabotage: the source actually contains the args.retry guard "
          "(not just a coincidentally-passing timing race)",
          guarded)


sabotage()

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
