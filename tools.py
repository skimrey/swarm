#!/usr/bin/env python3
"""Executing tool calls locally, from proposals made on the network.

The pool writes code; this runs actions. The difference is worth stating
because it changes what a mistake costs: a wrong component is caught by
`--check` and rewritten, while a wrong `delete` has already happened. There is
no repair loop for an action.

So the split here is the whole design. A network model is handed a schema and
a request and returns a *proposal* -- a tool name and some arguments, as data.
It holds no credentials, has no environment, and cannot execute anything.
Everything that touches this machine runs here, locally, after the proposal
has been checked against the schema mechanically and, for anything with side
effects, shown to the operator and approved.

Why the tool surface is the policy
----------------------------------
Measured on this pool: given five filesystem tools and asked to "delete
everything in the temp folder" -- for which no tool existed -- `gemma4:e4b`
called `run_shell(command="rm -rf temp/*")`. It neither refused nor asked. The
same model, asked something equally unsupported a minute later, did ask. So
the behaviour is not reliable in either direction, and the 26b was no better:
on the milder case it called `list_directory` unprompted rather than asking.
A stronger model buys capability here, not caution.

The conclusion is not that small models are careless. It is that one broad
tool turns every safety rule into a denylist over an infinite space, while a
registry of narrow ones makes the dangerous call *unrepresentable*. There is
deliberately no shell tool in this module, so `rm -rf` is not something a
model can propose, well or badly. That is the reasoning `crossref.py` and
`patterns.py` already apply to code -- decide mechanically whatever can be
decided mechanically, before consulting a model -- applied to actions.

Two consequences of that principle are load-bearing and easy to undo by
accident. A newly registered tool defaults to CONFIRM, never AUTO, so
forgetting to classify one fails safe. And path arguments are confined to the
project root in `execute`, in one place, rather than inside each tool: a check
every tool has to remember is a check some later tool will forget.

Single turn on purpose
----------------------
This proposes one call, runs it, and stops. No loop. Loop control -- knowing
when to stop, recovering from a tool result that says "error" rather than
retrying it forever -- is the part small models are genuinely weak at, and it
wants a step budget and repeat detection designed against real transcripts.
Those transcripts do not exist yet. This produces them.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

import swarm

# Tool selection is schema-matching, not judgement: measured 4/4 correct on
# overlapping filesystem tools with correctly typed arguments, on a
# capability-2 model. So this defaults to the cheap tier rather than the
# scarce one, and deliberately not to a model on `mac` -- a resident model
# there competes with the operator's own desktop for the same 16GB, which is
# why that node is `enabled: false` in the first place. Measured on this
# hardware: e4b at a 131072-token context sat at 5.9GB resident, against
# 3.2GB at 2048, and the machine froze hard when that landed on a nearly
# committed 16GB.
DEFAULT_NODE = "e4b-linux"
DEFAULT_MODEL = "gemma4-e4b-fixed:latest"

# Reasoning shares one budget with the answer, and a model that reasons past
# it emits empty content -- the single most expensive bug recorded in this
# project, because it looks exactly like incapacity. Tool selection needs no
# reasoning at all, so it is off here and the budget is small.
ANSWER_TOKENS = 400

AUTO = "auto"        # read-only; runs without asking
CONFIRM = "confirm"  # has effects; rendered and approved before running

MAX_RESULT_CHARS = 20000


class ToolError(RuntimeError):
    """A tool failed in a way worth reporting back rather than crashing."""


class Tool:
    def __init__(self, name: str, description: str,
                 params: Dict[str, Dict[str, Any]],
                 required: Sequence[str],
                 fn: Callable[..., Any],
                 policy: str = CONFIRM) -> None:
        self.name = name
        self.description = description
        self.params = params
        self.required = list(required)
        self.fn = fn
        self.policy = policy

    def schema(self) -> Dict[str, Any]:
        """The JSON-schema form Ollama's native tool calling consumes."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.params,
                    "required": self.required,
                },
            },
        }


class Registry:
    """The set of actions that exist. Anything absent cannot be proposed."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def tool(self, name: str, description: str,
             params: Optional[Dict[str, Dict[str, Any]]] = None,
             required: Sequence[str] = (),
             policy: str = CONFIRM) -> Callable[[Callable], Callable]:
        """Register a callable. `policy` defaults to CONFIRM, not AUTO.

        Defaulting to the safe value means a tool added in a hurry, without
        thinking about its blast radius, asks before it runs. The reverse
        default would make forgetting silently dangerous.
        """
        if policy not in (AUTO, CONFIRM):
            raise ValueError("unknown policy %r" % policy)

        def register(fn: Callable) -> Callable:
            self._tools[name] = Tool(name, description, params or {},
                                     required, fn, policy)
            return fn
        return register

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def schemas(self) -> List[Dict[str, Any]]:
        return [self._tools[n].schema() for n in self.names()]


# --------------------------------------------------------------------------
# mechanical validation
# --------------------------------------------------------------------------

_TYPES: Dict[str, tuple] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _coerce(value: Any, want: str) -> Any:
    """Narrowly repair a scalar a model sent as the wrong primitive.

    Only the unambiguous direction: "5" for an integer, "true" for a boolean.
    Nothing structural, and nothing that requires a guess about intent --
    turning "maybe" into False, or splitting a string into an array, would be
    this module inventing the call rather than checking it.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if want == "integer":
        try:
            return int(text)
        except ValueError:
            return value
    if want == "number":
        try:
            return float(text)
        except ValueError:
            return value
    if want == "boolean" and text.lower() in ("true", "false"):
        return text.lower() == "true"
    return value


def validate(reg: Registry, name: str,
             args: Dict[str, Any]) -> tuple:
    """(problems, cleaned_args). A non-empty problems list means do not run.

    Runs before the policy gate and before any prompt, because a malformed
    call is decidable without troubling the operator: asking "approve
    s3_put(buckt=...)?" invites a yes to something that was never going to
    work. Unknown arguments are rejected rather than dropped -- a model that
    invented an argument name may have misunderstood the tool, and silently
    discarding the evidence of that hides it.
    """
    problems: List[str] = []
    tool = reg.get(name)
    if tool is None:
        return (["no such tool: %s (have: %s)"
                 % (name, ", ".join(reg.names()))], {})

    if not isinstance(args, dict):
        return (["arguments must be an object, got %s"
                 % type(args).__name__], {})

    cleaned: Dict[str, Any] = {}
    for key, value in args.items():
        spec = tool.params.get(key)
        if spec is None:
            problems.append("unknown argument %r (accepts: %s)"
                            % (key, ", ".join(sorted(tool.params)) or "none"))
            continue
        want = spec.get("type", "string")
        value = _coerce(value, want)
        allowed = _TYPES.get(want)
        # bool is a subclass of int, so an integer field would otherwise
        # accept True. Rejected explicitly rather than coerced.
        if want in ("integer", "number") and isinstance(value, bool):
            problems.append("%s must be %s, got boolean" % (key, want))
            continue
        if allowed and not isinstance(value, allowed):
            problems.append("%s must be %s, got %s"
                            % (key, want, type(value).__name__))
            continue
        if "enum" in spec and value not in spec["enum"]:
            problems.append("%s must be one of %s, got %r"
                            % (key, spec["enum"], value))
            continue
        cleaned[key] = value

    for key in tool.required:
        if key not in cleaned:
            problems.append("missing required argument %r" % key)
    return (problems, cleaned)


def confine(root: str, path: str) -> str:
    """Resolve `path` and refuse anything outside the project root.

    Enforced here rather than in each tool, and applied to every argument a
    tool declares as a path, so a tool added later inherits it without having
    to remember. `..` and absolute paths are both handled by the realpath
    comparison; symlinks are resolved before the check, so a link pointing
    out of the tree does not slip through.
    """
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, path))
    if full != base and not full.startswith(base + os.sep):
        raise ToolError("path escapes the project root: %s" % path)
    return full


PATH_ARGS = ("path", "local_path", "file")


# --------------------------------------------------------------------------
# the starter tools: read-only inspection
# --------------------------------------------------------------------------

registry = Registry()


@registry.tool(
    "read_file", "Read the full text of one file in the project",
    {"path": {"type": "string", "description": "file path, relative to the project"}},
    required=["path"], policy=AUTO)
def read_file(root: str, path: str) -> str:
    full = confine(root, path)
    if not os.path.isfile(full):
        raise ToolError("not a file: %s" % path)
    with open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read()


@registry.tool(
    "list_directory", "List files and subdirectories of a directory",
    {"path": {"type": "string", "description": "directory path, default ."}},
    policy=AUTO)
def list_directory(root: str, path: str = ".") -> str:
    full = confine(root, path)
    if not os.path.isdir(full):
        raise ToolError("not a directory: %s" % path)
    out = []
    for name in sorted(os.listdir(full)):
        mark = "/" if os.path.isdir(os.path.join(full, name)) else ""
        out.append(name + mark)
    return "\n".join(out) or "(empty)"


@registry.tool(
    "grep", "Search for a text pattern in the project's files",
    {"pattern": {"type": "string", "description": "literal text or regex"},
     "glob": {"type": "string", "description": "filename filter, e.g. *.py"},
     "path": {"type": "string", "description": "directory to search, default ."}},
    required=["pattern"], policy=AUTO)
def grep(root: str, pattern: str, glob: str = "*", path: str = ".") -> str:
    full = confine(root, path)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError("bad pattern: %s" % exc)
    hits: List[str] = []
    for dirpath, dirnames, filenames in os.walk(full):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".swarm")]
        for name in sorted(filenames):
            if not fnmatch.fnmatch(name, glob):
                continue
            target = os.path.join(dirpath, name)
            try:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            rel = os.path.relpath(target, full)
                            hits.append("%s:%d: %s" % (rel, i, line.rstrip()))
                            if len(hits) >= 200:
                                return "\n".join(hits) + "\n(truncated at 200)"
            except OSError:
                continue
    return "\n".join(hits) or "no matches"


def _git(root: str, *argv: str) -> str:
    """Run git with a fixed argument vector. Never a shell, never a string.

    The arguments come from this file, not from the model -- a `git_command`
    tool taking arbitrary flags would be the shell escape hatch this module
    exists to not have.
    """
    try:
        done = subprocess.run(("git",) + argv, cwd=root, capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError("git failed: %s" % exc)
    if done.returncode != 0 and not done.stdout.strip():
        raise ToolError((done.stderr or "git exited %d" % done.returncode).strip())
    return done.stdout or "(no output)"


@registry.tool("git_status", "Show which files have uncommitted changes",
               policy=AUTO)
def git_status(root: str) -> str:
    return _git(root, "status", "--short")


@registry.tool(
    "git_diff", "Show the uncommitted changes, as a diff",
    {"path": {"type": "string", "description": "limit to one path, optional"}},
    policy=AUTO)
def git_diff(root: str, path: Optional[str] = None) -> str:
    if path:
        confine(root, path)
        return _git(root, "diff", "--", path)
    return _git(root, "diff")


@registry.tool(
    "run_check", "Run this project's configured check command and show output",
    policy=CONFIRM)
def run_check(root: str) -> str:
    """Deliberately takes no command argument.

    A `run(command)` tool would be `run_shell` with a friendlier name, and the
    measured failure this module is built around was a model reaching for
    exactly that. The command comes from local configuration -- see
    `check_command` -- so the model can ask for the check to be run without
    getting to say what the check is.
    """
    cmd = check_command(root)
    if not cmd:
        raise ToolError("no check configured for this project "
                        "(write one to .swarm-check in the project root)")
    try:
        done = subprocess.run(cmd, cwd=root, shell=True, capture_output=True,
                              text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError("check failed to run: %s" % exc)
    tail = (done.stdout + done.stderr).strip()
    return "exit %d\n%s" % (done.returncode, tail or "(no output)")


def check_command(root: str) -> str:
    """This project's check, read from `.swarm-check` in its root.

    A file rather than a tool argument, for the reason in `run_check`: the
    operator writes what "correct" means, and the model can only ask for it
    to be evaluated.
    """
    try:
        with open(os.path.join(root, ".swarm-check"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# proposing
# --------------------------------------------------------------------------

SYSTEM = ("You call tools on behalf of an operator. Use a tool when one fits "
          "the request. If no available tool fits, say so in plain words and "
          "call nothing -- never substitute a different tool to approximate "
          "the request. Do not explain tools you did not call.")


def propose(request: str, reg: Registry, node: swarm.Node, model: str,
            cfg: swarm.Config, state: swarm.State,
            timeout: float = 120.0) -> Dict[str, Any]:
    """Ask the network for a tool call. Returns {"calls": [...], "text": str}.

    Not streamed, and not routed through `swarm.chat`: that function collects
    `message.content` and `message.thinking` and would drop `tool_calls`
    entirely, and a tool call is a few dozen tokens, so streaming buys
    nothing here. `num_ctx` is sized once from this request and held, because
    Ollama reloads the model whenever it changes.
    """
    schemas = reg.schemas()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": request}]
    prompt_tokens = swarm.est_tokens(
        SYSTEM + request + json.dumps(schemas))
    num_ctx = swarm.pick_num_ctx(cfg, node, model, prompt_tokens,
                                 ANSWER_TOKENS, reasoning=False)
    payload = {
        "model": model,
        "messages": messages,
        "tools": schemas,
        "stream": False,
        "think": False,
        "options": {"num_ctx": num_ctx, "num_predict": ANSWER_TOKENS,
                    "temperature": 0.0},
    }
    resp = swarm._request(node.url + "/api/chat", payload, timeout)
    body = json.loads(resp.read().decode("utf-8", "replace"))
    if body.get("error"):
        raise swarm.OllamaError(str(body["error"]))
    message = body.get("message") or {}
    calls = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        calls.append({"name": fn.get("name") or "",
                      "args": fn.get("arguments") or {}})
    sample = {
        "prompt_tokens": body.get("prompt_eval_count") or 0,
        "output_tokens": body.get("eval_count") or 0,
        "prefill_tps": None,
        "decode_tps": ((body.get("eval_count") or 0) /
                       ((body.get("eval_duration") or 1) / swarm.NS)
                       if body.get("eval_duration") else None),
        "load_s": (body.get("load_duration") or 0) / swarm.NS,
        "total_s": (body.get("total_duration") or 0) / swarm.NS,
        "ttft_s": None,
    }
    try:
        state.record_perf(node.name, model, sample)
    except Exception:
        pass
    return {"calls": calls, "text": (message.get("content") or "").strip()}


# --------------------------------------------------------------------------
# executing
# --------------------------------------------------------------------------

def render(name: str, args: Dict[str, Any]) -> str:
    """The call as one line, for the operator to approve or refuse."""
    inner = ", ".join("%s=%r" % (k, args[k]) for k in sorted(args))
    return "%s(%s)" % (name, inner)


def ask_tty(line: str) -> bool:
    """Default confirmation: print the call, require an explicit yes.

    A non-interactive stdin declines rather than proceeds. An executor that
    treats "nobody is listening" as approval is one cron job away from
    running unattended, which is the opposite of what a confirm gate is for.
    """
    if not sys.stdin.isatty():
        sys.stderr.write("  declined: %s (no tty to confirm on)\n" % line)
        return False
    try:
        reply = input("  run %s ? [y/N] " % line)
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n  declined\n")
        return False
    return reply.strip().lower() in ("y", "yes")


def execute(reg: Registry, root: str, name: str, args: Dict[str, Any],
            confirm: Callable[[str], bool] = ask_tty,
            assume_yes: bool = False) -> Dict[str, Any]:
    """Validate, gate, and run one proposed call.

    Order matters and is the point: validate first (a malformed call is
    rejected without asking anybody), confine paths second, gate third, run
    last. Returns a record rather than raising, so a refusal and a failure
    are both reportable outcomes rather than control flow.
    """
    problems, cleaned = validate(reg, name, args)
    if problems:
        return {"tool": name, "args": args, "ran": False,
                "status": "invalid", "detail": "; ".join(problems)}

    tool = reg.get(name)
    assert tool is not None  # validate() already established this
    for key in PATH_ARGS:
        if key in cleaned:
            try:
                confine(root, str(cleaned[key]))
            except ToolError as exc:
                return {"tool": name, "args": cleaned, "ran": False,
                        "status": "refused", "detail": str(exc)}

    line = render(name, cleaned)
    if tool.policy == CONFIRM and not assume_yes and not confirm(line):
        return {"tool": name, "args": cleaned, "ran": False,
                "status": "declined", "detail": "operator declined"}

    try:
        result = tool.fn(root, **cleaned)
    except ToolError as exc:
        return {"tool": name, "args": cleaned, "ran": True,
                "status": "error", "detail": str(exc)}
    except Exception as exc:  # a tool bug should not take the harness down
        return {"tool": name, "args": cleaned, "ran": True,
                "status": "error",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    text = result if isinstance(result, str) else json.dumps(result, indent=2)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n(truncated)"
    return {"tool": name, "args": cleaned, "ran": True,
            "status": "ok", "detail": text}


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def resolve_target(cfg: swarm.Config, node_name: Optional[str],
                   model: Optional[str]) -> tuple:
    """Pick the node and model that will propose calls.

    Prefers the configured default, then any enabled node carrying a
    capability-2 model. Never falls back to `mac`: that node is
    `enabled: false` precisely because a resident model there competes with
    the operator's desktop, and `enabled_nodes()` already excludes it.
    """
    if node_name:
        node = cfg.node(node_name)
        if node is None:
            raise SystemExit("no such node: %s" % node_name)
        return (node, model or (node.models[0] if node.models else ""))

    node = cfg.node(DEFAULT_NODE)
    if node is not None and node.enabled:
        return (node, model or DEFAULT_MODEL)
    for candidate in cfg.enabled_nodes():
        for tag in candidate.models:
            if cfg.model_meta(tag).get("capability", 4) <= 2:
                return (candidate, model or tag)
    for candidate in cfg.enabled_nodes():
        if candidate.models:
            return (candidate, model or candidate.models[0])
    raise SystemExit("no enabled node available to propose tool calls")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Propose a tool call on the network; run it here.")
    ap.add_argument("request", nargs="*", help="what you want done")
    ap.add_argument("--root", default=".", help="project root (default .)")
    ap.add_argument("--node", help="override which node proposes")
    ap.add_argument("--model", help="override which model proposes")
    ap.add_argument("--list", action="store_true",
                    help="show the registered tools and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="propose and print the call, never execute")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (use deliberately)")
    args = ap.parse_args(argv)

    cfg = swarm.Config.load()
    root = os.path.abspath(args.root)

    if args.list:
        for name in registry.names():
            tool = registry.get(name)
            assert tool is not None
            params = ", ".join(sorted(tool.params)) or "-"
            print("%-16s %-8s %s" % (name, tool.policy, tool.description))
            print("%-16s %-8s args: %s" % ("", "", params))
        return 0

    request = " ".join(args.request).strip()
    if not request:
        ap.error("say what you want done, or pass --list")

    state = swarm.State()
    node, model = resolve_target(cfg, args.node, args.model)
    sys.stderr.write("  proposing on %s/%s\n" % (node.name, model))
    try:
        proposal = propose(request, registry, node, model, cfg, state)
    except swarm.OllamaError as exc:
        sys.stderr.write("  could not reach the pool: %s\n" % exc)
        return 1

    if not proposal["calls"]:
        # No tool fitting the request is a legitimate answer, and the system
        # prompt asks for it explicitly rather than for an approximation.
        print(proposal["text"] or "(no tool call proposed)")
        return 0

    records = []
    for call in proposal["calls"]:
        line = render(call["name"], call["args"])
        if args.dry_run:
            print("would run: %s" % line)
            records.append({"tool": call["name"], "args": call["args"],
                            "ran": False, "status": "dry-run", "detail": ""})
            continue
        record = execute(registry, root, call["name"], call["args"],
                         assume_yes=args.yes)
        records.append(record)
        if record["status"] == "ok":
            print(record["detail"])
        else:
            sys.stderr.write("  %s: %s -- %s\n"
                             % (record["status"], line, record["detail"]))

    try:
        import session
        session.record_tools(root, request, records)
    except Exception:
        pass
    return 0 if all(r["status"] in ("ok", "dry-run") for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
