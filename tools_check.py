"""Hand-written check for tools.py. Run: python3 tools_check.py

Written before any of it ran, and written by hand rather than generated, for
the reason `AGENTS.md` gives: generated oracles rejected six of six correct
files across two runs of the same plan while consuming the largest single
share of pool time, and they cannot protect the case they exist for.

What this is actually guarding is narrower than "does tools.py work". The
free gates already see crashes. What they cannot see is a *wrong answer*, and
in an executor the wrong answers that matter are all in one place: something
runs that should not have. So the assertions concentrate there --
confinement, the confirm gate, validation ordering, and the two structural
invariants (no shell tool, no command argument) that the whole design rests
on and that a later convenience tool would quietly undo.

Every network call is stubbed. Nothing here touches the pool: the pool's
behaviour is not what is uncertain, and holding a node for a test would cost
real minutes on shared hardware.

Imports the module named by $TOOLS_MODULE (default "tools") so the check can
be pointed at a stub to confirm it fails in the cheap direction.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

MODULE = os.environ.get("TOOLS_MODULE", "tools")
t = importlib.import_module(MODULE)

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s%s" % (label, (" -- " + detail) if detail else ""))
        FAILURES.append(label)


def section(name):
    print("\n%s" % name)


def _raises(fn, exc=Exception):
    """True if calling fn raises `exc`. Anything else raising is not a pass."""
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------
# a throwaway project to act on
# --------------------------------------------------------------------------

root = tempfile.mkdtemp(prefix="toolscheck-")
with open(os.path.join(root, "hello.py"), "w") as fh:
    fh.write("VALUE = 1\n\n\ndef greet(name):\n    return 'hi ' + name\n")
os.makedirs(os.path.join(root, "sub"), exist_ok=True)
with open(os.path.join(root, "sub", "other.py"), "w") as fh:
    fh.write("import os\nMARKER = 'findme'\n")
outside = tempfile.mkdtemp(prefix="toolsoutside-")
with open(os.path.join(outside, "secret.txt"), "w") as fh:
    fh.write("do not read me\n")


# --------------------------------------------------------------------------
# the structural invariants -- these are the design, not a detail
# --------------------------------------------------------------------------

section("structural invariants")

names = t.registry.names()
check("registry is non-empty", bool(names), repr(names))

# The measured failure this module exists to prevent: e4b, with no delete
# tool available, called run_shell("rm -rf temp/*"). The defence is that no
# such tool exists to be called. If a later convenience tool reintroduces
# one, this is where it gets caught.
banned = ("run_shell", "shell", "bash", "exec", "run", "system", "eval",
          "subprocess", "command", "sh")
offenders = [n for n in names if n.lower() in banned]
check("no general shell/exec tool is registered", not offenders, repr(offenders))

# Same invariant from the other direction: a tool that takes a command string
# is a shell by another name, whatever it is called.
cmd_takers = []
for name in names:
    tool = t.registry.get(name)
    for arg in tool.params:
        if arg.lower() in ("command", "cmd", "script", "code", "shell"):
            cmd_takers.append("%s.%s" % (name, arg))
check("no tool accepts a command/script argument", not cmd_takers,
      repr(cmd_takers))

# A tool whose policy was never considered must ask, not act.
unsafe_default = [n for n in names
                  if t.registry.get(n).policy not in (t.AUTO, t.CONFIRM)]
check("every tool has a known policy", not unsafe_default, repr(unsafe_default))

mutating = [n for n in names if t.registry.get(n).policy == t.CONFIRM]
check("at least one tool is gated behind CONFIRM", bool(mutating))

# Registering without naming a policy must land on CONFIRM. This is the
# fail-safe direction; if the default ever flips, a tool added in a hurry
# becomes silently dangerous.
probe = t.Registry()


@probe.tool("unclassified", "no policy given",
            {"path": {"type": "string"}}, required=["path"])
def _unclassified(root, path):
    return "ran"


check("an unclassified tool defaults to CONFIRM",
      probe.get("unclassified").policy == t.CONFIRM,
      probe.get("unclassified").policy)

check("an unknown policy is refused",
      _raises(lambda: probe.tool("bad", "d", policy="sometimes")))


# --------------------------------------------------------------------------
# validation: a malformed call must be rejected without asking anyone
# --------------------------------------------------------------------------

section("validation")

problems, cleaned = t.validate(t.registry, "no_such_tool", {})
check("unknown tool is rejected", bool(problems), repr(problems))

problems, cleaned = t.validate(t.registry, "read_file", {})
check("missing required argument is rejected", bool(problems), repr(problems))

problems, cleaned = t.validate(t.registry, "read_file",
                               {"path": "hello.py", "bogus": 1})
check("unknown argument is rejected rather than dropped",
      bool(problems) and "bogus" not in cleaned, repr(problems))

problems, cleaned = t.validate(t.registry, "read_file", {"path": 17})
check("wrong-typed argument is rejected", bool(problems), repr(problems))

problems, cleaned = t.validate(t.registry, "read_file", {"path": "hello.py"})
check("a well-formed call validates clean",
      not problems and cleaned == {"path": "hello.py"},
      "%r %r" % (problems, cleaned))

problems, cleaned = t.validate(t.registry, "read_file", "not a dict")
check("non-object arguments are rejected", bool(problems), repr(problems))

# Narrow coercion, and only the unambiguous direction.
probe2 = t.Registry()


@probe2.tool("typed", "types",
             {"n": {"type": "integer"}, "f": {"type": "number"},
              "b": {"type": "boolean"}, "choice": {"type": "string",
                                                   "enum": ["a", "b"]}},
             policy=t.AUTO)
def _typed(root, n=0, f=0.0, b=False, choice="a"):
    return "ran"


problems, cleaned = t.validate(probe2, "typed", {"n": "5", "b": "true"})
check("a numeric string coerces to an integer",
      not problems and cleaned.get("n") == 5, "%r %r" % (problems, cleaned))
check("a boolean string coerces to a boolean",
      cleaned.get("b") is True, repr(cleaned))

problems, _ = t.validate(probe2, "typed", {"n": "banana"})
check("an uncoercible string is rejected, not guessed at", bool(problems),
      repr(problems))

problems, _ = t.validate(probe2, "typed", {"n": True})
check("a boolean is not accepted as an integer", bool(problems), repr(problems))

problems, _ = t.validate(probe2, "typed", {"choice": "z"})
check("a value outside an enum is rejected", bool(problems), repr(problems))


# --------------------------------------------------------------------------
# confinement: the one check no individual tool has to remember
# --------------------------------------------------------------------------

section("path confinement")

check("a plain relative path resolves inside the root",
      t.confine(root, "hello.py") == os.path.realpath(
          os.path.join(root, "hello.py")))

check("dot-dot traversal is refused",
      _raises(lambda: t.confine(root, "../secret.txt"), t.ToolError))

check("a deeper traversal is refused",
      _raises(lambda: t.confine(root, "sub/../../secret.txt"), t.ToolError))

check("an absolute path outside the root is refused",
      _raises(lambda: t.confine(root, os.path.join(outside, "secret.txt")),
              t.ToolError))

# A symlink is the case a naive string check misses, so it is resolved
# before comparison rather than after.
link = os.path.join(root, "escape")
try:
    os.symlink(outside, link)
    check("a symlink pointing out of the root is refused",
          _raises(lambda: t.confine(root, "escape/secret.txt"), t.ToolError))
except OSError:
    print("  skip symlink escape (cannot create symlinks here)")

# And the confinement must be reached through execute(), not only when a tool
# happens to call it -- that is what makes it a property of the harness.
record = t.execute(t.registry, root, "read_file",
                   {"path": os.path.join(outside, "secret.txt")},
                   confirm=lambda line: True)
check("execute() refuses an escaping path", record["status"] == "refused"
      and not record["ran"], repr(record))
check("the refused call did not run", "do not read me" not in
      str(record.get("detail", "")), repr(record))


# --------------------------------------------------------------------------
# the confirm gate
# --------------------------------------------------------------------------

section("confirm gate")

asked = []


def spy_yes(line):
    asked.append(line)
    return True


def spy_no(line):
    asked.append(line)
    return False


del asked[:]
record = t.execute(t.registry, root, "read_file", {"path": "hello.py"},
                   confirm=spy_no)
check("an AUTO tool runs without consulting the gate",
      record["status"] == "ok" and not asked, "%r %r" % (record["status"], asked))
check("the AUTO tool returned real content",
      "VALUE = 1" in record["detail"], repr(record["detail"])[:120])

# A CONFIRM tool must ask, and a "no" must actually stop it.
probe3 = t.Registry()
side_effects = []


@probe3.tool("mutate", "has an effect", {"path": {"type": "string"}},
             required=["path"], policy=t.CONFIRM)
def _mutate(root, path):
    side_effects.append(path)
    return "did it"


del asked[:]
record = t.execute(probe3, root, "mutate", {"path": "hello.py"},
                   confirm=spy_no)
check("a CONFIRM tool consults the gate", len(asked) == 1, repr(asked))
check("declining reports 'declined'", record["status"] == "declined",
      repr(record))
check("declining actually prevents the effect", not side_effects,
      repr(side_effects))
check("the rendered call shown to the operator names the real arguments",
      asked and "hello.py" in asked[0] and "mutate(" in asked[0], repr(asked))

del asked[:]
record = t.execute(probe3, root, "mutate", {"path": "hello.py"},
                   confirm=spy_yes)
check("approving runs it", record["status"] == "ok" and
      side_effects == ["hello.py"], "%r %r" % (record, side_effects))

# Validation must happen before the gate: approving a call that could never
# work invites a yes to nothing.
del asked[:]
del side_effects[:]
record = t.execute(probe3, root, "mutate", {"nonsense": 1}, confirm=spy_yes)
check("an invalid call is rejected before the gate is consulted",
      record["status"] == "invalid" and not asked, "%r %r" % (record, asked))
check("the invalid call never ran", not side_effects, repr(side_effects))

# assume_yes bypasses the prompt but must not bypass validation.
record = t.execute(probe3, root, "mutate", {"nonsense": 1}, assume_yes=True)
check("--yes does not bypass validation", record["status"] == "invalid",
      repr(record))


# --------------------------------------------------------------------------
# non-interactive stdin must decline, not proceed
# --------------------------------------------------------------------------

section("non-interactive default")

script = (
    "import sys, os\n"
    "sys.path.insert(0, %r)\n"
    "import importlib\n"
    "t = importlib.import_module(%r)\n"
    "print('TTY', sys.stdin.isatty())\n"
    "print('DECIDED', t.ask_tty('mutate(path=%%r)' %% 'x'))\n"
) % (os.path.dirname(os.path.abspath(__file__)), MODULE)
done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                      text=True, stdin=subprocess.DEVNULL, timeout=60)
check("a non-tty confirmation declines rather than proceeds",
      "DECIDED False" in done.stdout,
      (done.stdout + done.stderr).strip()[:200])


# --------------------------------------------------------------------------
# the tools themselves do what they say
# --------------------------------------------------------------------------

section("tool behaviour")

record = t.execute(t.registry, root, "list_directory", {"path": "."},
                   confirm=spy_no)
check("list_directory lists real entries",
      record["status"] == "ok" and "hello.py" in record["detail"]
      and "sub/" in record["detail"], repr(record["detail"])[:160])

record = t.execute(t.registry, root, "grep",
                   {"pattern": "findme", "glob": "*.py"}, confirm=spy_no)
check("grep finds a real match with a file and line",
      record["status"] == "ok" and "other.py" in record["detail"]
      and ":2:" in record["detail"], repr(record["detail"])[:160])

record = t.execute(t.registry, root, "grep",
                   {"pattern": "definitely-not-present"}, confirm=spy_no)
check("grep reports no matches rather than failing",
      record["status"] == "ok" and "no matches" in record["detail"],
      repr(record)[:160])

record = t.execute(t.registry, root, "grep", {"pattern": "([unclosed"},
                   confirm=spy_no)
check("a bad regex is an error record, not a crash",
      record["status"] == "error", repr(record)[:160])

record = t.execute(t.registry, root, "read_file", {"path": "nope.py"},
                   confirm=spy_no)
check("reading a missing file is an error record, not a crash",
      record["status"] == "error", repr(record)[:160])

record = t.execute(t.registry, root, "read_file", {"path": "sub"},
                   confirm=spy_no)
check("reading a directory is an error record, not a crash",
      record["status"] == "error", repr(record)[:160])

# run_check must refuse when nothing is configured, rather than inventing a
# command or running an empty shell.
record = t.execute(t.registry, root, "run_check", {}, assume_yes=True)
check("run_check with nothing configured is an error, not a silent pass",
      record["status"] == "error" and "no check configured" in record["detail"],
      repr(record)[:200])

with open(os.path.join(root, ".swarm-check"), "w") as fh:
    fh.write("python3 -c \"print('CHECKRAN')\"\n")
record = t.execute(t.registry, root, "run_check", {}, assume_yes=True)
check("run_check runs the project's configured command",
      record["status"] == "ok" and "CHECKRAN" in record["detail"],
      repr(record)[:200])
check("run_check reports the exit status",
      "exit 0" in record["detail"], repr(record["detail"])[:120])

# git tools must not fall over in a directory that is not a repo.
record = t.execute(t.registry, root, "git_status", {}, confirm=spy_no)
check("git_status in a non-repo is a clean error, not a crash",
      record["status"] in ("ok", "error"), repr(record)[:160])


# --------------------------------------------------------------------------
# propose(): parsing a real Ollama tool-call response, with no network
# --------------------------------------------------------------------------

section("proposal parsing")


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data


captured = {}


def fake_request(url, payload, timeout, stream=False):
    captured["url"] = url
    captured["payload"] = payload
    return FakeResponse({
        "message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "grep",
                                         "arguments": {"pattern": "findme",
                                                       "glob": "*.py"}}}],
        },
        "eval_count": 20, "eval_duration": 1_000_000_000,
        "prompt_eval_count": 100, "load_duration": 0, "total_duration": 2_000_000_000,
    })


import swarm  # noqa: E402  (after t, deliberately: t imports it too)

real_request = swarm._request
cfg = swarm.Config.load()
node = cfg.enabled_nodes()[0] if cfg.enabled_nodes() else cfg.nodes[0]


class FakeState:
    def record_perf(self, *a, **kw):
        pass


try:
    swarm._request = fake_request
    proposal = t.propose("find findme in python files", t.registry, node,
                         "gemma4:e4b", cfg, FakeState())
finally:
    swarm._request = real_request

check("a tool_calls response is parsed into one call",
      len(proposal["calls"]) == 1, repr(proposal))
check("the parsed call keeps its name and arguments",
      proposal["calls"][0]["name"] == "grep"
      and proposal["calls"][0]["args"]["pattern"] == "findme",
      repr(proposal["calls"]))
check("the request sent tool schemas",
      bool(captured["payload"].get("tools")), repr(list(captured["payload"])))
check("every registered tool was offered",
      len(captured["payload"]["tools"]) == len(t.registry.names()))
check("reasoning is off for tool selection",
      captured["payload"].get("think") is False, repr(captured["payload"].get("think")))
check("a context size was pinned for the call",
      isinstance(captured["payload"]["options"].get("num_ctx"), int),
      repr(captured["payload"].get("options")))

# A schema must survive a JSON round trip, since that is how it reaches the
# model; a non-serialisable default would fail only at call time.
try:
    json.dumps(t.registry.schemas())
    serialisable = True
except (TypeError, ValueError):
    serialisable = False
check("all schemas are JSON-serialisable", serialisable)

for name in t.registry.names():
    schema = t.registry.get(name).schema()
    fn = schema["function"]
    bad = (not fn.get("name") or not fn.get("description")
           or fn["parameters"]["type"] != "object")
    if bad:
        check("schema for %s is well formed" % name, False, repr(schema))
        break
else:
    check("every schema is well formed", True)

# No tool call at all is a legitimate outcome and must not be turned into one.
def fake_no_call(url, payload, timeout, stream=False):
    return FakeResponse({"message": {"role": "assistant",
                                     "content": "No tool here fits that."},
                         "eval_count": 8, "eval_duration": 1_000_000_000})


try:
    swarm._request = fake_no_call
    proposal = t.propose("do something unsupported", t.registry, node,
                         "gemma4:e4b", cfg, FakeState())
finally:
    swarm._request = real_request
check("a reply with no tool call yields no calls and keeps the text",
      not proposal["calls"] and "fits" in proposal["text"], repr(proposal))

# A hallucinated tool name must die in validation, not at call time.
record = t.execute(t.registry, root, "delete_everything", {"path": "."},
                   assume_yes=True)
check("a proposed tool that does not exist is rejected",
      record["status"] == "invalid" and not record["ran"], repr(record))


# --------------------------------------------------------------------------

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(outside, ignore_errors=True)

print("\n%d check(s) failed" % len(FAILURES))
if FAILURES:
    for name in FAILURES:
        print("  - %s" % name)
sys.exit(1 if FAILURES else 0)
