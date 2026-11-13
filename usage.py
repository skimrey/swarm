"""How to run what was just written, read off the file rather than guessed.

A run ends with "1/1 passed every gate" and the operator is left holding a
script with no idea what it wants. The first live attempt at a Maven client
cost four rounds of guessing: `API_KEY` missing, then the same again after
an alias was exported before the value it aliased, then `PROJECT_ID`, none
of which the run had mentioned. Every one of those names was sitting in the
generated source the whole time.

So they are read out of it. `os.environ["API_KEY"]` is a required variable,
`os.getenv("PORT", "8080")` is an optional one, `add_argument("--project")`
is a flag, and `if __name__ == "__main__"` is what makes a file runnable at
all. All four are literals in the syntax tree, which is why this is a
function and not a prompt: a model asked to write usage notes can describe
a flag the file does not have, and this cannot.

The same rule as `crossref.py` and `patterns.py`: anything decidable from
the tree is decided there. Nothing is reported unless the name is a plain
string literal, because a computed variable name is unknowable and a
detector that invents an instruction is worse than one that says nothing.
"""

from __future__ import annotations

import ast
import os
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple


class Usage(NamedTuple):
    """Everything the file itself says about how to invoke it."""
    path: str
    runnable: bool                       # has a __main__ block
    required_env: List[str]
    optional_env: List[Tuple[str, str]]  # (name, default)
    flags: List[Tuple[str, bool, bool]]  # (--flag, required, takes a value)
    positionals: List[str]


def _literal(node: Optional[ast.AST]) -> Optional[str]:
    """The string this node is, or None if it is anything else.

    A computed name -- `os.environ[prefix + "_KEY"]` -- has no answer here,
    and guessing one would print an instruction that is wrong.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _env_reads(tree: ast.AST) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Environment variables the file reads, split by whether it needs one.

    A default is the file telling you the variable is optional, so
    `os.getenv("X")` with no default and `os.environ["X"]` are required
    while `os.getenv("X", "8080")` is not. `os.environ.get` is the same
    call wearing a different hat and is treated identically.
    """
    required: List[str] = []
    optional: List[Tuple[str, str]] = []

    for node in ast.walk(tree):
        # os.environ["NAME"]
        if isinstance(node, ast.Subscript):
            target = node.value
            if (isinstance(target, ast.Attribute) and target.attr == "environ"):
                name = _literal(node.slice)
                if name:
                    required.append(name)
            continue

        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        # os.getenv("NAME") / os.environ.get("NAME")
        is_getenv = func.attr == "getenv"
        is_env_get = (func.attr == "get"
                      and isinstance(func.value, ast.Attribute)
                      and func.value.attr == "environ")
        if not (is_getenv or is_env_get) or not node.args:
            continue
        name = _literal(node.args[0])
        if not name:
            continue
        if len(node.args) > 1:
            fallback = _literal(node.args[1])
            optional.append((name, fallback if fallback is not None else "..."))
        else:
            required.append(name)

    seen: set = set()
    req = [n for n in required if not (n in seen or seen.add(n))]
    opt = [(n, d) for n, d in optional if not (n in seen or seen.add(n))]
    return req, opt


def _arguments(tree: ast.AST) -> Tuple[List[Tuple[str, bool, bool]], List[str]]:
    """Flags and positionals declared through argparse.

    Matched on the method name rather than on tracing a parser object,
    because `add_argument` on anything else is vanishingly rare and losing
    the argument list to an unusual construction is the worse trade.
    """
    flags: List[Tuple[str, bool, bool]] = []
    positionals: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and node.args):
            continue
        name = _literal(node.args[0])
        if not name:
            continue
        if name.startswith("-"):
            required = any(kw.arg == "required"
                           and isinstance(kw.value, ast.Constant)
                           and kw.value.value is True
                           for kw in node.keywords)
            # A long form is what an operator types; keep it over `-p`.
            longest = name
            for extra in node.args[1:]:
                alt = _literal(extra)
                if alt and alt.startswith("--"):
                    longest = alt
            # store_true and friends are switches; writing `--dry-run ...`
            # for one tells the operator to pass a value argparse rejects.
            action = next((kw.value.value for kw in node.keywords
                           if kw.arg == "action"
                           and isinstance(kw.value, ast.Constant)), "")
            takes_value = not str(action).startswith(("store_true",
                                                      "store_false",
                                                      "store_const",
                                                      "count", "help",
                                                      "version"))
            flags.append((longest, required, takes_value))
        elif name not in positionals:
            positionals.append(name)
    return flags, positionals


def read(path: str, source: Optional[str] = None) -> Optional[Usage]:
    """What `path` says about running it, or None if it says nothing."""
    if not path.endswith(".py"):
        return None
    try:
        if source is None:
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return None

    runnable = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in ast.walk(tree))

    required, optional = _env_reads(tree)
    flags, positionals = _arguments(tree)
    if not (runnable or required or optional or flags):
        return None
    return Usage(path, runnable, required, optional, flags, positionals)


def placeholder_args(source: str) -> List[str]:
    """An argument vector satisfying whatever the parser insists on.

    A client with a required `--project` exits at argparse before it reaches
    a single request, so a bare smoke run observes nothing -- measured, a
    Maven client passed its gates having sent no traffic. Filling in the
    required arguments walks the code that matters.

    Only required ones. An optional flag left out is the path the author
    expected to be common, and switching on every feature at once is a
    stranger invocation than any operator would type.
    """
    found = read("probe.py", source)
    if found is None:
        return []
    argv: List[str] = []
    for flag, required, takes_value in found.flags:
        if not required:
            continue
        argv.append(flag)
        if takes_value:
            argv.append(_placeholder_for(flag.lstrip("-")))
    argv.extend(_placeholder_for(name) for name in found.positionals)
    return argv


def _placeholder_for(name: str) -> str:
    """A value shaped like what this name suggests it holds.

    `uuid.UUID("x")` raises where `uuid.UUID(<a uuid>)` does not, so the
    shape decides whether the run gets past the first line that touches it.
    """
    lowered = name.lower().replace("-", "_")
    if lowered.endswith(("_id", "id", "_uuid")):
        return "00000000-0000-4000-8000-000000000000"
    if lowered.endswith(("file", "path", "out", "outfile", "output")):
        return "out.txt"
    if "port" in lowered:
        return "8080"
    if "url" in lowered or "uri" in lowered:
        return "http://127.0.0.1:1"
    return "placeholder"


def render(paths: Sequence[str], root: str,
           env: Optional[Dict[str, str]] = None) -> str:
    """The block printed after a run: what to type, and what is missing.

    Variables already set are marked as such and the rest are shown as
    exports to fill in, so the difference between "you still need this" and
    "this is already handled" is visible without running anything and
    finding out. Values are never printed -- only whether a name is set --
    for the same reason `.env` is read for names only.
    """
    env = os.environ if env is None else env
    lines: List[str] = []

    for path in paths:
        full = path if os.path.isabs(path) else os.path.join(root, path)
        if path.endswith((".html", ".htm")):
            lines.append("  open %s in a browser" % path)
            continue
        found = read(full)
        if found is None or not found.runnable:
            continue

        missing = [n for n in found.required_env if not env.get(n)]
        present = [n for n in found.required_env if env.get(n)]
        for name in missing:
            lines.append("  export %s=...        # required, not set" % name)
        if present:
            lines.append("  # already set: %s" % ", ".join(present))
        for name, default in found.optional_env:
            if not env.get(name):
                lines.append("  # optional: %s (defaults to %s)"
                             % (name, default))

        invocation = ["  python3", path]
        for flag, required, takes_value in found.flags:
            shown = flag + " ..." if takes_value else flag
            invocation.append(shown if required else "[%s]" % shown)
        invocation.extend(name.upper() for name in found.positionals)
        lines.append(" ".join(invocation))

    if not lines:
        return ""
    return "how to run this:\n" + "\n".join(lines)
