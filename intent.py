#!/usr/bin/env python3
"""Work out what a request needs, so nobody has to say it.

Naming the mode -- audit this, fan that out, generate here -- is the part that
should not exist. Deciding what a request needs is a language problem, and
there are four language models on the LAN sitting idle. So a small fast one
reads the request and classifies it, and a capable one breaks the work into
pieces that can run at once.

Decomposition matters most for code, which is the bulk of the work here and has
been treated as a single indivisible lump. It is not. A change is usually one
genuinely hard piece plus a pile of boilerplate, and the boilerplate does not
need the 26b -- it needs to be finished before the 26b is, so the two can be
put together. Pieces are therefore tagged with the weakest model that can be
trusted to write them, and anything depending on another piece is handed that
piece's real generated source rather than a description of it, which is what
makes the parts fit when they meet.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

TRIAGE_PROMPT = """Classify this request. Answer in exactly the format below,
with no other text.

KIND must be one of:
  code    - write, change, implement, refactor, or fix code
  audit   - review existing code for bugs or correctness
  ask     - a question to answer, or analysis of something that is not code

PATHS lists any file or directory paths the request names, comma separated, or
NONE. Copy them exactly as written; do not invent paths.

SCOPE is wide if the request applies the same operation to many separate items,
otherwise narrow.

KIND: <code|audit|ask>
PATHS: <paths or NONE>
SCOPE: <wide|narrow>

REQUEST:
{request}"""


SHAPE_PROMPT = """Reply with exactly two lines, SHAPE and FILE. Both lines are required.

Choose stored when one function is called on many inputs and the hard part is the collection: their order, their variety, or each member being valid. A request that says "write a function" is still stored when that is true.
Choose script when there is one artifact, or each input is solved on its own with no property of the collection.

FILE must be one of: {files}. The file that holds the collection. NONE only if script.

REQUEST:
{request}

Both lines, now:
SHAPE: stored or script
FILE: filename or NONE
"""


ITEMS_PROMPT = """Reply with exactly ITEMS and nothing else.
ITEMS is how many times the one collection loop runs.
Do not add loops together. A loop inside a helper is not the collection.
Count runs, not arrays, not files, and not a width or a height.
A loop `i < N` that starts at 0 runs N times.
A loop `i <= N` that starts at 0 runs N+1 times.
range(N) runs N times.

CHECK:
{check}

ITEMS:"""


LOOKUP_PROMPT = """Reply with exactly INDEX and MISSING, and no other text.
INDEX is which argument, counting from 0, selects one stored element.
Use 0 when the first argument is the position, the name, or the path.
MISSING is one literal in {language} returned when that element is absent.
It must make the check fail and not throw. Use the empty value of the type the check reads.
Do not write a valid output.

CHECK:
{check}

INDEX:
MISSING:"""


BRIEFS_PROMPT = """The check below accepts many different values for one input.
Write {count} one-line briefs, one for each input, numbered from 0. Each brief
says what that input's value looks like, concretely enough that someone could
draw it from the brief alone: name a count, a placement, or a repeating
pattern ("three steps, each two cells higher than the last"), never an
adjective by itself ("a complex structure", "a challenge"). A brief describes
the arrangement of the whole value, how it is laid out and what repeats, never
one part of it, and the value it describes must still meet every minimum and
maximum the text below states. No two briefs may
name the same thing. Do not restate a rule the check already states,
including any order between inputs, because the check enforces that on its
own. Every brief must be something the check allows. Write no value.

CHECK:
{check}

Reply with exactly {count} lines in the form `0: the brief`. Nothing else."""


AXES_PROMPT = """The rules below accept many different values. Before any brief is written, list
the ways values can differ, so that no two briefs end up alike.

Reply with a theme line and 5 to 7 axes, one per line. An example for an
unrelated project (a set of soup recipes), to show the form only:
THEME: hearty weeknight soups a beginner can make, each clearly different
RAMP number of steps: 3 | 4 | 5 | 6 | 8 | 10
RAMP cooking time: 15 min | 25 min | 40 min | 1 hour | 90 min | 2 hours
FREE base: tomato | chicken stock | coconut milk | white beans | miso | lentils
FREE main vegetable: carrot | spinach | pumpkin | corn | leek | mushroom
Each axis name must describe the quality in words that belong to THIS
project's rules, never the word "name".

A RAMP axis is a quality whose values run from the simplest to the most
demanding or elaborate, in that order, so a set of values can climb along it.
A FREE axis is a quality whose values are different kinds with no order (a
motif, a shape, a material). Use 3 or 4 RAMP axes and 2 or 3 FREE axes. If the
rules say HOW DIFFICULTY IS MEASURED, build every RAMP axis from a quality that
raises that measure, in the order that raises it, and make every other quality
FREE; a quality that does not change the measure is not a ramp. Each
axis has 5 to 8 values. A value is a few concrete words someone could draw or
write from (a count, a size, a placement, a named pattern), never an adjective
by itself. Every value must be allowed by the rules, and the rules' own
minimums and maximums must hold for any combination.

RULES:
{check}

Nothing else."""


BRIEF_FROM_QUALITIES_PROMPT = """Each line below gives one input's qualities. Write one line per input, a brief
that says what that input's whole value looks like, using every quality on its
line. The briefs together should read as one project: {theme}
A brief names the arrangement of the whole value, not one part of it, and the
value it describes must still meet every minimum and maximum in the rules. Do
not add qualities that are not on the line. Write no value.

RULES:
{check}

QUALITIES:
{lines}

Reply with exactly {count} lines in the form `0: the brief`. Nothing else."""


VARIED_BRIEF_PROMPT = """One function was run on two inputs and returned the same value for both. The second input's brief did not change what was written, so it has to name something the first one does not.

Brief for input {first}: {first_brief}
Brief for input {second}: {second_brief}

Every other brief:
{others}

CHECK:
{check}

Write a replacement brief for input {second} only. One line. Name a particular feature, shape, quantity, or theme that none of the briefs above name. Be more concrete than the brief it replaces. Do not restate a rule of the check. Write no value. Reply with the brief and nothing else."""


ELEMENT_PROMPT = """Reply with these four lines. A line may be as long as the check.
CONTAINER: what one call returns. Not the variable that collects every call.
COUNT: the fewest parts that one return value may hold.
PART: what one of those parts is, and the smallest that one part may be.
RULES: the constraints the check states for one part, copied from the check. Write none when the check states none.
A part is one element of the container. Do not multiply separate sizes into one.
A number the check states is a minimum. Do not add a maximum the check does not state.
Do not write the value. Do not write a function.

CHECK:
{check}

FAILURES:
{failures}

CONTAINER:"""


DECOMPOSE_PROMPT = """Break this programming task into files that can be written
independently and in parallel.

There are {workers} machines free to write files at the same time, but they are
not interchangeable: {strong} of them can take FLOOR 4 work and {light} can only
take FLOOR 2.

Splitting is a cost, not a goal. Every boundary between two files is a place
where two models can disagree about a name, a signature or a data shape, and
those disagreements are the only defects this system actually produces. An idle
machine costs nothing. A bad seam costs the whole run.

So split only when the parts are genuinely separate work. If the task is one
small coherent program -- a game loop, an animation, a script of roughly 200
lines or less where everything touches everything else -- emit ONE component at
FLOOR 4 and let a single capable model write the whole thing. That is the right
answer surprisingly often, and it is always better than four files that do not
fit together. Measured: a dancing-shapes animation split into four components
produced one correct file and three that could not be joined, while the same
task as a single component was complete and correct on the first attempt.

Split when there is real breadth: several independent parsers, a set of
unrelated utilities, many files that share nothing but a data type.

FLOOR 4 is the scarce one, and there {is_are} only {strong}. Marking four
components FLOOR 4 does not make them careful, it makes them queue single file
on {strong} machine while the others sit empty. Treat FLOOR 4 as one queue:
ideally a single component carries the genuinely hard part, and everything
else is built around it in parallel. Spend it only where being wrong is likely: real algorithms, tricky
state, subtle correctness. Tokenizers, data holders, error classes, argument
parsing, formatting and assembling a public API from parts are FLOOR 2, and
FLOOR 2 work is checked by the same gates.

A chain wastes them. If tokenizer depends on errors, parser depends on
tokenizer, and evaluator depends on parser, then only one machine can ever be
working and the other {idle} sit idle -- measured on exactly that plan.

So DEPS is a last resort, not documentation. Two files are NOT dependent just
because one imports the other. They are dependent only when one genuinely
cannot be written without reading the other's finished code.

Instead of chaining, pin the contract. If several files must agree on a data
shape, an exception type, or a function signature, write that exact signature
into every SPEC that touches it -- the same names, arguments and types in each.
Files written against a stated contract fit together without waiting, and they
can all be written at once.

{context}

TASK:
{request}

Think about which parts are genuinely difficult and which are routine. Routine
parts -- data holders, simple helpers, string formatting, straightforward
wrappers, argument parsing -- should be marked FLOOR 2 so a small fast model
writes them. Reserve FLOOR 4 for the parts where being wrong is likely or
expensive: real algorithms, concurrency, tricky state, subtle correctness.

For every component, write the SPEC so precisely that someone who cannot see
the other components could still write it and have it fit: name the exact
functions, classes, arguments, return types, and exceptions.

Before writing DEPS for a component, ask one question: could someone write this
file from the CONTRACT alone, without seeing the other file's finished code? If
yes -- and with a contract stating the signature, the answer is almost always
yes -- then leave DEPS empty and the two get written at the same time on
different machines. Importing a name is not a reason. Calling a function is not
a reason. Needing to read a real implementation to know what to write is the
only reason.

The exception is the entry point, which wires finished parts together and
should list them all.

Measured on a four-file plan that chained constants -> shapes -> engine ->
main: exactly one file was eligible at any moment, so all four went to one
machine and the other two never woke up. Every one of those arrows was a file
importing a name the contract had already stated.

If you emit more than one component, first state the CONTRACT: the exact shared
names every file must agree on. Give the real signature of each shared class
and function, once, and every component will be handed it verbatim. This is the
single thing that decides whether the parts fit, because a file that invents
its own version of a shared type is the defect that survives every check.

Say which file owns each shared name, like this:

CONTRACT: shapes.py owns Shape(x: float, y: float, size: float, color: tuple)
with update(dt: float) and draw(surface); constants.py owns COLORS, a list of
RGB tuples

Naming only a method -- `Shape.draw(self, surface)` -- is not enough. It does
not say who defines Shape, so two files define it and the package breaks.
Each shared name is defined in exactly one file and imported everywhere else.

The CONTRACT must list every method any other component's own job requires
calling on a shared object, not only the ones already decided on. Leaving
one out does not remove the need for it -- it removes the only place its
name and signature were ever going to be agreed on, so the file that needs
it guesses, and the file that owns the class has no reason to define a
method nobody told it about. Measured live: a client class's CONTRACT
listed applet, project, task and unit methods but never a version-creating
one, even though the plan's own components required creating an applet
version before a project could be linked to it. The entry point guessed a
name and signature for it that the client was never told to implement, and
burned its entire repair budget calling a method that existed nowhere in
the package -- a defect the CONTRACT was the one place positioned to
prevent.

If the task asks for something runnable -- a script, a program, a game, an
animation, a tool -- exactly one component must be the entry point,
containing the loop or the command line and a `main()` called from
`if __name__ == "__main__":`. A plan of library modules with nothing to
run does not satisfy a request for a script.

Name it main.py, UNLESS the existing-files listing above already shows a
main.py (or another entry point) whose recorded purpose is a different
task from this one. Reusing that path is a full-file rewrite, not an
edit, and it deletes a script that already works. In that case give the
new entry point a name that describes what THIS request does --
`upload_unit.py` for a request to upload a unit, not `main.py` -- so it
sits beside the existing one instead of replacing it. This is the normal
case for a second, unrelated capability in a project that already has a
script; it is not a fallback or an error condition.

Exactly one means one. If the contract gives `main()` to some file, that file
is the entry point and there is no second one -- a plan with both `client.py
owns main()` and a separate main.py depending on it has two, and the extra
component does nothing but add a stage.

Every component that depends on another has to wait for it, and while it
waits the other machines have nothing to do. Components with no dependencies
all start at once, so a plan of three independent files finishes in the time
of the slowest, while a chain of three takes all three in turn. Only make one
component depend on another when it genuinely needs that file's contents.

If the context above includes API endpoints, treat that list as closed: a
CONTRACT method that makes a network call must correspond to a real listed
operation, and there is no other one to invent. Measured live: a contract
asked for `get_ui_url(base_url: str, project_id: str) -> str` when nothing
in the API creates or returns such a link, the writer had every real
endpoint in its own prompt and called a nonexistent one anyway, and the
result parsed, imported and smoke-ran before failing the moment it
actually executed. If the task describes something the API does not
expose, spec it as local computation -- format a URL from the pieces
already at hand, for instance -- not as a call to guess at.

The endpoint list is grouped under headings like "## Applets", "##
Projects", "## Tasks" so a writer can find a neighbour it needs -- it is
not a menu of areas to build a client for. Measured live: a request that
only ever said "create an applet; save its id" -- one action, no project,
no task -- produced FOUR components: `api_client.py`, `applet_creator.py`,
`project_api_client.py`, and `task_api_client.py`, the last two calling
endpoints the request never mentioned and the applet-creation script
never needed. Nothing in the task asked for project or task capability;
the tag headings in the endpoint listing did not mean "build one of
these per group," and the resulting package took three extra machines
and several extra minutes to build capability nobody requested. Build
exactly the client surface the TASK's own actions require -- usually one
client file, or none at all if it is one call folded straight into
main.py -- never one file per tag heading you see in the list above. An
endpoint under a tag the request never touches exists in that list so a
closure (an id one of your real calls needs) can be found, not so a
matching component gets planned for it.

A local-computation helper like that still has to say whether it is a
method or a plain function, and folding it into a class's `with` clause
answers that question wrong by default. Measured live, on the same
`get_ui_url`, after the invented-endpoint defect above was already fixed:
the CONTRACT read `client.py owns Client(...) with create_applet(...);
...; get_ui_url(api_base_url: str, project_id: str) -> str` -- grouped
under Client's own methods, which have `self` and therefore never take
`api_base_url` as an argument, since a method already has it on
`self.api_base_url`. A signature listing every input explicitly, with no
`self`, is a plain function, and a plain function does not belong inside
a class's `with` clause -- one writer read the grouping and implemented
it as a bound method, the other read the signature and called it as
`client.get_ui_url(...)` expecting a module-level name, and the file that
depended on it failed every attempt because the fix it needed was never
in its own code. If a shared name takes no `self` and needs no instance
state, give it its own line -- `client.py owns get_ui_url(api_base_url:
str, project_id: str) -> str` on its own, not nested under `Client(...)
with` -- so both sides agree it is called as `client_module.get_ui_url(...)`,
never `client_instance.get_ui_url(...)`.

Python is not the only thing you may plan. A component may be .py, .html,
.js, .css, .json, .md, .txt, .csv or .sql, and the file type is part of the
answer: a request for a page, a form, a survey, a template, a document or a
config wants that file, not a Python string containing it. If the operator
says they want to edit or reuse the artifact afterwards, it must be its own
component -- markup built by concatenation inside a .py cannot be opened in
an editor, and asking for "a standalone HTML page" and receiving a Python
script that prints one does not answer the request. A Python component may
then read that file and send it.

A clause about saving a result locally -- "save its id as applet_id in a
new output.json in the same directory as the script", "save the response
under the same key it uses for it" -- describes what the entry point does
with a value it already holds, the instant after it gets that value. It
is boilerplate, not a capability, and must never become its own component.
Do not plan a dedicated file (`output_utils.py`, `save_output.py`, a
generic "output helper") whose job is only writing output.json. Fold the
write directly into whichever component actually produces the value --
the same function that creates the applet also calls `json.dump` on its
own result, in its own file, with the literal filename right there in its
own source.

This is not a cosmetic preference. A shared output-writing helper is
naturally written as a small reusable function taking a path or a dict as
a parameter, with no literal "output.json" anywhere in its own source --
the filename lives only at the call site, in whichever file imports and
calls it. Measured live: exactly this shape was planned once, and the
mechanical check that exists to catch a step silently never writing its
promised output (`gate_missing_own_output_write`, which looks for a
literal write to "output.json" in the file whose SPEC asked for one) has
no way to see through that indirection -- it fired on the helper file on
every rung of the repair ladder, restart, and escalation, because the
literal string genuinely never appears there. The fix is not a smarter
check; it is not creating the file that check can never pass. One
component doing the whole job -- call the API, then write its own
output.json right there -- is simpler, cheaper, and the only shape this
project's own gates were built to verify.

Use this format exactly, and nothing else:

CONTRACT: <the exact shared signatures, semicolon separated, on one line; or
NONE if there is only one component>

COMPONENT: <short name>
FILE: <relative path; .py .html .js .css .json .md .txt .csv or .sql>
FLOOR: <2 or 4>
DEPS: <comma separated component names, or NONE>
SPEC: <a precise description of the entire file, on one line>

One component is a valid and often correct plan. Never emit more than five."""

REPLAN_PROMPT = """A generated file has failed twice against the same CONTRACT
and SPEC: its own repair attempts, then a full restart from a clean slate with
no memory of the first attempt. Two independently-written attempts failing the
same way is evidence about what they were given, not about how carelessly
either one was written -- decide whether the CONTRACT or the SPEC is the
actual problem, rather than the code.

REQUEST: {request}

CONTRACT: {contract}

FAILING FILE: {path}
SPEC: {spec}
LAST FAILURE: {history}

OTHER FILES IN THIS PACKAGE, AS THEY ACTUALLY STAND:
{sources}

If the CONTRACT is missing a shared name this file genuinely needs -- a
method it must call that no file defines, a field a dependency's class does
not have, a wrong signature -- reply with a corrected CONTRACT (same format
as above) and a corrected SPEC for the failing file only. If satisfying the
correction means an already-written file above must also change (for
example: it must gain a method the corrected CONTRACT now gives it), name
that one file and describe the single addition it needs in one sentence.

If neither the CONTRACT nor the SPEC is at fault -- the code was simply
written wrong, twice, against instructions that were already correct --
reply with exactly the word NONE and nothing else.

Reply in exactly this form:

CONTRACT: <the corrected contract, same format as above, or the word NONE if unchanged>
SPEC: <the corrected spec for {path} only, on one line>
SIBLING: <path> ADD: <the one addition that file needs> (omit this line entirely if no other file needs to change)

or, if replanning would not help:

NONE"""

_REPLAN_FIELD = re.compile(r"^\s*(CONTRACT|SPEC|SIBLING)\s*:\s*(.*)$", re.I)


def parse_replan(text: str, path: str) -> Optional[Dict[str, str]]:
    """The planner's verdict on a twice-failed component: a real fix, or NONE.

    Mirrors `parse_components`'s field-accumulation shape rather than
    inventing a new one: a value continues on following lines until the next
    recognised field starts, so a multi-sentence SPEC is not truncated at its
    first line break.

    Returns None both when the model says NONE and when the reply cannot be
    read at all -- an unparseable reply is not evidence the contract was
    wrong, and treating it as a fix would apply garbage to a component that
    is otherwise a known, named failure.
    """
    stripped = (text or "").strip()
    if not stripped or stripped.upper() == "NONE":
        return None
    fields: Dict[str, List[str]] = {"contract": [], "spec": [], "sibling": []}
    current: Optional[str] = None
    for line in stripped.splitlines():
        m = _REPLAN_FIELD.match(line)
        if m:
            current = m.group(1).lower()
            fields[current].append(m.group(2).strip())
        elif current is not None and line.strip():
            fields[current].append(line.strip())
    contract = " ".join(fields["contract"]).strip()
    spec = " ".join(fields["spec"]).strip()
    sibling = " ".join(fields["sibling"]).strip()
    if not spec:
        # A SPEC is the one field this exists to produce -- without it there
        # is nothing to hand the failing component that its own history
        # didn't already contain.
        return None
    out = {"contract": "" if contract.upper() == "NONE" else contract,
          "spec": spec}
    sib_match = re.match(r"^(\S+)\s+ADD\s*:\s*(.+)$", sibling, re.I)
    if sib_match:
        out["sibling_path"] = sib_match.group(1)
        out["sibling_add"] = sib_match.group(2).strip()
    return out


REQUEST_REPLAN_PROMPT = """A whole plan has now failed at two levels: the
per-component repair ladder (retry, restart, escalate to a stronger model,
and a CONTRACT/SPEC correction for the specific file that kept failing),
and then one full restart of decomposition from the very same request text.
Both independent decompositions of that text still produced components that
cannot be written correctly. Two independently-planned attempts converging
on the same failure is evidence about the REQUEST itself -- something it
never said, something it said inconsistently, something it implies exists
(an id, a file, an endpoint) that nothing anywhere actually produces -- not
about either roll of the dice landing badly.

ORIGINAL REQUEST: {request}

CONTRACT FROM THE MOST RECENT ATTEMPT: {contract}

COMPONENTS THAT STILL FAIL, WITH THE FULL TRAIL OF WHAT WAS TRIED AND HOW
EACH ATTEMPT SPECIFICALLY FAILED:
{failures}

COMPONENTS THAT SUCCEEDED, AS THEY ACTUALLY STAND -- read these before
proposing a fix, since a rewritten request must not contradict what
already works and already has real code depending on it:
{sources}

If the request text is the actual defect -- it never named a value a
failing file needed, it used two different names for the same thing, it
asked for behaviour that contradicts what the successful files above
already do, or its own ambiguity is what led two separate decompositions to
the same wrong guess -- reply with a corrected version of the REQUEST,
IN FULL, changing only what the failures above implicate and leaving
everything else exactly as the operator wrote it. This corrected text
replaces the original and gets decomposed from scratch, once.

If the request was clear and correct, and the failures are ordinary bugs
that a fresh, fair decomposition already had a real chance to avoid, reply
with exactly the word NONE and nothing else. Do not propose a rewrite just
to have something to say.

Reply in exactly this form:

REQUEST: <the corrected request, in full, on one line>

or, if rewriting the request would not help:

NONE"""

_REQUEST_REPLAN_FIELD = re.compile(r"^\s*REQUEST\s*:\s*(.*)$", re.I)


def parse_request_replan(text: str) -> Optional[str]:
    """The planner's verdict on a request that has already survived one
    whole-plan restart and still failed: a corrected request, or NONE.

    Mirrors `parse_replan`'s continuation-line handling for the same
    reason -- a corrected request is prose, not a single short token, and
    truncating it at the first line break would hand back a fragment.

    Returns None both when the model says NONE and when the reply cannot
    be read at all, on the same reasoning `parse_replan` already states:
    an unparseable reply is not evidence the request was wrong, and
    applying garbage in its place would replace a known, named failure
    with an unrecoverable one -- the run would decompose against text
    nobody wrote and nobody can trace back to what was actually asked.
    """
    stripped = (text or "").strip()
    if not stripped or stripped.upper() == "NONE":
        return None
    lines: List[str] = []
    current = False
    for line in stripped.splitlines():
        m = _REQUEST_REPLAN_FIELD.match(line)
        if m:
            current = True
            if m.group(1).strip():
                lines.append(m.group(1).strip())
        elif current and line.strip():
            lines.append(line.strip())
    request = " ".join(lines).strip()
    if not request or request.upper() == "NONE":
        return None
    return request


CHAIN_VERSION_REPAIR_PROMPT = """A chain of steps is about to run for real
against a live API. A mechanical check has found a step that consumes
"the new version" of something without ever saying which earlier step
made it or what key its id was saved under. Nothing downstream can prove
this from the text alone -- that is exactly why it is being asked here,
of a reader rather than a regex: whichever earlier step actually creates
that version, if any, may never say the word "version" at all (a step
that says "push this html to the applet" IS the one creating a version,
even though it never spells that out).

THE FULL CHAIN, IN ORDER, EACH STEP'S OWN DIRECTORY AND REQUEST TEXT
EXACTLY AS WRITTEN:
{chain_block}

THE PROBLEM: step "{consumer_dir}" reads as depending on a version it
never explicitly names the source of: {problem_text}

Decide what actually creates the version this step needs, if anything in
this chain does, and fix ONLY that: either
  (a) merge "{consumer_dir}" with whichever step actually creates the
      version into ONE step (the preferred fix when they are adjacent and
      both about the same applet/version), or
  (b) if they must stay separate, rewrite each of the (at most two) steps
      you are touching so the producer explicitly promises to save the
      id under a real key name, and the consumer explicitly reads that
      same key from the producer's own output.json.

Every step you do not need to touch must be reproduced exactly as given,
unchanged, in the same JSON shape -- do not rename, reword, or drop any
step this problem does not concern, and do not touch more than two
steps total no matter which route you take.

Reply with the ENTIRE corrected chain as one fenced json code block, an
array of objects each with "dir", "request", and "api" (copy "api"
unchanged from the original step it corresponds to; empty string "" for
a step with no api). Nothing else outside the fence.

```json
[{{"dir": "...", "request": "...", "api": "..."}}, ...]
```"""


def parse_chain_version_repair(text: str) -> Optional[List[Dict[str, str]]]:
    """The pool's proposed fix for a chain step that dangling-references
    "the new version" with no named source, as a full corrected chain.

    Deliberately permissive about *shape* here and strict about
    *content* at the call site: this only checks that the reply parses
    as a JSON array of `{dir, request, api}` objects, because the real
    guardrail -- did the fix actually resolve the problem, and did it
    leave every step it was not asked to touch alone -- needs the
    original chain to check against and belongs in the caller
    (`team.repair_dangling_version_chain`), the same division already
    used between `parse_replan` (shape) and `replan_component` (content,
    grounded in real sibling source).

    An unparseable reply, one that is not a list, or one whose items are
    missing "dir"/"request" all return None -- the same "no evidence, no
    change" rule every other best-effort parser in this file follows,
    because applying a malformed fix to a chain that already has a
    named, understood problem would trade a known failure for an
    untraceable one.
    """
    # A local fence-extractor rather than `codegen.extract_code`: that
    # module imports this one, so importing it back here would be a
    # circular import for the sake of one regex `codegen` already has a
    # more general version of.
    #
    # `think=False` does not stop this model from reasoning in visible
    # prose, and this prompt asks it to weigh two named alternatives
    # ("merge into one step" vs. "rewrite both explicitly") -- which
    # means it routinely talks through a first, abandoned attempt,
    # catches its own mistake in prose ("Wait, if I merge 07 into 06, 07
    # no longer exists..."), and only then writes the answer it actually
    # means. Measured live: a real reply carried two complete fenced JSON
    # chains, the first internally self-contradictory (07 both merged
    # away and still present as its own untouched step) and explicitly
    # disowned by the very next sentence, with the correct, validated
    # fix in the second fence. `re.search` finds the first match, so
    # every one of these replies was read as the model's rejected draft,
    # not its answer -- `repair_dangling_version_chain` reported "could
    # not repair ... over the network" on a fix that was sitting one
    # fence later in the same reply.
    #
    # So every fenced block is parsed, in order, and the last one that
    # parses as a well-formed chain wins -- not merely the last fence
    # found, since a stray, malformed trailing fence (a fragment, a
    # single object, ordinary prose someone wrapped in backticks) should
    # not shadow a good answer that came before it. The per-fence
    # well-formedness check below (list of dicts, non-empty dir/request)
    # is the same one the loop already needs to apply to whichever
    # candidate wins, just run once per candidate instead of once at the
    # end.
    fences = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.DOTALL)
    candidates = fences if fences else [text or ""]

    def _parse_one(body: str) -> Optional[List[Dict[str, str]]]:
        body = body.strip()
        if not body:
            return None
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, list) or not data:
            return None
        out: List[Dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                return None
            d = item.get("dir")
            r = item.get("request")
            if not isinstance(d, str) or not d.strip():
                return None
            if not isinstance(r, str) or not r.strip():
                return None
            a = item.get("api")
            out.append({"dir": d.strip(), "request": r.strip(),
                       "api": a.strip() if isinstance(a, str) else ""})
        return out

    result = None
    for candidate in candidates:
        parsed = _parse_one(candidate)
        if parsed is not None:
            result = parsed
    return result


_FIELD = re.compile(r"^\s*(KIND|PATHS|SCOPE|CONTRACT|COMPONENT|FILE|FLOOR|DEPS|SPEC)\s*:\s*(.*)$",
                    re.IGNORECASE)


def _fields(text: str) -> List[Tuple[str, str]]:
    out = []
    for line in (text or "").splitlines():
        m = _FIELD.match(line.lstrip("*-# "))
        if m:
            # `line.lstrip("*-# ")` above only ever strips markdown noise
            # off the front of the whole line, which is what lets a key
            # written as "**FILE:** unit_uploader.py" or "**FILE: x**"
            # match at all -- but the regex still runs against whatever is
            # left, and a bold marker landing *inside* the matched span
            # (right after the colon, or at the very end of the line)
            # survives straight into the captured value. Measured live: a
            # planner reply bolding one of its two FILE lines produced a
            # component whose path was the literal string
            # "** unit_uploader.py" -- asterisks and a stray space
            # included, since `\s*:\s*` in _FIELD matches zero characters
            # of actual whitespace between the colon and the leftover
            # "**". That path can never be `main.py`, never collides with
            # the correctly-parsed sibling naming the same real file (the
            # rename-on-duplicate logic below only fires on an exact
            # string match), and gets written, gated and repaired as its
            # own separate component for no reason. Stripped the same
            # conservative way as the key -- only a leading/trailing run
            # of `*`, never asterisks in the middle of real content like a
            # SPEC's own prose -- so a genuine value is untouched either
            # way.
            value = m.group(2).strip().strip("*").strip()
            out.append((m.group(1).lower(), value))
    return out


class Triage:
    def __init__(self, kind: str = "ask", paths: Optional[List[str]] = None,
                 scope: str = "narrow"):
        self.kind = kind
        self.paths = paths or []
        self.scope = scope

    def __repr__(self) -> str:
        return "Triage(kind=%r, paths=%r, scope=%r)" % (
            self.kind, self.paths, self.scope)


_BUILD_VERB = re.compile(
    r"\b(build|write|create|implement|generate|add|refactor|port)\b",
    re.IGNORECASE)
_ARTIFACT = re.compile(
    r"\b(package|module|script|library|program|class|function|cli|api|"
    r"endpoint|parser|test suite)\b", re.IGNORECASE)


def obvious_code(request: str, has_check: bool = False) -> bool:
    """True when the request is unmistakably 'write me code'.

    Triage runs on the smallest model on the LAN, which is the right trade for
    a one-line classification and the wrong one for a long request that spends
    most of its words specifying an API. Those read as prose and get called a
    question. A supplied check command settles it outright -- nothing but
    generated code is going to be run against a test -- and a build verb aimed
    at an artifact is close enough to certain to be worth not asking.
    """
    if has_check:
        return True
    head = request[:400]
    return bool(_BUILD_VERB.search(head) and _ARTIFACT.search(head))


def parse_triage(text: str, root: str) -> Triage:
    kind, scope = "ask", "narrow"
    paths: List[str] = []
    for key, value in _fields(text):
        if key == "kind":
            low = value.lower()
            for candidate in ("code", "audit", "ask"):
                if candidate in low:
                    kind = candidate
                    break
        elif key == "scope":
            scope = "wide" if "wide" in value.lower() else "narrow"
        elif key == "paths" and value.upper() != "NONE":
            for p in re.split(r"[,\s]+", value):
                p = p.strip().strip("`'\"")
                if p and os.path.exists(os.path.join(root, p)):
                    paths.append(p)
    return Triage(kind, paths, scope)


_SHAPE_FIELD = re.compile(r"^\s*(SHAPE|FILE|COUNT)\s*:\s*(.*)$", re.I)


def parse_shape(text: str) -> Tuple[str, str, int]:
    """The reply to `SHAPE_PROMPT`. Anything unusable is script.

    The size of the collection is a separate reply, `parse_items`.
    """
    shape, filename = "script", ""
    for line in (text or "").splitlines():
        match = _SHAPE_FIELD.match(line.strip().strip("*").strip())
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip().strip("*").strip()
        if key == "shape":
            shape = "stored" if "stored" in value.lower() else "script"
        elif key == "file" and value.upper() != "NONE":
            filename = os.path.basename(value.strip().strip("`'\" "))
    if shape != "stored" or not filename:
        return "script", "", 0
    return "stored", filename, 0


def parse_items(text: str) -> int:
    """The reply to `ITEMS_PROMPT`. One number, or 0 when the reply is mixed."""
    nums = {int(n) for n in re.findall(r"\d+", text or "")}
    if len(nums) != 1:
        return 0
    return nums.pop()


def parse_lookup(text: str) -> Tuple[Optional[int], str]:
    """The reply to `LOOKUP_PROMPT`. Index may be missing. Missing may be empty."""
    index: Optional[int] = None
    missing = ""
    for line in (text or "").splitlines():
        stripped = line.strip().strip("*").strip()
        if stripped.lower().startswith("index:"):
            nums = re.findall(r"\d+", stripped)
            if len(nums) == 1:
                index = int(nums[0])
        elif stripped.lower().startswith("missing:"):
            missing = stripped.split(":", 1)[1].strip()
    return index, missing


def parse_axes(text: str) -> Tuple[str, List[Tuple[str, bool, List[str]]]]:
    """The reply to `AXES_PROMPT`: (theme, [(name, ramps, values)]).

    A line that does not read `RAMP name: a | b | c` or `FREE name: ...` with
    at least 3 values is ignored. Nothing here invents an axis.
    """
    theme = ""
    axes: List[Tuple[str, bool, List[str]]] = []
    for raw in (text or "").splitlines():
        line = raw.strip().strip("*").strip()
        m = re.match(r"^THEME\s*:\s*(.+)$", line, re.I)
        if m:
            theme = m.group(1).strip()
            continue
        m = re.match(r"^(RAMP|FREE)\s+([^:]{1,40}):\s*(.+)$", line, re.I)
        if not m:
            continue
        values = [v.strip().strip("*").strip() for v in m.group(3).split("|")]
        values = [v for v in values if v]
        if len(values) >= 3 and m.group(2).strip().lower() not in ("name", "axis"):
            axes.append((m.group(2).strip(), m.group(1).upper() == "RAMP", values))
    return theme, axes


def compile_qualities(axes: Sequence[Tuple[str, bool, Sequence[str]]],
                      count: int) -> List[List[Tuple[str, str]]]:
    """One combination of qualities per input, no two alike.

    A RAMP axis climbs with the input's place in the set, with a small
    deterministic wobble so axes do not all step together. A FREE axis
    rotates by a stride that shares no factor with its length, a different
    stride per axis, so neighbours differ on every free axis. A combination
    that repeats an earlier one moves one axis by one step. Nothing here is
    random: the same axes and count give the same set.
    """
    from math import gcd
    seen: Set[Tuple[int, ...]] = set()
    out: List[List[Tuple[str, str]]] = []
    for i in range(count):
        picks: List[int] = []
        for k, (name, ramps, values) in enumerate(axes):
            m = len(values)
            if ramps:
                pos = i / max(1, count - 1) * (m - 1)
                wobble = ((i * (k + 3) + k * 5) % 3) - 1
                picks.append(max(0, min(m - 1, int(round(pos)) + (wobble if 0 < i < count - 1 else 0))))
            else:
                stride = next(s for s in range(k + 2, k + 2 + m + 1) if gcd(s, m) == 1)
                picks.append((i * stride + k) % m)
        tries = 0
        while tuple(picks) in seen and tries < 4 * len(axes) * 8:
            k = tries % len(axes)
            m = len(axes[k][2])
            picks[k] = (picks[k] + 1) % m
            tries += 1
        seen.add(tuple(picks))
        out.append([(axes[k][0], axes[k][2][picks[k]]) for k in range(len(axes))])
    return out


def parse_briefs(text: str, count: int) -> Dict[str, str]:
    """The reply to `BRIEFS_PROMPT`: input number to its one-line brief.

    A line that does not start with a number is ignored. A missing input
    simply has no brief. Nothing here writes one.
    """
    briefs: Dict[str, str] = {}
    for line in (text or "").splitlines():
        match = re.match(r"^\W*(\d+)\s*[:.)\-]\s*(\S.*)$", line.strip())
        if not match:
            continue
        index = int(match.group(1))
        if 0 <= index < count and str(index) not in briefs:
            briefs[str(index)] = match.group(2).replace("**", "").strip()[:400]
    return briefs


def parse_element(text: str) -> str:
    """The reply to `ELEMENT_PROMPT`, or empty when the shape is missing.

    Container, count, and part are required. Rules are optional. A value
    with one size is still a value. Nothing here decides what the parts are.
    """
    container = count = part = rules = ""
    for line in (text or "").splitlines():
        stripped = line.strip().strip("*").strip()
        low = stripped.lower()
        if low.startswith("container:"):
            container = stripped.split(":", 1)[1].strip()
        elif low.startswith("count:"):
            count = stripped.split(":", 1)[1].strip()
        elif low.startswith("part:"):
            part = stripped.split(":", 1)[1].strip()
        elif low.startswith("rules:") or low.startswith("symbols:"):
            rules = stripped.split(":", 1)[1].strip().strip(".").strip()
    if not (container and count and part):
        return ""
    described = "container %s; count %s; part %s" % (container, count, part)
    if rules and rules.lower() != "none":
        described += "; rules %s" % rules
    return described[:1500]


class Component:
    """One file in a decomposed programming task."""

    def __init__(self, name: str, path: str, floor: int, deps: List[str],
                 spec: str):
        self.name = name
        self.path = path
        self.floor = floor
        self.deps = deps
        self.spec = spec
        self.code: Optional[str] = None
        # The shared signatures every component in this plan must agree on.
        # Attached to each component so the writer sees it without needing a
        # dependency edge, which is the point: agreement should not require
        # waiting for someone else's file to be finished.
        self.contract = ""
        self.ok = False
        self.target = ""
        self.attempts = 0
        self.seconds = 0.0
        self.error = ""
        # `error` is only ever the last rung's condensed one-liner, since
        # each rung of `run_components`'s ladder overwrites it. This is
        # the trail across every rung tried -- initial repair, restart,
        # fallback/escalation, the CONTRACT/SPEC replan -- so a request-
        # level diagnosis (`team.replan_request`) has the actual sequence
        # of what was tried and how each attempt specifically failed,
        # rather than only the final word on it.
        self.history_trail: List[str] = []

    def __repr__(self) -> str:
        return "Component(%s -> %s, floor=%d, deps=%s)" % (
            self.name, self.path, self.floor, self.deps)


def _stdlib_names() -> set:
    """Module names a top-level generated file must not take.

    `sys.stdlib_module_names` only exists from 3.10, and this runs on 3.9, so
    fall back to probing the import machinery for a fixed list of the names a
    planner actually reaches for when naming parts of a program.
    """
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    guesses = """types math random string time json os sys re io abc enum
        queue select signal socket struct copy hashlib logging platform
        secrets shelve statistics token typing uuid array audioop base64
        bisect calendar cmd code codecs colorsys config contextlib csv
        datetime decimal difflib email errno filecmp fnmatch fractions ftplib
        gettext glob graphlib grp gzip heapq hmac html http imaplib imp
        inspect ipaddress keyword linecache locale mailbox mimetypes modulefinder
        netrc numbers operator optparse parser pathlib pdb pickle pipes pkgutil
        plistlib poplib pprint profile pty pwd py_compile pyclbr pydoc quopri
        reprlib resource runpy sched shutil site smtplib sndhdr spwd sqlite3
        ssl stat subprocess sunau symtable sysconfig tabnanny tarfile telnetlib
        tempfile termios textwrap threading timeit trace traceback tty turtle
        unicodedata warnings wave weakref webbrowser xdrlib zipfile zlib
        stats parser array test""".split()
    return set(guesses)


_STDLIB = _stdlib_names()


_RUNNABLE = re.compile(
    r"\b(script|program|game|app|application|tool|cli|animation|animate|"
    r"simulation|simulate|visuali[sz]|demo|render|draw|display|show)\w*\b",
    re.I)


# Only the genuinely static, client-side asset extensions -- deliberately
# excludes `.json`/`.csv`/`.sql`, which could plausibly be *produced by* an
# API call rather than be the whole of the request, and excludes `.py`
# outright, since a Python file always has to run something.
_STATIC_ASSET_EXT = r"html?|css|m?js|md|txt"
_PURE_STATIC_FILE_RE = re.compile(
    r"^\s*(?:create|write|make|generate)\s+(?:a|an|the)?\s*"
    r"([A-Za-z0-9_\-]+\.(?:%s))\b" % _STATIC_ASSET_EXT, re.I)

# A second action this request is asking for -- pushing the file
# somewhere, saving an id, reading a sibling's output -- really might
# need an endpoint, so the whole point of checking is to defer to
# `--api` the moment there is a second verb to be wrong about.
#
# This used to be a bare `\bword\b` search across the *entire* free-text
# description that follows the filename, and that is exactly what broke
# it: "create a survey.html that shows 5 point rating axes ..." was
# refused as non-static purely because the word "point" appears inside
# "5 point rating axes" -- a Likert-scale description with nothing to do
# with the verb "point the project at it" this list exists to catch. A
# design description is prose, and prose about ratings, buttons, colors,
# or any other UI content will always share incidental vocabulary with
# some verb on a list this short; scanning the whole clause for any of
# those words punishes the description for containing common words, not
# for actually asking for a second action.
#
# So this only ever looks at the *first word of a new clause* -- text
# introduced by `;`, `,`, "and", or "then" -- never at a bare word
# sitting anywhere inside one continuous description. "and push it to
# the applet" starts a clause with "push": a real second action.
# "5 point rating axes" never starts a clause at all; "point" is buried
# mid-sentence, so it is never even compared against this list.
_CLAUSE_BOUNDARY_RE = re.compile(r";|,|\band\b|\bthen\b", re.I)
_FOLLOWUP_VERB_RE = re.compile(
    r"^\s*(?:it\s+)?(push|upload|save|point|update|assign|create|read|"
    r"call|post|patch|delete|fetch|send|submit)\b", re.I)


def is_pure_static_file_request(request: str) -> Optional[str]:
    """The filename, if this request is nothing but "create/write/make/
    generate a FILENAME.ext" for a static, client-side asset, optionally
    followed by a plain description of its content -- else None.

    Measured live: `--api maven "create a survey.html"` -- the simplest
    possible request, with nothing in it that could plausibly need a
    network call -- still ran the full `--api` pipeline anyway, because
    nothing checks whether the request has any use for it before running
    it. `propose_multi` found "2 endpoints" for a static page (term
    overlap on "create" and "survey" against something unrelated), that
    became a CONTRACT broadcast into the planner's own prompt under "use
    these exact names," and the plan that came back was `main.py` +
    `survey_generator.py` + `data_processor.py` + `survey.html` -- three
    invented Python files building a "pipeline" around a three-question
    form that needed none of them, one of which failed six repair
    attempts on a component that should never have existed. `--api`'s own
    slicing, mock, and CONTRACT machinery exist to help a writer call the
    right endpoint; broadcasting any of it into a request that calls none
    at all can only ever bias the planner toward inventing structure to
    justify the context it was handed, which is the same failure this
    project already found once for a colour-list component handed a full
    `Shape` class signature and asked to build a whole game around it.

    The filename itself has to open the sentence with one of a small set
    of authoring verbs, so "push survey.html as a new version of ..." --
    which very much does need an endpoint -- never reaches the second
    check at all. What follows the filename may only be a plain
    description of its content ("a three-question survey with a
    color-wheel picker"), which this project's own boilerplate commonly
    appends and which names no action of its own; a `../sibling`
    reference, or a real second clause (introduced by `;`, a comma,
    "and", or "then") whose own first word is a verb implying a further
    call (push, upload, save, point, update, ...), defers to `--api`
    running normally, because those requests really might need it and a
    false match here would silently drop a real dependency the operator
    asked for. See `_FOLLOWUP_VERB_RE`'s own comment for why this checks
    each clause's leading word rather than scanning the whole
    description for any of those words appearing anywhere in it.
    """
    if not request:
        return None
    text = request.strip()
    m = _PURE_STATIC_FILE_RE.match(text)
    if not m:
        return None
    rest = text[m.end():]
    if "../" in rest or ";" in rest:
        return None
    clauses = _CLAUSE_BOUNDARY_RE.split(rest)
    if any(_FOLLOWUP_VERB_RE.match(clause) for clause in clauses[1:]):
        return None
    return m.group(1)


def ensure_entry_point(components: List["Component"],
                       request: str) -> List["Component"]:
    """Guarantee something to run when the request asked for a program.

    A planner splitting a task into parts will sometimes emit only library
    modules and nothing that runs them. Observed live: a request for "a python
    script that shows colorful dancing shapes" produced `core_engine.py`,
    `shapes_logic.py` and `utils.py` -- three files, no entry point, nothing to
    execute. Every gate passed, because each file was fine on its own.

    Adding the joining component is safe: it depends on all the others, so it
    is written last and is handed their real source. If the plan already has a
    main.py, nothing changes.
    """
    if not components or len(components) == 1:
        return components
    if any(os.path.basename(c.path) == "main.py" for c in components):
        return components
    if not _RUNNABLE.search(request or ""):
        return components

    parts = ", ".join(sorted(os.path.basename(c.path) for c in components))
    entry = Component(
        "main", "main.py", 2, [c.name for c in components],
        "The runnable entry point for this program. Import everything it needs "
        "from the other modules (%s) and use them -- do not define your own "
        "version of any class or function that already exists there. Contain a "
        "main() holding the loop or command line, called from an "
        "`if __name__ == \"__main__\":` block, so that running this file "
        "starts the program described by: %s" % (parts, request.strip()))
    entry.contract = components[0].contract
    return components + [entry]


def deshadow(components: List["Component"]) -> List[Tuple[str, str]]:
    """Rename top-level files whose module name shadows the standard library.

    A planner asked for a program's parts will happily call one of them
    `types.py`, and at the top level of a project that file wins over the
    stdlib `types` for every import in the process -- including the ones
    CPython performs while starting up. The result is that every component
    fails to import for a reason that has nothing to do with the code in it,
    which reads as five broken files rather than one bad filename. Observed on
    a real run: 4 of 5 components failed with import errors behind a single
    `types.py`.

    Renaming at plan time is safe in a way that renaming later is not: nothing
    has been written yet, and dependents are handed the real path when their
    turn comes, so their imports follow automatically.
    """
    renamed: List[Tuple[str, str]] = []
    for c in components:
        directory, filename = os.path.split(c.path)
        if directory:
            # Inside a package only the package name is importable from the
            # top level, so `pkg/types.py` shadows nothing.
            continue
        stem, ext = os.path.splitext(filename)
        if ext == ".py" and stem in _STDLIB:
            fresh = "%s_mod.py" % stem
            renamed.append((c.path, fresh))
            c.path = fresh
    return renamed


_SLUG_STOP = {
    "a", "an", "the", "that", "this", "for", "and", "or", "to", "of", "with",
    "write", "me", "script", "can", "it", "is", "just", "please", "create",
    "make", "build", "new", "so", "we", "i", "want", "need", "some", "into",
    "which", "given", "existing", "then", "also", "in", "on", "up", "return",
    "gives", "give", "back", "you", "your",
}


def _slug_from_request(request: str) -> str:
    """A short, meaningful filename guessed from what the request asks for.

    `main_2.py` names nothing -- it reads as "a second copy of main", not
    "the thing this actually is", and that ambiguity is itself worth fixing:
    an operator staring at `main.py` and `main_2.py` has no way to tell which
    one does what without opening both. `DECOMPOSE_PROMPT` already asks the
    planner for a name like `upload_unit.py` when it recognises the case;
    this is the same idea applied mechanically, for when it does not.
    """
    words = [w.lower() for w in re.findall(r"[a-zA-Z]+", request or "")]
    kept = [w for w in words if w not in _SLUG_STOP and len(w) > 1]
    if not kept:
        return "task"
    return "_".join(kept[:4])


def rescue_entry_points(components: List["Component"], blocked: List[str],
                        root: str, request: str = "") -> List[Tuple[str, str]]:
    """Force a component off a path `ground.entry_point_conflicts` refused.

    The planner was told in prose which existing files are safe to revise
    and which are not, and prose is advisory: a plan reusing main.py for a
    request that had nothing to do with what main.py already did was
    observed live, deleting a script the operator had just run
    successfully. This makes the same decision binding once it has been
    made, the same way `deshadow` makes a stdlib-collision decision
    binding -- neither asks the planner again, both just act.

    Everything else about the component -- its spec, its deps, the shared
    contract -- came from the planner's own decomposition and is left alone;
    only the path changes, and only because it collided with a file this
    request must not touch. The new name comes from the request
    (`_slug_from_request`), falling back to `<slug>_2.py`, `<slug>_3.py`, ...
    only if that exact name is already on disk or claimed by another
    component in this plan, so two unrelated rescues in one run cannot
    collide with each other either.
    """
    renamed: List[Tuple[str, str]] = []
    if not blocked:
        return renamed
    blocked_set = {os.path.normpath(p) for p in blocked}
    taken = {os.path.normpath(c.path) for c in components}
    for c in components:
        if os.path.normpath(c.path) not in blocked_set:
            continue
        ext = os.path.splitext(c.path)[1]
        base = _slug_from_request(request)
        fresh = base + ext
        key = os.path.normpath(fresh)
        n = 2
        while key in taken or os.path.isfile(os.path.join(root, fresh)):
            fresh = "%s_%d%s" % (base, n, ext)
            key = os.path.normpath(fresh)
            n += 1
        renamed.append((c.path, fresh))
        taken.discard(os.path.normpath(c.path))
        taken.add(key)
        c.path = fresh
    return renamed


# File types a component may target. Each one has a gate in
# `codegen.gate_syntax` that either establishes something real about it or
# openly establishes nothing: Python compiles, JSON parses, HTML must nest
# correctly, and prose or stylesheets are only required to be non-empty.
#
# Kept as an allowlist because the failure of the alternative is silent. A
# `.yaml` nobody wrote a gate for would be generated, checked by nothing,
# and reported alongside the files that were -- and "4/4 passed" would
# quietly mean three. Widening this is a decision about what proves the new
# type correct, which is why it is not automatic.
WRITABLE = frozenset({
    ".py",                                   # compiled, imported, smoke-run
    ".json",                                 # parsed
    ".html", ".htm",                         # tag nesting checked
    ".js", ".mjs", ".cjs",                   # node --check, when node exists
    ".css", ".md", ".txt", ".csv", ".sql",   # non-empty only
})

# Shared with `team._race_scratch_path`: a race/hedge candidate's scratch
# file keeps the real target's extension (so `codegen.is_python` and every
# Python-only gate still run on it correctly while it is being written --
# see that function's docstring for the live bug this exists to prevent),
# which means a straggler left behind by a losing candidate is otherwise
# indistinguishable, by name, from a real project file. Defined here rather
# than duplicated in every module that scans a project directory for real
# `.py` content: `team.py`'s own existing-file snapshot and `session.py`'s
# file-reasons/mentioned-files scans both filter on this so a straggler is
# never read back as something the operator actually wrote.
RACE_SCRATCH_MARKER = ".__swarmrace"


def is_race_scratch_name(name: str) -> bool:
    """True for any filename a race/hedge candidate's scratch path could
    have produced (see `RACE_SCRATCH_MARKER`), so it can be excluded from
    a directory scan for real, existing project content."""
    return RACE_SCRATCH_MARKER in name


_RACE_SCRATCH_SEGMENT_RE = re.compile(
    re.escape(RACE_SCRATCH_MARKER) + r"_[A-Za-z]+\d+__")


def race_scratch_display_name(name: str) -> str:
    """The real component filename a race/hedge scratch path stands in
    for -- `main.__swarmrace_race1__.py` back to `main.py`.

    A losing race/hedge candidate is a daemon thread that is not waited
    for, so it can still be mid-generation, still running its own gates,
    and still printing its own advisory notes well after the race has
    already concluded and moved on. Several of those notes (a missing
    sibling file, a malformed platform link) are printed unconditionally
    -- correctly, since they are meant to reach the operator regardless
    of `verbose` -- straight to the shared stderr stream, and print
    whatever path they were actually given, which for one of these
    candidates is the scratch path, not the real one. Measured live: a
    straggler printed `main.__swarmrace_race1__.py could not find
    ../02_create_task/output.json ...`, and read from outside this
    project as if some new, unexplained file had appeared and failed --
    it had not; `main.py` (the winner) had already been written
    successfully, and this was an abandoned attempt's own routine,
    harmless smoke-skip note continuing to print after the fact.

    Used only for display in these messages, never for anything that
    reads or writes a file -- the scratch path itself is still the real
    file for every operation that actually touches disk.
    """
    return _RACE_SCRATCH_SEGMENT_RE.sub("", name)


def parse_components(text: str, root: str = "") -> List[Component]:
    raw: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    contract = ""
    for key, value in _fields(text):
        if key == "contract":
            if value.strip().upper() not in ("", "NONE"):
                contract = value.strip()
            continue
        if key == "component":
            if current.get("component"):
                raw.append(current)
                current = {}
        if key in ("component", "file", "floor", "deps", "spec"):
            current[key] = value
    if current.get("component"):
        raw.append(current)

    # A small model repeating a COMPONENT block verbatim -- measured live
    # right after the markdown-stripping fix above surfaced it: two blocks
    # that both normalized to the same name, the same path, and the exact
    # same SPEC text -- is not two files that happen to share a name (the
    # rename-on-duplicate-name logic further below already handles that
    # case, for two blocks that differ in content). It is the model saying
    # the same thing twice. Collapsed here, before floors or deps get
    # computed for either copy, because this is also what let the plan
    # come back as two components for a request that only ever needed
    # one: `default_api_entry_point` only renames the entry point to
    # `main.py` when there is exactly one component, so a repeated block
    # silently disabled that default for every single-capability `--api`
    # request it happened to.
    deduped: List[Dict[str, str]] = []
    seen_raw = set()
    exact_dupes = 0
    for r in raw:
        key = (r.get("component", "").strip(),
              (r.get("file") or "").strip().strip("`").strip("*").strip(),
              (r.get("spec") or "").strip())
        if key in seen_raw:
            exact_dupes += 1
            continue
        seen_raw.add(key)
        deduped.append(r)
    if exact_dupes:
        sys.stderr.write(
            "  warning: the plan repeated %d component block(s) verbatim "
            "(same name, path and spec) -- kept one, dropped the "
            "repeat(s).\n" % exact_dupes)
    raw = deduped

    out: List[Component] = []
    names = {r.get("component", "").strip() for r in raw}
    # `existing_files_block` tells the planner to "list the existing files
    # it needs as dependencies" for anything it is not rewriting -- but a
    # dependency token only survived if it matched another component IN
    # THIS SAME RESPONSE, so following that instruction was self-defeating:
    # the token named a file, not a component, and got silently dropped
    # either way. A file already on disk is now accepted too. `root`
    # empty (the default, and every call before this existed) changes
    # nothing, since `on_disk` is then empty.
    on_disk = (set(os.listdir(root)) if root and os.path.isdir(root)
              else set())
    dropped: List[str] = []
    for r in raw:
        path = (r.get("file") or "").strip().strip("`")
        if os.path.splitext(path)[1].lower() not in WRITABLE:
            # An allowlist, not a rejection of everything unfamiliar. The
            # gates decide per extension what can be established about a
            # file, so a type nobody has written a gate for would be
            # generated and then waved through -- which reads as "passed"
            # while nothing was checked. Adding a type here means deciding
            # what proves it correct first.
            if path:
                dropped.append(path)
            continue
        if os.path.isabs(path) or ".." in path.split(os.sep):
            continue
        try:
            floor = int(re.findall(r"\d+", r.get("floor", "2"))[0])
        except (IndexError, ValueError):
            floor = 2
        floor = 4 if floor >= 4 else 2
        deps_raw = (r.get("deps") or "").strip()
        deps = [] if deps_raw.upper() in ("", "NONE") else [
            d.strip() for d in re.split(r"[,\s]+", deps_raw)
            if d.strip() and (d.strip() in names or d.strip() in on_disk)]
        spec = (r.get("spec") or "").strip()
        if not spec:
            continue
        component = Component(r["component"].strip(), path, floor, deps, spec)
        component.contract = contract
        out.append(component)

    if dropped:
        sys.stderr.write(
            "  note: %s not generated -- no gate exists for that file type.\n"
            "        Writable: %s. Ask for a .py that writes the file "
            "instead.\n" % (", ".join(sorted(set(dropped))),
                            " ".join(sorted(e for e in WRITABLE if e))))

    # Two COMPONENT blocks that both used the same `component:` name (the
    # planner is not asked to make it unique, only descriptive) collapse
    # onto one dict entry in every name-keyed structure downstream --
    # `by_name` in `_acyclic`, `dependents` in `critical_depth` -- so a
    # self-reference left in one of the two survives cycle-breaking under
    # the other's identity. Measured live: two blocks both named "main"
    # (one for main.py, one for a CLI wrapper) produced a plan whose
    # second "main" listed "main" as one of its own DEPS -- read as a
    # literal self-loop once both shared the one dict slot -- and
    # `critical_depth.compute` recursed until Python's stack gave out.
    # Renaming every duplicate after the first restores one name per
    # component before anything downstream keys off it; a DEPS token that
    # named the shared label still resolves, unambiguously, to whichever
    # component kept the original name.
    seen_names: Dict[str, int] = {}
    for c in out:
        seen_names[c.name] = seen_names.get(c.name, 0) + 1
        if seen_names[c.name] > 1:
            new_name = "%s_%d" % (c.name, seen_names[c.name])
            sys.stderr.write(
                "  warning: two components were both named %r; renaming "
                "the one writing %s to %r so dependency tracking is not "
                "ambiguous.\n" % (c.name, c.path, new_name))
            c.name = new_name

    # Drop dependencies on names that are neither a component that survived
    # parsing nor a file already on disk, and break any cycle by dropping
    # the offending edge -- a plan that cannot be ordered is worse than one
    # that loses a hint.
    kept = {c.name for c in out}
    for c in out:
        c.deps = [d for d in c.deps
                 if d != c.name and (d in kept or d in on_disk)]
    return _acyclic(out)


def _acyclic(components: List[Component]) -> List[Component]:
    by_name = {c.name: c for c in components}
    state: Dict[str, int] = {}

    def visit(name: str) -> None:
        state[name] = 1
        for dep in list(by_name[name].deps):
            # A dep naming an existing file this plan is not rewriting has
            # no Component of its own -- nothing to recurse into, and
            # nothing that could complete a cycle back to something that
            # is actually being generated.
            if dep not in by_name:
                continue
            if state.get(dep) == 1:
                by_name[name].deps.remove(dep)
            elif state.get(dep) is None:
                visit(dep)
        state[name] = 2

    for c in components:
        if state.get(c.name) is None:
            visit(c.name)
    return components


def critical_depth(components: List[Component]) -> Dict[str, int]:
    """Longest chain of dependents below each component.

    The scheduling priority: whatever has the most work stacked behind it
    should start first, so the tail of the plan is never waiting on something
    that could have been running an hour ago. Same idea as the upward rank in
    the HEFT scheduler, counted in components rather than seconds because the
    duration of a file nobody has written yet is not knowable.
    """
    by_name = {c.name: c for c in components}
    dependents: Dict[str, List[str]] = {c.name: [] for c in components}
    for c in components:
        for d in c.deps:
            # A retried run holds only the components that failed, while their
            # dependencies name ones that already succeeded and are not in this
            # list. An absent dependency has nothing stacked behind it here, so
            # it contributes no depth rather than being an error.
            if d in dependents:
                dependents[d].append(c.name)

    depth: Dict[str, int] = {}
    # `_acyclic` already guarantees the plan's own graph has no cycle, and
    # the duplicate-name fix above removes the specific way one used to
    # slip past that guarantee. `visiting` is a second, independent net:
    # if a cycle ever reaches back here anyway, from a source neither of
    # those two catches, breaking here trades a slightly wrong scheduling
    # priority for not taking the whole run down with a RecursionError.
    visiting: Set[str] = set()

    def compute(name: str) -> int:
        if name in depth:
            return depth[name]
        if name in visiting:
            sys.stderr.write(
                "  warning: dependency cycle detected at %r while ranking "
                "components by scheduling priority; treating it as having "
                "no further dependents.\n" % name)
            return 0
        visiting.add(name)
        depth[name] = 1 + max([compute(n) for n in dependents.get(name, [])]
                               or [0])
        visiting.discard(name)
        return depth[name]

    for c in components:
        compute(c.name)
    return depth


def dependency_context(component: Component,
                       done: Dict[str, Component]) -> str:
    """The real source of everything this component builds on.

    Handing over a description of a dependency invites a plausible guess at its
    interface, and the pieces then fail to meet. Handing over the actual code
    removes the guess.
    """
    parts: List[str] = []
    for dep in component.deps:
        other = done.get(dep)
        if not (other and other.code):
            continue
        if not other.path.endswith(".py"):
            # A page is opened at run time, not imported. Said the other
            # way -- `other.path[:-3]` on `survey.html` -- this instructed
            # a writer to `from survey.h import ...`, and fenced HTML as
            # Python while doing it. Same rule `crossref` already follows
            # for non-Python dependencies, and the same reason `attach`
            # picks its fence from the extension: a fence is a claim about
            # the content, and these readers will not override it.
            fence = {".html": "html", ".htm": "html", ".css": "css",
                     ".js": "javascript", ".json": "json"}.get(
                         os.path.splitext(other.path)[1], "")
            parts.append(
                "The file %s already exists, shown below. It is not a Python "
                "module: read it at run time if you need it, by that exact "
                "filename. Do not import it and do not write your own copy."
                "\n\n```%s\n%s```" % (other.path, fence, other.code))
            continue
        module = other.path[:-3].replace(os.sep, ".")
        names = sorted(set(re.findall(r"^\s*(?:class|def)\s+(\w+)",
                                      other.code, re.MULTILINE)))
        # "Match its interface" was read by a small model as "declare it
        # again", which type-checks, runs, and silently forks the
        # definition. Say the quiet part instead.
        parts.append(
            "The file %s already exists, shown below. Import what you need "
            "from it with `from %s import ...`.\n\n"
            "Do NOT copy, redeclare, or re-implement anything defined "
            "there%s. There must be exactly one definition of each of "
            "those names in the whole project, and it is the one below.\n\n"
            "```python\n%s```"
            % (other.path, module,
               (" -- specifically " + ", ".join(names)) if names else "",
               other.code))
    return "\n\n".join(parts)


# Names every module legitimately defines for itself. A demo block is required
# of generated components, so `main` appears in all of them by construction.
_ENTRY_POINTS = {"main", "run", "demo", "test", "setup", "cli"}


_OWNS = re.compile(r"([\w./]+\.py)\s+owns\s+([^;]*)", re.I)
_IDENT = re.compile(r"\b([A-Za-z_]\w*)\b")

# Types and containers a contract mentions in passing. Claiming one as an owned
# symbol both misattributes it and forbids it everywhere else, so a wrong entry
# here is worse than a missing one.
_NOT_SYMBOLS = frozenset("""
int float str bool bytes list dict set tuple frozenset object none any type
optional union callable iterable sequence mapping literal
""".split()) | frozenset("""
Optional Union Callable Iterable Sequence Mapping Literal Any None List Dict
Set Tuple FrozenSet Type
""".split())


_CONTRACT_FILE = re.compile(
    r"\b([\w./-]+\.(?:%s))\b" % "|".join(e.lstrip(".") for e in sorted(WRITABLE)),
    re.I)


def unbuilt_contract_files(text: str,
                           components: Sequence["Component"]) -> List[str]:
    """Files the contract names that no component in this plan writes.

    A truncated plan is the failure this exists to name, and it is invisible
    otherwise: the components emitted before the cut parse perfectly, the run
    proceeds on a fraction of the work, and the summary says everything
    passed. Measured on a live request for a page plus a script, where the
    reply was cut mid-emission -- the contract read `api_client.py owns ...;
    client.py owns main()` and only `api_client.py` had a COMPONENT block.

    Decidable from the planner's own two halves, so it is decided here. Both
    sides come out of one reply, which is why this cannot be wrong about
    intent the way a model reviewing the plan could be.
    """
    contract = ""
    for key, value in _fields(text):
        if key == "contract":
            contract = value
            break
    if not contract or contract.strip().upper() == "NONE":
        return []
    built = {os.path.basename(c.path) for c in components}
    missing: List[str] = []
    for name in _CONTRACT_FILE.findall(contract):
        base = os.path.basename(name)
        if base not in built and base not in missing:
            missing.append(base)
    return missing


_OUTPUT_HELPER_SIBLING_RE = re.compile(r"\.\./[\w\-./]*output\.json", re.I)

# "save its id as applet_id" / "...as project_id" / "...as task_id": the
# key name is whatever this specific request's value happens to be
# called, never a second job the file does. Stripped generically rather
# than enumerated, since a new request invents a new noun every time
# ("unit_id", "version_id", ...) and a fixed list would always be one
# request behind.
_OUTPUT_HELPER_KEYNAME_RE = re.compile(r"\bas\s+[a-z][a-z0-9_]*\b", re.I)

# Everything an output-saving clause is made of and nothing else: the
# filename, the verb, and the boilerplate phrasing this project's own
# templates use ("in the same directory as the script", "under the same
# key the API response uses for it"). Deliberately generous -- the point
# is not to guess precisely which words are boilerplate, it is that a
# SPEC left with *nothing* outside this list has no other job in it.
_OUTPUT_HELPER_NARRATIVE = frozenset(
    "save saves saved saving output json id ids new file result its "
    "it under key response uses use used for local locally write writes "
    "written directory same script this that entry point runnable main "
    "read reads dict object data value the and with once when where has "
    "have call calls calling function helper module component file's "
    "own current after before then also api".split())


def output_only_components(components: Sequence["Component"]) -> List[str]:
    """Components whose entire SPEC is the output.json-saving boilerplate
    and nothing else -- planned as their own file rather than folded into
    whichever component actually produces the value.

    `codegen.gate_missing_own_output_write` requires a literal write to
    "output.json" inside the file whose SPEC asked for one. A dedicated
    "output helper" is naturally written the reusable way -- a small
    function taking a path or a dict as a parameter -- with no literal
    filename anywhere in its own source; the filename lives only at
    whichever call site invokes it. Measured live: exactly this shape was
    planned once, needed three rungs (initial attempts, a raced restart,
    a model escalation) and failed the identical gate every single time,
    because the literal string genuinely never appears in that file no
    matter who writes it or how many times.

    `DECOMPOSE_PROMPT` now tells the planner outright not to plan this
    file; this is the mechanical backstop for when it does anyway,
    printed before any generation time is spent on a component this
    shape is structurally unlikely to ever pass.

    Deliberately conservative: a SPEC naming output.json alongside any
    other real word -- a verb, a field name, anything not in the fixed
    boilerplate vocabulary above -- is left alone, because that component
    may genuinely have other work in it and a detector that fires on a
    real component costs more than it saves.
    """
    out: List[str] = []
    for c in components:
        spec = c.spec or ""
        if not spec.strip():
            continue
        stripped = _OUTPUT_HELPER_SIBLING_RE.sub(" ", spec)
        if "output.json" not in stripped.lower():
            continue
        stripped = _OUTPUT_HELPER_KEYNAME_RE.sub(" ", stripped)
        words = {w for w in re.findall(r"[a-z0-9]+", stripped.lower())
                 if len(w) > 2}
        words -= _OUTPUT_HELPER_NARRATIVE
        if not words:
            out.append(c.name)
    return out


# Suffixes/prefixes a filename earns from being "the client wrapping some
# resource", stripped repeatedly from the end so "project_api_client" ->
# "project_api" -> "project". Not stripped from the middle or the start,
# on purpose: a genuine domain word can itself look like one of these
# tokens (a request actually about "services" naming `service.py` should
# not have its own name eaten down to nothing).
_CLIENT_FILE_SUFFIXES = frozenset(
    "client clients api apis creator creators service services util utils "
    "helper helpers wrapper wrappers handler handlers manager managers "
    "sdk adapter adapters gateway gateways connector connectors".split())


def _client_domain_words(basename: str) -> Set[str]:
    stem = re.sub(r"\.[a-z0-9]+$", "", basename.lower())
    tokens = [t for t in stem.split("_") if t]
    while tokens and tokens[-1] in _CLIENT_FILE_SUFFIXES:
        tokens.pop()
    return {t for t in tokens if len(t) > 2}


def unrequested_client_split(components: Sequence["Component"],
                             request: str) -> List[Tuple[str, str]]:
    """Components named after an API resource nobody asked about --
    `project_api_client.py`, `task_api_client.py` -- planned alongside a
    request that never mentions a project or a task.

    The endpoint index handed to the planner is grouped under headings
    like "## Applets", "## Projects", "## Tasks" purely so a writer can
    find a real neighbouring endpoint; it is not a menu of areas to build
    a client for. Measured live: a request reading only "create an
    applet; save its id" -- one action, zero mention of a project or a
    task -- came back as four components: `api_client.py`,
    `applet_creator.py`, `project_api_client.py`, `task_api_client.py`.
    The last two call endpoints the request never asked for, built by
    reading the tag headings in the endpoint listing as an instruction
    to build one client per group rather than as a place to look up an
    id a *real* call needs.

    Decided from names, not specs, because a client-shaped filename is
    the one part of this failure that is never ambiguous: strip the
    generic client/api/service/etc. suffix off the basename and what is
    left is the one resource this file claims to own. If that word (or
    any word sharing it, for a request using a different form of the
    same noun) never appears anywhere in the request text, this
    component is very likely scope the request never asked for.

    Deliberately narrow. `api_client.py` strips down to nothing (no
    domain claimed) and is never flagged -- a generic base client is
    legitimate scaffolding regardless of how many resources the task
    touches. A single client component is never flagged either,
    regardless of its name, since there is nothing to compare it
    against: this only fires when the *plan itself* already contains
    more than one client-shaped file, which is the actual shape of the
    live failure.
    """
    if not request or not request.strip():
        return []
    req_words = {w for w in re.findall(r"[a-z0-9]+", request.lower())
                if len(w) > 2}
    client_shaped: List[Tuple["Component", Set[str]]] = []
    for c in components:
        words = _client_domain_words(os.path.basename(c.path))
        if words:
            client_shaped.append((c, words))
    if len(client_shaped) < 2:
        return []
    out: List[Tuple[str, str]] = []
    for c, words in client_shaped:
        # Substring, not exact set membership: "project" must match a
        # request that says "projects", and a request that says
        # "project-scoped" must match a domain word of "project" too.
        # Plain plural/singular drift is not evidence of scope creep.
        if not any(w in rw or rw in w for w in words for rw in req_words):
            out.append((c.name, "/".join(sorted(words))))
    return out


_CONTRACT_OWNS_PY = re.compile(r"([\w./-]+\.py)\s+owns\s+([^;]*)", re.I)


def reconcile_contract_assets(contract: str,
                              components: Sequence["Component"]) -> str:
    """Correct a CONTRACT clause naming a .py module the plan never built,
    when a differently-typed component with the same stem was built instead.

    "the applet should be html" is a request the planner is meant to honour
    by planning a real `.html` component -- and it did, here, choosing
    `applet.html` over a Python generator. It just never told the CONTRACT
    line the same thing: the broadcast still read `applet.py owns
    generate_applet_html() -> str`, unchanged from whatever draft preceded
    that choice. `unbuilt_contract_files` already detects this shape and
    prints a warning, but a warning is prose, and prose lost this exact
    argument once already (see the entry-point rename this project fixed
    the same way). `dependency_context` correctly told `main.py` that
    `applet.html` exists and must be read from disk, not imported -- and
    the broadcast CONTRACT line, sent to every writer with "use these exact
    names" framing, said the opposite about the same file under its other
    name. Measured live: the writer followed the CONTRACT, hit an
    `ImportError` importing a module that was never going to exist, and
    "fixed" it by defining its own fallback and printing "Warning: Using
    mocked generate_applet_html function." -- passing every gate while
    silently discarding the real, reviewed HTML the pool had just spent
    3m26s writing.

    So the mismatch is corrected before any writer sees it, the same way a
    stdlib-name collision is renamed rather than explained: for every
    `X.py owns ...` clause where `X.py` was never built but some built
    component shares its stem under a different extension, the clause is
    replaced with a note naming the real file, its real type, and the
    literal fact that the claimed signature does not exist. A component
    whose own path really is `X.py` is untouched, and a stem with no
    same-named sibling of any kind is untouched too -- this only fires on
    the specific contradiction it exists to fix.
    """
    if not contract or contract.strip().upper() == "NONE":
        return contract
    built_paths = {os.path.basename(c.path) for c in components}
    by_stem: Dict[str, str] = {}
    for c in components:
        by_stem.setdefault(os.path.splitext(os.path.basename(c.path))[0],
                           c.path)

    def _fix(m: "re.Match") -> str:
        fname, body = m.group(1), m.group(2)
        base = os.path.basename(fname)
        if base in built_paths:
            return m.group(0)
        real = by_stem.get(os.path.splitext(base)[0])
        if not real or real == fname:
            return m.group(0)
        kind = os.path.splitext(real)[1].lstrip(".").upper()
        return ("%s (not a Python module -- there is no %s) is a static "
                "%s file; read it from disk with open(%r).read(), never "
                "import it, and the signature %r does not exist"
                % (real, fname, kind, real, body.strip()))

    return _CONTRACT_OWNS_PY.sub(_fix, contract)


_OWNS_WITH = re.compile(
    r"([\w./-]+\.py)\s+owns\s+(\w+)\(([^()]*)\)\s+with\s+(.*?)"
    r"(?=(?:[\w./-]+\.py\s+owns\b)|$)",
    re.I | re.S,
)
_MEMBER_SIG = re.compile(r"(\w+)\(([^()]*)\)(\s*->\s*[\w.\[\], ]+)?")


def _param_names(arglist: str) -> Set[str]:
    names: Set[str] = set()
    for part in arglist.split(","):
        part = part.strip()
        if not part:
            continue
        name = part.split(":", 1)[0].strip().lstrip("*")
        if name and name != "self":
            names.add(name)
    return names


def hoist_selfless_members(contract: str) -> str:
    """Pull a self-less function mistakenly nested under a class's `with`
    clause out onto its own line, before any writer reads the contract.

    A method never repeats a constructor argument it already has on `self`
    -- `Client(api_base_url: str, ...)` never needs a method that also takes
    `api_base_url` explicitly, since it is already `self.api_base_url`. A
    signature that does repeat one is a plain function, and grouping it
    under `Client(...) with ...` answers the "method or function" question
    wrong by default. Measured live, twice, on the same name: the CONTRACT
    read `client.py owns Client(...) with create_applet(...); ...;
    get_ui_url(api_base_url: str, project_id: str) -> str` -- one writer
    read the grouping and implemented it as a bound method, the other read
    the signature and called it as `client.get_ui_url(...)` expecting a
    module-level name, and the file that depended on it failed every
    attempt because the fix it needed was in its sibling's court, not its
    own. `DECOMPOSE_PROMPT` already asks the planner to give such a name
    its own line instead, and the planner ignored the instruction on the
    very request that motivated writing it down -- the reply still nested
    `get_ui_url` under `Client(...) with`, just with "(as a plain
    function)" tacked on in prose that nothing downstream reads. Prose
    already lost this argument once, for the entry-point rename this
    project fixed the same way: mechanically, not by asking more firmly.

    So this rewrites the shape directly. Only a member whose parameter
    names overlap the constructor's is moved -- an ordinary method with no
    such overlap is left exactly where it was -- and the hoisted line names
    what it replaces and how to call it, so a writer on either side of the
    dependency sees the same fact: `client_module.get_ui_url(...)`, never
    `client_instance.get_ui_url(...)`. This can misfire only if an ordinary
    method's own parameter happens to share a name with a constructor
    argument for an unrelated reason; that is rarer than the bug it fixes,
    which has now been hit twice.
    """
    if not contract or contract.strip().upper() == "NONE":
        return contract

    def _fix_group(m: "re.Match") -> str:
        path, owner, ctor_args, members_blob = m.groups()
        ctor_params = _param_names(ctor_args)
        members = [x.strip() for x in members_blob.split(";") if x.strip()]
        kept: List[str] = []
        hoisted: List[Tuple[str, str, str]] = []
        for member in members:
            sig = _MEMBER_SIG.match(member)
            if not sig:
                kept.append(member)
                continue
            name, args, ret = sig.groups()
            arg_names = _param_names(args)
            if ctor_params and arg_names and (arg_names & ctor_params):
                hoisted.append((name, args, (ret or "").strip()))
            else:
                kept.append(member)
        if not hoisted:
            return m.group(0)
        stem = os.path.splitext(os.path.basename(path))[0]
        rebuilt = "%s owns %s(%s)" % (path, owner, ctor_args)
        if kept:
            rebuilt += " with " + "; ".join(kept)
        rebuilt += ";"
        for name, args, ret in hoisted:
            sig_str = "%s(%s)" % (name, args)
            if ret:
                sig_str += " " + ret
            # The explanation has to sit *outside* the text `contract_owners`
            # captures for this clause -- that scan takes everything up to
            # the next `;` after `owns` and treats each comma- or
            # `and`-separated chunk as another declared symbol. A first
            # version of this put the whole sentence, colons and commas
            # included, before the semicolon, and `contract_owners` read
            # "get_ui_url ... NOT a method of Client: call it as ..." as
            # four separate symbols named `Client`, `NOT`, `get_ui_url` and
            # `never` -- all "owned" by this file. The writer's own
            # ownership note then read "You define Client, NOT, get_ui_url,
            # never," which is why the hoist alone did not fix the bug it
            # was written for: the signature moved, but the instruction
            # explaining it was mangled into noise before the writer saw it.
            # Ending the declaration at the signature's own `;` keeps the
            # parse clean; the explanation after it names no new symbol.
            rebuilt += (" %s owns %s;" % (path, sig_str))
            rebuilt += (" (%s is a module-level function, not a method of "
                       "%s -- call it as %s.%s(...), never as an instance "
                       "method.) " % (name, owner, stem, name))
        return rebuilt

    return _OWNS_WITH.sub(_fix_group, contract)


_OWNS_MAIN = re.compile(r"([\w./-]+\.py)\s+owns\s+[^;]*\bmain\s*\(", re.I)


def duplicate_entry_points(text: str,
                           components: Sequence["Component"]) -> List[str]:
    """Files this plan makes runnable when the format allows one.

    A second entry point is not merely untidy: it is a fully serial stage at
    the tail, which is the worst place to put one. Measured on a live run,
    the contract read `client.py owns main()` and the plan also carried a
    main.py depending on client.py -- so after two components had run in
    parallel, two machines idled through two more that could not overlap.

    Only the unambiguous case is reported: the contract says one file owns
    `main()` and a *different* component is a main.py. A plan naming the
    same file both ways is consistent and says nothing.
    """
    contract = ""
    for key, value in _fields(text):
        if key == "contract":
            contract = value
            break
    owners = {os.path.basename(m) for m in _OWNS_MAIN.findall(contract or "")}
    if not owners:
        return []
    entries = {os.path.basename(c.path) for c in components
               if os.path.basename(c.path) == "main.py"}
    extra = sorted(entries - owners)
    return sorted(owners) + extra if extra else []


_PARENT_STEP_RE = re.compile(r'\.\./[\w.\-]+/[\w.\-]+')


def reads_sibling_output(request: str) -> bool:
    """Does this request name itself as one link in a serial chain.

    Shared by `low_parallelism_note` (reports the shape after planning) and
    `cmd_do`'s single-file auto-detection (skips the planner before it runs
    at all) -- one regex, one place it can be wrong, rather than the same
    pattern maintained twice and drifting.
    """
    return bool(_PARENT_STEP_RE.search(request or ""))


# A short, deliberately generic list of extensions a request plausibly
# hands to a *later* step as a produced artifact -- html/css/js pages,
# data files, images. Never `.py`: a request mentioning `client.py` is
# naming a component of *this* plan (see `wants_multi_file` above), not a
# file some earlier, unrelated step produced.
_ASSET_EXT_RE = re.compile(
    r'\b([\w\-]+\.(?:html?|css|js|mjs|json|csv|txt|md|svg|png|jpe?g|gif|'
    r'pdf|xml|yaml|yml))\b', re.IGNORECASE)


_OUTPUT_JSON_READ_RE = re.compile(r'\.\./([\w.\-]+)/output\.json')
# Deliberately excludes the `../dir/` form above -- a bare "output.json"
# with nothing in front of it is talking about *this* step's own file,
# not a sibling's, and that is exactly the shape a producer's request
# uses when it promises to write one ("save the id to output.json").
_OUTPUT_JSON_WRITE_RE = re.compile(r'(?<!/)\boutput\.json\b')


def _promises_own_output_json(dirname: str, request: str) -> bool:
    """Does this step's own request promise to write its own
    output.json -- either the bare form ("save X to output.json") or the
    self-qualified form ("save X in ../DIRNAME/output.json", naming its
    own directory rather than a sibling's).

    The self-qualified form is a real gap this project's own generated
    requests hit: `LOCAL.md`'s hard chain-drafting rule and
    `context/policy-output-json.md` both teach the bare phrase, but a
    producer whose request text was itself built by copying an earlier
    "read ... from ../dir/output.json" clause pattern can just as
    naturally write its own promise the same qualified way -- "save its
    new version id as applet_version_id in ../06_update_applet/output.json"
    -- referring to *its own* directory, not a sibling's. That is a real,
    unambiguous promise to write the file; it is simply phrased with the
    step's own name in front of it instead of bare.

    Measured live: `chain_output_json_problems` and
    `autofix_chain_output_json` both stripped *every* `../DIR/output.json`
    occurrence via `_OUTPUT_JSON_READ_RE.sub("", req)` before checking for
    a bare write promise, on the reasoning that a qualified reference is
    always a *read* of some other step. That reasoning fails exactly when
    DIR is the step's own directory: the self-promise gets stripped away
    along with genuine sibling reads, nothing bare is left behind, and a
    step that had already promised to write its own file, in substance,
    was reported as never having promised to -- with no fix the
    autofixer could apply, since asking the operator to "add a promise"
    that is already there word-for-word would produce byte-identical
    text and the same false report on the next run.
    """
    req = request or ""
    for match in _OUTPUT_JSON_READ_RE.finditer(req):
        if match.group(1) == dirname:
            return True
    stripped = _OUTPUT_JSON_READ_RE.sub("", req)
    return bool(_OUTPUT_JSON_WRITE_RE.search(stripped))


def chain_output_json_problems(steps: Sequence[Tuple[str, str]]) -> List[str]:
    """Does every `../DIR/output.json` a chain of requests reads have a
    matching promise, in DIR's own request, to write one.

    This is the mechanical half of the fix for a whole orchestration
    chain silently defaulting id after id to placeholders: `05_update_applet`
    reading `../01_create_project/output.json` is worth nothing if
    `01_create_project`'s own request never said to create that file, and
    nothing before this caught that until the run actually happened, sent
    a wrong id to a real API, and the failure surfaced as a 404 two steps
    away from its actual cause.

    The check does not need a model, and deliberately does not reach for
    one, because the "dynamic wording" problem it looks like at first is
    self-inflicted: whoever drafts the whole chain's request text (an
    operator or an orchestrating agent) controls every line of it before
    any of them are ever run, so the fix is not to parse arbitrary prose
    after the fact -- it is to fix the vocabulary the chain is drafted in
    to begin with. A producer's request says the bare phrase
    `output.json` (`"...; save the project id to output.json as
    {\"project_id\": ...}"`); a consumer's says the qualified form
    (`"...read project_id from ../01_create_project/output.json"`). Once
    every request in the chain is drafted in that fixed vocabulary --
    which costs nothing, since nothing about the underlying request
    changes, only which of two already-natural phrasings is used -- the
    check that they agree with each other is exactly as mechanical as
    `crossref.py` checking that an import matches a real class name.

    `steps` is the whole chain in the order the directories will actually
    run, as `(dir_name, request_text)` pairs -- this only makes sense
    checked as a set, since a consumer's requirement is a fact about a
    *different* step's request, not about its own.
    """
    write_promises: Dict[str, bool] = {}
    for dirname, request in steps:
        write_promises[dirname] = _promises_own_output_json(dirname, request)

    problems: List[str] = []
    for dirname, request in steps:
        for match in _OUTPUT_JSON_READ_RE.finditer(request or ""):
            producer = match.group(1)
            if write_promises.get(producer):
                continue
            if producer not in write_promises:
                problems.append(
                    "%s reads ../%s/output.json, but no step named %s is "
                    "in this chain at all" % (dirname, producer, producer))
            else:
                problems.append(
                    "%s reads ../%s/output.json, but %s's own request "
                    "never promises to write one -- add \"save ... to "
                    "output.json\" to %s's request before running any "
                    "of this chain"
                    % (dirname, producer, producer, producer))
    return problems


# Captures the key name a consumer's own request says it expects, right
# before "from ../dir/output.json" -- "read project_id from
# ../01_create_project/output.json" gives ("project_id",
# "01_create_project"). Required to end in "_id" and carry a real prefix
# (`project_id`, not bare `id`), which is the exact convention
# `context/policy-output-json.md` and the hard chain-drafting rule in
# `LOCAL.md` both mandate -- this only exists to recover a key name the
# *consumer* already committed to, never to guess one. Without the "_id"
# requirement, "read the project's id from ../.../output.json" matches
# the bare word "id" immediately before "from" and would autofix the
# producer with a meaningless generic key nobody asked for -- worse than
# leaving the gap reported, so that phrasing is deliberately left for
# `chain_output_json_problems` to report as a plain, unfixable problem.
_OUTPUT_JSON_KEY_RE = re.compile(
    r'\b([a-z][a-z0-9]*_id)\s+from\s+\.\./([\w.\-]+)/output\.json')


def autofix_chain_output_json(steps: Sequence[Tuple[str, str]]
                              ) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Repair what `chain_output_json_problems` can prove is fixable from
    the chain's own text, and report by name whatever it cannot.

    A missing write promise is only mechanically fixable when the
    consumer's own request already commits to a key name in the exact
    `"<key> from ../dir/output.json"` shape -- at that point the fix is
    not a guess, it is copying a fact the chain already stated once into
    the one other place it needs to agree. Several consumers reading
    different keys from the same producer are all folded into one
    appended clause, deduplicated, so a producer needing to satisfy two
    downstream reads is fixed in a single edit rather than getting two
    appended clauses that might contradict each other.

    Returns the corrected `(dir, request)` list -- unchanged steps come
    back byte-identical, since a step with nothing wrong is not rewritten
    -- and a separate list of problems that remain even after fixing
    everything this function knows how to fix: a reference to a step not
    in the chain at all, or a read phrased in a way that names no
    recoverable key. Those are exactly the cases `LOCAL.md` says should
    surface as a real, visible failure rather than be papered over,
    because there is no fact anywhere in the chain to fix them from.
    """
    needed_keys: Dict[str, List[str]] = {}
    for _dirname, request in steps:
        for match in _OUTPUT_JSON_KEY_RE.finditer(request or ""):
            key, producer = match.group(1), match.group(2)
            keys = needed_keys.setdefault(producer, [])
            if key not in keys:
                keys.append(key)

    fixed_steps: List[Tuple[str, str]] = []
    for dirname, request in steps:
        req = request or ""
        already_promises = _promises_own_output_json(dirname, req)
        keys = needed_keys.get(dirname)
        if not already_promises and keys:
            fields = ", ".join(
                "\"%s\": ..." % k for k in keys)
            req = req.rstrip()
            if req and not req.endswith((".", ";")):
                req += ";"
            req += (" save %s to output.json as {%s}"
                    % (" and ".join(keys), fields))
        fixed_steps.append((dirname, req))

    problems = chain_output_json_problems(fixed_steps)
    return fixed_steps, problems


_DANGLING_VERSION_RE = re.compile(
    r'\b(?:with|using|via|to)\s+(?:the|a|this|that|its)\s+'
    r'(?:(?:new|newest|latest|updated|current)\s+)?version\b'
    r'(?!\s+(?:string|number|identifier|label|name|tag|id)\b)',
    re.IGNORECASE)
# The trailing negative lookahead excludes "version" used as a modifier
# of a different head noun -- "with a version string guaranteed to be
# different from any that already exist" describes an attribute of a
# version this same step is about to create, not a reference to one
# created elsewhere. Measured live: a real chain's own create-and-point
# step ("push X as a new version ...; with a version string guaranteed
# to be different ...; and point the project ... at that new version")
# matched on "with a version" from the "version string" clause, found
# no earlier step even mentioning "version" (producer=None), and
# `cmd_lint_chain` escalated straight to a real network call
# (`repair_dangling_version_chain`) asking the pool to fix a problem
# that did not exist -- hanging the whole chain on step one, before
# `orchestrate.sh` or any subfolder was ever produced. "at that new
# version" (the actually-dangling-shaped phrase, if it had been
# unqualified) is untouched by this lookahead, since it is not followed
# by one of these descriptor nouns.

# A version id this step already committed to reading from a sibling,
# in the same fixed vocabulary `_OUTPUT_JSON_KEY_RE` already established
# for any `_id` key -- "read applet_version_id from
# ../06_update_applet/output.json". If this is present, the dangling
# phrase below is not dangling; the request already says where the
# version comes from.
# The prefix before "version" is optional -- it was not, originally, and
# that made this regex unable to match the single most natural name a
# fix would ever propose. `[a-z][a-z0-9_]*version...` demands at least
# one character ahead of the literal "version", which a *prefixed* name
# like "applet_version_id" supplies (the "applet_" is that character)
# but the bare, unprefixed "version_id" cannot: "version" starts the
# word at position 0, leaving nothing for the mandatory leading
# `[a-z]` to consume before the literal match, and rewinding the
# leading char at position 0 breaks the literal ("ersion..." no longer
# starts with "version"). Measured live: every network-repair reply for
# a dangling version reference that correctly proposed reading
# "version_id from ../06_update_applet/output.json" -- the plainest,
# most obvious key name for exactly this fix -- was rejected by
# `_validate_dangling_version_fix`'s re-check against
# `chain_dangling_version_problems`, because this regex still read the
# corrected text as still dangling. The repair loop was never wrong;
# the regex that was supposed to recognize a correct fix could not
# recognize the most likely one.
_VERSION_KEY_READ_RE = re.compile(
    r'\b(?:[a-z][a-z0-9_]*)?version[a-z0-9_]*_id\b\s+from\s+\.\./', re.IGNORECASE)

# A loose signal that an earlier step's own request is the one that
# probably created the version this step is dangling a reference to --
# never trusted enough to autofix from, only enough to name as a
# plausible culprit in the warning.
_VERSION_MENTION_RE = re.compile(r'\bversion\b', re.IGNORECASE)


def dangling_version_reference(request: str) -> bool:
    """Does this request say "with the new version" (or "using the
    latest version", "to this version", ...) -- consuming a version that
    some *other* step must have created -- without ever citing where that
    version's id actually comes from.

    Measured live: a chain step's request read, verbatim, "read
    project_id from ../01_create_project/output.json; read applet_id
    from ../05_create_applet/output.json; update the project with the
    new version." It genuinely does read two sibling outputs -- so
    `reads_sibling_output` (which only asks "does this request reference
    *any* `../dir/output.json` at all") says this chain step is properly
    wired, and it is wrong, because it is wired to the wrong two things.
    A sibling step earlier in the same chain had already created a real
    applet version from real content; this step's own API slice has no
    way to see that, because nothing in its request names it, so the
    writer did the only thing it could do with the tools it was given:
    it called the version-creating endpoint itself, with placeholder
    content, and pointed the project at a second, throwaway version
    instead of the real one two steps back.

    Deliberately keyed to the consuming preposition ("with"/"using"/
    "via"/"to" immediately before the noun phrase) rather than the bare
    phrase "a new version" on its own, because the ambiguity is real:
    "create a new version of the applet" is the *producing* step saying
    what it is about to do, and flagging that would tell an operator to
    add a read for something this very step is the one creating. "update
    the project *with* the new version" is a *consuming* step reaching
    for something it assumes already exists elsewhere. Only the second
    shape is ever wrong to leave unqualified.

    A request already citing `<word>version<word>_id from ../dir/...`
    (`_VERSION_KEY_READ_RE`) is never flagged, regardless of how the rest
    of it reads -- it already says where the version comes from, which
    is exactly what this check exists to demand.
    """
    req = request or ""
    if not _DANGLING_VERSION_RE.search(req):
        return False
    return not _VERSION_KEY_READ_RE.search(req)


def dangling_version_findings(steps: Sequence[Tuple[str, str]]
                              ) -> List[Tuple[int, str, Optional[str]]]:
    """The structured facts behind `chain_dangling_version_problems`:
    for every step that consumes "the new version" with nothing citing
    where it comes from, its index, its own dirname, and the dirname of
    the most likely already-built producer (by literal mention of
    "version" in an earlier step), or None if no earlier step mentions
    one at all.

    Split out so a repair path (`team.repair_dangling_version_chain`) can
    act on the same facts the message-building code already computed,
    rather than re-deriving them from the rendered prose -- the message
    text is for a human to read, this is for code that needs the actual
    dirnames.
    """
    findings: List[Tuple[int, str, Optional[str]]] = []
    for i, (dirname, request) in enumerate(steps):
        if not dangling_version_reference(request):
            continue
        producer = None
        for earlier_dir, earlier_req in reversed(steps[:i]):
            if _VERSION_MENTION_RE.search(earlier_req or ""):
                producer = earlier_dir
                break
        findings.append((i, dirname, producer))
    return findings


def chain_dangling_version_problems(steps: Sequence[Tuple[str, str]]
                                    ) -> List[str]:
    """`dangling_version_reference`, run across a whole chain, naming the
    most likely already-built producer for the message so an operator
    does not have to hunt for it by hand.

    Never autofixed *mechanically*, unlike `chain_output_json_problems`.
    That check can prove a fix from the chain's own text because a
    consumer already names the exact key it expects ("read project_id
    from ..."); nothing here names a key at all -- "the new version" is
    prose, not a commitment -- so inventing `applet_version_id` and
    appending a read for it would be exactly the guess this project has
    refused to make everywhere else a key name was not already stated.
    (`team.repair_dangling_version_chain` asks the pool to make that
    judgement instead, and verifies its answer against this same
    function before trusting it -- see that function's docstring for why
    a model, not a regex, is what closes this specific gap.)
    """
    problems: List[str] = []
    for i, dirname, producer in dangling_version_findings(steps):
        if producer:
            problems.append(
                "%s says \"...with the new version...\" but never reads "
                "a version id from any sibling's output.json -- %s's own "
                "request looks like it already creates one. The "
                "preferred fix (see LOCAL.md, \"the single most common "
                "way this goes wrong\") is to merge %s and %s back into "
                "one step -- \"push X as a new version of the applet "
                "...; and point the project ... at that new version\" --  "
                "since --api already closes over both calls in one "
                "slice and this is exactly the split it warns against. "
                "If they must stay separate, make %s explicitly promise "
                "to save it (\"save its id as applet_version_id in a "
                "new output.json...\") and make %s explicitly read it "
                "(\"read applet_version_id from ../%s/output.json\") "
                "instead of letting %s invent its own duplicate version"
                % (dirname, producer, producer, dirname, producer,
                   dirname, producer, dirname))
        else:
            problems.append(
                "%s says \"...with the new version...\" but never reads "
                "a version id from any sibling's output.json, and no "
                "earlier step in this chain even mentions a version -- "
                "name the real step and key this comes from before "
                "running this chain, or -- per LOCAL.md, \"the single "
                "most common way this goes wrong\" -- fold whichever "
                "step actually creates the version and this one into a "
                "single request instead of splitting them" % dirname)
    return problems


_DIR_MENTION_RE_CACHE: Dict[str, "re.Pattern"] = {}


def _bare_directory_mention_re(dirname: str) -> "re.Pattern":
    """A mention of `dirname` that is not part of an already-wired
    `../dirname/...` reference -- not preceded by `../` and not followed
    by `/`. Cached per dirname since a chain checks every (consumer,
    producer) pair and there is no reason to recompile the same pattern
    once per pair.
    """
    pat = _DIR_MENTION_RE_CACHE.get(dirname)
    if pat is None:
        pat = re.compile(r'(?<!\.\./)\b' + re.escape(dirname) + r'\b(?!/)')
        _DIR_MENTION_RE_CACHE[dirname] = pat
    return pat


def chain_dangling_directory_reference_problems(
        steps: Sequence[Tuple[str, str]]) -> List[str]:
    """A consumer naming another step's directory by its literal name --
    "update the applet created in 05_create_applet" -- is exactly as real
    a dependency as one phrased "read applet_id from
    ../05_create_applet/output.json". This project's own orchestration
    convention numbers and names every step's directory after what it
    produces specifically so a later request can refer back to it, so
    naming that directory is not incidental prose, it is a claim that
    this step's real output is needed. Nothing else catches this shape:
    `chain_output_json_problems` only fires on the literal
    `../dir/output.json` substring, and a bare directory mention contains
    neither `../` nor `output.json`, so it is invisible to every existing
    chain check.

    Measured live: `06_update_applet`'s own request read "update the
    applet created in 05_create_applet with this html" -- naming
    05_create_applet by name, with no `../05_create_applet/output.json`
    (or any other `../05_create_applet/...`) reference anywhere in the
    same request. `05_create_applet`'s own request, in turn, never
    promised to save `applet_id` anywhere either, so there was no fact in
    the whole chain either side could have wired this to even if the
    prose had been machine-readable. `chain_output_json_problems` and
    `chain_dangling_version_problems` both stayed silent -- neither
    pattern appears anywhere in either step's text -- and both steps
    generated cleanly. The actual failure only surfaced three rungs into
    `06_update_applet`'s own repair ladder, when an escalated attempt,
    faced with a real dependency it had no wired way to see, went
    looking for *something* plausible and read a `project_id` key
    (invented -- nothing in this chain or the API schema for creating an
    applet needs one) out of a bare local `output.json` that was never
    going to exist during generation, since generation runs before any
    step has actually executed for real.

    Scoped to fire only when `../other_dir/` does not appear anywhere
    else in the same request: a request that already reads
    `../05_create_applet/output.json` for one thing and separately
    mentions "05_create_applet" in a sentence explaining why is not this
    failure -- the dependency is genuinely wired, and the bare mention is
    just prose describing it. Only a directory that is named and never
    once referenced through a real `../dir/...` path anywhere in the same
    request is flagged.

    Never autofixed mechanically, for the same reason
    `chain_dangling_version_problems` is not: nothing here names a key,
    so inventing one (`applet_id`, guessed) would be exactly the kind of
    guess this project has refused to make everywhere else a key name was
    not already stated by the chain's own text.
    """
    problems: List[str] = []
    dirnames = [d for d, _ in steps]
    for dirname, request in steps:
        req = request or ""
        for other_dir in dirnames:
            if other_dir == dirname:
                continue
            if ("../%s/" % other_dir) in req:
                # Already wired to something in that step -- output.json,
                # a produced asset, whatever -- so any bare mention of
                # the same name elsewhere in this request is just prose
                # describing an already-real reference, not this failure.
                continue
            if _bare_directory_mention_re(other_dir).search(req):
                problems.append(
                    "%s's request names %s directly (\"...%s...\") with "
                    "no ../%s/... reference anywhere in the same request "
                    "-- if this step needs something %s produced, name "
                    "it explicitly (\"read <key>_id from "
                    "../%s/output.json\", or the real relative path for "
                    "a produced asset); a bare directory name is not "
                    "something generated code can read, so it has "
                    "nothing to build the dependency from and will "
                    "either invent a path that never resolves or invent "
                    "a value outright instead of failing loudly"
                    % (dirname, other_dir, other_dir, other_dir,
                       other_dir, other_dir))
    return problems


# A request that creates or updates an applet, keyed to that specific
# resource noun rather than a bare "create"/"update" verb list, for the
# same reason `_DANGLING_VERSION_RE` is keyed to "version" and not to
# every possible consuming verb: an unqualified verb match would fire on
# any create/update request in any chain, most of which have nothing to
# do with content. "applet" is specific enough to name the one resource
# on this API that is never just what it says it is -- creating or
# updating one always eventually needs real artifact content, whether or
# not the request that triggers it says so. The verb has to bind directly
# to "applet" (at most one determiner between them) rather than merely
# appear somewhere in the same clause -- "update the project with the new
# applet version" updates a *project*, mentioning an applet only as the
# thing being pointed at, and a looser gap matched that sentence too,
# reporting a project-update step as an applet-content step that had
# nothing to do with either.
_APPLET_CONTENT_ACTION_RE = re.compile(
    r'\b(?:create|creates|creating|update|updates|updating|push|pushes|'
    r'pushing|attach|attaches|attaching)\b\s+(?:the\s+|an?\s+|this\s+|'
    r'that\s+|its\s+)?applet\b',
    re.IGNORECASE)

# A sibling reference to a real content-shaped file -- the same
# extensions `_ASSET_EXT_RE` already treats as "a produced artifact",
# narrowed to the ones an applet's `artifact_url` could plausibly be
# built from, and required to already carry the `../dir/` qualifier
# `reads_sibling_output`/`unqualified_sibling_asset_refs` also key on --
# a bare filename here is `unqualified_sibling_asset_refs`'s problem to
# report, not evidence this check should stay quiet.
_CONTENT_SOURCE_RE = re.compile(
    r'\.\./[\w.\-]+/[\w\-]+\.(?:html?|css|js|mjs|md|txt)\b', re.IGNORECASE)


def content_source_reference(request: str) -> bool:
    """Does this request create or update an applet with nothing in it
    naming where the real content comes from.

    Measured live: "create an applet; save its id as applet_id in a new
    output.json..." has no way to know an intake page even exists. Two
    separate facts about this API make that request implicitly a content
    request anyway, whether or not it says so: `openapi.close_content_pushes`
    documents that a create endpoint taking only a URL is never the end
    of the story for a versioned resource, and a bare "create an applet"
    scores against `POST /api/applets/{applet_id}/versions` almost as
    strongly as it scores against `POST /api/applets` itself (see
    `openapi._demote_unrequested_nested_creates`, added for the identical
    live failure) -- so the CONTRACT ends up requiring a version
    regardless of the request's own wording. With nothing textual to
    read, a writer fabricates content instead: the live case shipped
    `data:text/html;base64,` of the literal string "Hello World" as a
    real applet version's artifact.

    A request already naming a real content asset anywhere in it
    (`../04_create_survey/survey.html`) is never flagged -- it already
    says where the content comes from, which is exactly what this check
    exists to demand.
    """
    req = request or ""
    if not _APPLET_CONTENT_ACTION_RE.search(req):
        return False
    return not _CONTENT_SOURCE_RE.search(req)


_CONTENT_PRODUCING_EXTS = frozenset(
    ["html", "htm", "css", "js", "mjs", "md", "txt"])


def content_source_findings(steps: Sequence[Tuple[str, str]]
                            ) -> List[Tuple[int, str, Optional[str]]]:
    """The structured facts behind `chain_missing_content_source_problems`:
    for every step that creates or updates an applet with no named
    content source, its index, its own dirname, and the dirname of the
    most likely producer -- an earlier step whose own request creates a
    content-shaped asset (the `04_create_survey` shape: "create a
    survey.html that ...", with no `../` in front of it, since that step
    is the producer and has no sibling to read from) -- or None if no
    earlier step creates one at all.
    """
    findings: List[Tuple[int, str, Optional[str]]] = []
    for i, (dirname, request) in enumerate(steps):
        if not content_source_reference(request):
            continue
        producer = None
        for earlier_dir, earlier_req in reversed(steps[:i]):
            for match in _ASSET_EXT_RE.finditer(earlier_req or ""):
                ext = match.group(1).rsplit(".", 1)[-1].lower()
                if ext in _CONTENT_PRODUCING_EXTS:
                    producer = earlier_dir
                    break
            if producer:
                break
        findings.append((i, dirname, producer))
    return findings


def chain_missing_content_source_problems(steps: Sequence[Tuple[str, str]]
                                          ) -> List[str]:
    """`content_source_reference`, run across a whole chain, naming the
    most likely already-built content producer for the message so an
    operator does not have to hunt for it by hand.

    Mechanically autofixable exactly when a producer was found --
    `autofix_chain_missing_content_source` copies the producer's own
    named filename into the consumer's request rather than guessing at
    one. This function is what is left calling that fix cannot resolve:
    a chain where no earlier step names a content-producing file at
    all, which really does need a real edit, because there is no fact
    anywhere in the chain to fix it from.
    """
    problems: List[str] = []
    for i, dirname, producer in content_source_findings(steps):
        if producer:
            problems.append(
                "%s creates or updates an applet but never says where "
                "its real content comes from -- %s's own request looks "
                "like it already creates one. Name the real file this "
                "step should read (\"read the html from "
                "../%s/<file>\"), or fold %s into %s so the step that "
                "has the content is the one that sends it, instead of "
                "leaving %s to fabricate placeholder content to satisfy "
                "the version it will otherwise be forced to create"
                % (dirname, producer, producer, dirname, producer,
                   dirname))
        else:
            problems.append(
                "%s creates or updates an applet but never says where "
                "its real content comes from, and no earlier step in "
                "this chain creates a content asset either -- name the "
                "real file this step should read before running this "
                "chain" % dirname)
    return problems


def autofix_chain_missing_content_source(steps: Sequence[Tuple[str, str]]
                                         ) -> Tuple[List[Tuple[str, str]],
                                                    List[str]]:
    """Repair what `chain_missing_content_source_problems` can prove is
    fixable from the chain's own text, the same move
    `autofix_chain_output_json` already makes for a dangling `_id` key,
    applied here to a dangling content reference instead.

    The earlier docstring calling this "not mechanically autofixable"
    was true of the general case -- there is genuinely no fact to
    invent a content source from when nothing earlier in the chain
    produces one -- but wrong about the one shape this project's own
    orchestration convention actually produces: `content_source_findings`
    already proves a producer exists by finding a content-shaped
    filename (`survey.html`) sitting in that producer's own request
    text, unqualified, exactly the way `04_create_survey`'s "create a
    survey.html that ..." names it. That filename is not a guess; it is
    the same fact `chain_output_json_problems`'s fix already copies for
    an `_id` key, copied here instead into a "read the <ext> for its
    content from ../producer/filename" clause appended to the consumer's
    own request.

    Still left alone, and still reported, whenever there is truly
    nothing to copy: no earlier step names a content-producing file at
    all. `chain_missing_content_source_problems` on the *returned* steps
    is exactly that remainder.
    """
    fixed_steps: List[Tuple[str, str]] = []
    findings = {i: (dirname, producer)
               for i, dirname, producer in content_source_findings(steps)}
    for i, (dirname, request) in enumerate(steps):
        req = request or ""
        finding = findings.get(i)
        if finding is not None:
            _, producer = finding
            filename = None
            if producer:
                for earlier_dir, earlier_req in reversed(steps[:i]):
                    if earlier_dir != producer:
                        continue
                    for match in _ASSET_EXT_RE.finditer(earlier_req or ""):
                        candidate = match.group(1)
                        ext = candidate.rsplit(".", 1)[-1].lower()
                        if ext in _CONTENT_PRODUCING_EXTS:
                            filename = candidate
                            break
                    break
            if producer and filename:
                ext = filename.rsplit(".", 1)[-1].lower()
                kind = "html" if ext in ("html", "htm") else ext
                req = req.rstrip()
                if req and not req.endswith((".", ";")):
                    req += ";"
                req += (" read the %s for the applet's content from "
                        "../%s/%s" % (kind, producer, filename))
        fixed_steps.append((dirname, req))

    problems = chain_missing_content_source_problems(fixed_steps)
    return fixed_steps, problems


def unqualified_sibling_asset_refs(request: str, root: Optional[str] = None
                                   ) -> List[str]:
    """Filenames this request names without a path, that are not sitting
    in this directory either -- the shape that put `survey.html` in a
    `main.py` that resolved it next to itself and failed against the real
    file, rather than failing to find it at all.

    This is deliberately *not* the check that was proposed and rejected
    for this: scanning sibling directories for a same-named file and
    treating a hit as "not run yet, skip the smoke check" solves the
    wrong problem. That heuristic is for `../01_create_project/output.json`
    -- a real path the code already got right, just too early to exist.
    Here the code never had a real path at all: `survey.html` typed bare
    into a request becomes `open("survey.html")` or an
    `__file__`-relative equivalent, which resolves *successfully* against
    the wrong directory forever, on every future run, whether or not the
    sibling step has already run. There is no "run it again later and it
    works" for this one, so the fix cannot be a gate that waits -- it has
    to be the request saying where the file actually is, the same way
    `../01_create_project/output.json` already does it for a JSON file
    only because the literal relative path was typed into the request
    to begin with.

    So this checks the one fact decidable before any of that: has the
    request named a file, with an extension a produced artifact would
    have, that is neither already `../`-qualified (`reads_sibling_output`
    covers that shape and this skips it) nor sitting in `root` right now.
    That is exactly the gap between "this request implies a cross-step
    file" and "the request actually says which step" -- nothing on this
    side of the request can invent the missing directory, so the result
    is reported as something for the operator to fix in the request text,
    not silently patched.

    Best-effort like every other check here: `root=None` (no directory to
    check against, e.g. before a project exists) skips the local-file test
    and reports every bare asset name, which is the safe direction for a
    warning that costs nothing if ignored.
    """
    found: List[str] = []
    for match in _ASSET_EXT_RE.finditer(request or ""):
        name = match.group(1)
        # A bare "output.json" is not this shape at all -- it is this
        # step's own file, per the exact convention
        # `_OUTPUT_JSON_WRITE_RE` already encodes ("save its id to
        # output.json"). Measured live: this fired on that literal,
        # recommended phrasing, on nearly every step of every real
        # chain -- constant false-positive noise that drowns out the
        # one case this check exists for (a genuinely bare
        # `survey.html`-style reference). The dedicated
        # `_OUTPUT_JSON_READ_RE`/`_OUTPUT_JSON_WRITE_RE` machinery
        # already covers every real problem shape for this specific
        # filename; this generic asset check has nothing to add for it.
        if name.lower() == "output.json":
            continue
        # Already has its own relative path one token to the left --
        # `../foo/bar.html` -- so `reads_sibling_output` already covers
        # this reference; do not warn about a path that is already
        # qualified.
        start = match.start(1)
        prefix = (request or "")[max(0, start - 40):start]
        if re.search(r'\.\./[\w.\-/]*$', prefix):
            continue
        if root is not None and os.path.isfile(os.path.join(root, name)):
            continue
        if name not in found:
            found.append(name)
    return found


_NAMES_MULTIPLE_FILES_RE = re.compile(
    r'\bclient\.py\b|\bapi_client\.py\b|\btwo files\b|\bseparate files\b')


def wants_multi_file(request: str) -> bool:
    """Does the request itself ask for more than one file.

    A request naming a specific second file (`client.py`, an explicit "two
    files") is a real signal the operator wants the planner's multi-file
    split -- auto-forcing `--single-file` over that would silently ignore
    an explicit ask, which is worse than leaving the planner's default
    (usually a client/main split anyway) alone. Absence of this is not
    proof multiple files are unwanted; it only means nothing here overrides
    the chained-single-step default.
    """
    return bool(_NAMES_MULTIPLE_FILES_RE.search(request or ""))


def low_parallelism_note(request: str,
                          components: Sequence["Component"]) -> Optional[str]:
    """A serial chain-link masquerading as a job worth this whole pipeline.

    Every mechanism this project builds -- parallel writers, the contract
    broadcast, endpoint closure, the mock, the repair loop -- earns its cost
    across *several files written at once*. A plan with exactly one
    component has none of that to amortise against: there is nobody else to
    run in parallel with, and the planning/contract/gate ceremony is pure
    overhead on top of whatever a single file costs to write directly.

    That shape is invisible from inside any one `do` call, because each
    invocation is independent (see `session.py`) -- but a request that reads
    a sibling directory's output (`../01_create_project/output.json`, the
    exact form this project's own guidance tells a multi-step chain to use)
    is naming itself as one link in a strictly serial sequence, which can
    never gain from the pool's parallelism no matter how the plan is shaped.
    Measured live: a single-component step in exactly this shape took 8m15s
    to fail six attempts, a full plan restart, and 5m20s more to "pass" --
    for a file that calls one REST endpoint.

    Advisory only, on purpose, and mechanical rather than asked of the
    planner: prose telling a *writer* not to do something has repeatedly
    been ignored on the exact request that motivated the ask (the stale
    contract clause, the entry-point rename). This is not that -- it tells
    the *operator*, off two facts already sitting in the finished plan and
    the request text, so acting on it is a choice rather than a hope.
    """
    if len(components) != 1:
        return None
    if not reads_sibling_output(request):
        return None
    return (components[0].path if components else "the component")


def plan_entry_point(components: Sequence["Component"]) -> Optional[str]:
    """Which component's path this plan actually runs from, independent of
    whether the CONTRACT text happens to say so this round.

    `ownership_note` telling every other file "do NOT define main" depended
    entirely on the planner having phrased a `path.py owns ... main()`
    clause in the CONTRACT -- prose the planner is not obliged to repeat
    every time it replies. Measured live: an otherwise-identical request,
    replanned, produced a CONTRACT with no `main` clause at all, and the
    component that got no prohibition duplicated a full `main()` of its own
    right alongside `main.py`'s real one. The dependency graph already says
    the same fact structurally -- an entry point is the file nothing else
    depends on, and every other component in this plan depends on it
    (directly or by wiring) rather than the reverse -- so this reads it off
    the plan itself instead of trusting the CONTRACT to have mentioned it.

    A literal `main.py` is trusted outright, matching the naming convention
    `DECOMPOSE_PROMPT` asks for by default. Failing that, a plan with
    exactly one component nothing else depends on, and which itself depends
    on something, is unambiguous. Anything else -- no components, more than
    one sink, or a lone file with no dependencies at all -- returns None
    rather than guessing, because a wrong guess here forbids a real method
    name package-wide, which is worse than saying nothing.

    Two things about the checks above were wrong for years and only ever
    surfaced through a caller other than `ownership_note`. `by_name` is
    keyed by each component's short *name* ("main", "main_script",
    whatever the planner or `--single-file` called it) -- `"main.py" in
    by_name` was checking whether any component is literally *named*
    the string "main.py", which essentially never happens, rather than
    whether any component's *path* is "main.py". And the "lone file with
    no dependencies" exclusion, read literally, also fires on a plan
    that has exactly one component *in total* -- there is no second
    file it could possibly be confused with, so requiring `sinks[0].deps`
    to be truthy there is not caution, it is a wrong answer to a
    question that was never ambiguous. Measured live:
    `resolve_run_target` -- built specifically so `team do --retry --run`
    can find the one file to execute for a single-component,
    `--single-file`/`--api` chain step -- called this function on exactly
    that shape (one component, no deps, path `main.py`, name `main`) and
    got `None` both times: the name check missed because it was checking
    the wrong dict, and the sink check missed because a lone total
    component's `deps` is trivially empty. `--retry --run` printed
    "no single obvious entry point" and returned 1 without ever
    re-running the file for real, so a step's own real-world failure
    (a `KeyError` from a sibling's `output.json`) was reported as fixed
    by a dependency-repair pass that, in fact, never ran anything.
    """
    if not components:
        return None
    # Nothing else in this plan exists to be confused with the only
    # component in it -- this is the common shape for a retried,
    # per-directory chain step, and it is never ambiguous.
    if len(components) == 1:
        return components[0].path
    by_path = {c.path: c.name for c in components}
    if "main.py" in by_path:
        return "main.py"
    depended_on = {dep for c in components for dep in c.deps}
    sinks = [c for c in components if c.name not in depended_on]
    if len(sinks) == 1 and sinks[0].deps:
        return sinks[0].path
    return None


_REQUESTED_ENTRY_RE = re.compile(
    r"runnable entry point is ([A-Za-z0-9_\-]+\.py)\b", re.I)


def enforce_requested_entry_point(components: List["Component"],
                                  request: str) -> Optional[Tuple[str, str]]:
    """A request naming its own entry point file, verbatim, is not an
    aspiration -- it is the exact filename every downstream orchestration
    script is going to look for. `ensure_entry_point` already covers a
    multi-file plan that produces nothing runnable at all; it deliberately
    leaves a *single*-component plan untouched, since there is nothing to
    join a lone file to -- which is exactly the gap this fills: a
    one-file plan that runs, passes every gate, and simply picked a
    different name for its one file.

    Measured live: "create an applet; save its id ... ; the runnable
    entry point is main.py" -- a single-component request with no sibling
    path to read, so the `--single-file` auto-detection in `team.py`
    (which forces `--out main.py`) never triggers for it -- came back
    named `applet_creator.py`. `generate.sh`'s own boilerplate treats a
    missing `main.py` as "no main.py -- skipping", which is the *correct*
    behaviour for a genuinely static deliverable like `survey.html`, so
    the step was silently never run, the applet was never created, and
    the project was never assigned one -- three steps and a "no applet
    configured for production" error later, with nothing in between ever
    saying why.

    So a request's own literal "the runnable entry point is X" is read
    back and enforced mechanically, the same way an operator's own exact
    print-format wording already is (`reinforce_output_labels`) rather
    than trusted to have been followed -- prose asking a writer to pick
    a specific name has already been shown, repeatedly, not to survive on
    its own. Scoped to the unambiguous cases only: a single-component plan
    (nothing else it could be confused with), or a multi-component plan
    whose real entry point `plan_entry_point` already names with
    confidence -- never a guess at which of several files was "supposed
    to" be the one.
    """
    m = _REQUESTED_ENTRY_RE.search(request or "")
    if not m or not components:
        return None
    wanted = m.group(1)
    if any(os.path.basename(c.path) == wanted for c in components):
        return None  # already named correctly -- nothing to do

    if len(components) == 1:
        target = components[0]
    else:
        entry_path = plan_entry_point(components)
        if entry_path is None:
            return None
        target = next((c for c in components if c.path == entry_path), None)
        if target is None:
            return None

    old_path = target.path
    old_name = target.name
    new_dir = os.path.dirname(old_path)
    new_path = os.path.join(new_dir, wanted) if new_dir else wanted
    new_name = os.path.splitext(wanted)[0]

    target.path = new_path
    target.name = new_name
    old_base = os.path.basename(old_path)
    for c in components:
        if old_name in c.deps:
            c.deps = [new_name if d == old_name else d for d in c.deps]
        if c.contract:
            c.contract = c.contract.replace(old_base, wanted)
    return (old_path, new_path)


def default_api_entry_point(components: List["Component"]
                            ) -> Optional[Tuple[str, str]]:
    """The `--api` case above's own precondition, run when the request
    never said the magic sentence at all.

    `enforce_requested_entry_point` only fires when the request contains
    the literal phrase "the runnable entry point is X" -- which covers a
    request that remembered to say it, and does nothing for one that
    didn't. Measured live: a step generated under `--api maven` with no
    such phrase in its request text at all ("create a project; save its
    id in this directory's own output.json") planned as one component,
    correctly, and that component came back named `project_creator.py` --
    a perfectly reasonable name for a model to choose on its own, and
    exactly the one name `generate.sh`'s own `if [ -f main.py ]` check
    will never find, silently skipping the step the same way the
    verbatim-phrase case already did before that fix landed.

    `--api` is the narrower, safer signal to key this off than "no
    request said main.py": every `--api` component exists to execute a
    real request against a real service (the mock stands up specifically
    so `gate_smoke` can run it), so there is no legitimate case under
    `--api` for a single component that is a pure library never meant to
    run on its own -- unlike a bare `team do "write a slugify function"`,
    where forcing every one-file answer into `main.py` would be a real
    regression, not a fix. So this is called only when `--api` was given,
    immediately after `enforce_requested_entry_point` found nothing to
    do, and only for a single-component plan, the one shape neither
    `ensure_entry_point` (multi-component only) nor the verbatim check
    above (needs the literal phrase) already covers.
    """
    if not components or len(components) != 1:
        return None
    target = components[0]
    old_path = target.path
    if os.path.basename(old_path) == "main.py" or not old_path.endswith(".py"):
        return None
    new_dir = os.path.dirname(old_path)
    new_path = os.path.join(new_dir, "main.py") if new_dir else "main.py"
    target.path = new_path
    target.name = "main"
    return (old_path, new_path)


_COUNT_RE = re.compile(r"\b(\d+)\s+([a-z]+s)\b")


def required_counts(text: str) -> Dict[str, int]:
    """How many of something the request explicitly pins a number to, e.g.
    "uploads 3 units to it" -> {"units": 3}.

    Measured live: exactly that request produced a task with 2 units
    instead of 3, and every gate passed, because "the mock returned 200"
    says nothing about how many rows were in the body that earned it --
    the same blind spot `gate_smoke` had for missing print labels before
    `output_labels` existed, just one level deeper (in the request body
    rather than the printed output).

    Deliberately narrow to a digit immediately followed by a *plural*
    noun. "a 3 question survey" (singular "question") does not count as a
    request for 3 of anything an API call could be checked against --
    there is no `question` resource, and the singular form is exactly the
    signal that this number describes something else (how many questions
    the page asks) than an item count. A false match here costs nothing
    downstream anyway: `mockapi.count_problems_for` only ever reports a
    problem for a noun that also matches an actual call it saw, so
    "3 seconds" or "3 minutes" elsewhere in a request is silently ignored
    rather than becoming a spurious requirement.
    """
    counts: Dict[str, int] = {}
    for m in _COUNT_RE.finditer(text):
        n = int(m.group(1))
        if n > 0:
            counts[m.group(2).lower()] = n
    return counts


_BULK_VERB_RE = re.compile(
    r"\b(?:upload|uploads|uploading|add|adds|adding|import|imports|"
    r"importing|insert|inserts|inserting|attach|attaches|attaching|"
    r"submit|submits|submitting|push|pushes|pushing)\s+"
    r"(?:the\s+|these\s+|those\s+|new\s+|some\s+|all\s+)?"
    r"(\d+\s+)?([a-z]+s)\b", re.IGNORECASE)


def implied_nonempty_counts(text: str) -> Set[str]:
    """A bulk-action verb naming a plural resource with no number attached
    implies "some", never "none" -- "upload units to the task" is not
    satisfied by a payload with zero units in it, even though nothing in
    the sentence pins an exact count for `required_counts` (above) to
    check against.

    Measured live: exactly that request -- "read task_id from
    ../02_create_task/output.json; upload units to the task" -- produced a
    generated file that posted `{"units": []}` to the real import endpoint,
    with its own comment reading "Placeholder: In a real scenario, this
    list would be populated with unit data." The call succeeded (an empty
    array is a valid payload), `gate_smoke` saw no crash, and
    `required_counts` had nothing to check because the request never named
    a number. The task was left with zero units and the run reported every
    gate clean; the orchestration log even printed "Successfully initiated
    unit import" for a call that imported nothing.

    Deliberately a floor, not a target, and that asymmetry is the whole
    reason this is safe to make blocking-adjacent (`mockapi.
    zero_problems_for` only ever fires on a total of exactly zero): unlike
    `required_counts`, this can never reject a real, non-empty payload no
    matter how many items it actually carried, so it cannot be wrong about
    a request that really did just say "units" with no number attached.
    Callers should exclude any resource `required_counts` already pins a
    real number to -- that check is stricter and already covers those; this
    only fills the gap where no number was ever given at all.
    """
    words: Set[str] = set()
    for m in _BULK_VERB_RE.finditer(text):
        if m.group(1):
            continue  # a real number is here; required_counts handles it
        word = m.group(2).lower()
        if len(word) <= 2:
            continue
        words.add(word)
    return words


def backfill_entry_point_deps(components: Sequence["Component"]) -> Optional[str]:
    """An entry point with zero declared dependencies, next to a CONTRACT
    naming other files' owned symbols, is not a plan decision -- it is the
    planner's own DEPS field going missing from one COMPONENT block while
    the rest of the same reply (the CONTRACT) still names the files that
    own what an orchestrating entry point obviously has to call.

    Measured live: a 4-component Maven plan (api_client.py, utils.py,
    survey.html, main.py) parsed with every single component's `deps` at
    []. The CONTRACT read `api_client.py owns Client(...)` and `utils.py
    owns get_stamp()` -- both real, both meant to be called from
    somewhere -- and `main.py`'s own SPEC said, in English, "initializes
    the Client". Nothing was wired to anything. `dependency_context`
    handed `main.py` no real source, and `dep_modules`/`dep_paths` -- fed
    from the same empty `deps` -- return `{}`/`[]`, so `gate_uses` and
    `gate_interfaces`, the exact mechanical checks built to catch "defined
    your own version instead of importing", never ran at all: an empty
    dict is falsy, and `gate_uses` is gated on `if ... and uses and
    python`. The entry point was free to invent a self-contained mock
    client that fabricated plausible-looking IDs and made no network call
    whatsoever, and every gate -- including the ones built for exactly
    this -- reported clean, because there was no declared edge left for
    any of them to check.

    This is decidable from the planner's own reply without asking it
    again: `contract_owners` already reads which file the CONTRACT says
    owns each shared name, and that information survives in the same
    response that produced the broken `deps` field, just in a different
    clause. Restricted to the entry point, and to a *totally* empty deps
    list: a component that already named even one real dependency made a
    real decision that should not be second-guessed, and a component that
    never orchestrates anything (one file, no CONTRACT, the dancing-shapes
    case) has no owner clauses to backfill from, so this never fires for
    it.
    """
    entry_path = plan_entry_point(components)
    if entry_path is None:
        return None
    entry = next((c for c in components if c.path == entry_path), None)
    if entry is None or entry.deps:
        return None
    owner_paths = {p.strip() for p in contract_owners(entry.contract or "").values()}
    added = [c.name for c in components
             if c is not entry and c.path in owner_paths]
    if not added:
        return None
    entry.deps = added
    return entry.path


def contract_owners(contract: str) -> Dict[str, str]:
    """Which file the plan's contract says each shared name belongs to.

    The planner is asked to write lines like `shapes.py owns Shape(x, y)`, and
    that sentence is the only place the intended owner is ever stated. Without
    reading it, ownership has to be guessed from the dependency arrows, and the
    guess is backwards whenever the wrong file is the one that duplicated: a
    live run had `constants.py` declare `Shape` when the contract gave it to
    `shapes.py`, and because `shapes` depends on `constants` the repair pass
    rewrote the rightful owner to import from the offender. It failed twice and
    gave up, which is what it should do -- the instruction was impossible.
    """
    owners: Dict[str, str] = {}
    for path, names in _OWNS.findall(contract or ""):
        # Everything after `with` describes members of the symbol just named --
        # `Shape(...) with update(dt) and draw(surface)` -- not further
        # top-level names. Reading them as owned made every other file be told
        # "do NOT define update, draw", which forbids two ordinary method
        # names across the whole package.
        names = re.split(r"\bwith\b", names, 1)[0]
        # Signatures, annotations and parentheticals carry argument and type
        # names that nobody owns: `COLORS: list[tuple[int, int, int]]` yielded
        # `int` as a symbol, and `constants.py` was solemnly informed that it
        # was responsible for defining it.
        stripped = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", names)
        for chunk in re.split(r",|\band\b", stripped):
            # An annotation names a type, not an owned symbol, so only the part
            # before the colon can be a declaration.
            match = _IDENT.search(chunk.split(":", 1)[0])
            if match and match.group(1) not in _NOT_SYMBOLS:
                owners.setdefault(match.group(1), path.strip())
    return owners


def ownership_note(contract: str, path: str,
                   entry_point: Optional[str] = None) -> str:
    """What this one file may define, and what it must leave to its neighbours.

    The contract is broadcast to every writer so that unrelated files agree on
    a shared type without waiting for each other. Broadcasting it unqualified
    backfires: a complete class signature reads as an instruction to write that
    class, and the file that owns nothing but a colour list writes the whole
    program. Measured -- a `constants.py` asked for `COLORS` came back with
    `Shape`, `main()`, and a full pygame loop, because the contract handed it
    `Shape(x, y, size, color, ...)` and said "use these exact names".

    Naming the owner turns the same sentence from a specification into a
    prohibition, which is what it was always meant to be.

    `main` used to be only as protected as the CONTRACT text happened to
    say it was: the prohibition existed only if the planner had phrased a
    `path.py owns ... main()` clause that round, and a replan of the exact
    same request produced a CONTRACT with no such clause, so the next file
    over felt free to write its own `main()` alongside the real one.
    `entry_point`, when given, is read off the plan's own dependency graph
    (`plan_entry_point`) rather than off prose that may or may not have
    been repeated, and backs `main` the same way a CONTRACT clause would --
    but only when the CONTRACT did not already say something else, so an
    explicit clause is never overridden by the structural guess.
    """
    owners = dict(contract_owners(contract))
    if (entry_point and "main" not in owners
            and os.path.normpath(entry_point) != os.path.normpath(path)):
        owners["main"] = entry_point
    if not owners:
        return ""
    here = os.path.normpath(path)
    mine = sorted(s for s, p in owners.items() if os.path.normpath(p) == here)
    theirs: Dict[str, List[str]] = {}
    for symbol, owner in sorted(owners.items()):
        if os.path.normpath(owner) != here:
            theirs.setdefault(owner, []).append(symbol)
    lines = []
    if mine:
        lines.append("You define %s. Use the exact signature above."
                     % ", ".join(mine))
    for owner, symbols in theirs.items():
        lines.append("Do NOT define %s -- %s does. If this file needs one, "
                     "import it; if it does not, do not mention it at all."
                     % (", ".join(symbols), owner))
    return "\n".join("  " + line for line in lines)


def redefined_symbols(components: List["Component"], root: str
                      ) -> List[Tuple[str, str, str]]:
    """Names a component redeclares that one of its dependencies already owns.

    Per-file gates cannot see this: a file that duplicates a dataclass instead
    of importing it parses, imports, and passes its own tests. It is only wrong
    in relation to another file, so it needs a check that looks at the pair.
    Purely mechanical -- two definitions of one name is a fact.
    """
    import ast as _ast

    def top_level(path: str) -> Dict[str, int]:
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            return {}
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                tree = _ast.parse(fh.read())
        except (OSError, SyntaxError):
            return {}
        out: Dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                 _ast.ClassDef)):
                out[node.name] = node.lineno
        return out

    def kinds(path: str) -> Dict[str, str]:
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            return {}
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                tree = _ast.parse(fh.read())
        except (OSError, SyntaxError):
            return {}
        return {node.name: ("class" if isinstance(node, _ast.ClassDef)
                            else "function")
                for node in tree.body
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                     _ast.ClassDef))}

    defs = {c.name: top_level(c.path) for c in components}
    by_name = {c.name: c for c in components}
    declared = contract_owners(
        next((c.contract for c in components if getattr(c, "contract", "")), ""))
    by_path = {os.path.normpath(c.path): c for c in components}
    clashes: List[Tuple[str, str, str]] = []
    seen = set()

    def stated_owner(symbol: str) -> Optional["Component"]:
        path = declared.get(symbol)
        return by_path.get(os.path.normpath(path)) if path else None

    def record(duplicate: "Component", symbol: str,
               owner: "Component") -> None:
        """Rewrite the file that should not have the symbol, not the one that should."""
        stated = stated_owner(symbol)
        if stated is not None and stated.name != owner.name:
            if stated.name == duplicate.name:
                duplicate, owner = owner, duplicate
            else:
                owner = stated
        if duplicate.name == owner.name or (duplicate.path, symbol) in seen:
            return
        clashes.append((duplicate.path, symbol, owner.path))
        seen.add((duplicate.path, symbol))

    for c in components:
        mine = defs.get(c.name, {})
        for dep in c.deps:
            owner = by_name.get(dep)
            if not owner:
                continue
            for symbol in defs.get(dep, {}):
                # A dependency's demo entry point is not something a dependent
                # should be importing, so sharing the name is not a clash.
                if symbol in _ENTRY_POINTS or symbol.startswith("_"):
                    continue
                if symbol in mine:
                    record(c, symbol, owner)

    # Components with no dependency between them can still define the same
    # name, and that is just as broken while being invisible to the pass above.
    # Observed live: a types module described a shape as a dict while an
    # unrelated maths module declared its own `ShapeData` dataclass, so the
    # component joining them invented a throwaway class to bridge the two and
    # hardcoded a field to a constant. Every file passed its own gates and the
    # program did nothing.
    #
    # Ownership goes to whichever component is more foundational -- fewest
    # dependencies, then plan order -- because that is the one others should be
    # importing from.
    #
    # Between unrelated files, only a duplicated *class* counts. Two files each
    # defining `main()` in a demo block is normal and universal -- every
    # component this system writes has one -- and flagging it produced three
    # confident findings that were all wrong, then spent two repair rounds
    # rewriting working files to chase them. Shared type names are the real
    # smell; shared helper names are just Python.
    order = {c.name: i for i, c in enumerate(components)}
    symbol_kind = {}
    for c in components:
        symbol_kind[c.name] = kinds(c.path)
    holders: Dict[str, List["Component"]] = {}
    for c in components:
        for symbol in defs.get(c.name, {}):
            holders.setdefault(symbol, []).append(c)
    for symbol, owners in holders.items():
        if len(owners) < 2:
            continue
        if symbol in _ENTRY_POINTS or symbol.startswith("_"):
            continue
        if not all(symbol_kind.get(c.name, {}).get(symbol) == "class"
                   for c in owners):
            continue
        ranked = sorted(owners, key=lambda c: (len(c.deps), order[c.name]))
        source = stated_owner(symbol) or ranked[0]
        for other in ranked:
            if other.name != source.name:
                record(other, symbol, source)
    return clashes
