"""Grounding an ambiguous request in the project's actual code, before planning.

The planner already gets a listing of existing files and their public names
(`session.existing_files_block`) -- classes, functions, constants, read off
the syntax tree. A name list answers "does Shape exist" but not "which of
these files' logic actually controls how fast it moves" -- that needs bodies,
and handing every file's full body to the planner on every call does not
scale: the planner is the one machine also acting as the sole hard-tier
writer, and a thirty-file project would make its most expensive call pay for
content most requests never needed.

So bodies get read somewhere else, by something sized for a lot of cheap
reading. This runs entirely on the operator's own machine (`mac` in
config.json) against `gemma4:e4b` -- capability 2, this project's own
definition of routine work that does not require real judgement -- one file,
or one chunk of a file, at a time, however many that takes. It bypasses the
pool entirely: `chat_complete` is called directly against that node, never
through `pick_solvers`, because this was never meant to compete with the
swarm for a worker slot, and `enabled: false` on that node only gates the
pool's own selection functions, nothing else. Not the smaller `gemma4:e2b`:
measured live, it mislabeled its own citations (named the file instead of
the function it was reading), and e4b did not. Both are still far lighter
than the dense 12b this node is disabled for -- see `GROUND_MODEL` below.

Tokens spent here are free, so the loop is deliberately exhaustive rather
than clever about which files to skip, and it does not degrade as a project
grows the way a single-call, everything-in-one-prompt approach would: it
scales in call count, not in whether the next file still fits. A file larger
than one call can hold is split at top-level def/class boundaries -- found
with the same AST walk `team.public_names` already uses for ownership -- so a
chunk boundary never lands mid-function and hides the one line that would
have matched.

Every call is asked for a citable verdict, never a paraphrase: relevant or
not, and if relevant, the exact symbol and the exact line, quoted. A small
model free-summarizing "the file handles rotation" is a claim about code with
nothing checking it -- the same failure `audit`'s structured-claims design
already exists to prevent. Quoting the line back means the claim can be
checked against the file it names, and a hallucinated line is dropped by a
plain substring test rather than trusted on the model's say-so.
"""
from __future__ import annotations

import ast
import sys
from typing import Dict, List, Tuple

import swarm

MAC_NODE_NAME = "mac"
# e2b (quality 0.6, 60 tok/s) mislabeled its own citations live -- naming the
# file instead of the symbol, on a file with an unambiguous class and
# function to point at. e4b (quality 0.72, 40 tok/s per config.json) is the
# next size up on the same machine, still far lighter than the dense 12b that
# was measured to cause the stutter this node is disabled for. Traded ~33%
# decode speed for a model actually following the citation format asked of
# it; on files this small the difference is well under a second either way.
GROUND_MODEL = "gemma4:e4b"

# `ground()` and `entry_point_conflicts()` stay pinned to `mac` alone --
# reading a project's own file bodies runs exhaustively, one chunk at a
# time, and that only stays free of charge because it never competes with
# the pool for a worker slot. A one-shot judgement call (does this request
# split into several actions, which endpoints does it mean) is a different
# shape: it is a single request, not a loop, so there is real value in
# letting it fall through to an actual network node when the operator's own
# machine is asleep, busy, or simply turned off -- rather than the whole
# best-effort feature going dark because one specific box did. `e4b-linux`
# runs the same-quality e4b tag and is exactly this kind of light, capable-2
# work; `e2b-2` is weaker (it mislabeled its own citations in the file-body
# case above) but still asked for here, and a wrong split already falls
# back to treating the request as one action rather than acting on a bad
# answer, so the downside of a weaker reader is bounded.
READER_CANDIDATES: List[Tuple[str, str]] = [
    (MAC_NODE_NAME, GROUND_MODEL),
    ("mac2", "gemma4:e4b"),
    ("e4b-linux", "4skl/gemma4-e4b-mtp:latest"),
    ("e2b-2", "gemma4:e2b"),
]


def pick_reader(cfg: "swarm.Config", state: "swarm.State" = None
                ) -> Tuple[object, str]:
    """(node, model) for a single one-shot reading call, or (None, None).

    Tries `READER_CANDIDATES` in order -- `mac` first, since that is the
    node this module has always preferred and the one every docstring above
    was measured against, then the network's own e4b, then e2b. A node
    that does not exist in this config (disabled counts as existing;
    `enabled: false` only gates the pool's own selection, per the module
    docstring above) is skipped. When `state` is given, a candidate
    currently serving another in-flight request (a real lease, not mere
    warmth -- see `team._node_load`) is skipped in favour of the next one
    down the list rather than queuing behind it, since this call is a
    single best-effort judgement, not something worth waiting on one
    specific box for. If every candidate is busy, the most-preferred one is
    used anyway -- returning nothing here is reserved for "no candidate
    exists at all", not "the preferred one happened to be occupied."
    """
    if state is not None:
        try:
            # A lease from a process that has since died must not read as
            # busy -- see `swarm.State.prune_dead_leases`'s docstring for
            # the live case this caught: a crashed writer's ~8-minute-old
            # row made a genuinely idle node look occupied to every reader
            # of `load_for`, this function included.
            state.prune_dead_leases()
        except Exception:                                # noqa: BLE001
            pass
    fallback = None
    for name, model in READER_CANDIDATES:
        node = cfg.node(name)
        if node is None:
            continue
        if fallback is None:
            fallback = (node, model)
        if state is not None:
            try:
                depth, _queue_s = state.load_for(node.name)
                if depth >= node.max_parallel:
                    continue
            except Exception:                            # noqa: BLE001
                pass
        return node, model
    return fallback or (None, None)


def pick_readers(cfg: "swarm.Config", state: "swarm.State" = None,
                 max_n: int = 2) -> List[Tuple[object, str]]:
    """Up to `max_n` distinct (node, model) readers, idle ones preferred.

    `pick_reader` answers "who should do this" with a single name, which is
    the right shape for `ground()`'s own per-chunk loop (many calls, one
    reader, so consistency across chunks matters more than a second
    opinion on any one of them). It is the wrong shape for a single,
    high-stakes, one-shot judgement -- `openapi.propose` deciding which
    endpoints a request means -- where the whole answer rests on whichever
    one node happened to be free.

    Measured live: `propose` on "upload 3 units to the task" needed
    `POST /api/tasks/{task_id}/units/import`, which pure lexical scoring
    ranks 19th of 291 (nowhere near any reasonable cutoff) -- so whether
    the writer ever sees this endpoint rests entirely on one model's one
    sample. `mac` and `e4b-linux` were both busy with other components at
    the time, so the call fell through to `e2b-2`, the weakest reader in
    `READER_CANDIDATES`, with no second sample and no way to tell a good
    answer from a bad one afterward. Replaying the identical call against
    `e2b-2` after the fact got the right answer three times running, which
    means the live miss cannot be pinned on that model being categorically
    wrong here -- but it also means nothing would have caught it if the
    one sample taken *had* come back wrong, since there was only ever one.

    So this returns every currently-idle candidate from `READER_CANDIDATES`
    (via the same `state.load_for` busy check `pick_reader` already uses),
    up to `max_n`, and only falls back to the single most-preferred
    candidate when nothing is idle -- unchanged from `pick_reader`'s own
    behaviour in that case, and identical cost when only one reader is
    free, which is the common case this does not have to pay extra for.
    Callers union the RELEVANT side across every reader consulted (a hint
    from any one of them is still a hint worth having) and only trust
    AVOID where every consulted reader agrees, so one weak or unlucky
    sample can no longer unilaterally drop an endpoint nothing else was
    going to find.
    """
    if state is not None:
        try:
            state.prune_dead_leases()
        except Exception:                                # noqa: BLE001
            pass
    idle: List[Tuple[object, str]] = []
    fallback = None
    for name, model in READER_CANDIDATES:
        node = cfg.node(name)
        if node is None:
            continue
        if fallback is None:
            fallback = (node, model)
        busy = False
        if state is not None:
            try:
                depth, _queue_s = state.load_for(node.name)
                busy = depth >= node.max_parallel
            except Exception:                            # noqa: BLE001
                busy = False
        if not busy:
            idle.append((node, model))
        if len(idle) >= max_n:
            break
    if idle:
        return idle
    return [fallback] if fallback else []

# A file this size or smaller is scanned whole. One number, not a per-call
# estimate, and reused for every call in a pass -- num_ctx is computed once
# from this ceiling and held fixed, because Ollama reloads the model whenever
# num_ctx changes, and a per-file loop that changes it every iteration would
# pay a reload every iteration.
CHUNK_CHARS = 6000

GROUND_PROMPT = """A request was made about a project. Does the file below
matter to carrying it out?

REQUEST: {request}

FILE: {path}
```python
{content}
```

If this file is NOT relevant to the request, reply with exactly:
NOT RELEVANT

If it IS relevant, reply with exactly this form, one line per relevant spot,
quoting the real line from the file above verbatim -- do not paraphrase and
do not invent a line that is not there:
RELEVANT: <symbol name> | <exact line quoted from the file>
"""


def _chunks(path: str, content: str) -> List[Tuple[str, str]]:
    """(label, text) pairs, each small enough for one grounding call."""
    if len(content) <= CHUNK_CHARS:
        return [(path, content)]
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Not parseable -- fall back to a flat split rather than skipping the
        # file outright; a syntax error does not mean the content is useless
        # to a grounding read, only that boundaries cannot be found safely.
        return [("%s (part %d)" % (path, i + 1), content[i:i + CHUNK_CHARS])
                for i in range(0, len(content), CHUNK_CHARS)]
    lines = content.splitlines()
    chunks: List[Tuple[str, str]] = []
    buf: List[str] = []
    buf_len = 0
    for node in tree.body:
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", start)
        if start is None:
            continue
        piece = "\n".join(lines[start - 1:end])
        if buf and buf_len + len(piece) > CHUNK_CHARS:
            chunks.append(("%s (part %d)" % (path, len(chunks) + 1),
                          "\n".join(buf)))
            buf, buf_len = [], 0
        buf.append(piece)
        buf_len += len(piece)
    if buf:
        chunks.append(("%s (part %d)" % (path, len(chunks) + 1), "\n".join(buf)))
    return chunks or [(path, content[:CHUNK_CHARS])]


def _enclosing_symbol(content: str, quoted: str) -> str:
    """The real class/function a quoted line lives in, found mechanically.

    The model is asked to name the symbol itself, and e2b -- tried first,
    measured live against a dancing-shapes project -- sometimes answered with
    the file's own name instead ("RELEVANT: engine.py | def run_loop(shapes):"),
    a small model not quite following the format, not a wrong citation, since
    the quoted line was real either time. e4b (the current GROUND_MODEL) did
    not reproduce this, but nothing about a capability-2 model's own label is
    guaranteed, so this stays in place regardless of which one is configured:
    the same AST walk `team.public_names` uses for ownership finds the
    innermost class/function actually containing the quoted line, which
    cannot be wrong about a file it is reading directly. Falls back to "" if
    the line cannot be located or nothing encloses it (e.g. a bare top-level
    assignment, whose target name the model already gets right on its own).
    """
    lines = content.splitlines()
    lineno = next((i + 1 for i, ln in enumerate(lines)
                   if ln.strip() == quoted.strip()), None)
    if lineno is None:
        return ""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return ""
    scopes: List[Tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                name = "%s.%s" % (prefix, child.name) if prefix else child.name
                start, end = child.lineno, getattr(child, "end_lineno", child.lineno)
                scopes.append((start, end, name))
                walk(child, name)
    walk(tree, "")
    enclosing = [s for s in scopes if s[0] <= lineno <= s[1]]
    if not enclosing:
        return ""
    # Innermost is the one with the smallest span.
    return min(enclosing, key=lambda s: s[1] - s[0])[2]


_WRAP_CHARS = ('"', "'", "`")


def _unwrap(text: str) -> str:
    """Strip one matching pair of quote/backtick characters around a
    citation. Asked to "quote the real line", a model sometimes wraps it in
    its own quote marks or backticks on top of the actual text -- observed
    live, in different runs, as both `"self.pc = target"` and
    `` `self.pc = target` `` -- which then fails a literal substring check
    even though the real content matches once the wrapper is removed. Only
    one pair is stripped, not general quote-stripping, so a line that is
    itself a string literal spanning the whole line is left alone unless it
    was actually wrapped an extra time.
    """
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _WRAP_CHARS:
        return text[1:-1]
    return text


def _parse_findings(path: str, content: str, reply: str,
                    verbose: bool) -> List[str]:
    reply = reply.strip()
    if not reply or reply.upper().startswith("NOT RELEVANT"):
        return []
    out = []
    for line in reply.splitlines():
        line = line.strip()
        if not line.upper().startswith("RELEVANT:"):
            continue
        claim = line.split(":", 1)[1].strip() if ":" in line else ""
        symbol, _, quoted = claim.partition("|")
        symbol, quoted = symbol.strip(), quoted.strip()
        if not symbol:
            continue
        # Refuse anything that cannot be checked against the actual file --
        # the same discipline `audit`'s evidence pass applies to a model's
        # claims about code, just cheap enough here to do with a substring
        # test instead of a second model call. Tried once as given, once
        # with a wrapping quote/backtick pair removed -- never loosened
        # further than that, since a fabricated line (observed live: an
        # entire invented rewrite passed off as a "citation") must still
        # fail no matter how it is quoted.
        if quoted and quoted not in content:
            unwrapped = _unwrap(quoted)
            if unwrapped in content:
                quoted = unwrapped
            else:
                if verbose:
                    sys.stderr.write(
                        "  grounding dropped unverifiable claim in %s: %s | %s\n"
                        % (path, symbol, quoted))
                continue
        if quoted:
            symbol = _enclosing_symbol(content, quoted) or symbol
        out.append("  %s -- %s%s" % (path, symbol,
                                     (": %s" % quoted) if quoted else ""))
    return out


def ground(request: str, existing: Dict[str, str], cfg: swarm.Config,
          state: swarm.State, verbose: bool = True) -> str:
    """Read every existing file and report, per relevant spot, what matters.

    Runs on the operator's own machine against e4b, one chunk at a time,
    however many files or chunks that takes -- exhaustive on purpose, since
    tokens spent here are free and the alternative is guessing which files to
    skip. Returns "" rather than raising if there is nothing to ground (no
    existing files, or the node/model is unavailable): this is a bonus for
    ambiguous requests, never a required stage, so its absence must not fail
    the run that was about to happen anyway.
    """
    node = cfg.node(MAC_NODE_NAME)
    if node is None or not existing:
        return ""

    # One context size for the whole pass, sized off the largest a chunk can
    # be (CHUNK_CHARS), not off any one call's actual prompt -- see the note
    # on CHUNK_CHARS for why this must not vary call to call.
    worst_case = GROUND_PROMPT.format(
        request=request, path="x" * 40, content="x" * CHUNK_CHARS)
    ctx = swarm.pick_num_ctx(cfg, node, GROUND_MODEL,
                             swarm.est_tokens(worst_case), 300, reasoning=False)

    findings: List[str] = []
    scanned = 0
    for path in sorted(existing):
        content = existing[path]
        for label, text in _chunks(path, content):
            scanned += 1
            prompt = GROUND_PROMPT.format(request=request, path=label,
                                          content=text)
            try:
                r = swarm.chat_complete(
                    node, GROUND_MODEL, [{"role": "user", "content": prompt}],
                    num_ctx=ctx, answer_tokens=300, headroom=1.5,
                    think=False, temperature=0.0, timeout=3600)
            except swarm.OllamaError as exc:
                if verbose:
                    sys.stderr.write("  grounding skipped %s: %s\n"
                                     % (label, exc))
                continue
            state.record_perf(node.name, GROUND_MODEL, r["sample"])
            findings.extend(_parse_findings(path, content, r["text"], verbose))
    if verbose:
        sys.stderr.write("  grounding read %d file(s)/chunk(s), %d relevant spot(s)\n"
                         % (scanned, len(findings)))
        # A request is not obligated to name a file, but if reading every
        # file turned up next to nothing to cite, that is worth surfacing
        # rather than silently handing the planner thin context. Measured
        # live: an open-ended "make it run faster" (an invitation to propose
        # something, not a fact to point at) produced exactly this shape --
        # most citations rejected as fabricated -- while "make it dance
        # faster" (a real line, self.speed, existed to name) did not. The
        # fix for the former is wording it more concretely, which this
        # cannot do on the operator's behalf; it can only say so.
        if scanned and len(findings) < max(1, scanned // 3):
            sys.stderr.write(
                "  grounding found little to point at for this request. If "
                "it is open-ended (\"faster\", \"better\", \"cleaner\") "
                "rather than naming a specific behaviour or file, consider "
                "wording it more concretely -- the planner will otherwise "
                "have to guess broadly.\n")
    if not findings:
        return ""
    return ("The request below may be ambiguous about which file(s) it "
           "touches. Reading the project's actual code (not just names), "
           "these spots look relevant -- each one verified to be a real "
           "line in the file named:\n" + "\n".join(findings))


REUSE_PROMPT = """A project has one file that already does something. A new
request just came in. Decide whether the new request continues or revises
what that file already does, or whether it is unrelated work that merely
also wants to be run as a script.

WHAT {path} ALREADY DOES (from the request that produced it):
{reason}

NEW REQUEST:
{request}

Reply with exactly one word, nothing else:
SAME -- the new request revises, fixes, or extends what {path} already does
DIFFERENT -- the new request is a separate task that happens to want a script
"""


def entry_point_conflicts(request: str, reasons: Dict[str, str],
                          cfg: swarm.Config, state: swarm.State,
                          verbose: bool = True) -> List[str]:
    """Which existing entry points this request must not be allowed to reuse.

    `session.existing_files_block` already tells the planner in prose which
    of two things a request is and asks it to choose a path accordingly.
    That is advisory: it sits inside the same prompt as everything else the
    planner is weighing, and it was measured to lose. A request to "upload a
    dummy unit" -- a plainly different task from the applet/project/task
    flow main.py already ran -- was planned as an edit to main.py anyway,
    deleting a script the operator had just used successfully.

    So the same question is asked again, narrowly, and the answer is
    enforced rather than suggested: `intent.rescue_entry_points` renames any
    component whose path appears in this list, regardless of what the
    planner decided. Deciding SAME/DIFFERENT is a natural-language
    judgement no syntax tree can make, but the influence it is allowed to
    have on the plan is made mechanical once it is made.

    Only checks files `session.defines_main` says are entry points and that
    carry a recorded reason -- there is nothing to compare "creates a
    project" against for a file with no history, and nothing here should
    fire on a project's first request. Best-effort like `ground`: no node,
    no reasons, or a request timeout all return [] rather than blocking the
    run that was about to happen anyway. An unparseable reply is treated as
    DIFFERENT, not SAME -- a spurious rename costs a filename; a wrongly
    permitted overwrite costs the file.
    """
    node = cfg.node(MAC_NODE_NAME)
    if node is None or not reasons:
        return []
    blocked: List[str] = []
    for path, reason in sorted(reasons.items()):
        prompt = REUSE_PROMPT.format(path=path, reason=reason[:600],
                                     request=request)
        try:
            r = swarm.chat_complete(
                node, GROUND_MODEL, [{"role": "user", "content": prompt}],
                num_ctx=swarm.pick_num_ctx(cfg, node, GROUND_MODEL,
                                          swarm.est_tokens(prompt), 20,
                                          reasoning=False),
                answer_tokens=20, headroom=1.5, think=False,
                temperature=0.0, timeout=3600)
        except swarm.OllamaError as exc:
            if verbose:
                sys.stderr.write("  entry-point check skipped for %s: %s\n"
                                 % (path, exc))
            continue
        state.record_perf(node.name, GROUND_MODEL, r["sample"])
        verdict = r["text"].strip().upper()
        if verdict != "SAME":
            blocked.append(path)
            if verbose and verdict != "DIFFERENT":
                sys.stderr.write(
                    "  entry-point check for %s: unparseable reply %r, "
                    "treating as DIFFERENT\n" % (path, r["text"][:60]))
    return blocked
