#!/usr/bin/env python3
"""Slice a large OpenAPI document down to the part a request is about.

A spec is the case `attach.py` cannot take and `ground.py` should not:
too large for the narrowest window in the fleet, and not the kind of thing
a small model should be reading. The reason is the same one behind
`crossref.py` and `patterns.py` -- whatever is decidable mechanically is
decided mechanically, before any model is asked.

An OpenAPI document is entirely decidable. It is not prose that has to be
understood to be searched; it is a dict keyed by path, carrying the tags,
the operationId and the summary of every operation in it. Asking `gemma4:e4b`
to find the relevant endpoints is asking a 4B model to do `json.load` badly,
and it fails in a way that matters: handed a chunk containing

    "requestBody": {"$ref": "#/components/schemas/UserCreate"}

it has no way to know what fields `UserCreate` has, because that object is
somewhere else in a file it is reading one piece at a time. A writer given
that chunk invents the fields. Following the reference is a graph walk, it
is four lines, and it cannot be wrong.

So this module does two things a model cannot do reliably and one it cannot
do at all:

1. **Index.** One line per operation, for the whole document. Small enough
   to attach unconditionally regardless of spec size, which means the pool
   always knows the full surface exists even when it has been handed the
   detail for only a few endpoints. A slice with no index is how you get a
   writer inventing `POST /api/users/create` when `POST /api/auth/users` was
   right there.

2. **Select.** Score operations against the request by term overlap over
   their path, tags, operationId and summary. Purely lexical, deliberately:
   a scoring function that can be read off the input is one whose misses
   are diagnosable, and the failure mode of a model here -- confidently
   returning the wrong endpoint -- is worse than the failure mode of a
   substring match, which is returning too many.

3. **Resolve.** Follow every `$ref` reachable from the selected operations,
   transitively, and emit exactly those schemas. This is the part that makes
   the output usable rather than merely small.

Redundancy is dropped on the way out. In a FastAPI-generated document every
single operation carries an identical 422 `HTTPValidationError` response and
an identical `security` block; on a 150-endpoint spec that is thousands of
tokens saying one thing. It is stated once, in the header.
"""

import argparse
import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import swarm

# Terms that match everything match nothing useful. Kept deliberately short:
# a long stoplist starts discarding words that carry the request ("get",
# "list" and "create" are the verbs that distinguish one operation from
# another here, so they are emphatically not stopwords).
STOPWORDS = frozenset("""
a an and are as at be but by for from how i if in into is it its me my of on
or our so than that the their then there these they this to up us was we what
when where which who will with would you your
""".split())

# Boilerplate every FastAPI operation repeats. Said once in the header.
NOISE_RESPONSES = frozenset(["422"])
NOISE_KEYS = frozenset(["title"])


# Registered specs live beside the pool, not in a project tree, for the same
# reason `session.py` keeps transcripts there: a spec is a fact about an
# external service, not about the checkout that happens to be open. Putting
# them here means `--api maven` resolves from any directory, which is the
# whole point of registering one.
SPEC_DIR = os.path.join(swarm.HERE, ".swarm", "specs")
REGISTRY = os.path.join(SPEC_DIR, "registry.json")


def load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def registry() -> Dict[str, Any]:
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def registered_path(name: str) -> str:
    return os.path.join(SPEC_DIR, name + ".json")


def resolve(spec_arg: str) -> str:
    """A file path, or the name of a registered spec.

    A real path always wins. Checking the filesystem first means registering
    a spec can never shadow a file sitting in front of you, which is the one
    way an alias can quietly do the wrong thing.
    """
    if os.path.isfile(spec_arg):
        return spec_arg
    if spec_arg in registry():
        return registered_path(spec_arg)
    return spec_arg


def fetch(url: str, dest: str) -> int:
    """Pull a spec over HTTP and write it, parsed-and-reserialised.

    Parsing before writing is the check: a 200 carrying an HTML login page or
    a truncated body fails here, with the file left alone, instead of being
    saved and failing later as a confusing selection miss.
    """
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read().decode("utf-8")
    spec = json.loads(raw)
    if "paths" not in spec:
        raise ValueError("no 'paths' key -- this does not look like OpenAPI")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    return len(operations(spec))


def register(name: str, url: str) -> int:
    """Fetch a spec and remember where it came from, so it can be refreshed."""
    count = fetch(url, registered_path(name))
    known = registry()
    # A registered spec that already had excluded tags keeps them across a
    # `--refresh` -- the operator's "I never want this" is a fact about the
    # service, not about one fetch of its document, and re-pulling the spec
    # must not silently reopen a domain that was deliberately shut off.
    excluded = (known.get(name) or {}).get("exclude_tags", [])
    known[name] = {"url": url, "fetched": time.time(),
                   "operations": count}
    if excluded:
        known[name]["exclude_tags"] = excluded
    with open(REGISTRY, "w", encoding="utf-8") as fh:
        json.dump(known, fh, indent=2)
    return count


def excluded_tags(spec_arg: str) -> Set[str]:
    """Tags this spec's operations must never be selected under, standing
    across every future run rather than one request at a time.

    `avoid` (from `propose`) is a per-request judgement about one sentence;
    this is the opposite kind of fact -- "I never want telephony" said once
    means never again, not until the next request happens to word itself
    differently. Read by name (a real file on disk resolves to no entry and
    excludes nothing, matching `resolve`'s own rule that a file in front of
    the operator is never shadowed by registry metadata).
    """
    return set((registry().get(spec_arg) or {}).get("exclude_tags", []))


def set_excluded_tags(name: str, tags: Sequence[str], add: bool = True) -> Set[str]:
    """Persist (or clear) the standing tag exclusion for a registered spec.

    `add=False` removes the named tags instead of adding them, so a
    standing "never" can be undone the same way it was set, rather than
    only by hand-editing the registry file.
    """
    known = registry()
    entry = known.setdefault(name, {})
    current = set(entry.get("exclude_tags", []))
    current = (current | set(tags)) if add else (current - set(tags))
    entry["exclude_tags"] = sorted(current)
    with open(REGISTRY, "w", encoding="utf-8") as fh:
        json.dump(known, fh, indent=2)
    return current


def _terms(text: str) -> Set[str]:
    """Words worth matching on, lowercased, split on anything non-alphanumeric.

    Splitting on underscores and slashes is what makes an operationId like
    `list_projects_api_projects_get` and a path like `/api/projects` searchable
    with the same query that a human would type.
    """
    return {w for w in re.split(r"[^a-z0-9]+", text.lower())
            if len(w) > 1 and w not in STOPWORDS}


# A chained request's own dependency wiring names the PRODUCER step's
# directory, and this project's own convention numbers and names those
# directories after what that earlier step does -- "../05_create_applet/
# output.json". That is a fact about this project's folder-naming
# convention, not about the API, and `_terms`'s blanket split-on-anything-
# non-alnum cannot tell the two apart: "create" and "applet" come out of
# that path exactly like they would from a sentence genuinely about
# creating an applet. Usually harmless, because a step reading a
# sibling's id is usually still talking about the same resource -- but
# not always: "upload 3 units to the task whose task_id is read from
# ../02_create_task/output.json" is an upload, not a create, and the
# producer folder's own name leaks the word "create" into an action that
# never asked to create anything, right alongside the same collision this
# project already fixed once for "save"/"output"/"json"
# (`_NARRATIVE_TERMS`). Stripped before scoring and before `propose`'s own
# prompt, since neither one needs the literal path at all -- only the
# writer's SPEC does, and this never touches that text.
_SIBLING_PATH_RE = re.compile(r"\.\./[\w\-./]*")

# The replacement text used to read " a prior step's saved file " -- prose
# chosen to be readable, and never checked against the one thing that
# actually mattered: whether `_terms()` extracts anything new from it.
# "file", "step", "saved" and "prior" all survive that split, and "file"
# in particular carries a real IDF weight in a real spec (rare enough to
# score ~5.7, against ~1.8 for "task" in a document that is mostly about
# tasks) -- so a request that never said "file" anywhere gained a strong,
# spurious vote for every file-shaped endpoint in the document purely
# because this cleanup used the word. Measured live: it pushed two
# SharePoint sync/preview endpoints and a presign-upload endpoint above
# the actual unit-import match for "upload three units to this task".
# Every word in the replacement is now one `_terms()` already discards on
# its own (`STOPWORDS`), so the cleanup can never itself become a new,
# accidental search term -- checked directly in `openapi_check.py`.
_SIBLING_PATH_REPLACEMENT = " this "


def _strip_sibling_paths(text: str) -> str:
    """`text` with every relative sibling-directory reference replaced by
    a neutral phrase, for use anywhere a request is being matched against
    endpoints rather than handed to a writer.
    """
    return _SIBLING_PATH_RE.sub(_SIBLING_PATH_REPLACEMENT, text)


# A request's own verb and the API's verb for the identical action are
# frequently not the same word, and pure term overlap has no way to bridge
# that -- it can only match spelling, not meaning. Measured live: "upload
# three units to the task" needed `POST /api/tasks/{task_id}/units/import`,
# and lexical scoring alone ranked it 19th of 291 operations because
# "upload" and "import" share no substring. `propose`'s model hint can
# bridge this kind of gap (and did, every time this exact case was
# replayed against it directly) but that is a single best-effort model
# call on whichever node happens to be idle -- this is the same fix
# `close_over_ids`/`_NARRATIVE_TERMS` already use elsewhere in this file:
# where a specific collision has actually been measured, encode it once,
# mechanically, so the answer does not depend on a model sample at all.
#
# Deliberately narrow and one-directional (request word -> API word), the
# same restraint `_NARRATIVE_TERMS` documents for the identical reason: a
# synonym table that tries to cover every near-synonym in English stops
# being a targeted fix and becomes a second, unaudited scoring function.
# Only pairs actually measured causing a real endpoint to rank outside a
# usable cutoff belong here. See `openapi_check.py` for the sabotage check.
_REQUEST_SYNONYMS: Dict[str, Set[str]] = {
    "upload": {"import"},
    "uploads": {"import"},
    "uploading": {"import"},
    "uploaded": {"import"},
}


def _expand_request_terms(text: str) -> Set[str]:
    """`_terms(text)`, plus each hit's `_REQUEST_SYNONYMS` additions.

    Only ever adds terms -- it cannot cause a real match to be lost, the
    same one-directional safety `_NARRATIVE_TERMS`'s removal-only design
    has in the other direction. Use this in place of a bare `_terms(...)`
    call anywhere a *request* (not document text) is being turned into
    terms for scoring; document-side term extraction must stay on plain
    `_terms` so the synonym table cannot inflate its own IDF weights.
    """
    base = _terms(text)
    extra: Set[str] = set()
    for word in base:
        extra |= _REQUEST_SYNONYMS.get(word, set())
    return base | extra


def _synonym_segment_match(path: str, request: str) -> bool:
    """True when `path` has a whole segment (split on `/`, `{param}`
    braces stripped) that exactly equals one of `_REQUEST_SYNONYMS`'s
    expansions of a word actually present in `request`.

    Deliberately narrower than "any wanted term is an exact segment
    match": a *raw* word from the request coincidentally matching a path
    segment is precisely the case `slice_for`'s `avoid` veto exists to
    override -- "the unit should be empty, it's just data collection"
    scoring `.../collection/units` on the shared word "collection" is
    the documented, deliberate example, and a veto correcting that must
    keep working exactly as it does today.

    A synonym-table match is different in kind. It is this project's own
    curated, high-confidence claim that one word really does mean
    another, added specifically because a model was measured missing the
    connection on its own -- `_REQUEST_SYNONYMS`' only entries today map
    "upload"/"uploaded"/"uploading"/"uploads" to "import". Measured live:
    `units/import` scored #1 for "upload three units to that task" via
    exactly that mapping, a reader model vetoed it anyway, and the writer
    -- left with only `units/import-csv` and `units/import-multiturn`,
    which merely *contain* "import" as part of a compound segment rather
    than *being* it -- invented a wrong-purpose call to `claim-next`
    instead of using either. Reproduced directly: passing that same
    veto into `slice_for` here removes exactly `units/import` and leaves
    the two compound siblings, matching the live slice byte for byte.

    So a synonym-table match is protected from `avoid` the same way an
    explicit `paths`/`tags` argument already is (see `slice_for`'s own
    docstring) -- a model's per-request veto of a mapping this project
    itself curated is the thing most likely to be wrong, not the
    mapping, and unlike a raw word overlap this is never a coincidence.
    """
    base = _terms(_strip_sibling_paths(request))
    synonyms: Set[str] = set()
    for word in base:
        synonyms |= _REQUEST_SYNONYMS.get(word, set())
    if not synonyms:
        return False
    segs = {s.strip("{}").lower() for s in path.split("/") if s}
    return bool(segs & synonyms)


def _body_field_terms(op: Dict[str, Any], spec: Dict[str, Any]) -> Set[str]:
    """Words worth matching from what an operation actually reads, not just
    its name.

    `index_block` deliberately shows no schemas, and that has a cost this
    module was not paying attention to: "Update Project" carries zero
    lexical signal that the field being updated is `applet_version_id`, so
    a request built entirely out of "applet" and "version" scores it
    identically to every other "Update X" endpoint in the document.
    Measured live: asked to "assign the most recent version of the applet
    to the project", `score()` ranked `PATCH /api/applets/{applet_id}/status`
    -- an unrelated admin toggle whose body is an untyped object -- above
    `PATCH /api/projects/{project_id}`, whose `ProjectUpdate` schema is
    exactly `applet_version_id`, because "applet" is a literal path token
    on the wrong endpoint and nowhere at all on the right one.

    So the request body's own field names join the scored surface. Resolved
    through `$ref` the same way `reachable_schemas` does for the detailed
    slice, one level of `allOf`/`anyOf`/`oneOf` deep, which is enough for a
    Pydantic-shaped spec: the schema is either the object itself or one
    `$ref` away from it. This is a graph walk with a right answer, not a
    guess, so it belongs beside `reachable_schemas` rather than beside
    `propose`.

    Returned per field name rather than flattened into one set, because a
    single generic word inside one field name is exactly the coincidence
    `avoid` exists to police at the path/summary level, and flattening
    would let it back in through the side door: `AddMemberRequest.user_id`
    shares "user" with "create a user account" for a reason that has
    nothing to do with creating a user, and one shared word is not enough
    to tell that apart from `applet_version_id` sharing "applet" AND
    "version" with a request about assigning an applet version -- two
    words from the same field is not a coincidence in the way one word
    is. `score` uses this shape to require both.
    """
    body = op.get("requestBody")
    if not isinstance(body, dict):
        return []
    content = body.get("content") or {}
    payload = content.get("application/json") or {}
    schema = payload.get("schema")
    fields: List[Set[str]] = []
    seen_refs: Set[str] = set()
    pending = [schema]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            pending.append(_resolve(ref, spec))
            continue
        props = node.get("properties")
        if isinstance(props, dict):
            for name in props:
                terms = _terms(name)
                if terms:
                    fields.append(terms)
        for key in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(key) or []:
                pending.append(sub)
    return fields


def operations(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every operation in the document, flattened, in declaration order."""
    found = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in ("get", "put", "post", "delete", "patch",
                                      "head", "options", "trace"):
                continue
            if not isinstance(op, dict):
                continue
            found.append({
                "method": method.upper(),
                "path": path,
                "op": op,
                "tags": list(op.get("tags") or []),
                "summary": op.get("summary") or "",
                "operationId": op.get("operationId") or "",
                "body_terms": _body_field_terms(op, spec),
            })
    return found


def index_block(spec: Dict[str, Any],
                tags: Optional[Set[str]] = None) -> str:
    """One line per operation, grouped by tag. The always-affordable form.

    Deliberately carries no schemas and no parameters. Its job is to make the
    surface known, so a request that needs an endpoint nobody sliced in
    produces "I need /api/teams/{team_id}/members" rather than an invention.

    `tags` narrows it to the neighbourhoods the selection already landed in.
    Measured on a 291-operation spec, the whole index is 5701 tokens against
    an attachment budget of 16384 -- 35% of everything a request may spend,
    on a catalogue that is mostly endpoints it will never touch. Scoped to
    the tags actually chosen, the same insurance costs 1315-3418.

    It is also better insurance, not merely cheaper. A writer building a
    teams client is far more likely to need a Teams endpoint nobody sliced
    in than a Telephony one, and 129 lines it might use beats 291 it mostly
    will not.
    """
    ops = operations(spec)
    if tags is not None:
        ops = [e for e in ops if set(e["tags"] or ["Other"]) & tags]
    if not ops:
        return ""
    info = spec.get("info") or {}
    scope = ("every endpoint" if tags is None
             else "every endpoint in the areas below")
    lines = ["# %s %s -- %s" % (info.get("title", "API"),
                                info.get("version", ""), scope),
             "# Full detail is given below for the relevant ones only. If you "
             "need an endpoint listed here but not detailed, say so rather "
             "than guessing its parameters.", ""]
    if tags is not None:
        # Say what was left out, or the list reads as the whole API and an
        # absent endpoint reads as one that does not exist -- which is the
        # invention this block exists to prevent, reintroduced by the very
        # thing that made it affordable.
        lines.insert(2, "# Other areas of this API exist and are not listed "
                        "here. Ask if you need one.")
    by_tag: Dict[str, List[Dict[str, Any]]] = {}
    for entry in ops:
        by_tag.setdefault(entry["tags"][0] if entry["tags"] else "Other",
                          []).append(entry)
    for tag in sorted(by_tag):
        lines.append("## %s" % tag)
        for entry in by_tag[tag]:
            lines.append("  %-6s %s%s" % (entry["method"], entry["path"],
                                          ("  -- " + entry["summary"])
                                          if entry["summary"] else ""))
        lines.append("")
    return "\n".join(lines).rstrip()


def _strong_terms(entry: Dict[str, Any]) -> Set[str]:
    """The identifying surface of an operation: path, id, summary, tags."""
    return _terms(entry["path"] + " " + entry["operationId"] + " "
                  + entry["summary"] + " " + " ".join(entry["tags"]))


def weights(ops: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """How much each term distinguishes one operation from another, here.

    A fixed stoplist cannot know that `api` opens 98.3% of the paths in this
    document, and the consequence is not subtle: every term is worth the
    same, so "generate an API key" scores `api` -- which matches essentially
    everything -- exactly as highly as `key`, which matches almost nothing.
    Measured before this existed, that pushed `/collection/export` and
    `/telephony/units/bulk-download-urls` above `/api/auth/login`.

    So rarity is read off the document instead of guessed. A term in every
    operation gets weight zero and drops out; a term in three gets a large
    one. That is the ordinary inverse-document-frequency argument, and the
    reason it belongs here rather than in a constant is that the next spec
    will have a different common prefix and needs no code change to work.

    It also retires two special cases that would otherwise want hardcoding:
    `id` (78% of paths) and the `_get` / `_post` suffixes every operationId
    carries, which are redundant anyway because the method is a field.

    The English stoplist stays. It is doing the opposite job and this cannot
    replace it -- "the" is *rare* in a path, so frequency alone would make it
    a high-value term rather than a worthless one.
    """
    total = len(ops)
    if not total:
        return {}
    seen: Dict[str, int] = {}
    for entry in ops:
        for term in _strong_terms(entry):
            seen[term] = seen.get(term, 0) + 1
    return {term: max(0.0, math.log(total / count))
            for term, count in seen.items()}


DECOMPOSE_ACTIONS_PROMPT = """A developer asks: "%s"

Does completing THIS SPECIFIC request need ONE call against an API, or
SEVERAL distinct ones? A request often states several as if they were one
step: making a new thing can silently also require pointing something
that already exists at it, and a request naming only the first action
does not mean the second is not needed. Judge only the request quoted
above -- do not reuse the wording of this instruction as your answer.

A clause about saving a result to a local file -- "save its id to
output.json", "under the same key the API response uses for it", "the
runnable entry point is main.py" -- describes what the SCRIPT does with a
value it already has, not a further call against the API. Never list one
of these as its own action; fold it into whichever action actually
produces the value being saved.

If ONE, reply with exactly:
ONE

If SEVERAL, list each distinct action on its own line, using the
request's own words and details, each short enough to stand alone, most
important first. Nothing else."""


_STOPWORDS = frozenset(
    "a an the to of for in on at with and or that this these those is "
    "are was were be been being it its as by from into onto over under "
    "up down out off then than so if do does did not no yes each every "
    "any all some new use uses used using make made call calls step "
    "steps action actions distinct several one write script writes "
    "task tasks id ids".split())


def _content_words(text: str) -> Set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
           if len(w) > 2 and w not in _STOPWORDS}


def _actions_relate_to_request(actions: Sequence[str], request: str) -> bool:
    """Refuse a SEVERAL split whose own lines share no real vocabulary with
    the request they claim to decompose.

    A small model handed a prompt containing its own worked example
    ("creating the version AND separately pointing whatever already uses
    this thing at the new one") can echo that example back nearly
    verbatim as if it were the answer, regardless of what was actually
    asked. Measured live: asked to split "write a script that uploads n
    units to a task id", `gemma4:e4b` replied with "Create the new
    version" and "Update the task to use the new version" -- a request
    about versions was never made, and neither action names a unit or an
    upload. This is the same class of failure `propose()`'s AVOID veto and
    `ground()`'s substring check already exist to catch elsewhere in this
    module: a model's claim, verified against the thing it claims to be
    about, not trusted on its own say.

    Deliberately loose -- one shared content word across the *whole* set
    of actions is enough to pass, since a genuine split still uses the
    request's own nouns for at least one of its lines. Requiring every
    line to match would reject a correct split whose second action is a
    legitimate implication the request never stated in those words (the
    "point the project at it" half of a version push, which is exactly
    the case this mechanism exists to surface).
    """
    req_words = _content_words(request)
    if not req_words:
        return True
    action_words: Set[str] = set()
    for a in actions:
        action_words |= _content_words(a)
    return bool(req_words & action_words)


def decompose_actions(request: str, cfg: Any, state: Any,
                      verbose: bool = True) -> List[str]:
    """Split a request into distinct actions before picking endpoints.

    `propose` already sees the whole index in one call and can name several
    unrelated endpoints in one reply -- the shape of the mechanism was never
    the limit. What was missing is scrutiny of the request itself: asked
    outright "what endpoints does this need", a model tends to answer the
    most salient action and stop, the same way a person asked to summarise
    a to-do list mentions the interesting item and forgets the follow-up.
    Live: "push a new applet version... there is already an applet here"
    read as one action -- create the version -- and never surfaced the
    endpoint that points a project at it, because nothing in the prompt
    asked whether pushing a version was the whole job.

    So this asks that question first, separately, as its own narrow
    judgement: not "what endpoints" but "how many distinct things does this
    require". A request that really is one action costs one extra small
    call and changes nothing else. A request that is several gets each one
    handed to `propose` on its own, focused text rather than diluted across
    a compound sentence -- which also helps with the unrelated failure mode
    `propose`'s own prompt already guards against, a request carrying pasted
    output from a previous run: a shorter, single-purpose action line has
    less room for that noise to compete in.

    Best-effort, like the rest of this module: no local node, an error, or
    a reply that resolves to nothing all return `[request]` unchanged --
    exactly what running `propose` once on the original text already did.
    """
    if not request.strip():
        return []
    try:
        import ground
        node, model = ground.pick_reader(cfg, state)
    except Exception:                                   # noqa: BLE001
        return [request.strip()]
    if node is None:
        return [request.strip()]

    prompt = DECOMPOSE_ACTIONS_PROMPT % request.strip()
    try:
        ctx = swarm.pick_num_ctx(cfg, node, model, swarm.est_tokens(prompt), 200)
        # 3600s, not the usual few-hundred: `mac` and every other reader
        # candidate `pick_reader` can fall through to are each single
        # Ollama instances, and a `do` write phase now launches several
        # `team do` processes in parallel (see LOCAL.md's nohup section)
        # that can each want the same one at once. A short timeout here
        # just means "busy" reads as "unreachable" and this best-effort
        # feature degrades needlessly; the fallback on failure is always the
        # same regardless of how long the wait was, so there is no
        # correctness cost to waiting -- only a slower fallback in the rare
        # case every reader candidate is genuinely down rather than just
        # queued.
        reply = swarm.chat_complete(
            node, model, [{"role": "user", "content": prompt}],
            num_ctx=ctx, answer_tokens=200, headroom=1.2,
            think=False, temperature=0.0, timeout=3600)
    except Exception as exc:                            # noqa: BLE001
        if verbose:
            sys.stderr.write("  action split unavailable (%s) -- treating "
                             "as one action\n" % exc)
        return [request.strip()]
    try:
        state.record_perf(node.name, model, reply["sample"])
    except Exception:                                   # noqa: BLE001
        pass

    raw = [ln.strip().lstrip("-*0123456789.) ").strip().strip("`'\"")
          for ln in (reply.get("text") or "").splitlines()]
    # A stray ONE/SEVERAL heading token is stripped regardless of position,
    # rather than trusted only at the top -- a model that echoes the
    # instruction word before or after its answer must not turn a genuine
    # split into a phantom action.
    actions = [ln for ln in raw if ln and ln.upper().rstrip(":") not in
              ("ONE", "SEVERAL")]
    if len(actions) <= 1:
        # Either the model said ONE, or it said SEVERAL and then named only
        # one thing anyway -- both mean the original text is used as-is,
        # not a possibly-reworded single line, so wording never drifts on
        # the common case.
        return [request.strip()]
    if not _actions_relate_to_request(actions, request):
        # The split shares no real vocabulary with what was actually asked
        # -- the shape of a small model echoing this prompt's own worked
        # example instead of reading the request. A wrong split sends
        # `propose_multi` off after endpoints for a job nobody asked for
        # and, per this project's own rule for a model claim with nothing
        # checking it, that is worse than the one extra call this function
        # exists to make: fall back to treating the request as ONE, the
        # same outcome as if the model had said so honestly.
        if verbose:
            sys.stderr.write(
                "  %s's action split shares no words with the request; "
                "discarding it and treating this as one action\n" % model)
        return [request.strip()]
    if verbose:
        sys.stderr.write("  %s splits this into %d actions:\n"
                         % (model, len(actions)))
        for a in actions:
            sys.stderr.write("    - %s\n" % a)
    return actions


def propose_multi(spec: Dict[str, Any], request: str, cfg: Any, state: Any,
                  limit: int = 6, verbose: bool = True, max_actions: int = 4
                  ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """`propose`, run once per distinct action `decompose_actions` finds.

    A request needing one action costs exactly what calling `propose`
    directly always cost -- `decompose_actions` returns `[request]` and this
    makes one more call, to the same model, for the same judgement it was
    always going to make. The extra cost only appears on a request that
    turns out to need it, and buys a second, focused, on-its-own-terms
    reading of the part the first pass is most likely to drop.

    Capped at `max_actions`: `decompose_actions` already asks for the most
    important first, so a request that genuinely names more than a handful
    of actions is answered by its most important few rather than by an
    unbounded number of local-model calls.

    `avoid` from a later action never overrides `found` from an earlier
    one -- an endpoint one action affirms and another merely fails to
    mention is not a contradiction to resolve, it is the second action
    having nothing to say about it.
    """
    actions = decompose_actions(request, cfg, state, verbose=verbose)
    if len(actions) <= 1:
        return propose(spec, request, cfg, state, limit=limit,
                       verbose=verbose)
    found: List[Tuple[str, str]] = []
    avoid: List[Tuple[str, str]] = []
    for action in actions[:max_actions]:
        # A split action that is nothing but this project's own
        # output-saving boilerplate ("save its id to output.json under
        # the same key the API response uses for it", "the runnable
        # entry point is main.py") is not a distinct API capability, and
        # asking `propose` what endpoint it needs invites exactly the
        # answer a small model gives any question it must answer:
        # something, picked by whichever operation's name happens to
        # share a surface word with "save". Measured live: this exact
        # clause, split out as its own action, came back suggesting
        # `.../assignments/{assignment_id}/save-draft` and a clutch of
        # unrelated draft-version endpoints for a request that was
        # actually just "create a project" -- and because a `propose`
        # hint is trusted over a score, one of those unrelated
        # endpoints' own id requirement then closed over a completely
        # unrelated `POST /api/applets/{applet_id}/versions` and marked
        # it REQUIRED.
        #
        # `_content_words(action) - _NARRATIVE_TERMS` was tried first and
        # does not actually catch this: "under the same key the API
        # response uses for it" leaves `same`, `api`, `key`, `response`
        # behind, none of which `_NARRATIVE_TERMS` names, because that
        # list is tuned for `score()`'s IDF weighting, not for this
        # phrase-level convention. Widening `_NARRATIVE_TERMS` to cover
        # them risks the opposite failure it was built to avoid --
        # zeroing out "response" or "key" would blunt real matches on a
        # request that is genuinely about a response shape or an API
        # key. So this checks for the convention's own fixed vocabulary
        # instead of trying to strip every word it happens to contain:
        # `output.json` and `main.py` are literal filenames this
        # project's own boilerplate always uses and a real API action
        # description never has a reason to mention.
        #
        # That stopped being true the moment a chained request legitimately
        # names a *sibling's* output.json as the thing being read, and a
        # bare substring match cannot tell the two apart. Measured live: a
        # split action reading "push survey.html as a new version of the
        # applet whose applet_id is read from ../05_create_applet/
        # output.json" -- a real, substantive clause naming which applet
        # to act on -- was silently skipped as boilerplate, and so was its
        # sibling clause pointing the project at the new version, because
        # both happen to mention `output.json` while describing where an
        # id comes from. Neither ever reached `propose()`, so the model
        # never got a chance to name the version-push and project-patch
        # endpoints, leaving whatever the lexical scorer alone could find.
        # The distinguishing fact is already in the text: this project's
        # own save-instruction always names `output.json` bare, with no
        # path in front of it ("save its id ... in this directory's own
        # output.json"), while every reference to a *sibling's* file is a
        # `../`-prefixed relative path ("../05_create_applet/output.json")
        # -- reading one's own directory needs no path at all, writing to
        # or reading from another step's does. So only a bare mention -- one
        # with no `../` immediately in front of it -- counts as this
        # project's own boilerplate; a `../`-qualified one is exactly the
        # dependency plumbing `propose()` needs to see to name the right
        # endpoint.
        low = action.lower()
        output_tokens = re.findall(r"\S*output\.json", low)
        bare_output = any("../" not in tok for tok in output_tokens)
        if bare_output or "main.py" in low:
            if verbose:
                sys.stderr.write(
                    "  %r is this project's own output-saving boilerplate,"
                    " not a distinct API action -- skipping it rather than"
                    " asking what endpoint it needs\n" % action)
            continue
        # A split action that is nothing but reading a value out of a
        # sibling step's own file -- "read applet_id from
        # ../05_create_applet/output.json", "read survey.html from
        # ../04_create_survey/survey.html" -- is local file I/O, never an
        # API call, regardless of whether the file happens to be JSON or
        # HTML. Measured live: `decompose_actions` split exactly these two
        # out as their own actions and `propose` was asked what endpoint
        # each one needs, spending four local-model calls (two models x
        # two actions) to get back 6 and then 1 suggested endpoints for
        # clauses that call nothing at all -- noise `close_over_ids`/
        # `close_content_pushes` then had to sort through alongside the
        # one action (here, "update the applet with the html") that
        # actually named real work.
        #
        # This is deliberately narrower than the bare-output check above,
        # which stays scoped to *this project's own* output-saving
        # phrasing on purpose (a `../`-qualified mention embedded inside a
        # real action, like "update the project to point at the new
        # version id [implicitly using version_id from a sibling]", still
        # needs to reach `propose()` so the dependency is visible). This
        # only fires when the *entire* action is nothing but the read --
        # `read <name> from <relative path>` and nothing else -- so an
        # action that does real work in the same sentence as a sibling
        # reference is untouched and still reaches `propose()` as before.
        if re.match(r"^\s*read\s+\S+\s+from\s+\.\./\S+\s*$", action, re.I):
            if verbose:
                sys.stderr.write(
                    "  %r is reading a sibling step's own file, not a "
                    "distinct API action -- skipping it rather than "
                    "asking what endpoint it needs\n" % action)
            continue
        f, a = propose(spec, action, cfg, state, limit=limit, verbose=verbose)
        for key in f:
            if key not in found:
                found.append(key)
        for key in a:
            if key not in avoid and key not in found:
                avoid.append(key)
    return found, avoid


PROPOSE_PROMPT = """%s

A developer asks: "%s"

The request above may contain pasted program output, IDs, URLs, or a
transcript from a previous run, mixed in with what is actually being
asked for -- ignore all of that; answer only what is actually being
requested, not the words that happen to appear alongside it.

List the endpoints above that are needed to do that, most important first,
at most %d, under a line reading RELEVANT: one per line, METHOD and path
copied exactly as written above, nothing else.

Then, under a line reading AVOID:, list any endpoints that share a word
with the request but are NOT what it is asking for -- for example a
request that happens to mention "data collection" is not necessarily
about an endpoint whose path contains the word "collection". This
includes words the request uses to describe its OWN output or the
content of a file it wants built -- "prints a link", "start with a
3-question survey" -- which are not a request for a "share link" or
"survey" feature just because an endpoint happens to be named that.
Leave this list empty if there is nothing to warn about.

If nothing above fits either list, reply NONE."""


def propose(spec: Dict[str, Any], request: str, cfg: Any, state: Any,
            limit: int = 6, verbose: bool = True
            ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Ask the local model which endpoints the request means, and which not.

    Scoring by term overlap cannot match a request to an endpoint that
    shares no words with it, and that is not a rare corner. Measured against
    the real 291-operation spec: "sign in with an email and password" never
    found `/api/auth/login` -- it matched `forgot-password` and
    `reset-password` on the shared token "password" -- and both "how far
    along is the work" and "who is allowed to work on this" collapsed onto
    `work-limits`, having matched the literal string "work" while missing
    task-progress and qualification entirely. No weighting fixes those; the
    right answer shares zero tokens with the question.

    The same collision runs the other way too, and is the more expensive
    direction: term overlap does not just miss the right endpoint, it can
    rank a wrong one above it with real confidence. Measured live: "the
    unit should be empty, it's just data collection" pulled in a `.../
    collection/units` endpoint on the shared word "collection", ahead of
    the actual unit-upload endpoint the request meant -- worse when the
    request also carried pasted output from a previous run, whose own
    incidental words ("survey", "task", "project") outscored the real ask
    entirely. Overlap cannot tell a coincidence from a match; only reading
    the sentence can, so `AVOID` in the prompt above asks for exactly that
    judgement, and `slice_for` treats it as a veto over the scored path,
    not one more opinion weighed against it.

    Matching intent to endpoint is judgement, which is the one part of this
    module a model does better than a function. That is not in tension with
    resolving `$ref` mechanically: following a reference is a graph walk with
    a right answer, while choosing what "how far along" means is not.

    Two properties make it safe to rely on. The model sees the *whole* index
    in one call -- 5701 tokens on the real spec, comfortably inside e4b's
    window -- so unlike `ground.py` it is never reading a fragment and
    guessing about the rest. And every line it returns is checked against
    the real operation table, so an endpoint it invents is dropped rather
    than passed on. Free tokens on the operator's own machine decide what
    the pool's expensive ones carry, which is `ground.py`'s bargain exactly.

    Best-effort. No local node, a node that errors, or a reply naming
    nothing real all return ([], []) and leave the lexical path to answer
    alone. Measured warm at 0.5-3.7s per call.
    """
    ops = operations(spec)
    if not ops or not request.strip():
        return [], []
    real = {(e["method"].upper(), e["path"]): e for e in ops}
    try:
        import ground
        readers = ground.pick_readers(cfg, state)
    except Exception:                                   # noqa: BLE001
        return [], []
    if not readers:
        return [], []

    prompt = PROPOSE_PROMPT % (index_block(spec),
                              _strip_sibling_paths(request.strip()), limit)
    replies: List[Tuple[object, str, Dict[str, Any]]] = []
    for node, model in readers:
        try:
            ctx = swarm.pick_num_ctx(cfg, node, model, swarm.est_tokens(prompt), 400)
            reply = swarm.chat_complete(
                node, model, [{"role": "user", "content": prompt}],
                num_ctx=ctx, answer_tokens=400, headroom=1.2,
                think=False, temperature=0.0, timeout=3600)
        except Exception as exc:                        # noqa: BLE001
            if verbose:
                sys.stderr.write("  api hint from %s unavailable (%s)\n"
                                 % (model, exc))
            continue
        try:
            state.record_perf(node.name, model, reply["sample"])
        except Exception:                                # noqa: BLE001
            pass
        replies.append((node, model, reply))
    if not replies:
        if verbose:
            sys.stderr.write("  api hint unavailable -- "
                             "falling back to term matching\n")
        return [], []

    def parse_endpoints(lines: List[str],
                        cap: Optional[int]) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for line in lines:
            line = line.strip().lstrip("-*0123456789.) ").strip().strip("`'\"")
            parts = line.split()
            if len(parts) < 2:
                continue
            key = (parts[0].upper(), parts[1])
            # Verified against the operation table, never trusted. A path
            # the model improved on -- a plural corrected, a parameter
            # renamed -- is not a near miss to be repaired, it is an
            # endpoint that does not exist, and shipping it (or vetoing a
            # real one under a name it does not have) would be worse than
            # doing nothing.
            if key in real and key not in out:
                out.append(key)
            if cap is not None and len(out) >= cap:
                break
        return out

    # One reader's AVOID is a judgement call, not a fact, and a single bad
    # sample used to be able to drop an endpoint unilaterally -- nothing
    # else in the pipeline would surface it again if lexical scoring
    # already ranked it too low to matter on its own (see this function's
    # own docstring). So RELEVANT is unioned across every reader consulted
    # (any one of them naming an endpoint is still worth having, at the
    # cost of nothing -- a hint only ever adds), while AVOID requires every
    # reader that actually answered to agree before an endpoint is vetoed.
    # With one reader (the common case, when only one candidate is idle)
    # this reduces to exactly the old single-model behaviour.
    found: List[Tuple[str, str]] = []
    avoid_votes: Dict[Tuple[str, str], int] = {}
    per_reader: List[Tuple[str, List[Tuple[str, str]], List[Tuple[str, str]]]] = []
    for node, model, reply in replies:
        relevant_lines: List[str] = []
        avoid_lines: List[str] = []
        section = relevant_lines
        for raw in (reply.get("text") or "").splitlines():
            line = raw.strip()
            head = line.upper().rstrip(":")
            if head == "RELEVANT":
                section = relevant_lines
                continue
            if head == "AVOID":
                section = avoid_lines
                continue
            if line:
                section.append(line)
        found_i = parse_endpoints(relevant_lines, limit)
        avoid_i = [k for k in parse_endpoints(avoid_lines, None)
                  if k not in found_i]
        per_reader.append((model, found_i, avoid_i))
        for key in found_i:
            if key not in found:
                found.append(key)
        for key in avoid_i:
            avoid_votes[key] = avoid_votes.get(key, 0) + 1

    avoid = [k for k, votes in avoid_votes.items()
            if votes == len(replies) and k not in found]

    if verbose:
        for model, found_i, avoid_i in per_reader:
            if found_i:
                sys.stderr.write(
                    "  %s read the index and suggests %d endpoint%s\n"
                    % (model, len(found_i), "" if len(found_i) == 1 else "s"))
        if avoid:
            names = ", ".join(sorted({m for m, _, _ in per_reader}))
            sys.stderr.write(
                "  %s agree: veto %d endpoint%s that share a word "
                "with the request but are not what it means\n"
                % (names, len(avoid), "" if len(avoid) == 1 else "s"))
    return found, avoid


# Words that describe how a script narrates or presents its OWN
# behaviour -- "prints the project link", "to start with, let's make it a
# survey" -- rather than a capability being asked of the API. Every one of
# these is common enough in an ordinary automation request that it says
# nothing about which endpoint is wanted, and every one of them happens to
# also be rare enough in a real API document to score as strong evidence
# once IDF gets hold of it. That combination is what makes it dangerous:
# IDF rewards rarity, and rarity in the *document* has no relationship to
# relevance when the rare word is being used in the *request* to mean
# something else entirely.
#
# Measured on the real spec, request verbatim: "prints the project link"
# put `POST /api/units/{unit_id}/share-link` second overall (weight 5.67
# on "link" alone, worth more than "unit" and "units" combined), well
# above the actual task-creation endpoint. "To start with, let's make it
# a 3 question survey" put `.../detector-backfill` (whose summary reads
# "Start an async, resumable detector backfill...") at 19.86, almost
# entirely from the single word "start" (weight 4.57) -- a transition
# phrase, not a request to start anything. Removing both from scoring
# evidence dropped `share-link` out of the top 15 entirely and did not
# move a single legitimate match: `POST /api/applets` is still found on
# "applet" alone, `POST /api/tasks/{task_id}/units/import` still surfaces
# once "start"'s inflated neighbours stop crowding it out.
#
# Deliberately small and specific to demonstrated collisions rather than
# a broad "generic word" filter -- `_GENERIC_ENDPOINT_WORDS` already
# exists for CRUD verbs and is a different, narrower job (deciding
# whether a CONTRACT covers an endpoint, not deciding whether an endpoint
# is relevant at all). A stoplist that grows to cover every English word
# that could ever appear in a narrated request stops being a stoplist and
# becomes the same "no weighting fixes those" problem `propose`'s AVOID
# veto exists for -- this list is bounded to words actually measured
# causing a wrong endpoint to outrank a right one, not a guess at future
# ones. See `openapi_check.py` for the sabotage check.
_NARRATIVE_TERMS = frozenset({"link", "links", "url", "urls",
                              "start", "starting",
                              # "save its id to output.json" is this
                              # project's own convention for every single
                              # `--api` request (see LOCAL.md), not a fact
                              # about the API -- so "save", "output" and
                              # "json" appear in nearly every such request
                              # regardless of what it actually asks for.
                              # Measured live: a bare "create a project;
                              # save its id to output.json" scored all
                              # three at 5.67, this document's maximum
                              # term weight (each appears in exactly one
                              # unrelated operation), against `project`'s
                              # own weight of 0.92 -- so `POST /api/projects`
                              # ranked behind `save-draft`, `score-preview`,
                              # a submission PATCH, and a JSON export
                              # endpoint that share nothing with the
                              # request but this literal filename. The
                              # real API's own JSON-shaped endpoints are
                              # still reachable through their path, tag,
                              # operationId or summary containing other
                              # real matches, or through `propose`'s
                              # model-read hints -- this only removes the
                              # one guaranteed-boilerplate collision.
                              "save", "output", "json"})


def score(entry: Dict[str, Any], wanted: Set[str],
          weight: Optional[Dict[str, float]] = None) -> float:
    """How well one operation matches the request.

    Path, tag, operationId and summary are weighted above the description
    because a description is long enough to collide with almost anything --
    on this spec, "user" appears in the prose of endpoints that have nothing
    to do with users. Matching the identifying parts keeps a query specific
    without needing a cleverer function.

    A term the document has never heard of scores zero rather than defaulting
    to a high weight: an unknown word is not a rare one, it is a miss.

    Body field names sit between the two: stronger than prose, because a
    field name is a fact about what the endpoint accepts rather than words
    that happen to be nearby, but not as strong as the endpoint's own
    identity, because a field name can be generic (`name`, `id`) in a way a
    path or an operationId usually is not. This is the term that closes the
    gap `_body_field_terms` exists for -- without it, "assign the applet
    version to the project" and `PATCH /api/projects/{project_id}` share
    nothing at all, because the word "applet" appears only in
    `ProjectUpdate.applet_version_id`, never in the endpoint's own path,
    tags, operationId or summary. Rewarded relative to the same `weight`
    table as everything else, so a generic field name that scores near zero
    in `strong` (because it is common there too) scores near zero here.

    A field only counts if it shares *two or more* words with the request,
    not one. One shared word is the exact coincidence `avoid` exists to
    police at the path/summary level, reintroduced here unless guarded:
    measured live, `AddMemberRequest.user_id` shared "user" with "create a
    user account" and pulled an unrelated add-member endpoint into a slice
    that a veto on the real user-creation endpoint was supposed to leave
    empty. `applet_version_id` sharing "applet" and "version" with a
    request about assigning an applet version is not that -- two words
    naming the same field is a real match, not a common noun in the way
    "user" alone is.
    """
    if not wanted:
        return 0.0
    wanted = wanted - _NARRATIVE_TERMS
    if not wanted:
        return 0.0
    weight = {} if weight is None else weight
    strong = _strong_terms(entry)
    weak = _terms(str(entry["op"].get("description") or ""))
    qualifying: Set[str] = set()
    for field_terms in entry.get("body_terms") or []:
        overlap = wanted & field_terms
        if len(overlap) >= 2:
            qualifying |= overlap
    hit = sum(weight.get(t, 0.0) for t in wanted & strong)
    prose = sum(weight.get(t, 0.0) for t in (wanted & weak) - strong)
    fields = sum(weight.get(t, 0.0) for t in qualifying - strong)
    return 3.0 * hit + prose + 2.0 * fields


def _is_nested_create(parent_path: str, child_path: str) -> bool:
    """Whether `child_path` creates a resource nested one level under
    whatever `parent_path` creates -- `/api/applets` and
    `/api/applets/{applet_id}/versions` -- rather than something
    unrelated that merely shares a prefix.
    """
    p = parent_path.rstrip("/")
    if not child_path.startswith(p + "/"):
        return False
    rest = child_path[len(p) + 1:]
    segs = [s for s in rest.split("/") if s]
    return len(segs) >= 2 and segs[0].startswith("{") and segs[0].endswith("}")


def _demote_unrequested_nested_creates(
        ops: Sequence[Dict[str, Any]],
        wanted: Set[str],
        scored: List[Tuple[float, int, Dict[str, Any]]],
) -> List[Tuple[float, int, Dict[str, Any]]]:
    """A POST that creates a child of a resource shares every term its own
    parent-collection POST has, plus more, so pure term overlap ranks it
    at least as high and usually higher -- "create an applet" scores
    `/api/applets/{applet_id}/versions` above `/api/applets` itself
    (19.7 vs 18.9 on the real spec), even though the request never said
    "version" anywhere. The extra path segments are not a signal the
    request meant the more specific thing; they are just more surface for
    the same words to match against.

    Measured live: exactly this ranking is why a request that only ever
    said "create an applet" ended up with a CONTRACT requiring a version
    to be created too, and -- having no real content available in that
    request -- a component fabricated placeholder artifact content
    (`data:text/html;base64,` of the literal string "Hello World") to
    satisfy it.

    So a nested-create candidate is capped at its own parent's score
    unless the request shares a term that actually distinguishes them --
    `_strong_terms(child) - _strong_terms(parent)`, which is exactly
    "version"/"versions" here. `"id"` is excluded from that distinguishing
    set on purpose: a child path almost always introduces one more
    `{x_id}` parameter than its parent, tokenizing to a bare "id" that is
    common to nearly every resource in the document and therefore proves
    nothing about which specific resource this one is -- without dropping
    it, "save its id as applet_id" would supply the very word needed to
    call the nested endpoint un-demoted, defeating the guard on the exact
    request it exists for.

    Only ever lowers a score, never raises one, so a request that *does*
    name the child's own resource ("create a task in that project") is
    untouched -- "task" is shared, distinguishing is non-empty, nothing
    here fires.
    """
    raw = {(e["method"], e["path"]): v for v, _, e in scored}
    posts = [e for e in ops if e["method"].upper() == "POST"]
    adjusted: List[Tuple[float, int, Dict[str, Any]]] = []
    for value, i, entry in scored:
        if entry["method"].upper() == "POST" and value > 0:
            for parent in posts:
                if parent is entry:
                    continue
                if not _is_nested_create(parent["path"], entry["path"]):
                    continue
                distinguishing = (_strong_terms(entry) - _strong_terms(parent)
                                  - {"id"})
                if wanted & distinguishing:
                    continue
                parent_value = raw.get((parent["method"], parent["path"]), 0.0)
                if 0 < parent_value < value:
                    value = parent_value - 1e-6
        adjusted.append((value, i, entry))
    adjusted.sort(key=lambda t: (-t[0], t[1]))
    return adjusted


def _refs_in(node: Any) -> Iterable[str]:
    """Every `$ref` target anywhere under a node."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                for found in _refs_in(value):
                    yield found
    elif isinstance(node, list):
        for item in node:
            for found in _refs_in(item):
                yield found


def _resolve(ref: str, spec: Dict[str, Any]) -> Optional[Any]:
    if not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def reachable_schemas(nodes: Sequence[Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Every schema reachable from these operations, transitively.

    The whole reason this module exists rather than a grep. An operation that
    says `{"$ref": ".../UserCreate"}` is not self-describing, and a writer
    handed it without the target invents the fields -- which passes parse,
    import and the smoke run, and fails against the real API.

    Cycles are ordinary in generated specs (a schema referencing itself
    through a list), so `seen` is checked before descent rather than after.
    """
    seen: Set[str] = set()
    pending = [r for node in nodes for r in _refs_in(node)]
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        seen.add(ref)
        target = _resolve(ref, spec)
        if target is not None:
            pending.extend(_refs_in(target))
    out = {}
    for ref in sorted(seen):
        name = ref.rsplit("/", 1)[-1]
        target = _resolve(ref, spec)
        if target is not None:
            out[name] = target
    return out


def _strip(node: Any) -> Any:
    """Drop the parts every operation repeats identically.

    `title` is emitted by Pydantic for every field and restates the field
    name in title case; the 422 response is byte-identical across all 150
    operations here. Neither tells a reader anything the rest of the entry
    does not, and together they are most of the document.
    """
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items()
                if k not in NOISE_KEYS and k not in NOISE_RESPONSES}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


_EXAMPLE_MAX_DEPTH = 4


# A field whose OpenAPI description says it must be unique (e.g. a
# task/applet "slug") cannot be rendered as a fixed literal in an example
# call, or every writer that copies it produces the identical value on
# every real run -- correct on the mock (which keeps no state) and
# rejected by the real service the *second* run against the live
# platform, because the first run had already consumed that slug. `team
# do --retry --run` cannot fix this: the code is not wrong in any way a
# repair attempt can name, and every retry re-sends the identical
# hardcoded string, so it fails identically forever. The fix has to be
# upstream, in the example the writer copies from, not downstream in a
# repair loop reacting to a crash that looks exactly like every other
# crash.
_UNIQUE_FIELD_MARKER = "__swarm_unique_field__"
_UNIQUE_DESCRIPTION_RE = re.compile(r"\bunique\b", re.IGNORECASE)

# A field literally named `version` gets a different treatment than a
# generic unique field: `example_call` renders it as a retry-on-conflict
# loop (try "1.0.0", and on a rejection append "-2", "-3", ... until one
# is accepted) rather than a single random uuid-suffixed guess. Both are
# collision-free; this one is the shape an operator asked for directly
# after seeing the uuid-suffixed version -- "just retry while appending
# a number on the end till something works" -- because it keeps version
# strings human-readable instead of producing "version-a1b2c3d4".
#
# Matched by exact field name, not a substring or a description scan:
# measured live, on a real spec whose `AppletVersionCreate.version`
# description reads only "Version string (e.g., '1.0.0')" (no "unique"
# anywhere), two separately generated files (one creating an applet's
# first version, a later one pushing a new version onto the same applet)
# both hardcoded `"version": "1.0.0"`, copied verbatim from that exact
# parenthetical example -- and the second call got a real `400 Bad
# Request` from the live platform. Across virtually every REST API, a
# `version` field on a thing being created is, by convention, expected to
# differ from whatever versions already exist for the same parent --
# unlike a merely descriptive field that happens to carry an "e.g."
# example (a country code, a currency), where the same treatment would be
# wrong. `schema_version`/`api_version` (a field pinned to one fixed value
# on purpose, the opposite case) are never touched, because the match is
# exact, not a substring.
_VERSION_FIELD_MARKER = "__swarm_version_field__"

# A field whose schema-derived example already looks like a real
# `data:<mimetype>;base64,PAYLOAD` URI is marked the same way a `version`
# or `unique` field is, so `example_call`'s renderer below turns it into a
# genuine `base64.b64encode(...)` call instead of a static string a
# writer could copy verbatim. Measured live: a writer given only a static
# placeholder value (or, worse, a literal "..." lifted from prose) for
# this exact shape invented its own, wrong encoding
# (`requests.utils.quote`) rather than reaching for `base64.b64encode`,
# because nothing in its context was a real, computed example to copy.
_BASE64_DATA_URI_MARKER = "__swarm_base64_data_uri__"
_DATA_URI_BASE64_RE = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,")


def _mark_base64_data_uri_fields(value: Any) -> Any:
    """Walk a computed example payload and replace any string that
    already looks like `data:<mimetype>;base64,PAYLOAD` with
    `_BASE64_DATA_URI_MARKER + mimetype`, recursively -- applied uniformly
    to a payload from either source `example_call` can produce (a real
    schema, or `_example_from_description`'s extracted prose example), so
    the fix is not specific to one or the other.
    """
    if isinstance(value, dict):
        return {k: _mark_base64_data_uri_fields(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mark_base64_data_uri_fields(v) for v in value]
    if isinstance(value, str):
        m = _DATA_URI_BASE64_RE.match(value)
        if m:
            return _BASE64_DATA_URI_MARKER + m.group(1)
    return value


def _document_wide_base64_field_mimetypes(spec: Dict[str, Any]) -> Dict[str, str]:
    """Field names that *some* operation's own description, anywhere in
    this document, documents with a real `data:<mimetype>;base64,...`
    example -- keyed to that mimetype, first one found wins.

    A field name means the same thing everywhere it appears in one
    document; a document that only spells out `artifact_url`'s real
    shape once (on the operation whose schema happens to leave it
    untyped) has still settled the question for every other operation
    that shares the name, including one whose own schema renders a
    generic, wrong-shaped placeholder for it. See
    `_apply_document_wide_base64_hints`, the one caller, for the live
    case this exists to fix.
    """
    fields: Dict[str, str] = {}
    for path_item in (spec.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for op in path_item.values():
            if not isinstance(op, dict):
                continue
            example = _example_from_description(op.get("description") or "")
            if not example:
                continue
            for key, value in example.items():
                if key in fields or not isinstance(value, str):
                    continue
                m = _DATA_URI_BASE64_RE.match(value)
                if m:
                    fields[key] = m.group(1)
    return fields


def _apply_document_wide_base64_hints(payload: Dict[str, Any],
                                      spec: Dict[str, Any]) -> Dict[str, Any]:
    """Correct a field whose own schema resolved to a plain placeholder
    (`_example_scalar`'s generic "url"/"uri" branch, `"https://
    example.com"`) into the real `data:<mimetype>;base64,` shape, when
    some *other* operation in this same document documents that exact
    field name with a genuine base64 data URI example.

    Measured live: `POST /api/applets/{applet_id}/versions`'s own
    `AppletVersionCreate.artifact_url` schema says only "URL to the
    applet artifact (HTML/JS)", with no hint of an encoding anywhere in
    it, so `_example_scalar` rendered the generic placeholder
    `"https://example.com"` for it -- the exact shape a live writer
    copied as its signal that a normal, fetchable URL (not a data URI)
    was expected, and then invented its own encoding
    (`requests.utils.quote`) once it separately decided, from elsewhere
    in its own context, that a data URI was actually needed. `PATCH
    .../update-content`, elsewhere in the very same document, names the
    identical field `artifact_url` and spells out the real
    `data:text/html;base64,...` shape in full in its own description.
    This is what makes the correction a fact read off the document
    rather than a guess: the field name is exactly the same field, and
    the document itself already answered the question once.

    Only ever replaces a plain placeholder with a better one for a field
    the document itself names elsewhere -- a field already correctly
    shaped (already matching `data:...;base64,`) is left untouched, and
    a field this document never documents anywhere as a base64 URI is
    never touched at all.
    """
    hints = _document_wide_base64_field_mimetypes(spec)
    if not hints:
        return payload
    corrected = dict(payload)
    for key, mimetype in hints.items():
        value = corrected.get(key)
        if isinstance(value, str) and not _DATA_URI_BASE64_RE.match(value):
            corrected[key] = "data:%s;base64,PLACEHOLDER" % mimetype
    return corrected


def _example_from_description(description: str) -> Optional[Dict[str, Any]]:
    """A literal JSON example embedded in a free-text `description`, used
    only when the schema itself gave nothing to build one from.

    Measured live: `PATCH /api/applet-versions/{version_id}/update-
    content`'s requestBody schema is a bare, propertyless `{"type":
    "object"}` -- nothing for `_example_for_schema` to build a real
    example from, so it fell through to `{}`, an empty and useless call.
    The one real description of the shape lives entirely in the
    operation's own `description` string (this document reads like it
    was auto-generated from FastAPI docstrings, which routinely embed a
    literal example this way): "Request body:\\n{\\n  \\"artifact_url\\":
    \\"data:text/html;base64,...\\", ...\\n}". A writer handed `json={}`
    and only that prose invented its own mechanism for the one field the
    schema never described at all, which is the live failure this exists
    to prevent -- the example above is valid JSON as written (the "..."
    is just three literal characters inside a JSON string), so nothing
    here has to reconstruct or guess at it.

    Finds the first balanced `{...}` span in the text by counting braces
    (not a regex -- JSON nests, and a regex cannot match balanced
    delimiters in general) and parses it with `json.loads`. Returns None
    on anything that fails to parse as a JSON object, so this only ever
    uses a real, syntactically valid example that was already sitting in
    the document, never a reconstruction of one.
    """
    if not description:
        return None
    start = description.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(description)):
        ch = description[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(description[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
                return value if isinstance(value, dict) else None
    return None


def _example_scalar(schema: Dict[str, Any], name: str) -> Any:
    """A single placeholder value shaped like this field, and never a
    guess at what a real one should *contain* -- the same restraint
    `usage._placeholder_for` already applies to a gate's own smoke run,
    which is a different caller asking the identical question ("what
    survives the first line that touches it") for a different reason.
    """
    if schema.get("enum"):
        return schema["enum"][0]
    fmt = schema.get("format", "")
    typ = schema.get("type", "string")
    lowered = name.lower()
    if typ == "integer":
        return 1
    if typ == "number":
        return 1.0
    if typ == "boolean":
        return True
    if typ == "array":
        return []
    if typ == "object":
        return {}
    if fmt == "uuid" or lowered.endswith(("_id", "id")):
        return "00000000-0000-4000-8000-000000000000"
    if fmt == "date-time":
        return "2024-01-01T00:00:00Z"
    if fmt == "date":
        return "2024-01-01"
    if "url" in lowered or "uri" in lowered:
        return "https://example.com"
    if typ == "string" and lowered == "version":
        return _VERSION_FIELD_MARKER + name
    if typ == "string" and _UNIQUE_DESCRIPTION_RE.search(
            schema.get("description") or ""):
        return _UNIQUE_FIELD_MARKER + name
    return "example"


def _example_for_schema(schema: Any, spec: Dict[str, Any], name: str = "",
                        seen: Optional[frozenset] = None,
                        depth: int = 0) -> Any:
    """A minimal, correctly-shaped value for this schema.

    Required fields only -- `usage.placeholder_args` makes the identical
    call for the smoke run, for the identical reason: switching on every
    optional field at once is a stranger call than any real one would be.
    One element per array. Resolved through `$ref` exactly the way
    `reachable_schemas` already does, including the same cycle guard --
    schemas here are ordinary graphs, not trees, and a schema referring to
    itself through a list is unremarkable.

    This is a shape template, not a value to submit. Every id-shaped field
    below is the same placeholder a missing credential already gets in
    `gate_smoke`, not a real one, and the point of showing it at all is
    the keys and the nesting, never the numbers.
    """
    seen = seen or frozenset()
    if depth > _EXAMPLE_MAX_DEPTH or not isinstance(schema, dict):
        return "..."
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen:
            return "..."
        target = _resolve(ref, spec)
        if target is None:
            return "..."
        return _example_for_schema(target, spec, name, seen | {ref}, depth)

    merged_props: Dict[str, Any] = dict(schema.get("properties") or {})
    for sub in schema.get("allOf") or []:
        resolved = sub
        if isinstance(sub, dict) and "$ref" in sub:
            resolved = _resolve(sub["$ref"], spec) or {}
        if isinstance(resolved, dict):
            merged_props.update(resolved.get("properties") or {})

    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if options:
            return _example_for_schema(options[0], spec, name, seen,
                                       depth + 1)

    if merged_props:
        required = schema.get("required") or list(merged_props.keys())
        return {field: _example_for_schema(sub, spec, field, seen, depth + 1)
               for field, sub in merged_props.items() if field in required}
    if schema.get("type") == "array":
        return [_example_for_schema(schema.get("items") or {}, spec, name,
                                    seen, depth + 1)]
    return _example_scalar(schema, name)


def _response_id_field(entry: Dict[str, Any],
                       spec: Dict[str, Any]) -> Optional[str]:
    """The literal response-body key holding the id this call creates,
    read off the resolved response schema rather than assumed to be
    `id`. Only ever names a key that is actually a property of that
    schema, so this cannot assert an id field that does not exist --
    if the schema names nothing id-shaped, nothing is said.
    """
    op = entry.get("op") or {}
    responses = op.get("responses") or {}
    body = responses.get("201") or responses.get("200") or {}
    schema = ((body.get("content") or {})
              .get("application/json") or {}).get("schema")
    if not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        schema = _resolve(schema["$ref"], spec) or {}
    props = schema.get("properties") or {}
    if "id" in props:
        return "id"
    for field in props:
        if field.lower().endswith("_id"):
            return field
    return None


def example_call(entry: Dict[str, Any], spec: Dict[str, Any]) -> str:
    """A minimal, mechanically-derived request/response snippet for one
    operation.

    Every field in this project's own live history that came back wrong --
    `data=` sent instead of `json=`, an invented endpoint, an invented id
    where a produced one belonged -- was a writer guessing a shape the
    document already specified. This walks the identical resolved schema
    `reachable_schemas` already renders and turns it into one working
    call, in the same spirit `close_over_ids`/`close_pointer_updates`
    already apply to *which* endpoints belong in a slice, applied here to
    *how* the one that was chosen is actually called. `json=` is
    structural, never a choice the example leaves open, which forecloses
    the single most common documented mistake outright.
    """
    method = entry["method"].lower()
    op = entry.get("op") or {}
    path = entry["path"]

    query: Dict[str, Any] = {}
    for param in op.get("parameters") or []:
        if not isinstance(param, dict) or param.get("in") != "query":
            continue
        pname = param.get("name", "")
        if not pname:
            continue
        pschema = param.get("schema") or {}
        if isinstance(pschema, dict) and "$ref" in pschema:
            pschema = _resolve(pschema["$ref"], spec) or {}
        query[pname] = _example_scalar(pschema, pname)

    body = op.get("requestBody") or {}
    if isinstance(body, dict) and "$ref" in body:
        body = _resolve(body["$ref"], spec) or {}
    body_schema = ((body.get("content") or {})
                   .get("application/json") or {}).get("schema")
    payload = _example_for_schema(body_schema, spec) if body_schema else None
    # The schema itself gave nothing (an empty/propertyless requestBody
    # renders as `{}`, which is a useless call, not a genuinely empty
    # body) -- fall back to a real example sitting in the operation's own
    # prose description, if one is there. Never overrides a schema that
    # actually produced something, and never invents one when the
    # description has none either -- see `_example_from_description`'s
    # own docstring for the live failure (`update-content`) this covers.
    if body_schema is not None and not payload:
        payload = (_example_from_description(op.get("description") or "")
                  or payload)
    if isinstance(payload, dict) and payload:
        payload = _apply_document_wide_base64_hints(payload, spec)
    if payload is not None:
        payload = _mark_base64_data_uri_fields(payload)

    # `_example_scalar` cannot return an f-string directly -- this
    # function's own contract with `_example_for_schema` is a
    # JSON-serialisable placeholder value, and an f-string is not one --
    # so a "unique" field is smuggled through as a marked string and
    # turned back into a real, visibly dynamic expression here, after
    # `json.dumps` has already produced the quoted text around it. A
    # `version` field is smuggled through the same way but resolves to
    # a bare variable reference (`version_value`) instead of an inline
    # expression, because its value has to be assignable across retry
    # iterations below -- see the loop this builds further down.
    unique_fields: List[str] = []
    version_fields: List[str] = []
    base64_fields: List[str] = []

    def indented(value: Any) -> str:
        text = json.dumps(value, indent=4)

        def _unmark_unique(m: "re.Match") -> str:
            field = m.group(1)
            unique_fields.append(field)
            return 'f"%s-{uuid.uuid4().hex[:8]}"' % field

        def _unmark_version(m: "re.Match") -> str:
            field = m.group(1)
            version_fields.append(field)
            return "%s_value" % field

        def _unmark_base64(m: "re.Match") -> str:
            field, mimetype = m.group(1), m.group(2)
            base64_fields.append(field)
            return ('"%s": f"data:%s;base64,'
                    '{base64.b64encode(REPLACE_WITH_REAL_CONTENT_BYTES)'
                    '.decode(\'ascii\')}"' % (field, mimetype))

        text = re.sub(r'"%s([^"]*)"' % re.escape(_UNIQUE_FIELD_MARKER),
                      _unmark_unique, text)
        text = re.sub(r'"%s([^"]*)"' % re.escape(_VERSION_FIELD_MARKER),
                      _unmark_version, text)
        # Matches the whole `"field": "<marker>mimetype"` pair, not just
        # the value, because the real fix needs the field's own name in
        # the rewritten f-string's key -- the value-only markers above
        # never needed this since they rewrite in place without touching
        # the key.
        text = re.sub(r'"(\w+)":\s*"%s([^"]*)"' % re.escape(_BASE64_DATA_URI_MARKER),
                      _unmark_base64, text)
        return text.replace("\n", "\n    ")

    url_line = 'url = f"{API_BASE_URL}%s"' % path
    args = ["url"]
    if query:
        args.append("params=%s" % indented(query))
    args.append("headers=HEADERS")
    if payload is not None:
        args.append("json=%s" % indented(payload))
    call_line = ("resp = requests.%s(\n    %s\n)"
                 % (method, ",\n    ".join(args)))

    if version_fields:
        # Not a single guess like the uuid-suffixed unique-field case
        # above -- an operator reviewing that version asked for this
        # shape directly, since a random suffix makes version strings
        # unreadable ("version-a1b2c3d4") where an incrementing one
        # stays close to what a human would have typed by hand. No read
        # of existing versions first (there is no listing endpoint this
        # can assume exists, and even if there were, two callers reading
        # then both computing the same "next" value can still collide);
        # retrying on the exact response this rejection actually uses is
        # race-proof the way a read-then-compute guess is not, and it is
        # simpler code either way.
        field = version_fields[0]
        indented_call = "\n".join("    " + ln for ln in call_line.split("\n"))
        lines = [
            url_line,
            '%s_value = "1.0.0"' % field,
            "for _attempt in range(1, 101):",
            indented_call,
            "    if resp.status_code not in (400, 409, 422):",
            "        break  # accepted -- this exact value was not a duplicate",
            '    %s_value = "1.0.0-%%d" %% (_attempt + 1)  # rejected as a '
            "duplicate -- try the next suffix" % field,
        ]
    else:
        lines = [url_line, call_line]
    lines.append("resp.raise_for_status()")
    lines.append("data = resp.json()")

    id_field = _response_id_field(entry, spec)
    if id_field:
        lines.append("# data[%r] is the id this call produces; pass it to "
                     "whatever needs it" % id_field)
    if unique_fields or version_fields:
        # Same warning, same wording, for both marker kinds -- deliberately,
        # so `codegen.gate_hardcoded_unique_field`'s backstop (which reads
        # this exact comment shape back out of the prompt) still protects a
        # `version` field too if a writer ignores the retry loop above and
        # hardcodes a literal anyway. That backstop rewrites to a
        # uuid-suffixed value rather than a retry loop -- a weaker fallback
        # than the pattern shown here, but still collision-free, and better
        # than shipping a fixed literal a second time.
        all_fields = unique_fields + version_fields
        lines.insert(
            0, "# %s must be unique on every call this API ever receives, "
               "including a second run of this same script -- generate it "
               "with uuid/time/os.urandom at call time, never a fixed "
               "literal, or a second run collides with the first one's "
               "value and the real service (unlike the mock) rejects it."
            % " and ".join(repr(f) for f in all_fields))
    if base64_fields:
        # Measured live: a writer given only a static example for this
        # exact shape (or, worse, a literal "..." lifted from prose with
        # no working code behind it) invented its own encoding
        # (`requests.utils.quote`) instead of a real one -- percent-
        # encoding, not base64, which a base64 decoder (a browser's
        # atob(), Python's base64.b64decode) rejects outright. The
        # rewritten call above is real, working code, not a placeholder;
        # only the content being encoded is a stand-in.
        lines.insert(
            0, "# %s must be built from the REAL content being pushed, "
               "read as bytes -- e.g. base64.b64encode(open(PATH, "
               "\"rb\").read()).decode(\"ascii\"). Never percent-encoding "
               "(urllib.parse.quote / requests.utils.quote), never the "
               "raw text itself, and never a fixed placeholder -- only "
               "real base64 bytes are valid inside a `;base64,` data URI."
            % " and ".join(repr(f) for f in base64_fields))
    return "\n".join(lines)


# Values above are placeholders shaped like the field -- a fake uuid, a
# fake url, `1` for a number -- picked the same way `gate_smoke` fills a
# missing credential, never a guess at real content. Copy the keys and the
# nesting; never the literal values, and never `data=` where an example
# shows `json=`.
EXAMPLE_CALL_NOTE = (
    "# Each endpoint above includes an example call. The values in it are "
    "placeholders shaped like the field, not real content -- copy the "
    "keys and the request shape, never the literal values. Always send a "
    "JSON body with json=, never data= (data= form-encodes a dict and "
    "sends no JSON content-type, which the real API rejects with a body "
    "that was never going to parse as JSON no matter how many times the "
    "request is retried).")


def render(spec: Dict[str, Any], chosen: Sequence[Dict[str, Any]],
          core_keys: Optional[Set[Tuple[str, str]]] = None) -> str:
    """The selected operations plus every schema they reach.

    `core_keys`, when given, is the set of (method, path) pairs
    `slice_for`'s own `core_of` already judged to be the request's actual
    subject rather than filler that merely cleared "score above zero"
    while the slice filled out to `limit`. That judgement already existed
    -- it is what decides whether a filler entry's own `*_id` need is
    allowed to demand a REQUIRED closure -- and used to stop there,
    reaching only `close_over_ids`. Every entry in `chosen`, core or
    filler, was rendered identically under one "# Relevant endpoints, in
    full" heading, so a writer had no way to tell a low-confidence
    neighbor from the endpoint the request actually meant.

    Measured live: a request reading only "create a project; save its
    id..." had `POST /api/projects/{project_id}/tasks` and `POST
    /api/projects/{project_id}/telephony/sessions` sitting in the same
    slice -- both scored in as filler while filling toward the operator's
    `--api-limit`, both rendered with no annotation distinguishing them
    from `POST /api/projects` itself. The writer called all three; the
    mock accepted them (it does not enforce business rules), the real
    platform correctly 400'd on the telephony one
    ("This project does not have telephony collection configured"), and
    the whole chain died on step 1 over a call nothing in the request
    ever asked for.

    So a filler entry (no `_closure_for`, and not in `core_keys`) gets an
    explicit "-- background context only" heading instead of no
    annotation at all -- the same per-entry, evidence-based style already
    used for REQUIRED, just naming the opposite conclusion. This is
    deliberately not a hard cap on how many endpoints a task may call
    (`n actions get n endpoints`): a single action can legitimately need
    more than one call (an id-producing closure, a content push, a
    pointer update all exist for exactly that reason), and a fixed count
    would either block those or have to special-case them one by one.
    Restating the same relevance judgement the scorer already computed,
    in the writer's own prompt, costs nothing extra to compute and cannot
    block a genuine multi-call task the way a count ever could.
    """
    if not chosen:
        return ""
    # Whether a closure entry is really the only source of the id it was
    # added for, among the endpoints actually being rendered. Checked
    # rather than assumed, because a hint or an explicit --path can put a
    # second producer of the same stem in the same slice, and a claim of
    # exclusivity would be wrong exactly when a writer went looking for the
    # alternative and found one.
    produced_by: Dict[str, int] = {}
    for entry in chosen:
        for stem in _produces(entry):
            produced_by[stem] = produced_by.get(stem, 0) + 1

    bodies, stripped = [], []
    for entry in chosen:
        op = _strip(dict(entry["op"]))
        op.pop("security", None)   # stated once in the header
        stripped.append(op)
        why = entry.get("_closure_for")
        heading = "### %s %s" % (entry["method"], entry["path"])
        if why:
            # An instruction, not a fact about the document, because a fact
            # competes with everything else on the page and this one cannot
            # be optional: skip it and the caller has no id to pass and will
            # substitute one that merely looks right. No fixed "call this
            # first" here -- that phrasing was right for an id producer,
            # which must run before its consumer, and backwards for a
            # content push, which must run after the resource it fills in
            # exists. Each closure's own message states which.
            heading += "  -- REQUIRED: %s" % why
            stems = _produces(entry)
            if stems and all(produced_by.get(s, 0) <= 1 for s in stems):
                heading += ", and nothing else in this slice returns one."
        elif core_keys is not None and \
                (entry["method"], entry["path"]) not in core_keys:
            heading += ("  -- background context only: this scored as a "
                        "weak match for the request text and is shown so "
                        "you know it exists, not because the task should "
                        "call it. Do not implement a call for it unless "
                        "the request explicitly describes this action.")
        block = "%s\n```json\n%s\n```" % (heading, json.dumps(op, indent=2))
        # Best-effort: a malformed fragment of the document must not take
        # the whole slice down over one missing example. The operation's
        # own full JSON just above already carries everything a writer
        # needs even if this fails silently.
        try:
            example = example_call(entry, spec)
        except Exception:                          # noqa: BLE001
            example = ""
        if example:
            block += "\n```python\n%s\n```" % example
        bodies.append(block)
    # Resolved from the *stripped* operations, not the originals. Following
    # the originals drags in `HTTPValidationError` and `ValidationError` for
    # every slice via the 422 that was just removed -- schemas describing a
    # response nothing in the output mentions.
    schemas = reachable_schemas(stripped, spec)
    parts = ["# Relevant endpoints, in full"] + bodies
    if schemas:
        parts += ["# Schemas these endpoints reference\n```json\n%s\n```"
                  % json.dumps(_strip(schemas), indent=2)]
    return "\n\n".join(parts)


# The existing disclaimers only ever address a *listed* gap: an endpoint
# named in the index but not detailed ("say so rather than guessing"), or a
# tag scoped out entirely ("other areas exist, ask"). Neither says anything
# about a capability with no match anywhere in the document, and that gap
# is exactly what a contract asking for something plausible-sounding --
# `get_ui_url(base_url, project_id) -> str` -- walks straight into. Measured
# live: the real spec has no such endpoint, the writer had the full 17
# operations it needed for everything else in its own prompt, and it called
# `GET /api/projects/{id}/my-ui-url` anyway, which is nowhere in the
# document and 404s the moment it runs. Every other instruction here was
# about a real endpoint being under- or over-described; this is the first
# one about an endpoint that was never there to begin with, and it needs
# saying because otherwise nothing does.
CLOSED_WORLD_NOTE = (
    "# Every endpoint this task may call is listed above, in full detail "
    "or in the index by name. There is no other endpoint. If a piece of "
    "requested functionality has no match in either -- a UI link, a value "
    "derived from something already returned, anything the document does "
    "not expose -- implement it without a network call rather than "
    "inventing a path, method, or field that looks plausible. An invented "
    "endpoint is worse than a missing feature: it passes every gate that "
    "does not execute it and fails only when it actually runs.")

# CLOSED_WORLD_NOTE closes off invention (nothing outside this list is
# real). This closes off the opposite mistake: everything on the list IS
# real and callable, which is exactly what let a plain "create a project;
# save its id..." request also create a task and a telephony session that
# were never asked for -- both endpoints happened to be sitting in the
# slice as filler (see `render`'s own `core_keys` docstring for the live
# failure), and being present was read as being wanted. Every "-- REQUIRED"
# annotation already tells a writer which calls it cannot skip; this note
# is what a "-- background context only" annotation means for everything
# else, said once rather than repeated on every such heading. Only added
# when the slice actually contains a filler entry -- a slice made entirely
# of core/required endpoints has nothing for this note to warn about, and
# a note that never applies costs tokens for nothing.
MINIMAL_CALL_NOTE = (
    "# Call the fewest endpoints that satisfy this request, not every "
    "endpoint shown above. An entry marked \"background context only\" is "
    "there so you can see it exists and rule it out, not as an "
    "instruction to build a method for it -- its presence in this "
    "reference material is not evidence the task needs it. If the "
    "request does not explicitly describe an action, do not implement a "
    "call for it just because its endpoint happens to be listed. (An "
    "endpoint marked REQUIRED is the opposite case: it has no such "
    "annotation, and skipping it is the mistake.)")


def auth_block(spec: Dict[str, Any]) -> str:
    """How to authenticate, said once instead of on every operation.

    "Every operation accepts either" reads as neutral and is not: an
    `apiKey` scheme reads a static credential the operator already has --
    the standard `API_KEY` environment variable every writer is separately
    told about -- while an `http`/`bearer` scheme normally needs a token
    obtained by calling a login endpoint first. Measured live: with both
    offered and no preference stated, a writer chose Bearer, found no login
    endpoint in its own slice, and invented `API_JWT_TOKEN` to hold a token
    that nothing in the file could ever obtain. The run reported clean --
    `gate_smoke`'s placeholder filling papers over exactly this, since any
    unset env var gets a placeholder value regardless of whether a real one
    could ever exist -- and the operator only found out at the command line,
    for a credential name they had never been told to set and had no way to
    satisfy correctly even if they invented a value for it.

    Naming which scheme to use was not enough on its own, either. Measured
    live on a four-step Maven chain: two of the four writers sent a
    correct, literal `X-API-Key: <value>` header and the other two sent
    `Authorization: ApiKey <value>` -- a header that does not exist in the
    spec, invented by blending the scheme's own name (`ApiKeyAuth`) with
    the generic `Authorization: <Scheme> <token>` shape every `http`
    scheme actually uses. Both wrong writers had the correct header name
    sitting three words away in the same rendered line
    (`ApiKeyAuth: apiKey header X-API-Key`) and read past it anyway,
    landing on the more familiar-looking pattern instead. The two
    real 401s this produced only surfaced against the live platform,
    because `gate_smoke`'s own placeholder key clears the mock either way
    -- the mock only checks that *a* value was sent, not that it arrived
    on the header the real service actually reads. So the instruction now
    spells out the literal header syntax for the chosen scheme directly,
    and forbids the exact wrong shape that was measured, by name, rather
    than trusting "use ApiKeyAuth" to be read together with a header name
    stated in a different clause of the same line.
    """
    schemes = ((spec.get("components") or {}).get("securitySchemes") or {})
    if not schemes:
        return ""
    lines = ["# Authentication (applies to every endpoint below)"]
    for name, scheme in schemes.items():
        bits = [scheme.get("type", "")]
        if scheme.get("scheme"):
            bits.append(scheme["scheme"])
        if scheme.get("name"):
            bits.append("header %s" % scheme["name"])
        lines.append("  %s: %s" % (name, " ".join(b for b in bits if b)))
        if scheme.get("description"):
            lines.append("    %s" % scheme["description"].strip().splitlines()[0])
    api_key_schemes = [(n, s) for n, s in schemes.items() if s.get("type") == "apiKey"]
    if len(schemes) > 1 and api_key_schemes:
        chosen_name, chosen_scheme = api_key_schemes[0]
        header_name = chosen_scheme.get("name") or "X-API-Key"
        lines.append(
            "  Multiple schemes are accepted; use %s. It reads the "
            "standard API_KEY environment variable this task already has. "
            "The other scheme(s) above normally require a token obtained "
            "from a login endpoint first -- if no such endpoint is listed "
            "below or in the index, do not invent a new environment "
            "variable to hold a token for it; use the API key scheme "
            "instead." % " or ".join(n for n, _ in api_key_schemes))
        lines.append(
            "  Send it as a literal HTTP header named exactly `%s`, with "
            "the raw key value and no prefix: `%s: <API_KEY value>`. Do "
            "NOT send it inside an `Authorization` header. Do NOT write "
            "`Authorization: %s <API_KEY value>`, `Authorization: ApiKey "
            "<API_KEY value>`, or `Authorization: Bearer <API_KEY "
            "value>` -- none of those headers exist in this spec; the "
            "scheme name (`%s`) and the word \"apiKey\" are this "
            "credential's label in the document, not a value or a prefix "
            "that belongs inside a header." % (header_name, header_name,
                                                chosen_name, chosen_name))
        lines.append(
            "  Build the header dict yourself, in this file, from "
            "os.environ[\"API_KEY\"], before the first request that needs "
            "it -- do not assume a `HEADERS` (or similarly named) variable "
            "is already defined \"by the execution environment\" or by "
            "code elsewhere that this file does not itself import. If you "
            "reference a name and hit a NameError, the fix is to build "
            "that name for real, never to add a "
            "`try: HEADERS\\nexcept NameError: HEADERS = {}`-shaped guard "
            "-- that silences the crash by sending every request with no "
            "credentials at all, which fails at the real API instead of "
            "failing loudly here.")
    else:
        lines.append("  Every operation below accepts either.")
    lines.append("  A 422 validation error response is omitted throughout; "
                 "assume it exists.")
    return "\n".join(lines)


def connection_setup_block(spec: Dict[str, Any]) -> str:
    """The literal code shape for building the base URL and the auth
    header(s) -- handed over as code to copy, not described in prose the
    writer has to translate.

    `auth_block` already names which scheme to use and exactly which
    header to send it on, and that was not enough on its own twice: one
    writer built the header on the wrong shape entirely
    (`Authorization: ApiKey <value>` instead of `X-API-Key: <value>`,
    fixed by naming the exact wrong shape and forbidding it by name,
    above), and a different one never built it at all -- a `create_task`
    step referenced a global `HEADERS` in its own `requests.post(...,
    headers=HEADERS, ...)` calls with a comment reading "Assume client
    setup and headers are available from the execution environment," hit
    the resulting `NameError`, and silenced it with a
    `try: HEADERS / except NameError: HEADERS = {}` guard instead of
    building the header for real (see
    `codegen.gate_placeholder_credential_guard`). A sibling failure hit
    the identical shape one constant over: a `07_update_project` step's
    own comment read "Assume client setup handles API_BASE_URL and
    HEADERS initialization" directly above code that, on an earlier
    attempt, never actually built either one, and the real run failed
    with `NameError: name 'API_BASE_URL' is not defined` three steps
    into a live chain.

    Both of those are the same failure at different granularity: naming
    *which* header to send fixes a writer that builds one and gets the
    shape wrong, but does nothing for a writer that never builds one at
    all because it is treating "the execution environment" as a place
    that magically supplies configuration this file never actually
    imports or constructs. Prose describing that requirement has already
    lost twice; handing over the literal three lines that satisfy it,
    the way `maven_applet_boilerplate` hands over a working reference
    applet instead of describing the iframe protocol in prose, costs
    nothing and leaves nothing to mistranslate.

    Deliberately silent when there is no security scheme at all --
    `auth_block` already returns `""` for that case, and a boilerplate
    for building credentials that do not exist would be actively wrong
    advice, not merely unnecessary.
    """
    schemes = ((spec.get("components") or {}).get("securitySchemes") or {})
    if not schemes:
        return ""
    api_key_schemes = [(n, s) for n, s in schemes.items()
                       if s.get("type") == "apiKey"]
    if api_key_schemes:
        header_name = api_key_schemes[0][1].get("name") or "X-API-Key"
        example = (
            "API_BASE_URL = os.environ[\"API_BASE_URL\"]\n"
            "API_KEY = os.environ[\"API_KEY\"]\n"
            "HEADERS = {\n"
            "    \"%s\": API_KEY,\n"
            "    \"Content-Type\": \"application/json\",\n"
            "}" % header_name)
    else:
        example = (
            "API_BASE_URL = os.environ[\"API_BASE_URL\"]\n"
            "API_KEY = os.environ[\"API_KEY\"]\n"
            "HEADERS = {\n"
            "    \"Authorization\": f\"Bearer {API_KEY}\",\n"
            "    \"Content-Type\": \"application/json\",\n"
            "}")
    return (
        "# Connection setup -- build these once, exactly like this, at "
        "module scope,\n"
        "# before any function definition and before "
        "`if __name__ == \"__main__\":`,\n"
        "# in whichever file actually calls requests.*. Do not write a "
        "comment saying\n"
        "# these are \"assumed\", \"handled by client setup\", or "
        "\"available from the\n"
        "# execution environment\" and then skip building them -- nothing "
        "supplies\n"
        "# them but this exact code, in this exact file, and a name "
        "referenced but\n"
        "# never assigned raises NameError the first time it is used, "
        "which is a real\n"
        "# crash, not a placeholder that quietly works.\n"
        "import os\n"
        "import requests\n\n"
        + example +
        "\n\n"
        "# If this task is split across files (e.g. client.py + main.py), "
        "build these\n"
        "# once in whichever file owns the client and import them (or "
        "import that\n"
        "# module and use its constants) from any sibling that also "
        "needs them --\n"
        "# never redeclare a second copy, and never reference HEADERS/"
        "API_BASE_URL in\n"
        "# a file that does not itself build or import them.")


def _schema_name(node: Any) -> Optional[str]:
    """The named schema a response refers to, through a list if need be."""
    if isinstance(node, dict):
        if "$ref" in node:
            return node["$ref"].rsplit("/", 1)[-1]
        if "items" in node:
            return _schema_name(node["items"])
    return None


def _stem(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


# What a schema is called once the naming convention is taken off it.
# `AppletVersionResponse` describes an applet version; the suffix is a fact
# about the generator, not about the resource.
_WRAPPER_SUFFIXES = ("response", "out", "read", "public", "schema")


def _keys_for(name: str) -> Set[str]:
    """Stems a schema name may be referred to by in an `*_id` field.

    Exact, never prefix. `thing` is a prefix of `thingversionresponse`, so
    prefix matching made a chosen version-creating endpoint look like a
    thing-creating one, and the closure stayed silent about the endpoint
    that was actually missing.
    """
    stem = _stem(name)
    keys = {stem}
    for suffix in _WRAPPER_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix):
            keys.add(stem[:-len(suffix)])
    return keys


def producers(ops: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """What each POST creates, keyed by the stem of its response schema.

    `POST /api/applets/{applet_id}/versions` answers with
    `AppletVersionResponse`, so it is filed under `appletversion` -- which
    is what `applet_version_id` reduces to. The pairing is read off the
    document, not guessed.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for entry in ops:
        if entry["method"].upper() != "POST":
            continue
        responses = (entry.get("op") or {}).get("responses") or {}
        body = responses.get("201") or responses.get("200") or {}
        schema = ((body.get("content") or {})
                  .get("application/json", {}).get("schema"))
        name = _schema_name(schema or {})
        for key in _keys_for(name) if name else ():
            out.setdefault(key, []).append(entry)
    return out


def collection_producers(ops: Sequence[Dict[str, Any]]
                         ) -> Dict[str, List[Dict[str, Any]]]:
    """POSTs whose path ends in a collection, which is how one is created.

    A fallback for documents that do not name what they return. `POST
    /api/teams` answers with an unnamed object, so nothing files it under
    `team`, and eleven endpoints taking a `team_id` had no way to get one.
    Measured on the real spec, this tier is the difference between 74% and
    85% of `*_id` requirements resolving.

    The last segment must be a literal plural, which is what separates
    creating a member from acting on one. `POST /api/projects/{id}/tasks`
    makes a task; `POST /api/assignments/{id}/submit` and `POST
    /api/tasks/{id}/units/send-to-qa` do not make a submit or a send-to-qa,
    and both are excluded by that rule rather than by a list of verbs.

    Crude de-pluralising is safe here and not merely tolerable: it errs
    towards nonsense keys like `recordingstatu`, and since a field stem is
    matched exactly, a key nothing can ask for is a key that never fires.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for entry in ops:
        if entry["method"].upper() != "POST":
            continue
        last = entry["path"].rstrip("/").rsplit("/", 1)[-1]
        if "{" in last:
            continue
        stem = _stem(last)
        if stem.endswith("ies"):
            key = stem[:-3] + "y"
        elif stem.endswith("s"):
            key = stem[:-1]
        else:
            continue
        out.setdefault(key, []).append(entry)
    return out


def _produces(entry: Dict[str, Any]) -> Set[str]:
    responses = (entry.get("op") or {}).get("responses") or {}
    body = responses.get("201") or responses.get("200") or {}
    schema = ((body.get("content") or {})
              .get("application/json", {}).get("schema"))
    name = _schema_name(schema or {})
    return _keys_for(name) if name else set()


def required_ids(entry: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    """The `*_id` values an operation cannot be called without inventing.

    Both halves matter and each one produced a live defect. A path template
    `POST /api/tasks/{task_id}/units/import` needs a task that something
    else has to create, and a generated client hardcoded `task-123`. A body
    field `applet_version_id` needs a version, and a generated client
    passed the applet's own id and got a 500 from the real service.

    A body field only belongs here if the schema's own `required` list says
    so. `ProjectCreate.applet_version_id` is `anyOf: [uuid, null]` with
    `required: ["name"]` in the real spec -- a project can be created with
    no applet version at all and pointed at one later via PATCH -- but this
    used to collect every `*_id`-suffixed property regardless, so a plain
    "create a project" request was handed a CONTRACT note reading
    `POST /api/projects -- REQUIRED: call this first -- ... needs
    applet_version_id`, sending the writer off to also build
    applet-version-creation into a component that request never asked for.
    Path template ids stay unconditional: OpenAPI requires `required: true`
    on every path parameter, so there is no analogous optional case there.
    """
    names = list(re.findall(r"\{(\w+_id)\}", entry["path"]))
    op = entry.get("op") or {}
    body = op.get("requestBody") or {}
    if "$ref" in body:
        body = _resolve(body["$ref"], spec) or {}
    schema = ((body.get("content") or {})
              .get("application/json", {}).get("schema")) or {}
    if "$ref" in schema:
        schema = _resolve(schema["$ref"], spec) or {}
    required = set(schema.get("required") or [])
    for field in (schema.get("properties") or {}):
        if field.endswith("_id") and field in required:
            names.append(field)
    return names


def _candidate_stems(entry: Dict[str, Any], field: str) -> List[str]:
    """What resource an `*_id` refers to, by its name and by its position.

    The name alone answers for most of them, and it under-reaches on the
    short ones: `version_id` reduces to `version`, while the thing that
    makes one answers with `AppletVersionResponse`. The path says which
    version -- `/api/applet-versions/{version_id}/html` -- and the segment
    in front of a placeholder is the collection it indexes into, which is
    as mechanical as the rest of this. Measured on the real spec: 70% of
    requirements resolve by name, 4% more by position.

    Ordered, because the id's own name is the more specific statement:
    `applet_version_id` sitting under `/api/applets/` must not resolve to
    an applet.
    """
    stems = [_stem(field[:-3])]
    marker = "{%s}" % field
    if marker in entry["path"]:
        before = entry["path"].split(marker)[0].rstrip("/").rsplit("/", 1)[-1]
        collection = _stem(before)
        if collection.endswith("ies"):
            stems.append(collection[:-3] + "y")
        elif collection.endswith("s"):
            stems.append(collection[:-1])
        stems.append(collection)
    return [s for i, s in enumerate(stems) if s and s not in stems[:i]]


def _body_id_fields(entry: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    """The `*_id` fields this operation's own request body writes, ignoring
    its path -- the half of `required_ids` that names what a call points a
    resource *at*, ready for the opposite direction: not "what does this
    call need", but "what already-chosen id does this call accept."
    """
    op = entry.get("op") or {}
    body = op.get("requestBody") or {}
    if "$ref" in body:
        body = _resolve(body["$ref"], spec) or {}
    schema = ((body.get("content") or {})
              .get("application/json", {}).get("schema")) or {}
    if "$ref" in schema:
        schema = _resolve(schema["$ref"], spec) or {}
    return [f for f in (schema.get("properties") or {}) if f.endswith("_id")]


def close_pointer_updates(spec: Dict[str, Any], ops: Sequence[Dict[str, Any]],
                         chosen: List[Dict[str, Any]],
                         budget: int = 2) -> List[Dict[str, Any]]:
    """Add the endpoint that re-points an existing parent at something the
    chosen operations just created, when the document exposes one.

    `close_over_ids` looks backward from a chosen operation to whatever
    produces the id it needs. This looks the other way: forward, from a
    chosen operation that produces an id to whatever else in the document
    takes that same id as a body field -- the "attach the new thing to its
    parent" call a design-update flow is not complete without. Measured
    live, as a gap rather than a defect: creating an applet, a version,
    a project and a task in one request already worked, because the
    project's own creation takes `applet_version_id` directly and
    `close_over_ids` was already finding the version for it. What was
    never covered is the follow-up shape -- "push a new design" -- where
    the version is created on its own and nothing says the project that
    already points at the applet needs to be told about it. A version
    with nobody pointed at it is invisible in the same way a missing id
    is: the version is created, the mock accepts it, the file smoke-runs
    clean, and the live project keeps serving the old design.

    Matched by the same stem space `_produces`/`_candidate_stems` already
    share, so `applet_version_id` on `PATCH /api/projects/{project_id}`
    is found from `AppletVersionResponse` the identical way
    `close_over_ids` finds the producer from the consumer's own id field --
    same rule, opposite direction, not a second one to keep in sync by
    hand. Restricted to PATCH/PUT, the same as `close_content_pushes`: a
    POST here would be creating a second parent, not re-pointing an
    existing one, which is a different (and already-covered) shape.

    Not added at all when a *chosen* POST already threads the same id into
    its own creation body. That is the whole reason this closure exists in
    the first place, stated the other way round: "push a new design" has
    no creating call to hang the id on, because the project already exists
    and only the version is new -- but "create an applet, a project, and a
    task in one request" is the opposite shape, where `POST /api/projects`
    is itself chosen and *is* the moment the pointer gets set, by taking
    `applet_version_id` directly. There is no existing parent to re-point
    in that shape; the parent does not exist yet either. Measured live: a
    from-scratch creation request still got a `REQUIRED: PATCH
    /api/projects/{project_id}` note appended to its CONTRACT, purely
    because the document happens to expose a re-pointer that accepts the
    same field -- not because anything in this request's own chosen
    operations left the pointer unset. Told this was required, the writer
    added the method and called it with a placeholder id before the
    project existed to have a real one, which is exactly the dead,
    never-deletable call a repair loop cannot fix by rewriting code: the
    instruction itself was wrong, and no amount of retrying the file
    changes that.
    """
    have: Set[str] = set()
    for entry in chosen:
        have |= _produces(entry)
    if not have:
        return []
    consumed_at_creation: Set[str] = set()
    for entry in chosen:
        if entry["method"].upper() != "POST":
            continue
        for field in _body_id_fields(entry, spec):
            consumed_at_creation.add(_stem(field[:-3]))
    seen = {(e["method"], e["path"]) for e in chosen}
    added: List[Dict[str, Any]] = []
    for entry in ops:
        key = (entry["method"], entry["path"])
        if key in seen or entry["method"].upper() not in ("PATCH", "PUT"):
            continue
        for field in _body_id_fields(entry, spec):
            stem = _stem(field[:-3])
            if stem not in have or stem in consumed_at_creation:
                continue
            seen.add(key)
            entry["_closure_for"] = (
                "call this after the resource above is created -- its "
                "%s field is how the new one gets attached to its "
                "existing parent, and nothing else in this slice does "
                "that" % field)
            added.append(entry)
            break
        if len(added) >= budget:
            break
    return added


def close_content_pushes(spec: Dict[str, Any], ops: Sequence[Dict[str, Any]],
                         chosen: List[Dict[str, Any]],
                         budget: int = 2) -> List[Dict[str, Any]]:
    """Add the endpoint that pushes real content into something just created,
    when creating it required a URL rather than the content itself.

    `AppletVersionCreate` requires `artifact_url` up front -- a string, not
    a file -- which reads as "host this somewhere else first" and is not:
    the resource also exposes `PATCH .../update-content`, which is how its
    real bytes actually get in. Skipping that call is invisible in exactly
    the way a missing id is invisible: the version is created, the mock
    accepts a fabricated URL because a string satisfies a string field,
    the file smoke-runs clean, and only the live platform is left pointing
    at a link nothing serves. Measured live: a generated client sent
    `artifact_url="https://cdn.example.com/applets/.../survey.html"` --
    an invented address, never uploaded to -- and never called the one
    endpoint that would have pushed the real HTML it had already written
    to `applet.html` in the same run. Every gate here runs against a mock,
    so nothing catches a fabricated-but-well-formed URL; this closure
    exists because that class of defect has no gate that would see it.

    Matched the same way `close_over_ids` matches a missing id: the
    resource a chosen POST just produced is looked up by the same stems
    `_candidate_stems` already computes from a sibling operation's own id
    parameter and path, so this reuses the identical mechanical rule
    rather than inventing a second one. Restricted to PATCH/PUT operations
    whose path names content, so it cannot fire on an unrelated sibling
    (`GET .../html`, `DELETE .../{version_id}`) that happens to share the
    same id family. Where the document has no such endpoint, nothing is
    added -- the same rule `close_over_ids` follows for a producer that
    does not exist.
    """
    have: Set[str] = set()
    for entry in chosen:
        have |= _produces(entry)
    if not have:
        return []
    seen = {(e["method"], e["path"]) for e in chosen}
    added: List[Dict[str, Any]] = []
    for entry in ops:
        key = (entry["method"], entry["path"])
        if key in seen or entry["method"].upper() not in ("PATCH", "PUT"):
            continue
        if "content" not in entry["path"].lower():
            continue
        for field in re.findall(r"\{(\w+_id)\}", entry["path"]):
            if any(s in have for s in _candidate_stems(entry, field)):
                seen.add(key)
                entry["_closure_for"] = (
                    "call this after creating the resource above -- its "
                    "create endpoint only took a URL, not content, so the "
                    "real bytes still have to be pushed through here")
                added.append(entry)
                break
        if len(added) >= budget:
            break
    return added


def close_over_ids(spec: Dict[str, Any], ops: Sequence[Dict[str, Any]],
                   chosen: List[Dict[str, Any]],
                   budget: int = 4,
                   core: Optional[Sequence[Dict[str, Any]]] = None
                   ) -> List[Dict[str, Any]]:
    """Add the endpoints that create the ids the chosen ones demand.

    A slice missing these is worse than one missing an unrelated endpoint,
    because the gap is invisible: the writer cannot call what it was not
    given, so it substitutes something shaped like an id and every check
    passes. Measured twice on the same spec. A client told to import units
    into a task it had no way to create used `task-123`; a client needing
    `applet_version_id` passed the applet's id, cleared the mock -- a uuid
    is a uuid -- and drew a 500 from the real service. One of them said so
    in a comment: "in a real flow, one would call POST
    /api/applets/{id}/versions".

    Mechanical, and the reason it can be trusted is the reason `$ref`
    resolution can: it reads a pairing already written in the document
    rather than deciding anything. Where the document is ambiguous it takes
    the shortest path, which is the collection endpoint -- `POST
    /api/projects` over `POST /api/projects/{project_id}/clone` -- and
    where it says nothing it adds nothing.

    `core` is which of `chosen` actually gets to demand a closure --
    default is all of `chosen`, but `slice_for` passes a narrower set, and
    the difference matters. `chosen` includes plenty of context nobody
    asked to have closed over: `limit` deliberately keeps filling the
    slice with anything that scores above zero, on the theory that a
    writer is likelier to need an unlisted neighbour than an unlisted
    Telephony endpoint -- and every one of those filler entries has its
    own unrelated `*_id` needs that this function would otherwise treat as
    exactly as load-bearing as the request's actual subject. Measured
    live: "create a project" pulled in an unrelated `GET .../assignments/
    {assignment_id}/draft-versions/{version_id}` as filler, and closing
    over *its* `version_id` requirement escalated `POST /api/applets/
    {applet_id}/versions` to a REQUIRED contract clause on a script that
    creates a project and nothing else -- the writer was told, forcefully,
    to add an applet-versioning method it would never call. `chosen`
    having a `*_id` need is not evidence that need is real for *this*
    request; being the thing the request actually named, or being close
    enough to it, is. A filler entry's own ordinary needs are exactly the
    ones the rest of the slice already teaches a writer to leave alone,
    same as any other endpoint it is not using.
    """
    made = producers(ops)
    # Strictly a fallback. What a document says it returns beats what its
    # URL shape implies, every time that first one says anything at all.
    by_url = collection_producers(ops)
    have: Set[str] = set()
    for entry in chosen:
        have |= _produces(entry)
    seen = {(e["method"], e["path"]) for e in chosen}
    added: List[Dict[str, Any]] = []

    for entry in list(core if core is not None else chosen):
        for field in required_ids(entry, spec):
            stems = _candidate_stems(entry, field)
            if any(s in have for s in stems):
                continue
            # First stem that answers wins, rather than the best of all of
            # them pooled. Pooling loses the ordering: `thing_version_id`
            # under `/api/things/` offered both the version endpoint and
            # the shorter thing endpoint, and shortest-path picked the
            # thing -- resolving the id by where it sits over what it is
            # called, which is backwards.
            candidates: List[Dict[str, Any]] = []
            for stem in stems:
                for table in (made, by_url):
                    candidates = [c for c in table.get(stem, ())
                                  if (c["method"], c["path"]) not in seen]
                    if candidates:
                        break
                if candidates:
                    break
            if not candidates:
                continue
            # Shortest path is the collection endpoint, which is the one
            # that creates rather than one that acts on an existing member.
            pick = min(candidates, key=lambda c: (len(c["path"]), c["path"]))
            seen.add((pick["method"], pick["path"]))
            have |= _produces(pick)
            # Read by render(), which is the only reason this is here. A
            # closure entry looks exactly like every other operation in the
            # slice once it is rendered, and a planner skimming a flat list
            # of ten endpoints has no signal that one of them is mandatory
            # plumbing rather than an optional extra it can leave out.
            # Measured: the endpoint was in the slice and the planner did
            # not put it in the contract, so the writer fell back to
            # passing the applet's own id where a version id belonged.
            pick["_closure_for"] = "call this first -- %s %s needs %s" % (
                entry["method"], entry["path"], field)
            added.append(pick)
            if len(added) >= budget:
                return added
    return added


def slice_for(spec: Dict[str, Any], request: str, limit: int = 12,
              paths: Sequence[str] = (), tags: Sequence[str] = (),
              with_index: bool = True, hints: Sequence[Tuple[str, str]] = (),
              avoid: Sequence[Tuple[str, str]] = (),
              scope_index: bool = True,
              budget: Optional[int] = None,
              exclude_tags: Sequence[str] = ()) -> Tuple[str, List[Dict[str, Any]]]:
    """The whole pipeline: pick operations, resolve them, render.

    Explicit `paths`/`tags` are unioned with the scored matches rather than
    replacing them, because an operator who knows one endpoint they need
    usually does not know all of them, and the scorer is free.

    `hints` are verified (method, path) pairs from `propose`, and go in
    ahead of the scored ones: where the two disagree the model is usually
    right, because disagreement means the request and the endpoint share no
    words, which is precisely the case scoring cannot see. They are unioned
    rather than substituted, so a model that returns one endpoint and stops
    still gets the scorer's neighbours, and a model that returns nothing
    costs nothing.

    `avoid`, also from `propose`, is the other direction of the same
    judgement and is not unioned -- it is a veto. Term overlap does not
    just miss endpoints it should have found, it ranks wrong ones with real
    confidence: "data collection" scored a `.../collection/units` endpoint
    over the unit-upload endpoint the request actually meant. Weighting
    cannot fix that, because the collision is not about frequency, it is
    about the scorer having no idea "collection" meant something different
    here. So a name in `avoid` is dropped from the hint and scored paths
    outright, before either runs -- unlike a hint, which only ever adds,
    this one only ever removes, and always wins the disagreement. It does
    not reach `paths`/`tags`, which came from the operator directly rather
    than from a model guessing at the same request, and it does not reach
    `close_over_ids`: a producer another chosen endpoint cannot be called
    without is a structural fact, not a coincidental keyword, and the
    model was never asked about it.

    `scope_index` trims the catalogue to the tags actually chosen. On the
    real spec that is the difference between 5701 tokens of index and about
    1500, against a 16384 budget the detailed operations also have to fit
    in.

    `exclude_tags` is a standing "never", not a per-request judgement like
    `avoid` -- it comes from the operator's own registered preference
    (`openapi.excluded_tags`), not from a model reading one sentence. Unlike
    `avoid`, an explicit `paths`/`tags` still wins over it: naming a path or
    tag directly is a stronger, present-tense statement of intent than a
    standing default, the same asymmetry `avoid` already respects by not
    reaching those two.
    """
    wanted = _expand_request_terms(_strip_sibling_paths(request))
    ops = operations(spec)
    barred_tags = {t.lower() for t in exclude_tags}
    barred = {(e["method"].upper(), e["path"]) for e in ops
             if barred_tags and any(t.lower() in barred_tags
                                    for t in (e["tags"] or []))}
    by_key = {(e["method"].upper(), e["path"]): e for e in ops}
    # A synonym-table match (see `_synonym_segment_match`) is exempt from
    # `avoid` outright, computed once here so both the hints loop and the
    # scored-ranking loop below share the identical exemption rather than
    # each needing its own copy of this check.
    synonym_protected = {(e["method"].upper(), e["path"]) for e in ops
                         if _synonym_segment_match(e["path"], request)}
    vetoed = {(m.upper(), p) for m, p in avoid} - synonym_protected
    chosen: List[Dict[str, Any]] = []
    seen = set()
    # How hard each entry is to give up, for the trim below. Lower is
    # safer to drop.
    rank: Dict[Tuple[str, str], Tuple[int, float]] = {}

    for hint in hints:
        key = (hint[0].upper(), hint[1])
        entry = by_key.get(key)
        if entry is not None and key not in seen and key not in vetoed \
                and key not in barred:
            seen.add(key)
            rank[key] = (2, 0.0)
            chosen.append(entry)

    for entry in ops:
        key = (entry["method"], entry["path"])
        explicit = (any(p in entry["path"] for p in paths)
                    or any(t.lower() in [x.lower() for x in entry["tags"]]
                           for t in tags))
        if explicit and key not in seen:
            seen.add(key)
            rank[key] = (3, 0.0)
            chosen.append(entry)

    weight = weights(ops)
    ranked = _demote_unrequested_nested_creates(
        ops, wanted,
        sorted(((score(e, wanted, weight), i, e) for i, e in enumerate(ops)),
               key=lambda t: (-t[0], t[1])))
    for value, _, entry in ranked:
        # A float now, so compared against an epsilon rather than zero. Terms
        # the document considers worthless contribute exactly 0.0, which is
        # the whole point: matching only those is not a match.
        if value <= 1e-9 or len(chosen) >= limit:
            break
        key = (entry["method"], entry["path"])
        if key not in seen and key not in vetoed and key not in barred:
            seen.add(key)
            rank[key] = (1, value)
            chosen.append(entry)

    def build(picked: Sequence[Dict[str, Any]],
             core_keys: Optional[Set[Tuple[str, str]]] = None) -> str:
        scope: Optional[Set[str]] = None
        if scope_index and picked:
            scope = {t for e in picked for t in (e["tags"] or ["Other"])}
        has_filler = core_keys is not None and any(
            not e.get("_closure_for")
            and (e["method"], e["path"]) not in core_keys
            for e in picked)
        return "\n\n".join(p for p in (
            auth_block(spec),
            connection_setup_block(spec),
            index_block(spec, scope) if with_index else "",
            render(spec, picked, core_keys),
            EXAMPLE_CALL_NOTE if picked else "",
            CLOSED_WORLD_NOTE if picked else "",
            MINIMAL_CALL_NOTE if has_filler else "") if p)

    def core_of(base: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Which of `base` is allowed to demand a closure -- a hint (tier
        # 2), an explicit --path/--tag match (tier 3), or a scored match
        # (tier 1) strong enough to be the request's actual subject rather
        # than filler that merely cleared the "score above zero" bar to
        # fill out the slice. "Strong enough" is relative to this base's
        # own best scored match, not an absolute cutoff, since a request
        # with one clear match and a request with several close ones
        # should not share a fixed number.
        scored = [(rank.get((e["method"], e["path"]), (1, 0.0))[1], e)
                 for e in base
                 if rank.get((e["method"], e["path"]), (1, 0.0))[0] == 1]
        floor = (max(v for v, _ in scored) * 0.5) if scored else 0.0
        # The single best-scoring match is the one thing here actually
        # grounded in the request rather than merely close enough not to
        # get trimmed -- so a second tier-1 entry additionally has to
        # share a tag with it to be core. Term overlap alone already lets
        # an unrelated-domain endpoint clear the score floor just by
        # sharing a generic word ("upload") with the real match: measured
        # live, `POST /api/projects/{project_id}/collection/presign-upload`
        # (tag "Data Collection") scored 17.2 against a floor of 10.0 for
        # a request whose real, tag-"Units & Workflow" match scored 20.1
        # -- close enough on the score alone to demand its own REQUIRED
        # closure over an unrelated file-upload flow the request never
        # asked for. Only ever narrows tier 1; tier 2 (hints) and tier 3
        # (explicit --path/--tag) are unaffected, since both already carry
        # their own, separately-earned trust.
        top_tags: Set[str] = set()
        if scored:
            best_value = max(v for v, _ in scored)
            for v, e in scored:
                if v >= best_value - 1e-9:
                    top_tags |= set(e["tags"] or [])
        result = []
        for e in base:
            key = (e["method"], e["path"])
            tier, value = rank.get(key, (1, 0.0))
            if tier == 1:
                if value >= floor and (not top_tags
                                       or set(e["tags"] or []) & top_tags):
                    result.append(e)
                continue
            if tier == 2:
                # A hint (tier 2) is a model's own semantic leap across the
                # whole index, trusted with no term-overlap check at all --
                # that tolerance is the entire reason hints exist ("sign
                # in" -> /auth/login shares no words with the request that
                # needs it). But the exact same tolerance is what let a
                # request to "push survey.html as a new version" surface
                # POST /api/sharepoint/sync/{config_id} -- a completely
                # unrelated integration whose only connection to the
                # request is a guessed synonym ("push"/"sync") -- and
                # because a hint is unconditionally core-eligible, that one
                # bad guess forced a REQUIRED clause demanding the writer
                # invent a whole new "create a sharepoint config" client
                # method before doing anything else. Measured live: the
                # writer never made a single real API call and burned its
                # entire repair budget plus a full replan, on a task that
                # needed neither SharePoint endpoint.
                #
                # A hint still belongs in `base`/the slice unconditionally
                # -- it costs little to let the writer see an extra
                # endpoint and ignore it, which is the whole "hints only
                # ever add" design. What it should not get to do on its
                # own say is force a fabricated method into the CONTRACT.
                # So core-eligibility for a hint additionally requires the
                # same lexical grounding a scored match already needed:
                # score() > 0 against this request's own terms. A real
                # synonym match like login usually needs no further id
                # closure anyway, so this loses nothing there; a hint that
                # shares literally no vocabulary with the request is
                # exactly the shape of the sharepoint collision and is the
                # one case this is built to catch.
                if score(e, wanted, weight) > 1e-9:
                    result.append(e)
                continue
            # tier 3: an explicit --path/--tag the operator named directly,
            # a stronger, present-tense statement of intent than a model's
            # guess. Trusted unconditionally, as before.
            result.append(e)
        return result

    def closed(base: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Outside `limit`: an endpoint that creates an id another chosen
        # endpoint requires is not competing with them for a slot, it is
        # what makes them callable at all. Recomputed on every trim, or
        # dropping an endpoint would strand the producer it asked for.
        # Same treatment for the sibling that pushes real content into a
        # resource whose creation only ever took a URL for it, and for the
        # parent-pointer update that attaches a just-created resource to
        # something that already existed before this request.
        #
        # Only `close_over_ids` is narrowed to `core` -- a filler entry's
        # own `*_id` need is not evidence the request needs it satisfied,
        # for the reason documented on `close_over_ids` itself. The other
        # two closures key off a *chosen* entry's own creation shape
        # (a POST that only took a URL, a PATCH re-pointing an id this
        # request just produced), which is a fact about that entry
        # regardless of how strongly it scored, not a demand it makes on
        # something else.
        core = core_of(base)
        picked = (base + close_over_ids(spec, ops, base, core=core)
                 + close_content_pushes(spec, ops, base)
                 + close_pointer_updates(spec, ops, base))
        core_keys = {(e["method"], e["path"]) for e in core}
        return picked, core_keys

    picked, core_keys = closed(chosen)
    text = build(picked, core_keys)
    if budget is not None:
        # Trim rather than refuse. Closing over ids took a real request
        # from 13324 tokens to 17555, over a 16384 ceiling, and the run
        # stopped with advice to lower --api-limit -- which would have
        # dropped the very endpoints that had just been added. The weakest
        # term match is what should go: it was a guess in the first place,
        # while a hint came from a model that read the whole index and a
        # closure entry is what another endpoint cannot be called without.
        import swarm as _swarm
        while _swarm.est_tokens(text) > budget:
            # Never to nothing. A budget too small for one operation is a
            # misconfigured fleet, and answering it with an empty slice
            # would hand the writers an index and no detail -- strictly
            # worse than handing them the one endpoint that matched best,
            # and silent about why.
            if len(chosen) <= 1:
                break
            weak = [e for e in chosen
                    if rank.get((e["method"], e["path"]), (2, 0.0))[0] == 1]
            if not weak:
                break
            worst = min(weak, key=lambda e: rank[(e["method"], e["path"])][1])
            chosen = [e for e in chosen if e is not worst]
            picked, core_keys = closed(chosen)
            text = build(picked, core_keys)
    return text, picked


def reconsider_slice(spec: Dict[str, Any], request: str,
                     chosen: Sequence[Dict[str, Any]], cfg: Any, state: Any,
                     limit: int, hints: Sequence[Tuple[str, str]],
                     avoid: Sequence[Tuple[str, str]],
                     budget: Optional[int] = None,
                     exclude_tags: Sequence[str] = (),
                     ) -> Optional[Tuple[str, List[Dict[str, Any]], str]]:
    """Re-examines a slice that a planning attempt just failed against,
    asking the two questions `slice_for` itself never asks about its own
    output: does this look starved of a real match, or bloated with
    filler that just happened to clear "score above zero"?

    Deliberately not "does the CONTRACT have a method for every REQUIRED
    endpoint" -- that gap is already handled, unconditionally, by
    `contract_missing_endpoints` appending a note to the CONTRACT after a
    plan comes back, and a closure entry is guaranteed to already be in
    `chosen` (it sits outside `--api-limit` specifically so it cannot be
    squeezed out), so re-slicing cannot add what a `contract_missing_endpoints`
    gap is missing -- the endpoint was already offered; the planner just
    did not wire it. This function is about the complementary failure:
    the *slice itself* being wrong before any CONTRACT is even written,
    which is what actually correlates with a decompose attempt coming
    back empty or unusable.

    STARVED: nothing in `chosen` scored as a real match -- every entry is
    filler that only cleared "score above zero", the same real/filler
    line `slice_for`'s own `core_of` already draws for closures. A
    planner handed nothing but filler has nothing to build a real
    CONTRACT around. `propose_multi` is asked again (it talks to a model
    and is not guaranteed to answer the same way twice) and the limit is
    raised, so a second attempt can differ in more than just the roll of
    the planner call.

    BLOATED: the slice is not small and is still almost entirely filler --
    the same shape that once let an unrelated SharePoint sync endpoint
    surface for "push survey.html as a new version" and get mistaken for
    something to build toward. Narrowed to whatever actually scored as
    core, with everything else vetoed via `avoid` so `slice_for`'s own
    closures still run against the smaller base.

    Returns `None` when neither shape is detected -- expected to be the
    common case, since most failed decompositions have nothing to do with
    the slice, and re-slicing unconditionally would add noise to an
    unrelated failure the same way a detector firing on correct input
    always does.
    """
    if not chosen:
        return None
    wanted = _expand_request_terms(_strip_sibling_paths(request))
    weight = weights(operations(spec))
    scored = [(score(e, wanted, weight), e) for e in chosen]
    best = max((s for s, _ in scored), default=0.0)
    core_floor = best * 0.5
    core = [e for s, e in scored if s > 1e-9 and s >= core_floor]

    if not core and len(chosen) < limit:
        try:
            new_hints, more_avoid = propose_multi(spec, request, cfg, state,
                                                  verbose=False)
        except Exception:
            return None
        if not new_hints:
            return None
        new_avoid = list({(m.upper(), p) for m, p in
                          (list(avoid) + list(more_avoid))})
        new_limit = min(limit * 2, len(operations(spec)))
        new_text, new_chosen = slice_for(
            spec, request, limit=new_limit, hints=new_hints,
            avoid=new_avoid, budget=budget, exclude_tags=exclude_tags)
        old_keys = {(e["method"], e["path"]) for e in chosen}
        new_keys = {(e["method"], e["path"]) for e in new_chosen}
        if new_keys != old_keys:
            return (new_text, new_chosen,
                   "broadening the API slice (%d -> %d endpoints): nothing "
                   "in it scored as a real match for this request -- asked "
                   "again for endpoint hints and widened the limit"
                   % (len(chosen), len(new_chosen)))
        return None

    if len(chosen) >= 6 and len(core) <= len(chosen) * 0.3:
        keep = {(e["method"], e["path"]) for e in core}
        new_avoid = list({(m.upper(), p) for m, p in avoid} | {
            (e["method"].upper(), e["path"]) for e in chosen
            if (e["method"], e["path"]) not in keep})
        new_limit = max(4, len(core) or 4)
        new_text, new_chosen = slice_for(
            spec, request, limit=new_limit, hints=hints,
            avoid=list(new_avoid), budget=budget, exclude_tags=exclude_tags)
        if len(new_chosen) < len(chosen):
            return (new_text, new_chosen,
                   "narrowing the API slice (%d -> %d endpoints): most of "
                   "what was sliced in never scored as a real match for "
                   "this request"
                   % (len(chosen), len(new_chosen)))
    return None


def _endpoint_path_segs(entry: Dict[str, Any]) -> Set[str]:
    segs: Set[str] = set()
    for seg in re.split(r"[/{}_-]", entry.get("path") or ""):
        seg = re.sub(r"[^a-z0-9]", "", seg.lower())
        if len(seg) > 3 and seg not in ("api",):
            segs.add(seg)
    return segs


# CRUD-ish verbs and other words generic enough to appear in almost any
# endpoint's path or operationId regardless of which resource it names.
# "update" sits in this project's own real spec's path for exactly the
# endpoint this check exists to catch, and nowhere else in the same slice
# -- but a CONTRACT is free-form English, and `update_project` (a real,
# correctly-wired method two lines away) contains the substring "update"
# for a reason that has nothing to do with the missing endpoint. A word
# this generic must never be the sole thing that marks a gap as covered.
_GENERIC_ENDPOINT_WORDS = {
    "update", "create", "delete", "remove", "fetch", "list", "post",
    "patch", "status", "state",
}


def _endpoint_keywords(entry: Dict[str, Any]) -> Tuple[Optional[str], Set[str]]:
    """The operationId (as one collapsed token) and the set of individual
    path-segment keywords for one operation, each collapsed to bare
    alphanumerics so `update-content`, `update_content` and `updateContent`
    all normalise the same way. Kept apart because they are not equally
    strong evidence: a single generic segment like "update" or "version"
    turns up in unrelated methods (`update_project`, `applet_version_id`)
    purely by coincidence, the same collision `openapi.score`'s two-word
    gate for request-body fields already exists to police one layer up.
    """
    op_id = entry.get("operationId") or ""
    op_token = re.sub(r"[^a-z0-9]", "", op_id.lower()) or None
    return op_token, _endpoint_path_segs(entry)


def contract_missing_endpoints(
        contract: str,
        chosen: Sequence[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], str]]:
    """Which REQUIRED closure endpoints (see `close_content_pushes` and
    `close_pointer_updates`) the planner's own CONTRACT gives no method for.

    Marking an endpoint REQUIRED in the prompt is advice, and prose has
    already lost this argument once in this project (the entry-point
    rename that had to become a mechanical check instead of a stronger
    sentence). Measured live: a REQUIRED `PATCH .../update-content` sat in
    the same prompt as a CONTRACT wiring only `create_applet_version` and
    `update_project` onto `Client`, and the planner never added a third
    method for it -- so no file in the plan had any name to call for the
    one endpoint that actually pushes real content into a version. The gap
    is invisible in the plan printout: the CONTRACT parses fine, every
    component builds, and the missing capability only shows up as `main.py`
    burning its whole repair budget trying to call a method that was never
    going to exist, because inventing one violates the same CONTRACT's
    "use these exact names, do not define your own version of anything
    here" instruction that every other file was correctly told to obey.

    This is decidable without a model: `chosen` already carries which
    endpoints are REQUIRED (`_closure_for`, set by `slice_for`'s own
    closures) and the CONTRACT is plain text. A cheap keyword-in-text check
    is deliberately the whole test -- it can only ever under-report (an
    endpoint mentioned by coincidence reads as covered), never invent a gap
    that is not there, which is the same tradeoff `check_attributes` and
    every other mechanical detector in this project already makes.
    """
    if not chosen:
        return []
    # A CONTRACT is free-form English around its method list, not only the
    # method list itself -- "survey.html is the artifact content." sits in
    # the same string as "create_applet_version(...) -> str". Matching
    # against the whole normalised text let that sentence's own word
    # "content" read as a method covering `.../update-content`, when the
    # CONTRACT actually gives no method for it at all. Measured live: this
    # is exactly the false negative that let `client.py` ship with no way
    # to push real content, leaving `main.py` to fail its whole repair
    # budget against a requirement nothing could satisfy, silently, because
    # the mechanism meant to catch this specific gap reported it as covered.
    #
    # So matching is restricted to what a CONTRACT method actually is: an
    # identifier immediately followed by "(", the same shape every real
    # entry here has (`create_applet(...)`, `import_units(...)`). Prose
    # describing a file's content, a return type, or an argument name never
    # takes that shape, so it can no longer be mistaken for a method.
    method_names = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", contract or "")
    norm_contract = "\x00".join(re.sub(r"[^a-z0-9]", "", n.lower())
                                for n in method_names)
    # A single distinguishing path segment ("version") is a real word, and
    # a substring check against a method's full collapsed name would still
    # match it inside an unrelated method that legitimately contains that
    # word too -- "version" sits inside `create_applet_version` for a
    # reason that has nothing to do with pushing content. So a single
    # segment is only evidence when it is one whole snake_case/camelCase
    # word of a real method name, never a fragment of a longer one.
    method_words: Set[str] = set()
    for name in method_names:
        for word in re.findall(r"[A-Za-z0-9]+", re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name)):
            method_words.add(word.lower())
    # Rarity within this slice, the same idea `score()` already uses across
    # the whole document: a path segment shared by several endpoints here
    # ("applet", "version") says nothing about which one a CONTRACT method
    # covers, while one that appears on only a single endpoint ("content",
    # "status" as a resource name rather than the generic word) is real
    # signal. Computed over path segments only -- operationIds are already
    # specific enough on their own not to need this.
    from collections import Counter as _Counter
    seg_counts: "_Counter[str]" = _Counter()
    for entry in chosen:
        seg_counts.update(_endpoint_path_segs(entry))
    missing: List[Tuple[Dict[str, Any], str]] = []
    for entry in chosen:
        why = entry.get("_closure_for")
        if not why:
            continue
        op_token, segs = _endpoint_keywords(entry)
        # A full operationId match is strong evidence on its own -- it is
        # one specific multi-word token, not a common noun.
        covered = bool(op_token and op_token in norm_contract)
        if not covered:
            distinguishing = {s for s in segs
                              if seg_counts[s] <= 1
                              and s not in _GENERIC_ENDPOINT_WORDS}
            # One matching word is not enough even after the rarity filter:
            # "version" is rare *within this slice* (no sibling chosen path
            # repeats the singular form) and still a real, whole word inside
            # `create_applet_version` -- an unrelated method for a sibling
            # endpoint on the same resource, not evidence that anything
            # pushes content. Rarity within the slice says nothing about
            # rarity within the CONTRACT's own vocabulary, so a single
            # word survives by coincidence exactly where sibling endpoints
            # share a resource name. The same two-word gate `openapi.score`
            # already applies to request-body field overlap is applied
            # here: only two or more independently-matching words count as
            # real evidence, matching this project's existing rule that one
            # shared common word is a coincidence and two naming the same
            # thing is not.
            matches = {s for s in distinguishing if s in method_words}
            covered = len(matches) >= 2
        if covered:
            continue
        missing.append((entry, why))
    return missing


def describe_missing_endpoints(
        missing: Sequence[Tuple[Dict[str, Any], str]]) -> str:
    """Render `contract_missing_endpoints`'s findings as a note to append to
    the CONTRACT itself, so every writer -- not just whichever one hits the
    gap first -- sees it in the same place they are told the CONTRACT's
    other names are exhaustive. Phrased as an explicit exception to "do not
    define your own version of anything here", because that instruction is
    exactly what stops a writer from adding the missing method on its own.
    """
    if not missing:
        return ""
    lines = []
    for entry, why in missing:
        lines.append(
            "  %s %s -- REQUIRED: %s. No method above covers this endpoint;"
            " this is a gap in the CONTRACT, not a name to avoid. Whichever"
            " file owns the API client class must add one more method for"
            " it (a reasonable name and signature, inferred from the"
            " endpoint's own request/response shape above), and any file"
            " that needs the effect must call it. Adding this one method is"
            " not a violation of \"use these exact names\" -- the CONTRACT"
            " omitted it by mistake."
            % (entry.get("method", "?"), entry.get("path", "?"), why))
    return ("\n\nThe CONTRACT above does not account for every endpoint this"
            " task needs. Specifically:\n" + "\n".join(lines) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cut a large OpenAPI spec down to what a request needs.")
    parser.add_argument("spec", nargs="?", default="",
                        help="path to an openapi.json, or a registered name")
    parser.add_argument("request", nargs="?", default="",
                        help="what you are about to ask the pool to build")
    parser.add_argument("--out", help="write here (default: stdout)")
    parser.add_argument("--limit", type=int, default=12,
                        help="most operations to include in full")
    parser.add_argument("--path", action="append", default=[],
                        help="force-include operations whose path contains this")
    parser.add_argument("--tag", action="append", default=[],
                        help="force-include every operation with this tag")
    parser.add_argument("--no-index", action="store_true",
                        help="omit the endpoint listing")
    parser.add_argument("--no-assist", action="store_true",
                        help="skip the local model's reading of the index "
                             "and select by term matching alone")
    parser.add_argument("--whole-index", action="store_true",
                        help="list every endpoint, not just the areas the "
                             "selection landed in")
    parser.add_argument("--stats", action="store_true",
                        help="report sizes and exit without writing")
    parser.add_argument("--add", nargs=2, metavar=("NAME", "URL"),
                        help="fetch a spec and remember it under NAME")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch the named spec from where it came from")
    parser.add_argument("--list", action="store_true",
                        help="show registered specs")
    parser.add_argument("--exclude-tag", action="append", default=[],
                        metavar="TAG",
                        help="never select operations under this tag for "
                             "the named spec, from now on (persisted)")
    parser.add_argument("--include-tag", action="append", default=[],
                        metavar="TAG",
                        help="undo a previous --exclude-tag for this spec")
    args = parser.parse_args(argv)

    if args.add:
        name, url = args.add
        try:
            count = register(name, url)
        except Exception as exc:                       # noqa: BLE001 -- any
            # network, JSON or shape failure is the same outcome to the
            # operator: nothing was registered, and here is why.
            sys.stderr.write("could not fetch %s: %s\n" % (url, exc))
            return 1
        sys.stderr.write("registered %s -- %d operations from %s\n"
                         % (name, count, url))
        return 0

    if args.list:
        known = registry()
        if not known:
            sys.stderr.write("no specs registered. "
                             "./openapi.py --add NAME URL\n")
            return 0
        for name in sorted(known):
            entry = known[name]
            sys.stderr.write("  %-12s %4d ops  fetched %s\n     %s\n"
                             % (name, entry.get("operations", 0),
                                time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(
                                                  entry.get("fetched", 0))),
                                entry.get("url", "")))
            if entry.get("exclude_tags"):
                sys.stderr.write("     never selects: %s\n"
                                 % ", ".join(sorted(entry["exclude_tags"])))
        return 0

    if not args.spec:
        parser.error("a spec path or registered name is required")

    if args.exclude_tag or args.include_tag:
        if args.spec not in registry():
            sys.stderr.write(
                "%r is not a registered spec, so there is nowhere to "
                "remember an exclusion. ./openapi.py --add %s URL first\n"
                % (args.spec, args.spec))
            return 1
        if args.exclude_tag:
            current = set_excluded_tags(args.spec, args.exclude_tag, add=True)
            sys.stderr.write("%s will never select: %s\n"
                             % (args.spec, ", ".join(sorted(current)) or "(none)"))
        if args.include_tag:
            current = set_excluded_tags(args.spec, args.include_tag, add=False)
            sys.stderr.write("%s excluded tags now: %s\n"
                             % (args.spec, ", ".join(sorted(current)) or "(none)"))
        if not args.request:
            return 0

    if args.refresh:
        entry = registry().get(args.spec)
        if not entry or not entry.get("url"):
            sys.stderr.write("%s was not registered with a URL, so there is "
                             "nowhere to refresh it from\n" % args.spec)
            return 1
        try:
            count = register(args.spec, entry["url"])
        except Exception as exc:                       # noqa: BLE001
            sys.stderr.write("could not refresh %s: %s\n" % (args.spec, exc))
            return 1
        sys.stderr.write("refreshed %s -- %d operations\n"
                         % (args.spec, count))
        if not args.request:
            return 0

    path = resolve(args.spec)
    try:
        spec = load(path)
    except (OSError, ValueError) as exc:
        sys.stderr.write("cannot read %s: %s\n" % (path, exc))
        if not os.path.isfile(path) and args.spec not in registry():
            sys.stderr.write("  no registered spec called %r either. "
                             "./openapi.py --list\n" % args.spec)
        return 1

    cfg = swarm.Config.load()
    try:
        import attach
        allowed = attach.budget(cfg)
    except Exception:
        allowed = 16384

    if args.stats:
        whole = swarm.est_tokens(json.dumps(spec))
        idx = swarm.est_tokens(index_block(spec))
        print("operations:        %d" % len(operations(spec)))
        print("whole spec:        %d tokens" % whole)
        print("index only:        %d tokens" % idx)
        print("attach budget:     %d tokens" % allowed)
        print("whole spec fits:   %s" % ("yes" if whole <= allowed else "NO"))
        print("index fits:        %s" % ("yes" if idx <= allowed else "NO"))
        return 0

    hints: List[Tuple[str, str]] = []
    avoid: List[Tuple[str, str]] = []
    if args.request and not args.no_assist:
        hints, avoid = propose_multi(spec, args.request, cfg, swarm.State())
    text, chosen = slice_for(spec, args.request, limit=args.limit,
                             paths=args.path, tags=args.tag,
                             with_index=not args.no_index, hints=hints,
                             avoid=avoid,
                             scope_index=not args.whole_index,
                             exclude_tags=excluded_tags(args.spec))
    size = swarm.est_tokens(text)

    # Say what was selected, on stderr so `--out -` style piping still works.
    # A slice that silently matched nothing is the failure worth shouting
    # about: it looks identical to a slice that matched everything needed.
    if not chosen:
        sys.stderr.write(
            "WARNING: no endpoint matched \"%s\".\n"
            "  The index is still attached, so the pool knows what exists, but "
            "no endpoint\n  was detailed. Try naming a path with --path, a tag "
            "with --tag, or using\n  words from the endpoint summaries.\n"
            % args.request)
    else:
        sys.stderr.write("selected %d of %d operations:\n"
                         % (len(chosen), len(operations(spec))))
        for entry in chosen:
            sys.stderr.write("  %-6s %s\n" % (entry["method"], entry["path"]))
    sys.stderr.write("slice is %d tokens against a %d budget%s\n"
                     % (size, allowed, "" if size <= allowed else " -- TOO BIG, "
                        "lower --limit or use --no-index"))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("wrote %s\n" % args.out)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
