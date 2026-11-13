"""What the operator hands the pool as context: reference files, and credential names.

Two things live here because they are the same job from the pool's side --
material the operator supplies rather than material the pool discovers -- and
because they are each other's safety net. A `.env` is the one file that must
never be attached, and `env_names` is the reason nobody needs to: it supplies
the only part of that file a model can use.


The pool writes code for a project it cannot see. `session.py` tells the
planner what was asked before and what files exist; `ground.py` reads those
files to find the line a vague request is really about. Neither covers the
case where the thing the models need is not code at all -- a design document,
a wire format, a schema, an example of the output somebody wants. That was
previously supplied by pasting it into the request, which is the one thing a
context mechanism exists to avoid.

Three decisions here are load-bearing.

**Paths are stored, contents are not.** A turn in `session.py` is a few lines
of JSON and this is the same: a list of paths, re-read from disk on every run.
Storing the text would freeze it at the moment it was attached, so editing the
design document would silently stop affecting the build -- the failure would
look like the pool ignoring the spec, with nothing on screen to suggest why.

**Oversize is refused, and refused before the run starts.** Same rule as the
whole-file rewrite path, for a sharper reason. An attachment goes into *every*
writer's prompt, so checking it per-worker would attach the document on the
boxes with room and drop it on the ones without, producing a package where
some files honour the spec and some never saw it. Nothing downstream can
detect that. So the budget is checked once, against the narrowest window in
the fleet, before any pool time is spent.

**An attachment is labelled as reference, never as work.** The CONTRACT
broadcast already demonstrated what happens otherwise: handed a complete
`Shape(x, y, size, color)` signature under "use these exact names", a
`constants.py` asked for a colour list wrote the entire class, `main()`, and a
pygame loop. A design document describing a class is the same shape of input
and invites the same mistake, so the framing says outright that this is
background to satisfy and not a file to reproduce.
"""

import os
from typing import Dict, List, Optional, Sequence, Tuple

import swarm

# How much of the narrowest window an attachment set may occupy. The writer
# still needs room for its spec, the contract, any dependency source it was
# handed, and the answer itself -- the attachment is the one term here that is
# constant across every component, so it is also the one that can be budgeted
# up front. Raised 0.5 -> 1.0 on request (2x): on the narrowest box in the
# fleet this now leaves nothing reserved for the rest of that prompt, traded
# deliberately for headroom on real attachment sets -- an API slice alone hit
# 16327 of a 16384-token ceiling under the old fraction. `pick_num_ctx` still
# clamps to `node.max_ctx(model)` regardless of this value, so a component
# whose attachment plus spec together exceed the assigned node's own ceiling
# is not refused here; it is undersized at the model call, which is a real
# regression from the previous margin, not merely a bigger number. It only
# bites when a component actually lands on the box with the smallest window
# while attachments are near the new ceiling -- everywhere larger still has
# slack -- but it is not eliminated, only accepted as the cost of 2x.
ATTACH_FRACTION = 1.0

# Fence language by extension, so a markdown design note is not presented as
# Python. Cosmetic for a large model and not for a small one: a fence saying
# `python` over prose is a claim about the content that the reader has to
# override, and these readers are exactly the ones that will not.
_FENCE = {
    ".py": "python", ".pyi": "python",
    ".md": "markdown", ".markdown": "markdown", ".rst": "rst",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".csv": "csv", ".tsv": "tsv",
    ".sql": "sql", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".tsx": "tsx", ".jsx": "jsx", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".java": "java",
    ".html": "html", ".css": "css", ".xml": "xml", ".proto": "proto",
}


def fence(path: str) -> str:
    """The code-fence language for one path, or `text` when unknown."""
    return _FENCE.get(os.path.splitext(path)[1].lower(), "text")


# Files that exist to hold credentials. Matched by name, never by content:
# guessing "this looks like a secret" from the text would refuse legitimate
# attachments, and the house rule is that a detector which fires on correct
# input costs more than it saves. A name is decidable.
_SECRET_NAMES = {"credentials", "id_rsa", "id_ed25519", "id_ecdsa",
                 ".netrc", ".pgpass", ".htpasswd"}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")

# Read for variable names only. The right-hand side of a line here is never
# put into a variable, a log, or a prompt.
ENV_FILES = (".env", ".env.local")


def is_secret_file(path: str) -> bool:
    """True when this path is a credential store rather than reference material."""
    name = os.path.basename(path).lower()
    return (name in _SECRET_NAMES
            or name == ".env" or name.startswith(".env.")
            or name.endswith(_SECRET_SUFFIXES))


def env_names(root: str) -> List[str]:
    """Names of the variables in this project's `.env`, and nothing else.

    The point of a `.env` is that the values are secret, so they are never
    read here -- each line is split once and everything right of the `=` is
    dropped before anything else happens to it. What the pool gets is the
    vocabulary: that `STRIPE_API_KEY` is a thing that will exist at run time,
    so generated code can reach for it by the correct name instead of
    inventing `STRIPE_KEY` or, worse, a plausible-looking literal.

    Order is preserved and duplicates removed, so the block stays stable
    between runs -- a set would reorder it and change `num_ctx` for no reason.
    """
    names: List[str] = []
    for filename in ENV_FILES:
        full = os.path.join(root, filename)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].strip()
            if name.startswith("export "):
                name = name[len("export "):].strip()
            # An identifier, or it is not a variable declaration and this has
            # misread something. Refusing to guess keeps a stray `=` in a
            # comment or a URL out of the block.
            if name and name.replace("_", "").isalnum() and not name[0].isdigit():
                if name not in names:
                    names.append(name)
    return names


def render_env_names(names: Sequence[str]) -> str:
    """Tell the pool which variables exist, without telling it what they are."""
    if not names:
        return ""
    return ("\nThese environment variables will be set when this code runs. "
            "Read them with os.environ by these exact names. Their values are "
            "secret and have deliberately not been shown to you -- never "
            "guess one, never write a literal in their place, and never print "
            "or log one. If a required variable is missing at run time, raise "
            "an error naming it:\n%s\n"
            % "\n".join("  %s" % n for n in names))


def store(root: str, path: str) -> str:
    """How a path is recorded: relative when inside the project, else absolute.

    A design document living in the project should survive the directory being
    moved or cloned; one living elsewhere on the machine has no relative form
    worth keeping, so it is pinned absolutely and allowed to break loudly if
    it goes away.
    """
    full = os.path.abspath(os.path.join(root, os.path.expanduser(path)))
    rel = os.path.relpath(full, os.path.abspath(root))
    return full if rel.startswith(os.pardir) else rel


def resolve(root: str, path: str) -> str:
    """Absolute location of a stored path."""
    return os.path.abspath(os.path.join(root, os.path.expanduser(path)))


def read(root: str, paths: Sequence[str]) -> Tuple[Dict[str, str], List[str]]:
    """Read every attachment, returning the ones that worked and the failures.

    Missing files are reported rather than raised. An attachment that has been
    deleted or renamed since it was attached is a normal thing to happen to a
    sticky list, and it should be said on screen and then worked around, not
    turned into a crash that takes the run with it.
    """
    content: Dict[str, str] = {}
    problems: List[str] = []
    for path in paths:
        full = resolve(root, path)
        # A credentials file is never reference material, and attaching one
        # would put its contents in every writer's prompt on three machines.
        # Refused rather than warned about: there is no version of this that
        # the operator meant, and `env_names` already supplies the only part
        # of a `.env` that is useful to a model.
        if is_secret_file(path):
            problems.append("%s: refused, this holds credentials. Its "
                            "variable NAMES are shared automatically; the "
                            "values are not and should not be" % path)
            continue
        if not os.path.isfile(full):
            problems.append("%s: not found" % path)
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                content[path] = fh.read()
        except OSError as exc:
            problems.append("%s: %s" % (path, exc))
    return content, problems


def render(content: Dict[str, str]) -> str:
    """The block handed to the planner and to every writer.

    Says what the material is for before showing it. The instruction not to
    reproduce it is not boilerplate caution: a document describing a class,
    pasted under no framing at all, reads to a small model as a request to
    write that class -- the same failure the unqualified CONTRACT broadcast
    produced before ownership notes were added.
    """
    if not content:
        return ""
    parts = ["\nReference material the operator attached to this project. It "
             "is background for the work -- a specification to satisfy, a "
             "format to match, or context to respect. Do NOT reproduce any of "
             "it as a source file, and do not restate it in what you write:\n"]
    for path in sorted(content):
        parts.append("\n--- %s ---\n```%s\n%s\n```\n"
                     % (path, fence(path), content[path].rstrip("\n")))
    return "".join(parts)


def narrowest_window(cfg: swarm.Config) -> int:
    """Smallest context window any enabled model might have to write against.

    The plan does not know which box takes which component -- the claim loop
    decides that at run time, by who is free -- so the only window an
    up-front check can safely assume is the smallest one that could come up.
    """
    windows = [node.max_ctx(model)
               for node in cfg.enabled_nodes() for model in node.models]
    return min(windows) if windows else 16384


def budget(cfg: swarm.Config) -> int:
    """Token ceiling for the whole attachment set."""
    return int(narrowest_window(cfg) * ATTACH_FRACTION)


def check_size(block: str, cfg: swarm.Config) -> Optional[str]:
    """Why this attachment set cannot be used, or None when it fits.

    Refusing names the cost and the limit, because the operator's next move
    depends on the gap: a document slightly over wants a section removed, one
    at four times the ceiling wants a different approach entirely.
    """
    cost, limit = swarm.est_tokens(block), budget(cfg)
    if cost <= limit:
        return None
    return ("attachments total ~%d tokens, over the %d-token limit "
            "(%.0f%% of the narrowest window in the fleet, %d). Every writer "
            "carries this, so it cannot be attached to only some of them. "
            "Attach fewer files, or a shorter extract."
            % (cost, limit, ATTACH_FRACTION * 100, narrowest_window(cfg)))


def describe(content: Dict[str, str], cfg: swarm.Config) -> str:
    """One line per attachment plus the total, for printing before a run.

    Attachments are sticky, so they are also the thing most likely to be
    forgotten about -- an operator who attached a document three requests ago
    and has since changed their mind should not have to go looking for why the
    pool keeps mentioning it. Everything carried gets named every time.
    """
    if not content:
        return ""
    lines = ["attached:"]
    for path in sorted(content):
        lines.append("  %-40s ~%d tokens"
                     % (path[:40], swarm.est_tokens(content[path])))
    if len(content) > 1:
        lines.append("  %-40s ~%d of %d allowed"
                     % ("total", swarm.est_tokens(render(content)),
                        budget(cfg)))
    return "\n".join(lines)
