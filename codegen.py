#!/usr/bin/env python3
"""Write code on the local pool, and prove it before keeping it.

The economics here are the whole point: frontier tokens cost money and local
tokens cost time. So the frontier model should spend its budget on the parts
that are actually hard -- deciding what to build, what the interfaces are, what
counts as correct -- and the 26b should write the body.

What makes that safe is not trusting the 26b. Nothing it produces is kept
unless it survives checks that do not involve asking a language model anything:

  - it must parse
  - it must import without raising
  - it must pass whatever command you name as the test

Failures are fed back verbatim -- the real traceback, not a summary -- and it
tries again. Only output that clears every gate is written to disk. If the
loop cannot get there, the original file is restored and you are told plainly,
rather than being handed something that looks finished.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse
from typing import (Any, Callable, Dict, FrozenSet, List, Optional, Sequence,
                    Set, Tuple)

import crossref
import intent
import swarm
import usage

GENERATE_PROMPT = """Write a complete {language} file.

{spec}

Rules:
- Output the entire file in one fenced ```{fence} block, and nothing outside it.
- No commentary, no explanation, no partial snippets, no "..." placeholders.
- The file must be complete and work as written.
- Standard library only unless the spec names a dependency.
- If the spec names a specific file path to read a value from (an
  `output.json` written by an earlier step, a sibling file, anything with
  an actual path in it), read that file directly -- open it, parse it,
  use the value. Do not require an environment variable instead: an env
  var is only for a value nothing on disk already has, and inventing one
  for a value the spec already told you where to find makes the file
  fail with "environment variable not set" against input that was sitting
  on disk the whole time.
- If this task calls a real API, make the real HTTP request with the real
  library (`requests.get/post/put/patch/delete(...)`, or the shared client
  class this component depends on) against the real base URL read from the
  environment. Never write your own mock, fake, stub, or simulated client
  -- a hand-written class or function that prints something like
  "MOCK POST ..." and returns hardcoded data instead of actually calling
  `requests`. If something below mentions a mock API or a mock server,
  that is test infrastructure for whoever runs this file; it is invisible
  to the code you are writing, and you must never write a mock yourself,
  under any name, to stand in for it. This applies even if you are unsure
  of the exact response shape -- make the real call and handle a real
  response, do not invent one.
- Never fabricate a platform *identifier* -- a project id, task id, applet
  id, version id, unit id, or anything else the real API assigns. Those
  come only from a real API response you just received or a file on disk
  the spec named. An id you made up (`"unit-id-1"`, `"task-123"`,
  a zero-filled uuid) is never acceptable, because the next step, or the
  real service itself, treats it as if it were genuine and fails or
  silently corrupts state on it later, far from where the invented value
  was written.
- Content is different from an identifier, and a task whose whole point is
  generating or uploading placeholder/dummy/test content (a data-collection
  task with no real source data given) is asking you to synthesize that
  content -- do it, do not refuse or leave it out. The part that has to be
  real is the *shape*: build the request body from the actual schema given
  to you above (the real field names and types for whatever you are
  creating), not a shape you guessed. `{{"unit_id": "..."}}` invented for
  an endpoint whose real schema wants full record content is wrong for the
  same reason a fabricated id is wrong -- it is a guess standing in for
  something the document already specifies exactly.
- If you write `if __name__ == "__main__":`, its body must actually call
  whatever this file defines to do its real job -- `main()` if you wrote
  one, or the class instance and method that performs the work otherwise.
  A guard containing only `pass`, a comment, or a docstring means running
  this file does nothing at all: no request is sent, output.json is never
  written, and every later step reading this file's output fails with a
  confusing error about a missing file, not about this one. This is true
  even if you also had to leave an unresolved naming conflict elsewhere in
  the file -- an empty guard is never an acceptable way to sidestep it.
- Never read a business id (a project id, task id, applet id, version id,
  and so on) from `os.environ`. An id comes only from a file on disk this
  spec named (a sibling step's `output.json`) or a real API response you
  just received -- never from the environment, which this project's own
  orchestrator only ever populates with `API_BASE_URL`/`API_KEY` and
  nothing else. If you find yourself wanting an id that is not available
  from either of those two places, that is a sign the endpoint you are
  calling does not actually require it -- re-check its real required
  fields in the schema above rather than inventing an environment
  variable nothing will ever set.
- Do not add a lookup call (a GET to re-fetch something) to obtain a value
  you already have from your own inputs. If a value the spec asked you to
  use is already sitting in a variable you read from a sibling output.json
  or a prior response in this same file, use it directly; adding an extra
  read step to re-derive the same value from a different, less reliable
  source (a field on a parent object that may not be populated yet) only
  creates a new way for this file to fail for no reason.
{extra}"""

REPAIR_PROMPT = """The file you wrote does not work. Here is exactly what
happened when it was run.

FAILING STAGE: {stage}

ERROR:
{error}
{avoid}

YOUR CURRENT FILE:
```{fence}
{code}
```

The original task is unchanged. Everything it asked for still has to be
true of the corrected file, not just the one line the error names:

{spec}
{extra}

Fix the specific cause of the error above without dropping anything the
task above still requires. Do not rewrite the parts that work and do not
change the interface unless the error requires it.

Output the entire corrected file in one fenced ```{fence} block, and nothing
else."""


def avoid_repeats(history: List[Dict[str, Any]], current: str) -> str:
    """A standing list of this component's own past mistakes, so fixing
    today's error does not casually reintroduce yesterday's.

    Every attempt already reports its own `error`, and `history` already
    accumulates every one of them across the whole retry budget -- but
    nothing downstream ever read `history` back. `REPAIR_PROMPT` saw only
    the single most recent failure and the single most recent draft, so a
    whole-file rewrite aimed at fixing that one had nothing telling it to
    preserve a fix from three attempts ago that the current draft happens
    to still have right. Small models regenerating an entire file from
    scratch each attempt drift, and nothing stopped that drift from landing
    back on an error already diagnosed and already corrected once.

    Measured live: a `main.py` depending on an already-finished
    `api_client.py` cycled through 6 attempts, a full whole-loop restart,
    and 6 more, ending on the exact `imports a name api_client.py does not
    define` defect `gate_interfaces` had already caught and fed back
    earlier in the very same run. The mechanical check was never the gap --
    it fires every time the mistake reappears -- the gap was that nothing
    told the next attempt the mistake had a history.

    Deduplicated on each error's own first line, which is the part that
    actually names the mistake; a multi-line traceback or finding repeats
    structure underneath that is not itself informative here. The current
    failure is excluded on purpose -- it is already the attempt's own ERROR
    field above, and listing it a second time as something to "avoid"
    would only be confusing, not additive.
    """
    def headline(err: str) -> str:
        for line in (err or "").splitlines():
            line = line.strip()
            if line:
                return line
        return ""
    current_head = headline(current)
    seen = set()
    lines: List[str] = []
    for h in history:
        head = headline(h.get("error", ""))
        if not head or head == current_head or head in seen:
            continue
        seen.add(head)
        lines.append(head)
    if not lines:
        return ""
    return ("This file has also already been wrong in these OTHER ways "
           "during earlier attempts on it, each already corrected once. "
           "Fixing the error above must not bring any of these back:\n  - "
           + "\n  - ".join(lines))

# Any language tag, not just Python. A writer asked for HTML answers in a
# ```html block, and a pattern that only knew ```python matched nothing --
# read by the loop as "the reply contained no code block", which spends a
# repair attempt on a file that was written correctly the first time.
FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)

# What to call the file when asking for it, and what to write on the fence.
# A fence saying `python` over markup is a claim about the content that a
# small model will follow rather than override -- the same reasoning
# `attach.py` uses when it picks a fence from the extension.
LANGUAGES = {
    ".py": ("Python", "python"),
    ".js": ("JavaScript", "javascript"),
    ".mjs": ("JavaScript", "javascript"),
    ".cjs": ("JavaScript", "javascript"),
    ".html": ("HTML", "html"),
    ".htm": ("HTML", "html"),
    ".css": ("CSS", "css"),
    ".json": ("JSON", "json"),
    ".md": ("Markdown", "markdown"),
    ".sql": ("SQL", "sql"),
    ".csv": ("CSV", "csv"),
    ".txt": ("plain text", "text"),
}


def language_for(path: str) -> Tuple[str, str]:
    """(what to call it, what to write on the fence) for this file."""
    return LANGUAGES.get(os.path.splitext(path)[1].lower(), ("Python", "python"))


BACKUP_DIR = os.path.join(swarm.HERE, ".swarm", "backups")


def backup_path(out_path: str) -> str:
    """Where a file's pre-generation copy goes, and stays.

    Outside the project tree, for the same reason `session.py` keeps
    transcripts beside the pool: a checkout gains nothing, several projects
    stay separate, and a directory listing does not fill with `.bak` files
    that then have to be explained to every later reader.

    Never pruned. Generation replaces whole files rather than patching them,
    so the previous content is unrecoverable from the result, and the
    realistic operator does not commit before every run -- which makes this
    the only undo that exists. A few KB per generated file is not a cost worth
    weighing against that.

    The directory name keeps the project's own name in it rather than only a
    hash, because a backup nobody can find by looking is not a backup.
    """
    full = os.path.abspath(out_path)
    key = hashlib.sha1(full.encode("utf-8")).hexdigest()[:8]
    project = os.path.basename(os.path.dirname(full)) or "root"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(BACKUP_DIR, "%s-%s" % (project, key),
                        "%s.%s" % (stamp, os.path.basename(full)))


def backups_for(out_path: str) -> List[str]:
    """Every kept copy of one file, newest first."""
    folder = os.path.dirname(backup_path(out_path))
    try:
        names = sorted(os.listdir(folder), reverse=True)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names]


FAILURE_DIR = os.path.join(swarm.HERE, ".swarm", "failures")


def save_failed_attempt(out_path: str, attempt: int, stage: str,
                        code: str, error: str) -> None:
    """Keep the actual source of a failed attempt, not just the condensed
    one-line summary `team.py`'s `history_trail` shows an operator.

    Every gate failure this module produces has always had two lives: the
    full text (`failure`, sometimes several KB with a mock's own rejected-
    request detail attached) lived only inside `history` for the duration
    of this one `generate_file` call, and `why_it_failed` further
    condensed even that down to one exception line before it ever reached
    a screen or a saved plan. On total failure the file itself is removed
    or restored to whatever backup preceded this call (see the return path
    below), so a component that failed four different ways on four
    different rungs left literally nothing on disk to look at afterward --
    "bulk debug what made a run hang" had a timeline (via
    `history_trail`'s per-rung durations) but no source to go with it,
    which is exactly backwards for tracking down a mechanical bug like the
    live `io.UnsupportedOperation: not readable` case this project's own
    gates were extended to catch afterward, once and only because an
    operator happened to still be watching the live log when it occurred.

    Kept forever, like `backup_path`'s own backups, and named the same
    way -- outside the project tree, keyed by a short hash of the real
    path so several projects never collide, with the project's own
    directory name kept in the folder name so a listing is still legible
    without decoding the hash. Best-effort: a failure to write this is
    never a reason to fail the run over, since it only ever adds a record
    that would otherwise not exist at all.
    """
    if not code:
        return
    try:
        full = os.path.abspath(out_path)
        key = hashlib.sha1(full.encode("utf-8")).hexdigest()[:8]
        project = os.path.basename(os.path.dirname(full)) or "root"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = os.path.join(FAILURE_DIR, "%s-%s" % (project, key))
        os.makedirs(folder, exist_ok=True)
        base = "%s.attempt%d.%s.%s" % (stamp, attempt, stage,
                                       os.path.basename(full))
        with open(os.path.join(folder, base), "w", encoding="utf-8") as fh:
            fh.write(code)
        with open(os.path.join(folder, base + ".error.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write(error or "")
    except OSError:
        pass


def extract_code(text: str, fence: str = "python") -> str:
    """Pull the file out of a reply, tolerating a chatty model.

    The fence tag is a hint, not a filter. Matching every tag is what lets an
    HTML component through at all -- a pattern that only knew ```python read
    a perfectly good ```html reply as no code block and burned a repair
    attempt on it. But matching every tag also means a reply that opens with
    a ```bash line showing how to run the thing has two blocks, so the one
    whose tag was asked for wins before size is considered.
    """
    blocks = [(tag.lower(), body) for tag, body in FENCE.findall(text or "")]
    if blocks:
        wanted = [b for tag, b in blocks if tag == fence]
        if not wanted and fence == "python":
            wanted = [b for tag, b in blocks if tag in ("py", "")]
        return max(wanted or [b for _, b in blocks], key=len).strip() + "\n"

    # No fence at all. If the whole reply parses as the language asked for, it
    # is probably just the file. Only decidable for Python, so anything else
    # is treated as no answer: a chatty reply saved as .html would pass the
    # nesting gate as prose and ship, which is worse than one repair attempt.
    stripped = (text or "").strip()
    if stripped and fence == "python":
        try:
            ast.parse(stripped)
            return stripped + "\n"
        except SyntaxError:
            pass
    if fence != "python":
        return ""
    # An opening fence with no closing one means the reply was cut off. The
    # code before the cut is usually fine, and throwing it away scores a
    # truncation as "the reply contained no code block" -- which spends a
    # repair attempt telling the model to fix a file it never saw rejected.
    opened = re.search(r"```[a-zA-Z0-9_+-]*\n", text or "")
    if opened:
        lines = text[opened.end():].rstrip().splitlines()
        while lines:
            candidate = "\n".join(lines)
            try:
                ast.parse(candidate)
                return candidate + "\n"
            except SyntaxError:
                lines.pop()
    return ""


# --------------------------------------------------------------------------
# gates -- none of these consult a model
# --------------------------------------------------------------------------


def is_python(path: str) -> bool:
    return path.endswith(".py")


def gate_html(code: str) -> Optional[str]:
    """Every opened tag is closed, and closed in the right order.

    Deliberately not a validator. `HTMLParser` accepts nearly anything, so
    asking it merely to parse is a gate that cannot fail -- and a gate that
    cannot fail is worse than none, because it reports success it did not
    establish. Mismatched nesting is the one defect that is both common in
    generated markup and decidable without a DOM.

    Void elements are not closed and must not be counted, and anything
    inside `<script>` or `<style>` is not markup at all -- `a < b` in a
    script is a comparison, and reading it as a tag would fail correct
    files, which is the failure mode that costs the most.
    """
    from html.parser import HTMLParser

    void = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}
    problems: List[str] = []

    class Check(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack: List[Tuple[str, int]] = []

        def handle_starttag(self, tag, attrs):
            if tag not in void:
                self.stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag):
            if tag in void:
                return
            if not self.stack:
                problems.append("</%s> on line %d closes nothing"
                                % (tag, self.getpos()[0]))
                return
            if self.stack[-1][0] == tag:
                self.stack.pop()
                return
            for depth in range(len(self.stack) - 1, -1, -1):
                if self.stack[depth][0] == tag:
                    unclosed = [t for t, _ in self.stack[depth + 1:]]
                    problems.append(
                        "</%s> on line %d closes <%s> from line %d, leaving "
                        "<%s> unclosed"
                        % (tag, self.getpos()[0], tag, self.stack[depth][1],
                           ">, <".join(unclosed)))
                    del self.stack[depth:]
                    return
            problems.append("</%s> on line %d was never opened"
                            % (tag, self.getpos()[0]))

    parser = Check()
    try:
        parser.feed(code)
        parser.close()
    except Exception as exc:                            # noqa: BLE001
        return "could not parse as HTML: %s" % exc
    for tag, line in parser.stack:
        problems.append("<%s> opened on line %d is never closed" % (tag, line))
    if problems:
        return "HTML nesting: " + "; ".join(problems[:4])
    return None


# A short list of tags that mean the page has something in it even when the
# visible text is empty -- an id'd container is read as substance too,
# because a legitimately JS-rendered page mounts into one and has nothing
# static for a text scan to find.
_SUBSTANCE_TAGS = {"input", "textarea", "select", "button", "form", "img",
                   "canvas", "svg", "video", "audio", "iframe", "table",
                   "h1", "h2", "h3", "h4", "h5", "h6"}


def gate_html_placeholder(code: str) -> Optional[str]:
    """A page that renders to nothing is worse than a missing feature.

    `gate_html` only checks that tags nest correctly, and an empty document
    nests perfectly -- the same gap `gate_platform_facts` was built to close
    one layer up for a hallucinated link, and the same class of bug as a
    generated `reverse()` that returned its input unchanged: it passes every
    check that only looks at shape. Measured live: a survey page's entire
    generated content was `<pre><code>\\n</code></pre>\\n` -- two tags,
    correctly nested, containing nothing but a newline -- which cleared the
    nesting gate and the "reply contained no code block" emptiness check
    (a newline inside tags is technically non-empty), and `main.py`
    faithfully base64-encoded and pushed an empty page. Everything downstream
    of the writer was correct; there was simply nothing to push.

    Deliberately narrow, in the direction this project always narrows a
    detector: it flags a document only when there is *no* visible text
    outside `<script>`/`<style>` *and* none of a short list of substance
    tags anywhere in it. A page with a single sentence, one `<input>`, or a
    container a script mounts into all clear this without a warning; it can
    only ever catch a page with nothing in it at all, never one with a
    little.
    """
    from html.parser import HTMLParser

    class Scan(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.text_len = 0
            self.substance = False
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag in ("script", "style"):
                self.skip += 1
            if tag in _SUBSTANCE_TAGS or any(k.lower() == "id" for k, _ in attrs):
                self.substance = True

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            if tag.lower() in ("script", "style") and self.skip:
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.text_len += len(data.strip())

    parser = Scan()
    try:
        parser.feed(code)
        parser.close()
    except Exception:                                    # noqa: BLE001
        return None
    if parser.text_len == 0 and not parser.substance:
        return ("the page has no visible text and none of input, form, "
                "button, image, table, heading, or an id'd container -- it "
                "would render blank")
    return None


# A `.forEach`/`.map` call whose own body opens a template literal is the
# standard way these generated pages build one repeated widget per item --
# one rating axis per food, one row per option. The template literal
# itself may embed a further template literal (a nested `.map(...)` for
# the stars inside each row), so this only has to locate where the outer
# template starts; every `class="X"` anywhere in the generous window that
# follows is a candidate, found separately below rather than by one regex
# capturing only the first.
_LOOP_TEMPLATE_START_RE = re.compile(r"\.(?:forEach|map)\s*\([^`]{0,300}?`")
_CLASS_ATTR_RE = re.compile(r"\bclass\s*=\s*[\"']([^\"']+)[\"']")
_LOOP_TEMPLATE_WINDOW = 3000

# A bare, non-interpolated class selector -- `.stars`, not
# `` `.stars[data-food="${x}"]` `` -- handed straight to `querySelector`
# and then used to attach a listener, either chained directly or through
# an intermediate variable.
_QS_CHAIN_RE = re.compile(
    r"document\.querySelector\(\s*['\"]\.([\w-]+)['\"]\s*\)\s*\.addEventListener\(")
_QS_ASSIGN_RE = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*document\.querySelector\(\s*"
    r"['\"]\.([\w-]+)['\"]\s*\)")


def gate_repeated_widget_query_selector(script: str) -> Optional[str]:
    """`querySelector` on a class the page renders once per item is a bug.

    Measured live, in two of seven independently generated star-rating
    survey pages: a script that correctly builds one `.stars` element per
    food inside `foodNames.forEach(...)`, and correctly queries a specific
    one elsewhere with `` document.querySelector(`.stars[data-food-name=
    "${foodName}"]`) `` -- and then, in its own `initialize()`, wires up
    the actual click/hover listeners with the bare, singular
    `document.querySelector('.stars').addEventListener(...)`.
    `querySelector` (unlike `querySelectorAll`) returns only the *first*
    matching element in document order, silently. The first rating axis
    works; every other one renders, looks identical, and is permanently
    inert -- no error, no crash, and a value that visibly changes on
    screen (star fill) for a click that was never wired to anything.

    This is never an intentional design: there is no legitimate reason to
    wire up interaction on only the first of several rendered instances of
    the same per-item widget while leaving the rest dead. So it fires only
    on the specific, decidable combination of both being true in the same
    script -- a class built inside a `.forEach`/`.map` template literal
    (evidence it is rendered more than once), and that exact class handed
    to a singular `querySelector` that goes on to attach a listener
    (evidence only the first instance will ever respond). A selector
    scoped to one instance (an attribute qualifier, or a template literal
    with `${...}` interpolation) never matches `_QS_CHAIN_RE`/`_QS_ASSIGN_RE`
    at all, so a correctly-scoped query into a repeated list is left alone.
    """
    repeated_classes = set()
    for match in _LOOP_TEMPLATE_START_RE.finditer(script):
        window = script[match.end():match.end() + _LOOP_TEMPLATE_WINDOW]
        for attr in _CLASS_ATTR_RE.finditer(window):
            repeated_classes.update(attr.group(1).split())

    if not repeated_classes:
        return None

    hits: List[str] = []
    for match in _QS_CHAIN_RE.finditer(script):
        cls = match.group(1)
        if cls in repeated_classes:
            hits.append(".%s" % cls)
    for match in _QS_ASSIGN_RE.finditer(script):
        varname, cls = match.group(1), match.group(2)
        if cls in repeated_classes and re.search(
                r"\b%s\s*\.addEventListener\(" % re.escape(varname), script):
            hits.append(".%s" % cls)

    if not hits:
        return None
    seen = sorted(set(hits))
    return (
        "document.querySelector('%s') wires up an event listener, but "
        "%s is a class this same script builds once per item inside a "
        ".forEach()/.map() template -- querySelector returns only the "
        "first matching element, so every instance after the first is "
        "rendered but permanently unresponsive to clicks. Use "
        "querySelectorAll('%s').forEach(el => el.addEventListener(...)) "
        "instead, or delegate the listener to a stable parent container."
        % (seen[0], " and ".join(seen) if len(seen) > 1 else seen[0],
           seen[0])
    )


def _node() -> Optional[str]:
    """Node, if this machine has it. Absent is a normal answer, not an error."""
    return shutil.which("node")


def _chrome_binary() -> Optional[str]:
    for candidate in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def page_console_errors(root: str, timeout: int = 20) -> str:
    """Open each plain page in a browser and return its console errors.

    A Node check can `require` a file and exit 0 while the same file, loaded
    by a `<script src>` in the page, throws `require is not defined`. The
    page check that only searches the HTML for the word `canvas` never sees
    that. No browser means no opinion, the same rule as `gate_js` without
    node: a missing tool must not fail a page it did not open.
    """
    chrome = _chrome_binary()
    if not chrome or not root:
        return ""
    pages: List[str] = []
    try:
        names = os.listdir(root)
    except OSError:
        return ""
    for name in sorted(names):
        if not name.lower().endswith((".html", ".htm")):
            continue
        if "__swarmrace" in name or name.startswith("."):
            continue
        full = os.path.join(root, name)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                body = fh.read(200_000)
        except OSError:
            continue
        if _TEMPLATED.search(body):
            continue
        pages.append(full)
    if not pages:
        return ""
    failed: List[str] = []
    seen = set()
    line_re = re.compile(
        r'INFO:CONSOLE:\d+\] "(.*)", source: (\S+) \((\d+)\)')
    for page in pages:
        profile = tempfile.mkdtemp(prefix="swarm-chrome-")
        proc = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--allow-file-access-from-files",
             "--virtual-time-budget=3000", "--enable-logging=stderr",
             "--user-data-dir=" + profile, "--dump-dom",
             "file://" + os.path.abspath(page)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            failed.append("CHECK FAILED: %s did not finish loading within %ds"
                          % (os.path.basename(page), timeout))
            shutil.rmtree(profile, ignore_errors=True)
            continue
        shutil.rmtree(profile, ignore_errors=True)
        text = err.decode("utf-8", errors="replace")
        for match in line_re.finditer(text):
            message = match.group(1)
            if not re.search(r"Uncaught|Error|Failed to", message):
                continue
            source = unquote(urlparse(match.group(2)).path)
            name = os.path.basename(source)
            row = match.group(3)
            line = "CHECK FAILED: %s:%s %s" % (name, row, message)
            if line not in seen:
                seen.add(line)
                failed.append(line)
    return "\n".join(failed)


def page_failure_lines(root: str, path: str) -> str:
    """Browser console lines that name this file, quoted as printed.

    The page check writes `CHECK FAILED: level.js:1 Uncaught ReferenceError:
    require is not defined`. That line names no call, so the call extractor
    returns nothing and a writer told only to keep the current file repeats
    the line. This is the text the writer is shown.
    """
    if not root or not path:
        return ""
    try:
        with open(os.path.join(root, ".ralph", "last-check.txt"),
                  encoding="utf-8", errors="replace") as fh:
            failure = fh.read()
    except OSError:
        return ""
    base = os.path.basename(path)
    lines: List[str] = []
    for raw in failure.splitlines():
        line = raw.strip()
        if not line.startswith("CHECK FAILED:") or base not in line:
            continue
        if not re.search(r"Uncaught|is not defined|Failed to", line):
            continue
        if line not in lines:
            lines.append(line)
    return "\n".join(lines[:8])


def _page_loads_script(root: str, base: str) -> bool:
    """True when an html file in the project loads `base` as a script."""
    if not root or not base:
        return False
    src = re.compile(r"<script\b[^>]*\bsrc\s*=\s*['\"][^'\"]*%s['\"]"
                     % re.escape(base), re.I)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (
            ".git", "node_modules", ".ralph", ".swarm")]
        for name in files:
            if not name.endswith((".html", ".htm")):
                continue
            try:
                with open(os.path.join(dirpath, name), encoding="utf-8",
                          errors="replace") as fh:
                    html = fh.read()
            except OSError:
                continue
            if src.search(html):
                return True
    return False


def gate_undefined_binding(code: str, path: str, root: str) -> Optional[str]:
    """Reject a file that still uses a name the page says is not defined.

    `node --check` accepts `require()` and `module.exports`. A page loaded
    by a script tag then throws `require is not defined`, the file's own
    gates pass, and the next rewrite is handed that file and told to keep
    its names. The failure text names no call, so the call-extractor has
    nothing to quote. The name in the error is the fact: a use is only
    legal on the same line as `typeof <name>`, which is how a file stays
    loadable both in the page and under Node.
    """
    if not root or not path:
        return None
    try:
        with open(os.path.join(root, ".ralph", "last-check.txt"),
                  encoding="utf-8", errors="replace") as fh:
            failure = fh.read()
    except OSError:
        return None
    base = os.path.basename(path)
    names: List[str] = []
    for match in re.finditer(
            r"CHECK FAILED:\s*(\S+):\d+\s+.*?:"
            r"\s*([A-Za-z_$][\w$]*) is not defined", failure):
        if match.group(1) == base and match.group(2) not in names:
            names.append(match.group(2))
    # The page check runs only after the node check exits 0, so a script
    # tag that throws on require or module.exports is invisible for every
    # round the node check still fails. A file an html page actually loads
    # has no CommonJS either way. typeof on the same line stays legal.
    if _page_loads_script(root, base):
        for extra in ("require", "module"):
            if extra not in names:
                names.append(extra)
    if not names:
        return None
    # Blank a comment instead of deleting the line, so the row quoted back
    # is the row in the file the writer just produced.
    cleaned: List[str] = []
    in_block = False
    for raw in code.splitlines():
        line = raw
        if in_block:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block = False
            else:
                cleaned.append("")
                continue
        while "/*" in line:
            before, _, rest = line.partition("/*")
            if "*/" in rest:
                line = before + rest.split("*/", 1)[1]
            else:
                line = before
                in_block = True
                break
        line = re.sub(r"//.*", "", line)
        line = re.sub(r"<!--.*?-->", "", line)
        line = re.sub(r"""(['"`])(?:\\.|(?!\1).)*\1""", "''", line)
        cleaned.append(line)
    for name in names:
        for row, line in enumerate(cleaned, 1):
            if name == "require":
                used = re.search(r"\brequire\s*\(", line) is not None
            else:
                used = re.search(r"\b%s\b" % re.escape(name), line) is not None
            if not used:
                continue
            if re.search(r"\btypeof\s+%s\b" % re.escape(name), line):
                continue
            return ("CHECK FAILED: %s:%d still uses %s, and the page says "
                    "%s is not defined. Delete the use. A script the page "
                    "loads has no %s. The only legal use is on the same "
                    "line as typeof %s."
                    % (base, row, name, name, name, name))
    return None


def gate_js(code: str, label: str = "script") -> Optional[str]:
    """Syntax-check JavaScript with node, or say nothing.

    `node --check` is a real parser, so unlike a hand-rolled brace counter it
    cannot be fooled by a regex literal containing `}` or by a template
    string, and it accepts both module and script syntax. Verified on
    exactly those cases before this was relied on.

    That precision is the reason no fallback is attempted when node is
    missing. Balancing braces by hand would reject `/\\}/.test(s)` -- correct
    code -- and a detector that fires on correct input costs more than it
    saves, which is the rule the AST detectors in `patterns.py` already
    follow. No node means no opinion.
    """
    node = _node()
    if node is None:
        return None
    tmp = tempfile.mkdtemp(prefix="jscheck-")
    try:
        target = os.path.join(tmp, "probe.js")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(code)
        proc = subprocess.run([node, "--check", target], timeout=30,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode == 0:
            return None
        out = proc.stdout.decode("utf-8", errors="replace")
        detail = ""
        for line in out.splitlines():
            if "Error" in line:
                detail = line.strip()
                break
        return "%s: %s" % (label, detail or out.strip()[-300:])
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# `{{ }}` and `{% %}` are Jinja/Handlebars, not JavaScript. An inline script
# in a template is not meant to parse on its own, so checking it would fail
# correct work -- the one direction of error worth engineering against.
_TEMPLATED = re.compile(r"\{\{|\{%|<%")


def inline_scripts(code: str) -> List[str]:
    """The bodies of `<script>` elements that carry code of their own.

    Skips anything with a `src`, which has no body worth checking, and
    anything whose `type` is not JavaScript -- `application/json` and
    `text/template` are routinely parked in a script tag precisely because
    the browser will not execute them.
    """
    from html.parser import HTMLParser

    class Grab(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.found: List[str] = []
            self.live = False

        def handle_starttag(self, tag, attrs):
            if tag != "script":
                return
            got = {k.lower(): (v or "").lower() for k, v in attrs}
            kind = got.get("type", "")
            self.live = ("src" not in got
                         and kind in ("", "text/javascript", "module",
                                      "application/javascript"))

        def handle_endtag(self, tag):
            if tag == "script":
                self.live = False

        def handle_data(self, data):
            if self.live and data.strip():
                self.found.append(data)

    parser = Grab()
    try:
        parser.feed(code)
        parser.close()
    except Exception:                                   # noqa: BLE001
        return []
    return parser.found


def gate_syntax(code: str, path: str) -> Optional[str]:
    """Whatever can be decided about this file type from the text alone.

    Python is compiled, JSON is parsed, HTML is checked for nesting. Prose
    and stylesheets get nothing beyond being non-empty, and that is the
    honest answer rather than a gap: there is no cheap mechanical defect to
    look for in a Markdown file, and inventing one would reject correct
    work. The rule throughout this project is that a detector firing on
    correct input costs more than it saves.
    """
    if not code.strip():
        return "the reply contained no code block"
    ext = os.path.splitext(path)[1].lower()
    if ext in ("", ".py", ".pyw"):
        try:
            compile(code, path, "exec")
        except SyntaxError as exc:
            return "SyntaxError: %s (line %s)\n    %s" % (
                exc.msg, exc.lineno, (exc.text or "").rstrip())
        return None
    if ext == ".json":
        try:
            json.loads(code)
        except ValueError as exc:
            return "not valid JSON: %s" % exc
        return None
    if ext in (".js", ".mjs", ".cjs"):
        return gate_js(code, "JavaScript")
    if ext in (".html", ".htm"):
        failure = gate_html(code)
        if failure is not None:
            return failure
        # Skipped for a template for the same reason the inline-script check
        # below is: `{{ content }}` alone is a legitimate minimal template
        # with no static text of its own, and a checker that cannot see what
        # the templating engine will fill in has no basis to call it empty.
        if not _TEMPLATED.search(code):
            failure = gate_html_placeholder(code)
            if failure is not None:
                return failure
        # A survey page keeps its behaviour in an inline <script>, so a page
        # whose markup nests perfectly and whose script does not parse is
        # a blank page in a browser and a pass here. Checked only when the
        # file is plain HTML: a template's script is not standalone code.
        if not _TEMPLATED.search(code):
            for body in inline_scripts(code):
                failure = gate_js(body, "inline <script>")
                if failure is not None:
                    return failure
                failure = gate_repeated_widget_query_selector(body)
                if failure is not None:
                    return failure
    return None


def vocabulary(source: str, limit: int = 12) -> List[str]:
    """The words a program says it accepts, read off its own syntax tree.

    A gate cannot invent commands for a program it has never seen, and does not
    have to: anything reading typed input compares that input against string
    literals, and those literals are the vocabulary. `verb == "look"` and
    `exits={"north": ...}` between them yield look, go, north -- enough to walk
    the program through its own code paths without a single assertion being
    guessed at.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    words: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for side in [node.left] + list(node.comparators):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    words.append(side.value)
                elif isinstance(side, (ast.Tuple, ast.List, ast.Set)):
                    words += [e.value for e in side.elts
                              if isinstance(e, ast.Constant)
                              and isinstance(e.value, str)]
        elif isinstance(node, ast.Dict):
            words += [k.value for k in node.keys
                      if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    seen, out = set(), []
    for word in words:
        word = word.strip()
        low = word.lower()
        if (word and " " not in word and 1 < len(word) < 20
                and not word.startswith("__") and low not in seen):
            seen.add(low)
            out.append(word)
    return out[:limit]


def exercise_script(source: str) -> str:
    """Lines to type at an interactive program, ending with a way out.

    Verbs are paired with every other word as a noun, because `go` alone
    exercises the error branch and `go north` exercises the code that matters.
    Nonsense is harmless: a program that does not recognise a word prints so
    and carries on, and only a traceback fails the gate.
    """
    words = vocabulary(source)
    if not words:
        return "help\n\n"
    quits = [w for w in words if w.lower() in ("quit", "exit", "q", "bye")]
    rest = [w for w in words if w not in quits]
    lines = ["help"] + rest
    # Which words are verbs and which are nouns is not knowable, so pair by
    # noun rather than by verb: every word gets tried against the first
    # candidate noun before any word gets a second one. Pairing the other way
    # round produced `north east` six times and never `go north`, which is the
    # one combination that reached the defect.
    lines += ["%s %s" % (verb, noun)
              for noun in rest[:4] for verb in rest if verb != noun][:24]
    lines += quits[:1]
    return "\n".join(lines) + "\n"


GUARD_DIR = os.path.join(swarm.HERE, ".swarm", "guard")

# Imported automatically by every Python the gates start, because CPython
# imports `sitecustomize` at interpreter startup if it is anywhere on the
# path. That is the only hook that covers code nobody has read yet.
_GUARD_SOURCE = '''\
"""Refuse to leave this machine. Installed by codegen.run_command.

`gate_import` executes a generated file's module level and `gate_smoke` runs
its `main`, which is the point -- running the code is the only gate that
observes behaviour rather than shape. It is also how a generated API client
gets to POST to a live service while merely being tested, and unlike a wrong
file that is caught and rewritten, a created record stays created.

So the loop is kept and the blast radius is removed: loopback is allowed, so
a mock server still answers, and everything else raises with a message that
names the address rather than looking like a network fault.
"""
import ipaddress
import os
import socket

if not os.environ.get("SWARM_ALLOW_NETWORK"):

    def _refuse(host):
        raise OSError(
            "network blocked while testing: tried to reach %r. Generated "
            "code must not call a live service from a gate. Point it at "
            "a base URL from the environment instead." % host)

    def _local(host):
        """Loopback by what the address means, not by how it is spelled.

        A set of five literal strings answers "is this 127.0.0.1" and
        nothing else. It reads 127.0.0.2, ::ffff:127.0.0.1 and a resolver
        handing back a mapped form as remote, which is wrong but safe --
        and the reverse is the dangerous direction, so the test is made to
        follow the address family rather than the spelling.
        """
        if isinstance(host, bytes):
            host = host.decode("utf-8", "replace")
        if not isinstance(host, str):
            # AF_UNIX and friends carry a path, not a host. Nothing leaves
            # the machine, so there is nothing here to refuse.
            return True
        name = host.strip().strip("[]")
        if name in ("", "localhost") or name.endswith(".localhost"):
            return True
        try:
            addr = ipaddress.ip_address(name)
        except ValueError:
            # Not an address at all, so it is a name needing a resolver.
            return False
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        return addr.is_loopback or addr.is_unspecified

    # Resolution is the earliest chokepoint and the only one that still
    # knows the hostname. Refusing at connect() works on the address the
    # resolver returned, so the message names an anonymous CDN IP, and any
    # client reaching the network by a path that does not end in
    # socket.socket.connect gets through. Refusing the lookup also means a
    # gate cannot leak which hosts it was going to call.
    _getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if not _local(host):
            _refuse(host)
        return _getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo

    # Still needed: an address literal never goes near a resolver.
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex

    def _check(address):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if not _local(host):
            _refuse(host)

    def connect(self, address):
        _check(address)
        return _connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return _connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex

# Which component's gate this is. Components are written in parallel against
# one mock, so without a label its call log cannot say who sent what -- and
# handing a writer another file's rejected request is exactly the kind of
# confident wrong instruction the repair loop will dutifully act on.
_TAG = os.environ.get("SWARM_COMPONENT")
if _TAG:
    try:
        import http.client

        _putrequest = http.client.HTTPConnection.putrequest

        def putrequest(self, method, url, *args, **kwargs):
            result = _putrequest(self, method, url, *args, **kwargs)
            try:
                self.putheader("X-Swarm-Component", _TAG)
            except Exception:
                pass
            return result

        http.client.HTTPConnection.putrequest = putrequest
    except Exception:
        # Attribution is a convenience. Losing it must not stop a gate.
        pass
'''


def guard_dir() -> Optional[str]:
    """Write the import-time network guard once, and say where it lives."""
    try:
        os.makedirs(GUARD_DIR, exist_ok=True)
        target = os.path.join(GUARD_DIR, "sitecustomize.py")
        if (not os.path.exists(target)
                or open(target, encoding="utf-8").read() != _GUARD_SOURCE):
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(_GUARD_SOURCE)
        return GUARD_DIR
    except OSError:
        # Not being able to install the guard must not stop the run. It makes
        # gates less safe, not broken, and the caller has no better option.
        return None


def local_urls(env: Dict[str, str]) -> Dict[str, str]:
    """Aim every URL a gate can read at loopback, so it cannot try.

    The socket guard is the thing that stops a request; this is the thing
    that stops the attempt existing. They are worth having both, because
    they fail in different ways: a guard is one patched function away from
    a gap, while an environment that names no live host gives generated
    code nothing to reach for even when the guard is gone.

    It matters most when `--api` is absent. There is no mock then, so
    nothing overrode `API_BASE_URL`, and the operator's real one -- along
    with the real key beside it -- was handed straight to a smoke run
    whose whole job is to execute code nobody has read. Measured: a
    generated client POSTed to the live platform and came back with a 405.

    A loopback value is left exactly as it is, so the mock keeps working
    and stays the target for every other URL in the environment. Only
    http and https are touched; `file://` and `s3://` name no host to
    confine, and rewriting them would break a correct file.
    """
    import urllib.parse

    def host_of(value: str) -> Optional[str]:
        try:
            parts = urllib.parse.urlsplit(value.strip())
        except ValueError:
            return None
        if parts.scheme not in ("http", "https"):
            return None
        return parts.hostname or ""

    def is_local(value: str) -> bool:
        host = host_of(value)
        if host is None:
            return True
        if host in ("", "localhost") or host.endswith(".localhost"):
            return True
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return False
        return addr.is_loopback or addr.is_unspecified

    # Port 1 rather than a plausible one: nothing listens there, so a
    # request fails immediately and loudly instead of reaching whatever
    # else the operator happens to be running on 8000.
    sink = "http://127.0.0.1:1"
    base = env.get("API_BASE_URL", "")
    if base and is_local(base):
        sink = base.rstrip("/")

    out: Dict[str, str] = {}
    for name, value in env.items():
        upper = name.upper()
        if not (upper.endswith("_URL") or upper.endswith("_URI")):
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if not is_local(value):
            out[name] = sink
    return out


ENV_LOADER_MARKER = "# --- .env loader (added by codegen.py) ---"

_ENV_LOADER_TEMPLATE = """%s
import os as _os
if _os.path.isfile(%r):
    with open(%r, "r", encoding="utf-8") as _envf:
        for _line in _envf:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
del _os
%s

"""


def _read_dotenv_values(path: str) -> Dict[str, str]:
    """The same parse `_ENV_LOADER_TEMPLATE` embeds as source, run here in
    the swarm's own process instead of the generated one.

    Exists so `run_command` can hand a gate subprocess the identical
    values a real run would have picked up from the injected loader --
    without this, `gate_import` on a *non-entry* sibling file (a
    `client.py` a writer gave its own module-level `API_KEY =
    os.environ[...]`) has no way to see a credential at all, because the
    loader is only ever injected into the file `usage.read` calls
    runnable, and `gate_import` probes every file standalone, never
    through the entry point that would have loaded it first. Measured
    live: a `client.py` reading its credential at import time failed
    `gate_import` with "API_KEY environment variable is not set" even
    though the swarm's own `.env` had a real value sitting right there --
    the gate was rejecting code that would have run correctly the moment
    the entry point actually imported it.

    Read here, in Python, rather than shelling out to source the file:
    this never runs as a script, so there is no shell-quoting surface to
    get wrong, and the rules stay byte-for-byte the same ones the injected
    loader above uses on the exact same file.
    """
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def env_loader_snippet() -> str:
    """A stdlib-only loader for the swarm's own `.env`, meant to be
    prepended to a generated entry point.

    A gated run already has real credentials handed to it -- `env_extra`
    puts the mock's URL straight into the gate's own subprocess
    environment. Nothing does the equivalent for the *real* run: an
    operator who later runs `python3 main.py` by hand, from a brand new
    directory `--api` work happened to land in, gets a bare
    `KeyError`/"environment variable is not set" no matter how many times
    they export it in some other shell, because a fresh directory and a
    fresh shell remember nothing from the last one.

    So the entry point looks for one fixed, known file instead --
    `.env` at the swarm's own root, the same file `env_vocabulary` already
    reads for *names* -- and fills in whatever it finds with
    `os.environ.setdefault`. `setdefault` is the whole safety property:
    it can only ever add a name that was not already set, so a value the
    operator genuinely exported themselves is never shadowed, the same
    rule already applied to placeholder-filling in `gate_smoke`. A
    missing, unreadable, or empty `.env` changes nothing -- the file
    behaves exactly as it would have without this, falling through to
    whatever `KeyError` or `os.getenv` default it always had.
    """
    env_path = os.path.join(swarm.HERE, ".env")
    return _ENV_LOADER_TEMPLATE % (ENV_LOADER_MARKER, env_path, env_path,
                                   ENV_LOADER_MARKER)


def maybe_inject_env_loader(code: str, out_path: str,
                            env_extra: Optional[Dict[str, str]]) -> str:
    """Prepend the `.env` loader to a generated entry point, once.

    Gated on `env_extra` being supplied, which is this call's own proxy
    for "this run has `--api` set" -- `run_components` only ever builds
    that dict when a mock (and therefore a real API) is in play, and a
    run with nothing to talk to has no credential for this to help with
    anyway. Gated on `found.runnable` rather than on which env names this
    exact file's own AST reads, because the file that owns the network
    call is often a sibling (`client.py`) the entry point only imports --
    `os.environ` is shared for the whole process once the entry point
    starts, so loading it once, first, at the top of whichever file
    actually runs standalone, reaches every file it imports too.
    """
    if not env_extra or ENV_LOADER_MARKER in code:
        return code
    found = usage.read(out_path, source=code)
    if found is None or not found.runnable:
        return code
    return env_loader_snippet() + code


def run_command(cmd: str, cwd: str, timeout: int = 120,
                stdin_text: Optional[str] = None,
                env_extra: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """Run a shell command, returning (ok, combined output).

    The project root goes on PYTHONPATH. Running `python3 pkg/test.py` puts
    pkg/ on sys.path rather than the working directory, so `from pkg.mod
    import x` raises ModuleNotFoundError no matter what the file contains --
    an unsatisfiable gate that rejects correct code and sends the repair loop
    chasing an error that was never in the generated file.

    The network guard goes on there too, so anything these gates execute is
    confined to loopback. See `_GUARD_SOURCE`.
    """
    env = dict(os.environ)
    # A check that calls `judge.py` finds it, and the project's cache, here.
    env.setdefault("SWARM_HOME", swarm.HERE)
    if env_extra:
        # A gate probing one file standalone (`gate_import`) or running
        # `main` (`gate_smoke`) should see the same credential a real run
        # would have -- and for a real run, that comes from the loader
        # injected into the entry point (`maybe_inject_env_loader`), not
        # from this file's own environment. A non-entry sibling that reads
        # its credential at import time never goes through that loader
        # during a gate, because the gate imports it directly rather than
        # via the entry point that would load `.env` first. `setdefault`
        # semantics via a plain dict merge (never overwriting something
        # already in `os.environ`, and always losing to `env_extra`'s own
        # explicit values below) keep this exactly as inert as the
        # injected loader is: a value the operator genuinely exported
        # still wins, and the mock's own `API_BASE_URL` still overrides
        # whatever `.env` says once `env.update(env_extra)` runs after this.
        for k, v in _read_dotenv_values(os.path.join(swarm.HERE, ".env")).items():
            env.setdefault(k, v)
        env.update(env_extra)
    existing = env.get("PYTHONPATH", "")
    parts = [cwd]
    guard = guard_dir()
    if guard:
        parts.append(guard)
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    if guard and not env.get("SWARM_ALLOW_NETWORK"):
        # A proxy defeats the socket guard completely, and silently. The
        # guard sees a connection to the proxy -- frequently on loopback, and
        # allowed for that reason -- while the proxy happily forwards it to
        # the internet. Found by the check in `sandbox_check.py`, which
        # reported a live 403 from a proxy on a request that was supposed to
        # be refused outright.
        #
        # Emptied rather than deleted, because some clients treat an absent
        # variable as "look somewhere else" and an empty one as "no proxy".
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
                     "http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
            if name in env:
                env[name] = ""
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        env.update(local_urls(env))
    # shell=True makes the child a shell and the real check its grandchild.
    # Killing only the shell leaves the grandchild running, which is how a
    # check that never returns occupied a CPU for hours. A new session lets
    # one signal take the whole group.
    # Stdout goes to a file. A pipe fills, the check blocks on write, and a
    # timeout then has nothing to show for whatever it did print. The file
    # keeps that, and the deadline line is appended rather than replacing it.
    fd, out_path = tempfile.mkstemp(prefix="swarm-check-")
    os.close(fd)
    timed_out = False
    try:
        with open(out_path, "wb") as fh:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd, env=env, start_new_session=True,
                stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                stdout=fh, stderr=subprocess.STDOUT)
            if stdin_text:
                proc.stdin.write(stdin_text.encode())
                proc.stdin.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait()
        with open(out_path, "rb") as fh:
            out_b = fh.read()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    out = out_b.decode("utf-8", errors="replace")
    if timed_out:
        line = "CHECK FAILED: the check did not return within %ds" % timeout
        # The tail is the part a later plan can still read. The head of a
        # check that logged every input is not.
        tail = out[-100_000:].strip()
        if line not in tail:
            tail = (tail + "\n" + line).strip()
        return False, tail
    return proc.returncode == 0, out.strip()


_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError"}


def gate_no_stubs(code: str, modules) -> Optional[str]:
    """Reject a file that quietly substitutes its own stub for a real import.

    The most destructive thing a writer does, because it defeats every gate at
    once by design. Handed the finished modules, a model writes:

        try:
            from engine import GameState
        except ImportError:
            class GameState:
                def __init__(self, starting_room): pass

    and then codes against the stub. The file imports, runs, and smoke-passes
    whether or not its neighbours exist -- that is the stated purpose of the
    fallback -- so nothing downstream can tell it apart from working code. It
    fails only when a person runs it: the real `GameState` takes
    `initial_room`, and the invented one taught the rest of the file the wrong
    name. This is the same defect that made a pygame animation draw five
    motionless shapes, where the comment read "mocking imports to ensure the
    file runs standalone".

    The dependency is written before this file and its real source is in the
    prompt, so the import cannot fail and the fallback is never justified.
    """
    if not modules:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    wanted = {m[:-3] if m.endswith(".py") else m for m in modules}
    guilty = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        imported = {a.name for stmt in ast.walk(node)
                    if isinstance(stmt, (ast.Import, ast.ImportFrom))
                    for a in stmt.names}
        modules_hit = {stmt.module.split(".")[-1] for stmt in ast.walk(node)
                       if isinstance(stmt, ast.ImportFrom) and stmt.module}
        if not modules_hit & wanted:
            continue
        for handler in node.handlers:
            names = {n.id for n in ast.walk(handler.type or ast.Pass())
                     if isinstance(n, ast.Name)}
            if handler.type is not None and not (names & _IMPORT_ERRORS):
                continue
            for stmt in handler.body:
                if isinstance(stmt, (ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    if stmt.name in imported:
                        guilty.append(stmt.name)
                elif isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if isinstance(t, ast.Name) and t.id in imported:
                            guilty.append(t.id)
    if not guilty:
        return None
    return ("this file defines its own fallback version of %s inside an "
            "`except ImportError` block. Delete every one of those try/except "
            "wrappers and the stub classes behind them, and import the real "
            "names directly at the top of the file. Those modules exist and "
            "their real source is above, so the import cannot fail -- and a "
            "stub silently teaches the rest of this file the wrong signatures."
            % ", ".join(sorted(set(guilty))))


def gate_interfaces(out_path: str, root: str,
                    dep_paths: Sequence[str]) -> Optional[str]:
    """Catch a call into a dependency's interface that does not exist.

    `crossref.scan` already knows how to decide this from the syntax tree --
    it is the same check `interface_gate` runs across a whole finished
    package, `calls client.create_applet_version, but MavenClient in
    api_client.py has no such attribute`. That pass only runs once every
    component has already spent its own repair budget, which means a
    mismatch it could catch for free is instead discovered by `gate_smoke`
    crashing against a live mock, once per attempt, for as many attempts as
    the component has. Measured live: a main.py depending on an
    already-finished api_client.py burned 6 attempts plus a full whole-loop
    restart -- 12 attempts, several minutes -- on a mismatch this function
    finds in one AST walk with no subprocess at all.

    `dep_paths` is expected to carry only already-completed dependencies'
    real paths -- the same files already sitting on disk for `gate_import`
    to see -- so a class this file depends on that has not been written yet
    is correctly invisible here rather than a false positive waiting to
    happen. Python-only, like every other crossref check: a page or a
    stylesheet parses as neither Python nor a defect.
    """
    if not dep_paths or not is_python(out_path):
        return None
    rel = os.path.relpath(out_path, root)
    paths = [rel] + [p for p in dep_paths if p != rel]
    findings = [f for f in crossref.scan(paths, root) if f.path == rel]
    if not findings:
        return None
    return ("this file does not fit the code it depends on -- these are "
            "facts read from the other files, not opinions:\n  "
            + "\n  ".join(f.text() for f in findings))


def gate_uses(code: str, modules) -> Optional[str]:
    """Fail a file that was given other components to build on and ignored them.

    This is the defect that survives everything else, because the file it
    produces is not broken -- it is complete, self-contained, and wrong. Asked
    for the entry point of an animation, a model was handed the finished
    `shapes.py` and `constants.py` and instead declared cut-down copies of both
    inside `main.py`: five shapes at fixed positions with no velocity. It
    parsed, imported, ran, drew a window, and never moved, and every mechanical
    gate passed it, because nothing about one file in isolation is wrong.

    Checking it after the fact is too late and was measured failing: by then
    the file is written and a repair prompt has to talk it out of its own
    design. Here the dependency's real source is already in the prompt and the
    ordinary repair loop does the work.
    """
    if not modules:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    # Either a bare list of module names, or a mapping from module name to the
    # names it actually defines, which lets the failure quote the import line.
    offered = dict(modules) if isinstance(modules, dict) else {}
    wanted = {m[:-3] if m.endswith(".py") else m for m in modules}
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                used.add(node.module.split(".")[-1])
            # `from mypkg import constants` names the module in the alias, not
            # the module field, and a package layout makes that the normal form.
            used.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                used.add(alias.name.split(".")[-1])
    missing = sorted(wanted - used)
    if not missing:
        return None
    # Say exactly which line to write. "Import from your dependencies" is
    # advice and a model can agree with it and then not do it -- measured, the
    # 12b spent all three attempts rewriting its own copies. The names come
    # from the dependency's own syntax tree, so this cannot be wrong, and it
    # leaves nothing to decide.
    lines = []
    for module in missing:
        names = list(offered.get(module) or ())
        lines.append("  from %s import %s" % (module, ", ".join(names[:8]))
                     if names else "  import %s" % module)
    return ("this file must be built out of the other files it was given, and "
            "it imports nothing from %s. Add these lines and use what they "
            "give you, instead of defining your own versions:\n%s"
            % (" or ".join("%s.py" % m for m in missing), "\n".join(lines)))


# The Maven admin UI is a different host from the API, and nothing in the
# OpenAPI document describes it -- there is no response field and no
# endpoint, so it cannot be derived and can only be recorded verbatim (see
# `context/platform-maven-urls.md`, which is attached to any component whose
# spec plausibly needs it). Measured live, with that exact card sitting in
# the same prompt: a generated `get_ui_url` produced
# `https://dztfdhkmj6wqi.cloudfront.net/projects/{id}/ui` -- a host that is
# neither the real UI host nor the card's own API-host example used
# correctly, with an invented `/ui` suffix on top. Prose stating the exact
# right answer, verbatim, in the same prompt, was not enough to stop a
# second invented value -- the same finding this project already made once
# for an entry-point rename -- so this is decided mechanically rather than
# asked for a second time more forcefully.
MAVEN_UI_HOST = "d30ca7kumywvjf.cloudfront.net"
_CLOUDFRONT_RE = re.compile(r"\b[a-z0-9]{8,25}\.cloudfront\.net\b")
# The two good shapes immediately following the host -- a project link and
# a task link, the only two the operator has confirmed. Optional, since the
# bare host (defining a constant, for instance) is also fine.
_GOOD_TAIL = re.compile(r"/(?:projects|tasks)/(\{[^}]*\}|[\w\-]+)?")
# Whatever legitimately ends a URL sitting in running text or in a string
# literal: whitespace, the quote that closes the literal, a backslash
# escape, ordinary sentence punctuation, or the end of the text. Excluding
# these from a "still going" read is what tells a correct
# `.../projects/{id}. Task ID: ...` apart from a doubled
# `.../projects/{id}/projects/{id}` -- the character right after the good
# match is a real terminator in the first case and another path segment in
# the second.
_TERMINATOR = re.compile(r'[\s"\'\\.,;:!?)\]}>|]')


def gate_platform_facts(code: str) -> Optional[str]:
    """Catch a hallucinated Maven UI host, or a bad path on the real one --
    fabricated, an invented suffix, or the whole `/projects/{id}` shape
    appended a second time onto an already-complete link.

    A `cloudfront.net` literal only ever belongs in a correct file, or a
    correct program's *output*, as the exact recorded host with nothing but
    `/projects/{id}` after it -- and this is called on both: `generate_file`
    runs it against the source text, and `gate_smoke` runs it again against
    what the program actually printed, because a doubled link is invisible
    to a source scan when it comes from two separately-correct string
    literals concatenated at runtime (a helper that already returns the
    full link, plus a caller that appends `/projects/{id}` again). Neither
    literal is wrong in isolation; only the printed result is.

    Matching "the good shape, then a real terminator" rather than "capture
    everything until whitespace, then compare" is what keeps this from
    misfiring on ordinary prose: `.../projects/{id}. Task ID: ...` ends the
    good match right before the period, which is a terminator, while
    `.../projects/{id}/projects/{id}` hits a `/` where a terminator should
    be.
    """
    for m in _CLOUDFRONT_RE.finditer(code):
        host = m.group(0)
        if host != MAVEN_UI_HOST:
            return ("%r is not a real Maven host. The admin UI is always "
                    "exactly %r -- see context/platform-maven-urls.md. Do "
                    "not invent a different cloudfront.net domain."
                    % (host, MAVEN_UI_HOST))
    for m in re.finditer(re.escape(MAVEN_UI_HOST), code):
        rest = code[m.end():]
        if not rest or _TERMINATOR.match(rest[0]):
            continue  # bare host, e.g. a constant definition -- fine
        good = _GOOD_TAIL.match(rest)
        if good:
            after = rest[good.end():good.end() + 1]
            if not after or _TERMINATOR.match(after):
                continue
        bad_tail = re.match(r'[^\s"\'\\]*', rest).group(0)
        return ("%s%s -- a project link is exactly "
                "%s/projects/{project_id}, a task link is exactly "
                "%s/tasks/{task_id}, and nothing follows the id, and "
                "nothing else builds one on top of a link that already has "
                "it. See context/platform-maven-urls.md."
                % (MAVEN_UI_HOST, bad_tail, MAVEN_UI_HOST, MAVEN_UI_HOST))
    return None


_UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12})\b")
# Position 12 (the version nibble, conventionally '1'-'5') and position 16
# (the variant nibble, conventionally '8'/'9'/'a'/'b') are the only two
# hex digits a placeholder generator ever has a reason to set to anything
# but zero -- `codegen.placeholder_env`'s own literal,
# "00000000-0000-4000-8000-000000000000", sets exactly those two and
# nothing else. A real UUID is 30+ independently random hex digits, so
# the odds of one landing all-zero by chance are astronomically small;
# this is not a heuristic in the "probably fine" sense, it is a
# near-certain identification of a fabricated placeholder.
_PLACEHOLDER_UUID_FREE_POSITIONS = frozenset({12, 16})


def _looks_like_placeholder_uuid(value: str) -> bool:
    match = _UUID_RE.fullmatch(value)
    if not match:
        return False
    hexdigits = value.replace("-", "")
    return all(c == "0" or i in _PLACEHOLDER_UUID_FREE_POSITIONS
               for i, c in enumerate(hexdigits))


def gate_placeholder_id_literal(code: str) -> Optional[str]:
    """A hardcoded, all-zero-shaped UUID anywhere in a file's own source
    is never real data -- it is exactly the fallback this project's own
    `placeholder_env` substitutes for a MISSING credential or argument
    during a smoke run, injected into the subprocess's *environment*,
    never written into the file's own text. A generated file containing
    that same shape as a Python string literal did not get it from this
    project's gate machinery; it wrote it itself, which only happens one
    way: a lookup into an API response (or a previous step's
    `output.json`) failed or returned something unexpected, and instead
    of raising, the code silently fell back to a value that merely looks
    like a real one.

    Measured live, at the end of the exact chain of events this project
    has already hit once before (the very first entry in this project's
    own error log was a `422` caused by one): a generated `main.py`
    built an admin UI link during its own smoke run reading
    `.../projects/00000000-0000-4000-8000-000000000000/applets/
    00000000-0000-4000-8000-000000000000` -- the mock's own call log for
    that same attempt showed real `POST /api/projects` and `POST
    /api/applets` calls succeeding with 201s, so real ids existed and
    were available; the code simply never got them into the variable it
    printed from. `gate_platform_facts` flagged the link's *shape* (an
    invented `/applets/{id}` suffix) as an advisory note, because a bad
    shape on an otherwise-correct link is cosmetic -- but the zeroed-out
    id inside it is not cosmetic, it is the same class of defect a 422
    against the real platform already cost this project once: a fake id
    that looks plausible enough to pass every existing gate and fail
    only against the real service, three steps downstream, for a reason
    nothing printed at generation time.

    So this is decided from the source, exactly like a fabricated
    Maven host: any string literal that is a UUID with every hex digit
    zero except the two positions a placeholder generator has a reason
    to touch (`_looks_like_placeholder_uuid`) fails the file outright,
    at the same free, pre-subprocess stage as `gate_platform_facts`.
    Unlike that gate, this one blocks -- a fabricated id is not a
    cosmetic wart on one string, it is proof the code has a silent
    fallback where a raise belongs, and letting it through "this once"
    is exactly how it reaches the real platform. The fix is almost
    never rewording this literal; it is fixing whatever `.get(...,
    default)`, bare `except: pass`, or unchecked response field let the
    code reach for a placeholder instead of the real value the response
    (or a sibling's `output.json`) already had.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and _looks_like_placeholder_uuid(node.value):
            return ("contains %r hardcoded in the source. An all-zero-"
                    "shaped UUID is never real data the API returns -- it "
                    "is exactly the placeholder this project's own gate "
                    "substitutes for a MISSING credential or argument "
                    "during a smoke run, and that substitution happens in "
                    "the subprocess's environment, never in a file's own "
                    "text. Something in this file fell back to a fake id "
                    "instead of raising when a real one could not be "
                    "found -- find whichever response field, dict lookup, "
                    "or previous step's output.json read this came from "
                    "and remove the fallback: a missing or unexpected id "
                    "should be a fatal error with the field name in the "
                    "message, the same rule this project already applies "
                    "to a missing credential, never a plausible-looking "
                    "substitute that only fails once it reaches the real "
                    "platform." % node.value)
    return None


# RFC 2606 reserves these for documentation and examples specifically so
# nothing real is ever supposed to answer at them -- a stronger guarantee
# than "looks like a placeholder": there is no such thing as a legitimate
# production host under any of these, ever, by the same standard that
# defines them. Matched with an optional subdomain (`api.example.com`,
# `www.example.org`) since that is exactly the shape a model reaches for
# when it wants "a plausible API host" and has nothing real to put there.
_PLACEHOLDER_HOST_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]+\.)*example\.(?:com|org|net)\b", re.IGNORECASE)

# A bracketed or all-caps "fill this in" token is not a credential in the
# sense a real one is -- nothing this project's own `placeholder_env`
# generates ever looks like this (its fallbacks are UUID-shaped or aimed
# at loopback, never a literal instruction to the reader), so this can
# only ever be a model's own stand-in for a value it never obtained, the
# credential-shaped sibling of the zeroed UUID above.
_PLACEHOLDER_CREDENTIAL_RE = re.compile(
    r"^(?:your[_-]?(?:api[_-]?)?(?:key|token|secret|password)"
    r"|<[^<>]*(?:key|token|secret|password)[^<>]*>"
    r"|(?:api[_-]?)?(?:key|token|secret)[_-]?(?:here|goes[_-]?here))$",
    re.IGNORECASE)


def gate_placeholder_host_literal(code: str) -> Optional[str]:
    """A hardcoded `example.com` (or `.org`/`.net`) host, or a bracketed
    "YOUR_API_TOKEN"-shaped credential string, anywhere in a file's own
    source is the same class of defect as a zeroed-out UUID above: a
    plausible-looking stand-in for a real value the code never obtained,
    written into the file's own text rather than raised as an error.

    Measured live: a generated `main.py` correctly read `API_BASE_URL`
    from the environment on its first successful attempt, was then
    rewritten by `interface_gate`'s cross-file repair pass to fix an
    unrelated mismatch, and the rewrite hardcoded
    `api_url = f"https://api.example.com/tasks/{task_id}/units"` and
    `headers = {"Authorization": "Bearer YOUR_API_TOKEN"}` -- neither of
    which is derived from any environment variable, response, or sibling
    `output.json`. `local_urls` cannot catch this: it only ever rewrites
    environment *variables* named `*_URL`/`*_URI` before a subprocess
    starts, and has no way to intercept a URL built entirely from a
    string literal inside the function body. The socket guard could catch
    it at runtime (a real, resolvable, non-loopback host), but only if
    the exception it raises is not itself caught and swallowed by the
    file's own `except requests.exceptions.RequestException` -- which,
    per `gate_swallowed_api_failure`'s own rule, is allowed to happen as
    long as it re-raises or exits, and this file did exactly that,
    reporting a clean, printed failure and exit 1 that looks identical
    to a legitimate "the real API rejected this" outcome. Nothing
    downstream of generation time was ever going to catch a hardcoded
    fake host that fails exactly the way a real failure would.

    So this is decided from the source, at the same free, pre-subprocess
    stage as the zeroed-UUID check: an `example.com`/`.org`/`.net` host
    (RFC 2606-reserved -- there is no legitimate production use of it,
    ever, which is a stronger guarantee than "looks fake") or a
    bracketed/ALL-CAPS "fill this in" credential string anywhere in the
    file's source fails it outright. The fix is never rewording the
    literal; it is finding whatever caused the code to reach for a
    made-up value instead of `os.environ.get("API_BASE_URL"/"API_KEY")`,
    the shared client class this component depends on, or a response
    field, and using that instead.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant)
                and isinstance(node.value, str)):
            continue
        value = node.value
        host_match = _PLACEHOLDER_HOST_RE.search(value)
        if host_match:
            return ("contains %r hardcoded in the source, which reaches "
                    "into an RFC 2606-reserved domain (%s) that is never a "
                    "real production host, by definition -- there is "
                    "nothing at that address to talk to. The real base URL "
                    "must come from os.environ.get(\"API_BASE_URL\") (with "
                    "the spec's own server URL as a fallback if one was "
                    "given), or from the shared client class this "
                    "component depends on, never a hardcoded literal."
                    % (value, host_match.group(0)))
        stripped = value.strip()
        if stripped and _PLACEHOLDER_CREDENTIAL_RE.match(stripped):
            return ("contains %r hardcoded in the source -- this is a "
                    "fill-in-the-blank placeholder, not a real credential. "
                    "The real value must come from an environment "
                    "variable (os.environ[\"API_KEY\"] or similar, named "
                    "in this project's own env var list), never a literal "
                    "string that only looks like where a credential goes."
                    % stripped)
    return None


_NON_BASE64_TEXT_ENCODE_NAMES = frozenset(
    {"quote", "quote_plus", "quote_from_bytes", "urlencode"})
_BASE64_ENCODE_NAMES = frozenset(
    {"b64encode", "standard_b64encode", "urlsafe_b64encode"})


def _call_func_name(node: ast.AST) -> Optional[str]:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _b64_encode_shape(node: ast.AST) -> bool:
    """True iff `node` is (optionally `.decode(...)`-wrapped) a call to a
    real base64 encoder -- the one shape that actually belongs after
    `;base64,` in a data URI."""
    if isinstance(node, ast.Call) and _call_func_name(node) == "decode" \
            and isinstance(node.func, ast.Attribute):
        return _b64_encode_shape(node.func.value)
    return _call_func_name(node) in _BASE64_ENCODE_NAMES


def _non_base64_encode_shape(node: ast.AST) -> Optional[str]:
    """The function name, if `node` is a call to one of the *specific*
    text-encoding functions this exact mistake has been observed reaching
    for instead of a real base64 encoder -- `quote`/`quote_plus`/
    `urlencode`, from `urllib.parse` or `requests.utils`. `None` for
    anything else, including a shape this cannot classify at all -- this
    only ever names a known wrong answer, never guesses that an unknown
    one must be wrong too."""
    name = _call_func_name(node)
    return name if name in _NON_BASE64_TEXT_ENCODE_NAMES else None


def gate_mislabeled_base64_data_uri(code: str) -> Optional[str]:
    """A `data:...;base64,PAYLOAD` string whose payload never actually
    went through a base64 encoder is not a data URI anything can read --
    it is a string that merely claims to be one.

    Measured live: a component reading a real `survey.html`'s content
    built `f"data:text/html;base64,{requests.utils.quote(html, "
    "safe='')}"`, with its own comment admitting "Base64 encoding is
    complex, using a placeholder structure" in place of an actual
    `base64.b64encode(...)` call. `requests.utils.quote`/`urllib.parse.
    quote` percent-encode text (`<` becomes `%3C`), which is a different,
    unrelated encoding -- `%` is not even in the base64 alphabet, so
    `base64.b64decode` on the result raises `binascii.Error: Non-base64
    digit found` immediately, and a browser's own `atob()` fails the
    same way. The generated file parsed, imported, smoke-ran, and passed
    every existing gate, because nothing in this pipeline decodes a data
    URI's payload to check it -- and the project was pointed at exactly
    this version afterward, which is why the applet showed "Loading
    applet..." indefinitely even though the real `survey.html` on disk
    was correct the entire time: the iframe never receives HTML or JS it
    can parse, so `APPLET_READY` never fires.

    Deliberately narrow, the same way `gate_placeholder_host_literal`
    just above is: rather than trying to prove an unrecognized shape is
    *correct* (which this project has consistently refused to guess at),
    this only ever fires on a short, named list of functions already
    observed making this exact mistake. A real `base64.b64encode(...)`
    call (optionally `.decode(...)`-ed to text) is recognized and cleared
    explicitly; anything this cannot classify -- a helper function this
    scan cannot see into, a name this simple one-assignment tracker does
    not resolve -- is left alone rather than flagged on a guess.

    Scoped to f-strings, the shape the live failure actually took.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    assigns: Dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            assigns[node.targets[0].id] = node.value

    def resolve(node: ast.AST) -> ast.AST:
        if isinstance(node, ast.Name) and node.id in assigns:
            return assigns[node.id]
        return node

    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for i, part in enumerate(node.values[:-1]):
            if not (isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and part.value.endswith(";base64,")):
                continue
            following = node.values[i + 1]
            if not isinstance(following, ast.FormattedValue):
                continue
            expr = resolve(following.value)
            if _b64_encode_shape(expr):
                continue
            bad_name = _non_base64_encode_shape(expr)
            if bad_name is not None:
                try:
                    shown = ast.unparse(expr)
                except Exception:
                    shown = bad_name + "(...)"
                return (
                    "builds a `...;base64,...` data URI whose payload "
                    "comes from %s -- that is a different text encoding, "
                    "not base64 (`%%`/`+` characters it produces are not "
                    "even in the base64 alphabet), and a base64 decoder "
                    "(a browser's atob(), Python's base64.b64decode) will "
                    "reject it or produce garbage. Use "
                    "base64.b64encode(data.encode(\"utf-8\")).decode"
                    "(\"utf-8\") for the payload instead."
                    % shown[:120])
    return None


_CONFLICT_RETRY_CODES = frozenset({400, 409, 422})


def gate_narrowed_conflict_retry_status_codes(code: str) -> Optional[str]:
    """A retry loop for a value that must be unique on the real service
    (a version string, a slug, a name) checked `response.status_code`
    against a *subset* of this project's own three-code conflict set.

    `openapi.example_call()`'s own literal, copy-pasteable retry-loop
    boilerplate for a `version`-marked field checks `resp.status_code
    not in (400, 409, 422)` -- all three together, on purpose, because
    which one a duplicate value comes back as is a fact about the real
    service, not something the OpenAPI document necessarily documents.
    Measured live: the real Maven API's own spec for `POST /api/applets/
    {applet_id}/versions` documents only 201 and 422 as possible
    responses -- no 400 anywhere in it -- and a second version push
    against an applet that already had one came back 400 anyway, purely
    empirically. `06_update_applet/main.py` was handed the correct
    literal boilerplate (400 included) and wrote its own retry loop
    checking only `(409, 422)`, narrower than what it was shown. The
    real service's 400 fell straight through to `resp.raise_for_status()`
    on the very first attempt, with no retry at all, and the file failed
    immediately with a bare "400 Client Error: Bad Request" and no
    response body to say why -- exactly the case the full three-code set
    exists to protect against, since nothing in the document itself
    could have told a writer to expect it.

    Deliberately advisory, not blocking, the same way `gate_platform_
    facts` is: a status-code set is a real design choice a file might
    have a genuine, different reason for (treating 400 as a permanent
    client error while only retrying 409/422, say), and this cannot tell
    that apart from a writer who simply narrowed the literal example it
    was given. Fires only when the set actually used *overlaps* the
    three-code set without covering all of it -- a file that never
    mentions any of these three codes has nothing to do with this retry
    idiom at all and is correctly left alone.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "status_code"):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if not isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                continue
            codes: set = set()
            readable = True
            for elt in comparator.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                    codes.add(elt.value)
                else:
                    readable = False
                    break
            if not readable or not codes:
                continue
            overlap = codes & _CONFLICT_RETRY_CODES
            if overlap and overlap != _CONFLICT_RETRY_CODES:
                missing = sorted(_CONFLICT_RETRY_CODES - overlap)
                return (
                    "this file checks response.status_code against %s, "
                    "which overlaps this project's own conflict-retry "
                    "status set {400, 409, 422} but is missing %s. A "
                    "create-or-update call whose value must be unique "
                    "(a version string, a slug, a name) can be rejected "
                    "as a duplicate under any of these three codes "
                    "depending on the real service -- this project's own "
                    "generated retry-loop example always checks all "
                    "three together for exactly that reason, since "
                    "which one a given service actually uses is not "
                    "always documented in its own OpenAPI spec. Retry "
                    "on the full set unless there is a specific, known "
                    "reason this service never returns %s for this call."
                    % (sorted(codes), missing, missing))
    return None


def autofix_narrowed_conflict_retry_status_codes(code: str) -> Optional[str]:
    """The one deterministic fix `gate_narrowed_conflict_retry_status_codes`'s
    own note already names exactly: which code(s) are missing from the
    set. Unlike failing the file outright -- which the gate itself
    declines to do, because a narrowed set could be a real, deliberate
    choice -- *widening* it is safe regardless of whether that choice
    was deliberate: retrying on a code the file previously treated as
    fatal can only ever make an eventually-failing call retry a few more
    times before it fails the same way for the same underlying reason
    (a real, non-conflict error under one of these codes still exhausts
    the loop and still raises). It can never turn a real success into a
    missed one, and it can never turn a real permanent failure into an
    infinite retry, since the loop this project's own boilerplate always
    generates is bounded. That asymmetry -- widening is harmless,
    narrowing might not be -- is exactly what lets this exist as an
    unconditional autofix even though the gate that finds the same
    defect stays advisory-only.

    Applied at the exact `response.status_code in/not in (...)` literal
    the gate already located, replacing only that tuple/list/set's
    elements with their union against `{400, 409, 422}` and leaving
    everything else in the file untouched -- this is a textual splice of
    one small literal, not a rewrite of the surrounding retry logic.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "status_code"):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if not isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                continue
            codes: set = set()
            readable = True
            for elt in comparator.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                    codes.add(elt.value)
                else:
                    readable = False
                    break
            if not readable or not codes:
                continue
            overlap = codes & _CONFLICT_RETRY_CODES
            if not (overlap and overlap != _CONFLICT_RETRY_CODES):
                continue
            full = sorted(codes | _CONFLICT_RETRY_CODES)
            if isinstance(comparator, ast.List):
                replacement = "[%s]" % ", ".join(str(c) for c in full)
            elif isinstance(comparator, ast.Set):
                replacement = "{%s}" % ", ".join(str(c) for c in full)
            elif len(full) == 1:
                replacement = "(%s,)" % full[0]
            else:
                replacement = "(%s)" % ", ".join(str(c) for c in full)
            edits.append((comparator.lineno, comparator.col_offset,
                          comparator.end_lineno, comparator.end_col_offset,
                          replacement))
    if not edits:
        return None
    lines = code.splitlines(keepends=True)
    for lineno, col, end_lineno, end_col, replacement in sorted(
            edits, key=lambda e: (e[0], e[1]), reverse=True):
        if lineno == end_lineno:
            line = lines[lineno - 1]
            lines[lineno - 1] = line[:col] + replacement + line[end_col:]
        else:
            first_part = lines[lineno - 1][:col]
            last_part = lines[end_lineno - 1][end_col:]
            lines[lineno - 1:end_lineno] = [first_part + replacement + last_part]
    return "".join(lines)


def _literal_str_parts(node: ast.AST) -> Optional[str]:
    """The static text of a string or f-string, ignoring interpolated
    parts, or None if this is not a string-shaped literal at all.

    An f-string's `{variable}` pieces contribute nothing knowable at this
    level, so they are dropped rather than guessed at -- `f"../{other}/
    output.json"` reads the same as `"../X/output.json"` for the one thing
    this is used to detect: whether the *fixed* part of the path climbs out
    of this directory and ends in the project's own output-file name. That
    is enough; nothing here needs to resolve what `other` actually is.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts) if parts else None
    return None


def gate_own_output_path(code: str) -> Optional[str]:
    """A step's own creation output must land in its own output.json, not
    a sibling's -- catches a `main.py` that writes its result to the exact
    path it just read a dependency's id from.

    Measured live: `02_create_task/main.py` read `project_id` from
    `../01_create_project/output.json`, correctly, then saved the
    `task_id` it created to that same `../01_create_project/output.json`
    path instead of its own `output.json` -- overwriting the producer's
    file with the consumer's result. `03_upload_units` then failed with
    "Required file not found at ../02_create_task/output.json", because
    that file was never written; the id it needed was sitting one
    directory over, in a file that already had a different meaning. The
    request text this was generated from already distinguished the two
    paths clearly ("...whose project_id is read from ../01_create_project/
    output.json; save its id to output.json as task_id"), and the writer
    read past the distinction anyway -- the same shape of failure this
    project has hit before with a correct answer sitting unused in the
    same prompt (the invented UI link, the entry-point rename).

    So this is decided from the source rather than asked for a second
    time: nothing a script does ever legitimately writes to a *sibling*
    directory's `output.json`. Reading one (`open(path, "r")`, or the
    implicit read-mode default) to pick up an id a previous step already
    produced is exactly the documented, correct pattern -- only a WRITE
    mode (`"w"`, `"a"`, `"x"`, or any mode string containing one of those
    letters) aimed at a path containing `../` and ending in `output.json`
    is flagged, since that combination has no legitimate reading: a step's
    own output always belongs at the plain, local `output.json` in its own
    directory.

    Deliberately narrow to `open(...)` calls with a literal (or partially
    literal, via a simple same-module assignment lookup) path -- a path
    built from something this scan cannot see the shape of at all is left
    alone rather than guessed at, since a detector that fires on correct
    code costs more than the one it might have caught.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    # Best-effort: the last string/f-string literal assigned to each name
    # at any point in the module, so `OUT = "../x/output.json"` followed
    # later by `open(OUT, "w")` is still caught. Not real data-flow -- just
    # enough to see through the single-assignment-then-reused-variable
    # shape the live failure actually took.
    literal_names: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            text = _literal_str_parts(node.value)
            if text is not None:
                literal_names[node.targets[0].id] = text

    def _resolve(node: ast.AST) -> Optional[str]:
        text = _literal_str_parts(node)
        if text is not None:
            return text
        if isinstance(node, ast.Name):
            return literal_names.get(node.id)
        return None

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open" and node.args):
            continue
        path = _resolve(node.args[0])
        if path is None or "../" not in path or not path.endswith("output.json"):
            continue
        mode = None
        if len(node.args) > 1:
            mode = _literal_str_parts(node.args[1])
        else:
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = _literal_str_parts(kw.value)
        mode = mode or "r"
        if not any(c in mode for c in "wax"):
            continue  # a read of a sibling's output.json is the correct pattern
        return ("opens %r in write mode (%r) -- this path climbs into a "
                "SIBLING step's own directory. A step's own creation "
                "output always belongs in the plain, local `output.json` "
                "in its own directory, never in another step's; opening a "
                "sibling's output.json is only ever correct for reading "
                "(mode 'r') an id that step already produced." % (path, mode))
    return None


_SIBLING_OUTPUT_RE = re.compile(r"\.\./[\w\-./]*output\.json", re.I)


def spec_requires_own_output_write(spec: str) -> bool:
    """True if `spec` asks this component to save something to its OWN
    `output.json`, as opposed to only reading a sibling's.

    A spec chains two different mentions of the same filename with two
    different meanings -- "create a task in the project whose project_id
    is read from ../01_create_project/output.json [sibling, read-only];
    save its id as task_id in this directory's own output.json [local,
    write]" -- and `gate_own_output_path`/`gate_output_json_shape` above
    both already tell the two apart the same way this does: strip every
    `../.../output.json` reference (a read) and ask whether the bare
    filename still appears anywhere in what is left (a write this
    component owns). A spec mentioning only a sibling's file -- a pure
    consumer with nothing of its own to produce -- correctly returns
    False, and nothing below applies to it.
    """
    if not spec:
        return False
    stripped = _SIBLING_OUTPUT_RE.sub(" ", spec)
    return "output.json" in stripped.lower()


def own_output_write_block(spec: str, original_request: str = "") -> str:
    """Literal, copy-pasteable code for the one shape `gate_missing_own_
    output_write` exists to catch.

    Measured live: a component whose own request read, verbatim, "save
    its id as task_id in a new output.json in the same directory as the
    script" failed the same gate four times running -- the original
    attempt(s), a full whole-loop restart, an escalation to a stronger
    model, and a CONTRACT/SPEC replan -- with the identical
    `missing_own_output_write` message every time, including the gate's
    own suggested fix (`json.dump({...}, open("output.json", "w"))`)
    quoted verbatim into every repair prompt via `REPAIR_PROMPT`. Prose
    was not the missing piece: `context/policy-output-json.md` is an
    `always: true` card carrying the identical literal example already,
    reaching every writer's system prompt regardless of relevance
    scoring. Four independent rolls -- two of them genuinely fresh, with
    no repair history to have drifted from -- still produced a file with
    no write at all.

    This is the same lesson already learned once for the auth/header
    shape (`openapi.connection_setup_block`, built for the identical
    reason): a general instruction, however many times it is repeated in
    however many different places in the prompt, is followed less
    reliably than one literal example built from *this component's own
    real key name*, sitting in the SPEC/`extra` text a writer reads to
    decide what this specific file does -- as opposed to a standing
    policy card that reads the same on every single call regardless of
    which id this one happens to be about.

    That same reasoning turned around and bit this exact function.
    Measured live: a request read "save its id as project_id...", the
    planner's own paraphrase of it (the SPEC this function actually
    received) read "...writing the resulting project ID to output.json"
    -- true to the request's meaning, but the one literal token
    `_promised_output_json_keys` needs was gone. With no key recovered,
    this function fell back to a hardcoded example, `result_id`, and
    presented it as *the* correct key to copy verbatim -- and the writer
    did exactly that: `output_data = {"result_id": project_id}`, passed
    every gate (nothing had a real promise to check it against either),
    and broke the very next step's `data["project_id"]` with a
    `KeyError`. The fabricated example was not a neutral placeholder; it
    was a specific, wrong, confidently-presented instruction, and this
    function is exactly the mechanism ("a literal example beats prose")
    documented above as the reason to trust it.

    `original_request`, when the caller has it, closes most of this gap
    before it opens: this project's own convention is that every chain
    step's request states the promise explicitly, so checking the
    request text in addition to `spec` recovers the real key even when
    the planner's paraphrase dropped it (see `_promised_keys_from_texts`
    for why both are checked and in what order). What remains -- a
    request that itself never names a key, however rare that should be
    with the convention followed -- gets guidance with no fabricated
    key at all, only a rule and named anti-examples, so there is nothing
    wrong left to copy verbatim if a writer takes the example literally
    the way the last one did.

    Silent whenever `spec_requires_own_output_write` is False, so a
    component with nothing of its own to hand forward (a pure consumer
    of a sibling's file, or a step whose request explicitly said no
    hand-off is needed) is never shown this and never biased toward
    adding output.json code it has no reason to write -- the same
    conditioning the gate itself already uses, reused rather than
    duplicated, so the two can never disagree about which components
    this applies to.
    """
    if not spec_requires_own_output_write(spec):
        return ""
    keys = _promised_keys_from_texts(spec, original_request)
    if keys:
        key = keys[0]
        return (
            "\n\nThis component's own request promises to save its "
            "result to this directory's own output.json. Write it "
            "exactly like this, near the end of the real work, using "
            "the real value you just computed -- not a placeholder, "
            "and not only a print():\n\n"
            "```python\nimport json\n\n"
            "with open(\"output.json\", \"w\") as f:\n"
            "    json.dump({\"%s\": %s}, f)\n```\n\n"
            "This is not optional boilerplate to skip. It is the one "
            "thing the next step in the chain reads; printing %s is "
            "not enough, because a later step runs as its own separate "
            "process with no memory of this one's stdout.\n\n"
            "If satisfying this request means creating more than one "
            "thing along the way -- a parent record and then a "
            "version, draft, or sub-resource of it, because that is "
            "how the real API's create flow works -- \"%s\" must hold "
            "that ONE specific resource's own id, the one this key "
            "actually names, not a different id you also computed or "
            "received in the same sequence of calls. Assign it to a "
            "variable literally named `%s` right where you get it back "
            "from the response that creates *that* resource, and write "
            "that same variable here -- never a variable with a "
            "different name, even if it also looks like a valid id."
            % (key, key, key, key, key))
    # No literal key recoverable from either the spec or the original
    # request. Deliberately no example dict literal here at all --
    # see this function's own docstring for why showing one, even a
    # generic-looking one, is not a safe fallback: it reads as *the*
    # answer to copy, not an illustration of the shape.
    return (
        "\n\nThis component's own request promises to save its result "
        "to this directory's own output.json, but does not spell out "
        "the exact key name to use. Pick one descriptive, specific name "
        "for the ONE thing this component actually creates or fetches "
        "-- the pattern is `<what this is>_id` (e.g. `project_id`, "
        "`task_id`, `applet_id`), never a generic placeholder like "
        "`result_id`, `output_id`, `id_value`, or bare `id`: a later "
        "step reads this file by an exact name, and a vague one gives "
        "it nothing to ask for. Write it near the end of the real "
        "work, using the real value you just computed:\n\n"
        "```python\nimport json\n\n"
        "with open(\"output.json\", \"w\") as f:\n"
        "    json.dump({\"<descriptive>_id\": ...}, f)\n```")


_SIBLING_READ_KEY_RE = re.compile(
    r"read\s+([a-z][a-z0-9_]*)\s+from\s+(\.\./[\w.\-/]+/output\.json)",
    re.I)


def sibling_output_read_block(spec: str) -> str:
    """Literal code for reading a named key out of a sibling step's
    output.json, matching this project's own request phrasing exactly
    (`"read KEY from ../DIR/output.json"`, the same convention
    `_missing_sibling_error`/`intent.chain_output_json_problems` already
    read off request text elsewhere in this project).

    Handed over as working code for the identical reason `own_output_
    write_block` above is, applied to the opposite side of the same
    hand-off: a component told only in prose to "read the id from the
    sibling's file" has to invent the exact `json.load(...)[...]` shape
    itself, and this project has already measured that invention going
    wrong in several different ways -- a bare scalar assumed instead of
    a dict, a wrong key guessed instead of the one actually promised, a
    local `output.json` read instead of the sibling's. A literal example
    built from *this component's own real path and key*, both read
    directly off its own SPEC text rather than guessed, cannot be
    misread the same way.

    Silent whenever the spec names no such reference (`re.findall`
    returns nothing), so a component with nothing upstream to read -- an
    entry point with no dependency, or a first step in a chain -- is
    never shown code it has no use for and never biased toward reading a
    file that was never going to exist for it.
    """
    matches = _SIBLING_READ_KEY_RE.findall(spec or "")
    if not matches:
        return ""
    lines = ["import json", ""]
    seen: set = set()
    for key, path in matches:
        if (key.lower(), path) in seen:
            continue
        seen.add((key.lower(), path))
        lines.append("with open(\"%s\") as f:" % path)
        lines.append("    %s = json.load(f)[\"%s\"]" % (key.lower(), key.lower()))
    return (
        "\n\nThis component's own request names a value to read from a "
        "sibling step's output.json. Read it exactly like this -- the "
        "real relative path and the real key name this request already "
        "gave, not a placeholder and not a local file:\n\n```python\n%s"
        "\n```" % "\n".join(lines))


def _writes_local_output_json(tree: ast.AST) -> bool:
    """True if `tree` contains any `open(...)` call, in a write-capable
    mode, aimed at a path that is exactly `output.json` in this
    directory -- never a sibling's (that direction is `../` and is
    `gate_own_output_path`'s job, not this one).

    Shares the same literal-tracking approach as `gate_own_output_path`
    (a best-effort look at direct literals and simple same-module
    variable assignments, not real data-flow) because the two checks are
    mirror images of the same question: does a write exist, and if so,
    where does it point.
    """
    literal_names: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            text = _literal_str_parts(node.value)
            if text is not None:
                literal_names[node.targets[0].id] = text

    def _resolve(node: ast.AST) -> Optional[str]:
        text = _literal_str_parts(node)
        if text is not None:
            return text
        if isinstance(node, ast.Name):
            return literal_names.get(node.id)
        return None

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open" and node.args):
            continue
        path = _resolve(node.args[0])
        if path is None or "../" in path or not path.endswith("output.json"):
            continue
        mode = None
        if len(node.args) > 1:
            mode = _literal_str_parts(node.args[1])
        else:
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = _literal_str_parts(kw.value)
        mode = mode or "r"
        if any(c in mode for c in "wax"):
            return True
    return False


def gate_missing_own_output_write(spec: str, code: str) -> Optional[str]:
    """A spec that asks this component to save something to its own
    `output.json` and gets a file with no write to one anywhere is the
    same class of failure as an empty API call log: a file that never
    even attempted the thing it was asked for passes every existing gate
    silently, because both `gate_own_output_path` and
    `gate_output_json_shape` above only ever look at a write that already
    exists -- neither one fires when there is no write at all, which
    reads identically to "correctly wrote it" to both of them.

    Measured live: a `create an applet; save its id in this directory's
    own output.json` component created the applet for real against the
    live API and never wrote `output.json` at all -- no sibling-path
    mistake, no bare-scalar mistake, just no write, so neither existing
    gate had anything to object to and the run reported this step
    succeeded with nothing on disk for the next step to read. This is
    the missing third case: exists-but-wrong-place (`gate_own_output_
    path`), exists-but-wrong-shape (`gate_output_json_shape`), and now
    does-not-exist-at-all.

    Deliberately conditioned on the spec actually asking for one
    (`spec_requires_own_output_write`) -- a component whose whole job is
    reading a sibling's id and acting on it, with nothing of its own to
    hand forward, correctly writes no `output.json` and must not be
    flagged for it.
    """
    if not spec_requires_own_output_write(spec):
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    if _writes_local_output_json(tree):
        return None
    return ("the request asks this step to save its result to this "
            "directory's own output.json, but no write to a local "
            "output.json (mode 'w'/'a'/'x') appears anywhere in the file "
            "-- add one, e.g. json.dump({...}, open(\"output.json\", "
            "\"w\")), so the next step has something to read.")


def _shape(node: Optional[ast.AST], dict_shaped: Dict[str, Optional[bool]]
          ) -> Optional[bool]:
    """True: provably a dict/object. False: provably a bare scalar. None:
    cannot tell either way, so say nothing about it."""
    if node is None:
        return None
    if isinstance(node, ast.Dict):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "dict":
        return True
    if isinstance(node, (ast.Constant, ast.JoinedStr, ast.List, ast.Tuple)):
        return False
    if isinstance(node, (ast.Subscript, ast.Attribute)):
        return False
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("get", "strip", "lower", "upper",
                                   "format", "join"):
        return False
    if isinstance(node, ast.Name):
        return dict_shaped.get(node.id)
    return None


def gate_output_json_shape(code: str) -> Optional[str]:
    """A step's own output.json must be a JSON object, not a bare scalar
    -- every consumer down a chain reads it with `json.load(...).get(key)`
    or `[key]`, per this project's own convention, and both raise the
    instant they run against a plain string.

    Measured live: a producer's `main.py` wrote `output.json` as a bare
    JSON string -- the raw project id, no `{"project_id": ...}` wrapper
    -- despite the request saying, verbatim, "save its id as project_id
    in this directory's own output.json". Two different consumers then
    failed two different ways on the very same root cause:
    `AttributeError: 'str' object has no attribute 'get'` in one,
    `TypeError: string indices must be integers` in the other -- the
    same shape of bug this project has hit before where the correct
    instruction sat in the prompt and the writer produced something else
    anyway.

    Decided from the source, at the same free, pre-subprocess stage as
    `gate_own_output_path` just above: finds a write (not a sibling's --
    see that gate for the opposite direction) to this file's own bare
    `output.json`, and checks whether the value handed to `json.dump`/
    `json.dumps` is provably a dict (or `dict(...)`) versus provably a
    bare scalar -- a string/number/f-string literal, a subscript or
    attribute access, or a `.get(...)`-shaped call. Deliberately
    conservative: anything this cannot classify (a variable built from a
    branch, a function call of unknown shape) is left alone rather than
    guessed at, matching every other AST check in this file.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    dict_shaped: Dict[str, Optional[bool]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            shape = _shape(node.value, dict_shaped)
            if shape is not None:
                dict_shaped[node.targets[0].id] = shape

    def _is_local_output_write(call: ast.Call) -> bool:
        if not (isinstance(call.func, ast.Name) and call.func.id == "open"
                and call.args):
            return False
        path = _literal_str_parts(call.args[0])
        if not path or not path.endswith("output.json") or "../" in path:
            return False
        mode = None
        if len(call.args) > 1:
            mode = _literal_str_parts(call.args[1])
        else:
            for kw in call.keywords:
                if kw.arg == "mode":
                    mode = _literal_str_parts(kw.value)
        mode = mode or "r"
        return any(c in mode for c in "wax")

    handle_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and _is_local_output_write(call) \
                        and isinstance(item.optional_vars, ast.Name):
                    handle_names.add(item.optional_vars.id)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call) \
                and _is_local_output_write(node.value):
            handle_names.add(node.targets[0].id)
    if not handle_names:
        return None

    def _describe(arg: ast.AST) -> str:
        try:
            return ast.unparse(arg)
        except Exception:                                # noqa: BLE001
            return "the value written"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "dump" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "json" and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Name) \
                and node.args[1].id in handle_names:
            if _shape(node.args[0], dict_shaped) is False:
                return ("writes %s -- a bare scalar -- to this step's own "
                        "output.json instead of a JSON object. Every "
                        "downstream step reads this file with "
                        "json.load(...).get(key) or [key], and both raise "
                        "on a plain string or number. Write it as "
                        "{\"<name>\": %s}, not the bare value itself."
                        % (_describe(node.args[0]), _describe(node.args[0])))
        if isinstance(node.func, ast.Attribute) and node.func.attr == "write" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in handle_names and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Attribute) \
                    and arg0.func.attr == "dumps" \
                    and isinstance(arg0.func.value, ast.Name) \
                    and arg0.func.value.id == "json" and arg0.args:
                if _shape(arg0.args[0], dict_shaped) is False:
                    return ("writes %s -- a bare scalar -- to this "
                            "step's own output.json instead of a JSON "
                            "object. Every downstream step reads this "
                            "file with json.load(...).get(key) or [key], "
                            "and both raise on a plain string or number. "
                            "Write it as {\"<name>\": %s}, not the bare "
                            "value itself."
                            % (_describe(arg0.args[0]), _describe(arg0.args[0])))
    return None


def gate_output_json_not_json(code: str) -> Optional[str]:
    """A step's own output.json has to actually be JSON, not merely a file
    that happens to have that name -- every consumer down a chain opens it
    with `json.load(...)` unconditionally, and that raises on anything
    else, the same way `gate_output_json_shape` catches a bare scalar
    *after* it has already gone through `json.dump`. This catches the
    write that never goes through `json`/`json.dumps` at all.

    Measured live: a `save_project_id(project_id, filename)` helper built
    its output as `content = f"project_id: {project_id}"` and wrote that
    string directly to `output.json` via an atomic tempfile-and-`os.replace`
    pattern -- correct engineering for atomicity, and not JSON at all. The
    very next step's `json.load(...)` raised `JSONDecodeError`, caught by a
    bare `except json.JSONDecodeError: print(...); return` with no
    `sys.exit`, so the step printed an error and still reported success
    (a separate, already-covered failure --
    `gate_swallowed_api_failure` -- but this gate is what stops the root
    cause from ever reaching that point).

    `gate_output_json_shape` cannot see this, on purpose: it only ever
    inspects the *argument* to a `json.dump`/`json.dumps` call it has
    already found, so a file that never calls either function has nothing
    for it to inspect and it stays silent -- correctly conservative for
    its own question ("is the JSON object well-shaped"), wrong for this
    one ("is this JSON at all").

    Two shapes are checked, because the live bug wrote through a helper
    function rather than a literal `open("output.json", "w")` at the
    write site:

    1. **Direct**: `open`/`os.fdopen` opened in write mode against a path
       that is provably `output.json` (a literal, an f-string with only
       that fixed tail, or a same-file variable holding one of those),
       whose handle is `.write(...)`-ed with something that is not a call
       to `json.dumps(...)`, and no `json.dump(...)` call anywhere in the
       file targets that same handle.
    2. **Indirect**: a locally-defined function is called anywhere in this
       file with a literal (or same-file-variable) `"output.json"`
       argument -- positional or keyword, matching the exact live shape
       `save_project_id(project_id=project_id, filename=output_filename)`
       -- and that function's own body writes to a file handle
       (`.write(...)`) without ever calling `json.dump`/`json.dumps`
       anywhere inside it.

    Both directions are deliberately narrow. A function that writes
    output.json correctly via `json.dump` is untouched regardless of how
    many indirections it is called through. A function called with some
    other filename entirely is not this component's concern -- `filename`
    resolving to anything other than a bare `output.json` (no `../`, no
    other suffix) is left alone, the same scoping `gate_own_output_path`
    already applies for the opposite direction.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    str_vars: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            lit = _literal_str_parts(node.value)
            if lit is not None:
                str_vars[node.targets[0].id] = lit

    def resolve(node: ast.AST) -> Optional[str]:
        lit = _literal_str_parts(node)
        if lit is not None:
            return lit
        if isinstance(node, ast.Name):
            return str_vars.get(node.id)
        return None

    def is_bare_output_json(value: Optional[str]) -> bool:
        return bool(value) and value.endswith("output.json") and "../" not in value

    def is_write_mode(call: ast.Call) -> bool:
        mode = None
        if len(call.args) > 1:
            mode = resolve(call.args[1])
        else:
            for kw in call.keywords:
                if kw.arg == "mode":
                    mode = resolve(kw.value)
        mode = mode or "r"
        return any(c in mode for c in "wax")

    def is_json_dumps_call(node: Optional[ast.AST]) -> bool:
        return bool(node) and isinstance(node, ast.Call) \
            and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps" \
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "json"

    def json_dump_targets(tree_: ast.AST) -> Set[str]:
        targets: Set[str] = set()
        for n in ast.walk(tree_):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "dump" and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "json" and len(n.args) >= 2 \
                    and isinstance(n.args[1], ast.Name):
                targets.add(n.args[1].id)
        return targets

    # --- Direct: open()/os.fdopen() against a literal-or-resolved bare
    # output.json path, write()-ed with something that is not json.dumps.
    handle_names: Set[str] = set()
    for node in ast.walk(tree):
        call = None
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call) \
                        and isinstance(item.optional_vars, ast.Name):
                    call = item.context_expr
                    handle_name = item.optional_vars.id
                    _maybe_add_handle(call, handle_name, resolve, is_bare_output_json,
                                       is_write_mode, handle_names)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            _maybe_add_handle(node.value, node.targets[0].id, resolve,
                               is_bare_output_json, is_write_mode, handle_names)

    if handle_names:
        dumped = json_dump_targets(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "write" and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id in handle_names \
                    and node.func.value.id not in dumped and node.args \
                    and not is_json_dumps_call(node.args[0]):
                try:
                    written = ast.unparse(node.args[0])
                except Exception:                        # noqa: BLE001
                    written = "the value written"
                return ("writes %s directly to this step's own output.json "
                        "without ever calling json.dump/json.dumps -- every "
                        "downstream step opens this file with json.load(...) "
                        "unconditionally, and that raises on anything that "
                        "is not real JSON. Build the value as a dict and "
                        "write it with json.dump(data, f), not a plain "
                        "string." % written)

    # --- Indirect: a local function called with a literal/resolved
    # "output.json" argument, whose own body writes a file handle but
    # never touches json.dump/json.dumps anywhere inside it.
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if not funcs:
        return None

    def body_uses_json(func_node: ast.FunctionDef) -> bool:
        for n in ast.walk(func_node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("dump", "dumps") \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "json":
                return True
        return False

    def body_writes_a_handle(func_node: ast.FunctionDef) -> bool:
        for n in ast.walk(func_node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "write":
                return True
        return False

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in funcs):
            continue
        args_resolved = [resolve(a) for a in node.args]
        kw_resolved = [resolve(kw.value) for kw in node.keywords]
        if not any(is_bare_output_json(v) for v in args_resolved + kw_resolved):
            continue
        fn = funcs[node.func.id]
        if body_writes_a_handle(fn) and not body_uses_json(fn):
            return ("calls %s(...) with \"output.json\" as an argument, but "
                    "%s's own body writes to a file handle without ever "
                    "calling json.dump/json.dumps anywhere inside it -- "
                    "every downstream step opens this file with "
                    "json.load(...) unconditionally. Have %s serialize the "
                    "value with json.dump(data, f) before writing it."
                    % (node.func.id, node.func.id, node.func.id))
    return None


def _maybe_add_handle(call: ast.Call, handle_name: str, resolve, is_bare_output_json,
                       is_write_mode, handle_names: Set[str]) -> None:
    func = call.func
    is_open = (isinstance(func, ast.Name) and func.id == "open") or \
        (isinstance(func, ast.Attribute) and func.attr in ("open", "fdopen")
         and isinstance(func.value, ast.Name) and func.value.id == "os")
    if not is_open or not call.args:
        return
    if is_open and isinstance(func, ast.Attribute) and func.attr == "fdopen":
        # os.fdopen(fd, mode) never names a path -- nothing to resolve here,
        # the atomic-tempfile shape is caught by the indirect (function-call)
        # branch instead, keyed off the caller's own literal filename.
        return
    path = resolve(call.args[0])
    if is_bare_output_json(path) and is_write_mode(call):
        handle_names.add(handle_name)


# Three phrasings this project's own guidance and its own autofix
# (`intent.autofix_chain_output_json`) ever produce for a *write* promise:
# "...save its id as project_id in a new output.json in the same directory
# as the script" (the canonical hand-written form every current `LOCAL.md`
# template uses), the older "...in this directory's own output.json" form
# it replaced (still recognized so a plan or request drafted before the
# wording changed keeps matching), and "...save project_id to output.json
# as {\"project_id\": ...}" (the exact clause `autofix_chain_output_json`
# appends). All three name the key right next to the promise, in a fixed
# shape this project controls, so recovering it is a fact-lookup rather
# than a guess -- the same reason `intent._OUTPUT_JSON_KEY_RE` can recover
# a *read* promise's key. Limited to a single `[a-z][a-z0-9]*_id`
# identifier for the same reason that one is: a bare `id` or a multi-word
# phrase ("the project's id") names nothing a producer's own code can be
# checked against without guessing.
_PROMISED_WRITE_KEY_RE = re.compile(
    r"\bas\s+([a-z][a-z0-9]*_id)\s+in\s+"
    r"(?:a\s+new\s+output\.json\s+in\s+the\s+same\s+directory\s+as\s+"
    r"the\s+script"
    r"|this\s+directory's\s+own\s+output\.json)"
    r"|save\s+[a-z][a-z0-9]*_id\s+to\s+output\.json\s+as\s+\{\\?\"?"
    r"([a-z][a-z0-9]*_id)\\?\"?",
    re.IGNORECASE)


def _promised_output_json_keys(spec: str) -> List[str]:
    keys: List[str] = []
    for match in _PROMISED_WRITE_KEY_RE.finditer(spec or ""):
        key = (match.group(1) or match.group(2)).lower()
        if key not in keys:
            keys.append(key)
    return keys


def _promised_keys_from_texts(*texts: Optional[str]) -> List[str]:
    """`_promised_output_json_keys`, unioned across several texts rather
    than read from just one.

    Exists because the one text this project's own convention guarantees
    will state the promise verbatim -- the operator's or orchestrator's
    original request -- is not the only text a promise gets checked
    against. `own_output_write_block` and `gate_output_json_key` are
    both called with a *component's* `spec`, and for anything routed
    through the ordinary planner (as opposed to `--single-file`, which
    uses the request verbatim as its one component's spec) that spec is
    the planner's own paraphrase of the request, not the request itself.

    Measured live: a request read, verbatim, "create a project; save its
    id as project_id in a new output.json in the same directory as the
    script" -- an exact match for `_PROMISED_WRITE_KEY_RE`. The planner
    decomposed it into a single component whose saved SPEC read "...and
    then writing the resulting project ID to output.json" -- true to the
    request's *meaning*, and it drops the one literal token
    (`_promised_output_json_keys` needs `as project_id in ...`, not
    prose about "the resulting project ID") every mechanical check here
    depends on.     `gate_output_json_key` saw an empty promise list and
    stayed silent, correctly per its own rule of never guessing -- and
    `own_output_write_block`, seeing the same empty list, filled in a
    fabricated example key instead of admitting it had no real one to
    show (see that function's own docstring for what happened next).
    Neither function did anything wrong given only the paraphrase; the
    paraphrase was missing a fact the original request still had.

    So both now also check the original, pre-planner request text
    whenever a caller has it (every chain step does, following this
    project's own convention of one explicit `as X_id` promise per
    request) -- unioned with whatever `spec` itself recovers, spec's
    own matches ordered first since they are the more specific source
    when both happen to name something. A caller with no original
    request text (or one that also never states a promise) sees exactly
    what `_promised_output_json_keys` on `spec` alone would have shown;
    this function changes nothing for that case.
    """
    keys: List[str] = []
    for text in texts:
        for key in _promised_output_json_keys(text or ""):
            if key not in keys:
                keys.append(key)
    return keys


def _is_local_output_open(call: ast.Call, modes: str = "rwax") -> bool:
    """Is this an `open(...)` call whose literal path is this file's own,
    local `output.json` (never a sibling's, via `../`), opened in a mode
    among `modes`? Shared between `_local_output_dict_literals` (which
    only ever cares about a write) and `gate_output_json_overwritten`
    (which needs to tell a write from a read, to find a write that never
    merges what a read would have shown it)."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "open"
            and call.args):
        return False
    path = _literal_str_parts(call.args[0])
    if not path or not path.endswith("output.json") or "../" in path:
        return False
    mode = None
    if len(call.args) > 1:
        mode = _literal_str_parts(call.args[1])
    else:
        for kw in call.keywords:
            if kw.arg == "mode":
                mode = _literal_str_parts(kw.value)
    mode = mode or "r"
    return any(c in mode for c in modes)


def _handles_for_local_output(tree: ast.AST, modes: str = "rwax") -> set:
    """Every variable name ever bound to `open("output.json", <a mode in
    `modes`>)`, via a `with` statement or a plain assignment."""
    handle_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) \
                        and _is_local_output_open(call, modes) \
                        and isinstance(item.optional_vars, ast.Name):
                    handle_names.add(item.optional_vars.id)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call) \
                and _is_local_output_open(node.value, modes):
            handle_names.add(node.targets[0].id)
    return handle_names


def _dict_literal_by_name(tree: ast.AST) -> Dict[str, Optional[ast.Dict]]:
    """Every name assigned a dict *literal* exactly once anywhere in the
    file, mapped to that literal. A name assigned a dict literal more than
    once maps to `None` -- ambiguous, left unresolved rather than picking
    one arbitrarily.

    Shared by every scan below that needs to see through a dict built one
    line before it is written -- `output_data = {...}; json.dump(output_
    data, f)` -- rather than only an inline literal at the call site.
    Measured live, twice, in real generated code that used exactly this
    shape: `01_create_project/main.py` built `output_data = {"project_id":
    ...}` and passed the variable, and `_local_output_dict_literals`'s
    inline-only scan (before this existed) read that call site as
    unreadable and gave up entirely -- silently disabling `gate_output_
    json_key` on that file, which is how a request that explicitly said
    "save its id as first_id" shipped writing "project_id" instead, with
    every gate reporting clean on the first attempt.
    """
    by_name: Dict[str, Optional[ast.Dict]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    by_name[tgt.id] = (None if tgt.id in by_name
                                       else node.value)

    # A literal assigned once (often `data = {}`) and then mutated
    # afterward -- `data["key"] = value`, `data.update(...)` -- no longer
    # reflects what actually gets written, so its entry is invalidated
    # rather than reporting the stale, pre-mutation contents. This is the
    # exact "dynamically built dict" shape this project has already
    # decided must be left alone rather than guessed at.
    for node in ast.walk(tree):
        mutated_name = None
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    mutated_name = tgt.value.id
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("update", "setdefault", "pop",
                                       "popitem", "clear") \
                and isinstance(node.func.value, ast.Name):
            mutated_name = node.func.value.id
        if mutated_name is not None and mutated_name in by_name:
            by_name[mutated_name] = None
    return by_name


def _resolve_dict_literal(value: ast.AST,
                          by_name: Dict[str, Optional[ast.Dict]]
                          ) -> Optional[ast.Dict]:
    """`value` itself if it is already a dict literal; the literal it
    resolves to via `_dict_literal_by_name` if it is a bare variable
    reference; `None` otherwise (a call, an f-string, anything else)."""
    if isinstance(value, ast.Dict):
        return value
    if isinstance(value, ast.Name):
        return by_name.get(value.id)
    return None


def _local_output_dict_literals(tree: ast.AST) -> List[Optional[FrozenSet[str]]]:
    """One entry per attempt this file makes to write a dict to its own,
    local `output.json` -- `frozenset` of the dict's literal string keys
    when every key is a literal this scan can read, `None` when the dict
    is built in a shape this cannot fully see (a `**spread`, a
    non-literal key expression, or anything `gate_output_json_shape`
    itself could not prove is even a dict at all). `None` entries exist
    to be treated as "might contain anything" by the caller -- reporting
    a promised key missing because of an entry this scan could not read
    would be exactly the false positive this project has refused to ship
    everywhere else in this file.

    Resolves a dict assigned to a variable one line before the write
    (`output_data = {...}; json.dump(output_data, f)`) via `_dict_literal_
    by_name`/`_resolve_dict_literal`, not only an inline literal at the
    call site -- see `_dict_literal_by_name`'s own docstring for the live
    bug this closes.
    """
    handle_names = _handles_for_local_output(tree, "wax")
    if not handle_names:
        return []
    by_name = _dict_literal_by_name(tree)

    def _dict_keys(value: ast.AST) -> Optional[FrozenSet[str]]:
        resolved = _resolve_dict_literal(value, by_name)
        if resolved is not None:
            value = resolved
        if isinstance(value, ast.Dict):
            keys: List[str] = []
            for k in value.keys:
                if k is None:  # a **spread inside the literal
                    return None
                lit = _literal_str_parts(k)
                if lit is None:
                    return None
                keys.append(lit)
            return frozenset(keys)
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                and value.func.id == "dict":
            if value.args:  # dict(some_mapping) -- contents unknown
                return None
            return frozenset(kw.arg for kw in value.keywords if kw.arg)
        return None

    found: List[Optional[FrozenSet[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "dump" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "json" and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Name) \
                and node.args[1].id in handle_names:
            found.append(_dict_keys(node.args[0]))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "write" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in handle_names and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Attribute) \
                    and arg0.func.attr == "dumps" \
                    and isinstance(arg0.func.value, ast.Name) \
                    and arg0.func.value.id == "json" and arg0.args:
                found.append(_dict_keys(arg0.args[0]))
            else:
                found.append(None)  # some other .write(...) shape -- unknown
    return found


def _local_output_dict_write_nodes(tree: ast.AST) -> List[Optional[ast.Dict]]:
    """The same write attempts `_local_output_dict_literals` finds, but
    returning each one's actual, resolved `ast.Dict` node (after
    following one level of variable indirection, same as that function)
    instead of a frozenset of its keys -- `None` wherever that function
    would also give up.

    Needed by `autofix_output_json_key`, which has to locate one
    specific key's `ast.Constant` node to rewrite its exact source text
    in place; a frozenset has already thrown that position away. Kept
    as a separate function rather than changing what
    `_local_output_dict_literals` returns, since every existing caller
    of that one wants the cheaper, order-independent set."""
    handle_names = _handles_for_local_output(tree, "wax")
    if not handle_names:
        return []
    by_name = _dict_literal_by_name(tree)

    def _resolved(value: ast.AST) -> Optional[ast.Dict]:
        resolved = _resolve_dict_literal(value, by_name)
        return resolved if resolved is not None else (
            value if isinstance(value, ast.Dict) else None)

    found: List[Optional[ast.Dict]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "dump" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "json" and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Name) \
                and node.args[1].id in handle_names:
            found.append(_resolved(node.args[0]))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "write" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in handle_names and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Attribute) \
                    and arg0.func.attr == "dumps" \
                    and isinstance(arg0.func.value, ast.Name) \
                    and arg0.func.value.id == "json" and arg0.args:
                found.append(_resolved(arg0.args[0]))
            else:
                found.append(None)
    return found


def _output_json_dict_value_names(tree: ast.AST) -> List[Dict[str, str]]:
    """One entry per literal dict this file writes to its own `output.json`,
    mapping each literal string key to the plain variable name assigned as
    its value -- populated only for a (key, value) pair this scan can read
    with certainty: a literal string key, and a value that is a bare `Name`
    reference rather than a call, an f-string, or another literal. A key
    whose value is not a plain variable is left out entirely, since there
    is no variable name to compare against anything in that case.

    Deliberately narrower than `_local_output_dict_literals` (which only
    needs the key strings): this exists for `gate_output_json_wrong_
    variable`, which needs to know *which variable* ended up under a
    promised key, not merely that the key is present. Unlike that
    function, this one does resolve a dict built one line earlier and
    passed by name (`output_data = {...}; json.dump(output_data, f)`) --
    the exact shape the live bug this exists to catch used -- as long as
    the name is assigned a dict literal exactly once anywhere in the
    file; assigned more than once, it is left unresolved rather than
    guessed at.
    """
    handle_names = _handles_for_local_output(tree, "wax")
    if not handle_names:
        return []

    # A dict is not always built inline at the `json.dump(...)` call site --
    # see `_dict_literal_by_name`'s own docstring for the live bug this
    # resolution closes, shared with `_local_output_dict_literals`.
    by_name = _dict_literal_by_name(tree)

    def _dict_value_names(value: ast.AST) -> Optional[Dict[str, str]]:
        resolved = _resolve_dict_literal(value, by_name)
        if resolved is None:
            return None
        result: Dict[str, str] = {}
        for k, v in zip(resolved.keys, resolved.values):
            if k is None:
                continue
            lit = _literal_str_parts(k)
            if lit is None:
                continue
            if isinstance(v, ast.Name):
                result[lit] = v.id
        return result

    found: List[Dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "dump" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "json" and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Name) \
                and node.args[1].id in handle_names:
            d = _dict_value_names(node.args[0])
            if d:
                found.append(d)
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "write" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in handle_names and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Attribute) \
                    and arg0.func.attr == "dumps" \
                    and isinstance(arg0.func.value, ast.Name) \
                    and arg0.func.value.id == "json" and arg0.args:
                d = _dict_value_names(arg0.args[0])
                if d:
                    found.append(d)
    return found


def _all_locally_assigned_names(tree: ast.AST) -> set:
    """Every identifier ever bound by a plain assignment anywhere in the
    file, regardless of which function it is bound in -- deliberately not
    scoped to one function, because the live bug this exists to catch has
    the correct id computed inside a helper function and the wrong one
    written from the `__main__` block, two different scopes entirely."""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                for n in ast.walk(tgt):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                        names.add(n.id)
    return names


def gate_output_json_wrong_variable(spec: str, code: str) -> Optional[str]:
    """A component that creates more than one resource along the way can
    write the *right* key under the *wrong* value -- correct key name,
    wrong resource's id -- and nothing checking only the key name
    (`gate_output_json_key`) or only the shape (`gate_output_json_shape`)
    can see that, because both pass: the promised key is right there, and
    the write is a real JSON object.

    Measured live: a request read, verbatim, "create an applet; save its
    id as applet_id in output.json". Creating the applet's actual content
    requires the API's own `POST /api/applets/{id}/versions` endpoint (the
    applet's create endpoint only takes a slug/name; content is pushed by
    creating a version), so the generated file, reasonably, created both
    an applet and its first version in one function. That function
    computed the real applet id (`applet_id = applet_data["id"]`), printed
    it, and then returned the *version's* id instead -- and the
    `__main__` block saved that returned value under the promised key:
    `output_data = {"applet_id": applet_version_id}`. The next step in
    the chain read `applet_id`, got a real UUID, and got a 404 from the
    real platform three steps later: the UUID was real, just the wrong
    kind of resource.

    This is decidable without guessing at intent, on one narrow condition:
    the file must *itself* already contain a variable named exactly the
    promised key (case-insensitively) -- `applet_id = applet_data["id"]"`,
    in the example above -- so the comparison is never "this value looks
    wrong," it is "this file already computed and threw away a value it
    named exactly what the promise asked for, and saved a different
    variable instead." A promised key with no same-named variable
    anywhere in the file has nothing to compare against and is correctly
    left alone; a promised key whose only write already uses the
    identically-named variable is correctly left alone too, no matter
    what the variable holds.

    Advisory, not blocking. The comparison is exact-string, not semantic
    -- it cannot know whether `applet_id` unrelated to the write happens
    to be reused for something else in a large file, and a detector that
    fires on correct code costs more than it saves. Reported the same way
    `gate_platform_facts` is: printed to stderr so an operator sees it,
    never fed into the repair loop as a failure, since it would otherwise
    spend an attempt rewriting a file that may well be correct.
    """
    promised = _promised_output_json_keys(spec)
    if not promised:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    writes = _output_json_dict_value_names(tree)
    if not writes:
        return None
    assigned = _all_locally_assigned_names(tree)
    for key in promised:
        if key not in assigned:
            continue  # nothing in this file is named exactly this promise
        for write in writes:
            used = write.get(key)
            if used is not None and used.lower() != key:
                return (
                    "output.json saves \"%s\" from the variable `%s`, but "
                    "this file also has a variable named exactly `%s` "
                    "assigned elsewhere that is never used for this write. "
                    "If this component creates more than one resource "
                    "along the way (e.g. a parent record and a version or "
                    "sub-resource of it), \"%s\" must hold that exact "
                    "resource's own id -- check which of the two variables "
                    "actually belongs under this key."
                    % (key, used, key, key))
    return None


def gate_output_json_overwritten(code: str) -> Optional[str]:
    """A helper that writes this file's own `output.json` is called more
    than once, and never reads the file back first -- so only the *last*
    call's value survives, silently discarding everything an earlier
    call in the very same run already wrote.

    Measured live: `01_create_project`'s own request said only "create a
    project; save its id as project_id" -- but the generated file also
    created a task (an endpoint the request never asked for, sitting in
    the API slice alongside the one it did need), and saved *that* id
    through the same helper, `print_and_save_id(key, id_value)`, which
    built `output_data = {}` fresh and wrote it every single call. The
    project-creation call wrote `{"project_id": ...}` first; the
    task-creation call, later in the same `main()`, wrote
    `{"task_id": ...}` on top of it, and `output.json` ended the run
    holding only the second. `gate_output_json_key` had nothing to say
    about it: the dict is passed to `json.dump` by variable name, built
    up via a later subscript assignment (`output_data[f"{key}_id"] =
    id_value`) rather than an inline literal, which is exactly the shape
    that scan already declines to guess the contents of rather than risk
    a false positive.

    This does not try to guess which key wins -- that depends on
    execution order, which is not decidable from source. What is
    decidable is the shape that makes a second call destructive at all:
    a function containing a write to the local `output.json`, called
    more than once anywhere in this file, whose own body never reads
    that file back (`open(..., "r")`/no read-then-merge) before writing
    to it again. A function that loads what is already there before
    writing is safe to call as many times as it likes -- only one that
    starts fresh every time is a defect for calling twice.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    writer_funcs: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = bool(_handles_for_local_output(node, "wax"))
        reads = bool(_handles_for_local_output(node, "r"))
        if writes and not reads:
            writer_funcs.append(node.name)
    if not writer_funcs:
        return None

    call_counts = {name: 0 for name in writer_funcs}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in call_counts):
            call_counts[node.func.id] += 1

    repeated = [name for name, n in call_counts.items() if n >= 2]
    if not repeated:
        return None
    return ("%s writes this file's own output.json and is called %d "
            "times, but its own body never reads output.json back before "
            "writing to it -- each call starts from a fresh dict, so only "
            "the LAST call's value survives and every earlier call's id "
            "is silently discarded. Either call it only once (with "
            "everything this step needs to save, gathered first), or have "
            "it load the existing output.json and merge into that "
            "instead of starting from {} every time."
            % (repeated[0], call_counts[repeated[0]]))


_READ_METHODS = {"read", "readline", "readlines"}
# ast.Call.func.value.attr does not exist for plain-string `mode` argument
# reading -- writing modes only, checked against the literal text of the
# `mode` argument (or its default, "r", when omitted).
_WRITE_ONLY_MODE_RE = re.compile(r"^[wxa]")


def gate_write_then_read_same_handle(code: str) -> Optional[str]:
    """A file opened for writing is read back through the same handle,
    without ever reopening it -- and CPython does not allow that: calling
    `.read()`/`.readline()`/`.readlines()` on a handle whose mode is
    write-only raises `io.UnsupportedOperation: not readable` the instant
    it runs, unconditionally, no matter what was actually written.

    Measured live, repeatedly, across several completely independent
    write attempts on different nodes and different models (a first
    attempt, an idle-race restart, a model escalation, and a CONTRACT/SPEC
    replan all failed identically): a generated `main.py` for "create a
    project; save its id ... in output.json" opened `output.json` in
    `"w"` mode specifically to write the id, and then, still inside the
    same `with` block or against the same bound name, tried to read the
    content back -- to log it, confirm it, or return it -- without
    reopening the handle for reading. The condensed one-line summary this
    project's own `history_trail` shows an operator (`smoke:
    io.UnsupportedOperation: not readable`) says the exception's class
    and message but throws away the traceback's own file/line, which is
    exactly the information that would have named this pattern directly
    instead of leaving it looking like unexplained noise repeating across
    four unrelated attempts.

    No rewrite by a different model fixes this, because the mistake is
    not about *what* the code does with the id, it is a fixed, mechanical
    fact about the file mode -- which is exactly why escalating to a
    stronger model and asking the planner to reconsider the CONTRACT both
    reproduced the identical error: neither one addresses a mode
    argument, and there was nothing else to fix.

    Deliberately narrow, matching every other gate in this module's "no
    evidence, no claim" rule: only a `with open(...) as NAME:` binding is
    tracked (the overwhelmingly common shape, and the one that lets this
    check see exactly which lines are inside the same scope the handle is
    valid for), the mode has to be a literal string starting with `w`,
    `x`, or `a` and containing no `r`/`+` (a genuine write-only open,
    never `"r+"` or `"a+"`, both of which are legitimately readable), and
    the read call has to name that exact bound variable, not merely any
    `.read()` anywhere in the file. A file that reopens the same path for
    reading afterward is a different, correct pattern and is not this.
    """
    found = _find_write_then_read(code)
    if found is None:
        return None
    call, mode, handle, read_attr, _stmt_idx, _with_node = found
    return (
        "opens %r in write-only mode %r as `%s`, then "
        "calls `%s.%s(...)` on that same handle inside "
        "the same `with` block -- this raises "
        "`io.UnsupportedOperation: not readable` "
        "unconditionally, every time, regardless of what "
        "was written. If the content needs to be read "
        "back (to log it, confirm it, or return it), "
        "keep the value in the variable that was already "
        "written rather than re-reading the file, or "
        "reopen the same path in a separate `with "
        "open(..., \"r\") as ...:` block afterward."
        % (getattr(call.args[0], "value", "<path>")
           if call.args and isinstance(call.args[0], ast.Constant)
           else "<path>",
           mode, handle, handle, read_attr))


def _find_write_then_read(code: str
                          ) -> Optional[Tuple[ast.Call, str, str, str, int,
                                              ast.With]]:
    """Shared detection between the gate above and the autofix below, so
    the two can never quietly drift apart about what counts as this
    defect. Returns `(open_call, mode, handle, read_method,
    first_offending_stmt_index, with_node)` for the first match, or
    `None`. `first_offending_stmt_index` is the index within
    `with_node.body` of the first statement that contains the offending
    read call anywhere inside it -- the autofix needs a *statement*
    boundary to split on, not just the call site, since the call is
    usually nested (`print(f.read())`, not a bare `f.read()`).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in ("open", "fdopen")):
                continue
            mode = None
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
                mode = call.args[1].value
            else:
                for kw in call.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
            if not isinstance(mode, str) or not _WRITE_ONLY_MODE_RE.match(mode) \
                    or "+" in mode:
                continue
            if not isinstance(item.optional_vars, ast.Name):
                continue
            handle = item.optional_vars.id
            for idx, stmt in enumerate(node.body):
                for inner in ast.walk(stmt):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Attribute)
                            and inner.func.attr in _READ_METHODS
                            and isinstance(inner.func.value, ast.Name)
                            and inner.func.value.id == handle):
                        return call, mode, handle, inner.func.attr, idx, node
    return None


def autofix_write_then_read_same_handle(code: str) -> Optional[str]:
    """Deterministically repairs the defect `gate_write_then_read_same_
    handle` finds, with no model call at all.

    Unlike almost every other gate in this module, the fix here needs no
    judgement: the file mode is a fixed, mechanical fact, so "reopen the
    same path for reading before the first statement that needs to read
    it" is the one and only correct repair, every time, regardless of
    what the surrounding code is trying to do. Escalating to a stronger
    model or asking the planner to reconsider the CONTRACT (both real
    rungs of the ordinary repair ladder) cannot do better than this,
    because neither one is a question of judgement -- which is exactly
    why both were measured to reproduce the identical error live rather
    than fix it.

    Implemented as a line-level text splice rather than an AST rewrite
    unparsed back to source, on purpose: `ast.unparse` would regenerate
    the *entire* file from the tree, discarding every comment and any
    formatting choice this project's own gates do not care about but a
    human reading the file later might. Splicing in one new line at the
    original `with` statement's own indentation, immediately before the
    first offending statement, changes nothing else in the file -- the
    statements from that point on were already indented one level deeper
    than the `with` header, which is exactly the indentation a *second*
    `with` header at the same column needs its own body to sit at, so
    they become that new block's body without needing to be touched.

    Returns `None` -- decline to fix -- whenever the offending statement
    is the *first* statement in the `with` body: that shape means nothing
    was actually written before the read was attempted, which is not the
    "write, then forgot to reopen" pattern this exists to fix, and
    guessing at a `pass` to keep the first block non-empty is exactly the
    kind of invented content this project's "no evidence, no claim" rule
    exists to avoid. Left to the ordinary model-repair path instead.
    """
    found = _find_write_then_read(code)
    if found is None:
        return None
    call, mode, handle, _read_attr, idx, node = found
    if idx == 0:
        return None
    path_src = ast.get_source_segment(code, call.args[0]) if call.args else None
    if path_src is None:
        return None

    lines = code.splitlines(keepends=True)
    with_header_line = node.lineno  # 1-indexed
    indent = with_header_line and (
        len(lines[with_header_line - 1])
        - len(lines[with_header_line - 1].lstrip(" \t")))
    offending_stmt = node.body[idx]
    insert_before = offending_stmt.lineno - 1  # 0-indexed line to insert before
    new_line = ("%s" % (" " * indent)
               + "with %s(%s, \"r\") as %s:\n"
               % (call.func.id, path_src, handle))
    lines.insert(insert_before, new_line)
    return "".join(lines)


def _main_guard(tree: ast.Module) -> Optional[ast.If]:
    """The `if __name__ == "__main__":` node, or `None`.

    Matches either operand order (`__name__ == "__main__"` and
    `"__main__" == __name__`) since both are correct Python and this
    project's own generated files are not consistent about which one they
    write.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)):
            continue
        operands = [test.left] + test.comparators
        names = [o.id for o in operands if isinstance(o, ast.Name)]
        consts = [o.value for o in operands if isinstance(o, ast.Constant)]
        if "__name__" in names and "__main__" in consts:
            return node
    return None


def _top_level_defs(tree: ast.Module) -> Tuple[Set[str], Set[str]]:
    """Top-level function/class names in `tree`, split into ones that do
    real work and ones whose body is trivial (bare `pass`, a docstring
    with nothing else, or `...`) -- a file defining nothing but stubs has
    no "real job" for a dead `__main__` guard to have failed to do."""
    real: Set[str] = set()
    trivial: Set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = [n for n in node.body
                if not (isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Constant)
                        and isinstance(n.value.value, str))]
        if (not body or all(isinstance(n, ast.Pass) for n in body)
                or (len(body) == 1 and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value is Ellipsis)):
            trivial.add(node.name)
        else:
            real.add(node.name)
    return real, trivial


def _guard_calls_local(guard: ast.If, real_defs: Set[str]) -> bool:
    for stmt in guard.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and node.id in real_defs:
                return True
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in real_defs):
                return True
    return False


def gate_dead_entry_point(code: str) -> Optional[str]:
    """The `if __name__ == "__main__":` guard exists, and never calls
    anything this file itself defines -- so running it does nothing.

    Measured live: a `main.py` whose job was to create a project defined
    a full `Client` class with a working `create_project` method (POST,
    read the response, write `output.json`) and closed with:

        if __name__ == '__main__':
            # This block is for local testing/demonstration and should
            # not be part of the final submission logic ...
            pass

    `interface_gate` had already flagged this exact file for a real
    defect -- it redeclared `Client`, which belonged to a sibling
    `client.py` -- and printed "still duplicated after repair; fix by
    hand," advisory only. Nothing else in the pipeline treats "the entry
    point's guard is a no-op" as a defect at all: `gate_import` only runs
    module-level code, `gate_smoke` only runs whatever the guard actually
    calls, and an empty guard makes both of them trivially pass -- no
    exception, no traffic to the mock, "3/3 passed every gate." The
    orchestrator then ran `python3 main.py` for real, got exit code 0,
    reported "01_create_project succeeded," and never wrote
    `output.json` at all. Every one of the six downstream chain steps
    that read that file then failed with the same
    `FileNotFoundError: ../01_create_project/output.json` -- the whole
    chain died from one dead stub that looked, to every existing gate,
    identical to success.

    Scoped narrowly on both sides of the check, the same way every other
    detector in this file is, so it cannot fire on a file that has
    nothing to call in the first place: `_top_level_defs` only counts a
    function or class as "real" when its body does more than `pass` /
    a docstring / `...`, so a pure-constants or pure-dataclass module
    that legitimately has nothing to do when run standalone is left
    alone. And the guard is read structurally, not by name -- it does
    not matter whether the real work happens through `main()` (this
    project's own convention everywhere else) or through instantiating
    a class and calling a method directly; either one satisfies the
    check, and only a guard that calls neither is a defect.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    guard = _main_guard(tree)
    if guard is None:
        return None
    real, _trivial = _top_level_defs(tree)
    if not real:
        return None
    if _guard_calls_local(guard, real):
        return None
    return (
        "the `if __name__ == \"__main__\":` guard never calls any of "
        "this file's own top-level definitions (%s) -- running this "
        "file does nothing: no request is made, nothing is printed, "
        "output.json is never written, and every downstream step "
        "reading this file's output fails with a FileNotFoundError "
        "that looks like the wrong file's fault. Call whichever of "
        "these actually performs this file's job from inside the "
        "guard -- `main()` if one is defined, or instantiate the class "
        "and call its method -- the same way every other generated "
        "entry point in this project does with "
        "`if __name__ == \"__main__\": main()`."
        % ", ".join(sorted(real)))


def autofix_dead_entry_point(code: str) -> Optional[str]:
    """The one case of `gate_dead_entry_point`'s defect this project can
    fix with no judgement call: the file already defines a top-level
    `main()` that does real work, and the convention every other
    generated entry point in this project already follows is exactly
    `if __name__ == "__main__": main()`. Splicing that in is not a guess
    at intent here the way it would be for, say, picking which class to
    instantiate or which of several methods to call -- `main` naming the
    one function meant to run when the file runs is this project's own
    fixed convention, not this file's choice to make.

    Declines (`None`) for every other shape, including the actual live
    failure this gate was built from -- a file with no `main()` at all,
    only a class and methods -- because there `main()`-if-defined is not
    available, and guessing which method the class is meant to run
    (and with what arguments) is exactly the kind of invented content
    this project's autofixes refuse to produce. Left to the ordinary
    model-repair path, which has the SPEC and can actually decide.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    guard = _main_guard(tree)
    if guard is None:
        return None
    real, _trivial = _top_level_defs(tree)
    if "main" not in real or _guard_calls_local(guard, real) or not guard.body:
        return None
    lines = code.splitlines(keepends=True)
    first, last = guard.body[0], guard.body[-1]
    indent = " " * first.col_offset
    lines[first.lineno - 1:last.end_lineno] = ["%smain()\n" % indent]
    return "".join(lines)


_ID_ENV_NAME_RE = re.compile(r"(?:^|_)(ID|UUID)$", re.IGNORECASE)


def _is_os_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def gate_invented_env_id(code: str,
                         allowed: Optional[Sequence[str]] = None
                         ) -> Optional[str]:
    """Reads a business id (`*_ID`/`*_UUID`) straight out of the process
    environment, instead of from a sibling step's `output.json` or this
    step's own API responses -- the only two places an id ever legitimately
    comes from in this project's chains.

    Measured live: a `main.py` whose whole job was "read task_id from
    ../02_create_task/output.json; upload units to the task" additionally
    demanded `os.environ["PROJECT_ID"]`, raising if it was unset. Nothing
    in that request, nor anywhere else in the chain, ever promises a
    `PROJECT_ID` -- the real endpoint it calls only needs the task id it
    already had. This passed every existing gate: `placeholder_env` fills
    any missing `*_ID`-shaped variable with a UUID specifically so a smoke
    run can exercise the file, so the invented requirement never crashes
    generation. It only crashes for real, three attempts and a
    whole-loop restart later, when the actual orchestrator -- which sets
    only the pool's own credential names, never a per-step business id --
    runs the file with nothing to fill the gap.

    `allowed` is the pool's real credential vocabulary (`API_BASE_URL`,
    `API_KEY`, plus anything a real `.env` contributes) -- exactly the
    set `usage.py`'s own "what does this file need" scan already treats
    as legitimate environment reads. Anything outside that set, shaped
    like an id, is not a credential; it is plumbing the writer invented
    because it could not find the id anywhere else, which is itself the
    signal that the id was never actually required. Scoped to that
    specific shape on purpose -- a *credential* named `SOME_SERVICE_ID`
    would be indistinguishable from a real one by shape alone, which is
    exactly what `allowed` (sourced from the pool's actual configured
    names and the operator's real `.env`) is for: nothing on that list is
    ever flagged, no matter how id-shaped its name looks.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    allowed_set = {a.upper() for a in (allowed or DEFAULT_ENV_NAMES)}
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            name = _const_str(node.slice)
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)):
            if node.func.attr == "get" and _is_os_environ(node.func.value):
                if node.args:
                    name = _const_str(node.args[0])
            elif (node.func.attr == "getenv"
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "os"):
                if node.args:
                    name = _const_str(node.args[0])
        if (name and name.upper() not in allowed_set
                and _ID_ENV_NAME_RE.search(name)):
            return (
                "reads `os.environ` for `%s`, which is shaped like a "
                "business id, not a credential -- this project's ids "
                "come only from a sibling step's own output.json or "
                "this step's real API responses, never from the "
                "environment (the orchestrator only ever exports "
                "%s). If `%s` is genuinely unavailable from any sibling "
                "output.json this step already reads, that is a sign "
                "the endpoint being called does not actually need it; "
                "re-check its real required fields rather than adding "
                "an environment variable nothing will ever set."
                % (name, " or ".join(sorted(allowed_set)), name))
    return None


DEFAULT_ENV_NAMES = ("API_BASE_URL", "API_KEY")


def gate_output_json_key(spec: str, code: str,
                         extra_spec: str = "") -> Optional[str]:
    """A producer's own `output.json` write uses a different key than the
    one its own request promised -- and the request was right there.

    `extra_spec`, when the caller has it, is the component's *original,
    pre-planner request text* -- see `_promised_keys_from_texts` for why
    this matters: a planner-produced SPEC can paraphrase away the exact
    key name a request stated explicitly, and this check has always
    been "compare the write against the promise," never "compare the
    write against the SPEC specifically." Checking both texts, not only
    `spec`, is that same rule applied to the actual location the promise
    turned out to live in for a live failure this exact gate should have
    caught and initially did not.

    Measured live: a request read, verbatim, "create a project; save its
    id as project_id in this directory's own output.json" -- the exact
    phrasing `LOCAL.md` mandates precisely so both sides of a chain name
    the same key without guessing. The producer created the project for
    real, printed `project_id: <uuid>`, and then wrote `output.json` as
    `{"id": "<uuid>"}` -- a real dict, not a bare scalar, so
    `gate_output_json_shape` had nothing to say about it. The consumer's
    own request, symmetrically correct, read "read project_id from
    ../01_create_project/output.json," `chain_output_json_problems`
    confirmed the two requests agreed on the name, and the run still
    failed with `KeyError: 'project_id'` against a file that had every
    id in it except the one under that name. Every mechanical check this
    project had already built passed: the shape was right, the sibling
    path was right, the write existed, the chain's own *text* was
    internally consistent. None of them ever compared what the code
    actually wrote against what the request actually promised.

    This is exactly that missing comparison, and it is decided the same
    way every other gate in this project is when a right answer already
    exists to check against: `_promised_output_json_keys` recovers the
    key from the request's own text (the two fixed phrasings this
    project's guidance and its own autofix ever produce -- see that
    function), and `_local_output_dict_literals` finds every fully
    literal dict this file writes to its own `output.json`. A promised
    key absent from *every* fully literal write, with no write left
    unreadable enough to plausibly contain it, is provably wrong -- not
    a guess, because the promise and the write are both being read
    directly rather than inferred.

    Silent whenever there is nothing to compare: no promise recoverable
    from the spec (an older or more free-form request naming no key),
    the file makes no local output.json write at all (`gate_missing_
    own_output_write`'s job), or every write this scan found is built in
    a shape it cannot fully read (a variable dict, a `**spread`) -- in
    every one of those cases reporting a mismatch would be a guess this
    project has repeatedly refused to make everywhere else in this file.
    """
    promised = _promised_keys_from_texts(spec, extra_spec)
    if not promised:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    writes = _local_output_dict_literals(tree)
    if not writes:
        return None  # gate_missing_own_output_write's job, not this one
    if any(w is None for w in writes):
        return None  # at least one write is unreadable -- might match
    all_keys: FrozenSet[str] = frozenset().union(*writes) if writes else frozenset()
    missing = [k for k in promised if k not in all_keys]
    if not missing:
        return None
    return ("the request promises to save %s in this directory's own "
            "output.json, but the file's own write%s to output.json "
            "use%s a different key name (found: %s). Downstream steps "
            "read this file by the exact promised name -- write it as "
            "{\"%s\": ...}, matching the request, not whatever name felt "
            "natural."
            % (" and ".join("\"%s\"" % k for k in missing),
               "s" if len(writes) != 1 else "",
               "" if len(writes) != 1 else "s",
               ", ".join(sorted(all_keys)) or "no string keys at all",
               missing[0]))


def autofix_output_json_key(spec: str, code: str,
                            extra_spec: str = "") -> Optional[str]:
    """Mechanically renames the one wrong key `gate_output_json_key`
    flags, when doing so is not a guess.

    Measured live: a request promised "project_id", the file wrote
    `output_data = {"result_id": project_id}` -- a real, single,
    fully-literal dict, with the *right value* under the *wrong name*.
    There is nothing to decide here once the promise is known: rename
    the one key. No repair attempt, no model call, the same reasoning
    `autofix_stale_precondition_before_overwrite` already applies to a
    different provably-mechanical shape.

    Deliberately declines (returns `None`, leaving the blocking gate
    and the ordinary repair loop to handle it) the moment the rename
    would be a guess rather than a fact:

    - more than one write to the local output.json (which one to touch
      is ambiguous), or the one write is in a shape `_local_output_dict_
      write_nodes` cannot fully resolve;
    - not exactly one promised key missing from the write, or not
      exactly one key in the write that isn't any promised key -- with
      more than one of either, there is no unambiguous 1:1
      correspondence between "the key that's wrong" and "the key it
      should have been." A write correctly carrying extra keys the
      request never promised (a component saving more than the one id
      a chain step reads) is common enough that guessing across several
      candidates would rename something that was never wrong.

    The rename is a source-text splice of the one `ast.Constant` key
    node -- never `ast.unparse` on the surrounding dict or the module --
    preserving the original quote character and everything else about
    the line untouched. Re-parses and re-runs the gate on the result
    before returning it, so a bug here fails closed rather than shipping
    an unverified rewrite.
    """
    promised = _promised_keys_from_texts(spec, extra_spec)
    if not promised:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    write_nodes = _local_output_dict_write_nodes(tree)
    if len(write_nodes) != 1 or write_nodes[0] is None:
        return None
    dict_node = write_nodes[0]

    actual_keys: List[str] = []
    key_nodes: List[ast.Constant] = []
    for k in dict_node.keys:
        if k is None or not isinstance(k, ast.Constant) \
                or not isinstance(k.value, str):
            return None                  # a **spread or non-literal key
        actual_keys.append(k.value)
        key_nodes.append(k)

    missing = [k for k in promised if k not in actual_keys]
    extra = [k for k in actual_keys if k not in promised]
    if len(missing) != 1 or len(extra) != 1:
        return None                      # no unambiguous 1:1 rename

    wrong_key_node = key_nodes[actual_keys.index(extra[0])]
    lines = code.splitlines(keepends=True)
    lineno = wrong_key_node.lineno - 1
    line = lines[lineno]
    start_col = wrong_key_node.col_offset
    end_col = wrong_key_node.end_col_offset
    if wrong_key_node.lineno != wrong_key_node.end_lineno:
        return None                      # a key literal split across
                                          # lines -- not worth the risk
    original_literal = line[start_col:end_col]
    quote = original_literal[0] if original_literal[:1] in ("'", '"') else '"'
    new_literal = "%s%s%s" % (quote, missing[0], quote)
    lines[lineno] = line[:start_col] + new_literal + line[end_col:]
    fixed = "".join(lines)

    try:
        ast.parse(fixed)
    except SyntaxError:
        return None
    if gate_output_json_key(spec, fixed, extra_spec) is not None:
        return None
    return fixed


def _is_argparse_parser_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
        return True
    if isinstance(func, ast.Name) and func.id == "ArgumentParser":
        return True
    return False


def gate_required_cli_args(code: str) -> Optional[str]:
    """A chain-step entry point demands a command-line argument nobody
    will ever supply.

    This project's own orchestration convention (`LOCAL.md`'s `run_step`,
    the thing every generated `orchestrate.sh` uses) always invokes a
    step's entry point as `python3 main.py` with zero command-line
    arguments -- every value a step needs is supposed to come from
    `os.environ` or a sibling step's `output.json`, never from a flag.
    Measured live: a request that never said where an applet id, version
    string, or artifact URL should come from (no prior step in the chain
    ever created an applet, and none was named) got a `main.py` whose
    parser declared `applet_id` positional and `--version`/
    `--artifact_url` `required=True` -- a reasonable-looking choice in
    isolation, and one that can never once succeed inside this harness:
    every real invocation dies at `argparse` before `main()` runs,
    printing the exact same usage error, and `team do --retry --run`
    reported it FAILED three times running because retrying reruns the
    identical zero-argument invocation against identical code -- there
    was never anything for a repair attempt to fix, since the code was
    not wrong, the request was silent about where these values come
    from.

    This gate exists because nothing else in the pipeline would ever
    have caught it before that third identical failure: `gate_smoke`'s
    own placeholder-filling fills a required argument with a UUID or a
    string specifically so the smoke run can still exercise the file,
    which is exactly what let this ship "3/3 passed every gate" the
    first time -- the placeholder answered the required flag, and
    nothing ever checked whether the real orchestrator would too.

    Only wired in for the one call site that actually knows a file is
    this kind of entry point (`--api`, `--single-file`, chain-style);
    see the `forbid_required_cli_args` parameter on `generate_file`. A
    genuine "write me a CLI tool" request, run outside this harness by a
    human who will type real flags, is never checked, and correctly so
    -- a positional argument there is the whole point of the request,
    not a defect.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    parser_vars: set = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and _is_argparse_parser_call(node.value)):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    parser_vars.add(tgt.id)
    if not parser_vars:
        return None
    required: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"
                and isinstance(func.value, ast.Name)
                and func.value.id in parser_vars):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) \
                or not isinstance(node.args[0].value, str):
            continue
        name = node.args[0].value
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if name.startswith("-"):
            req = kwargs.get("required")
            if isinstance(req, ast.Constant) and req.value is True:
                required.append(name)
        else:
            # A positional is mandatory by default -- only `nargs='?'`/
            # `nargs='*'` or an explicit `default=` make it optional.
            nargs = kwargs.get("nargs")
            nargs_val = nargs.value if isinstance(nargs, ast.Constant) else None
            if nargs_val not in ("?", "*") and "default" not in kwargs:
                required.append(name)
    if not required:
        return None
    return ("this file's own argparse parser requires %s, but every step "
            "in this orchestration is run as `python3 main.py` with no "
            "command-line arguments at all -- this will fail at argument "
            "parsing on every real invocation, before main() ever runs, "
            "and no rewrite of the logic fixes that, because the logic is "
            "not what's wrong. Read %s from os.environ, a sibling step's "
            "output.json (see the request for which file), or a hardcoded "
            "default -- never require it as a command-line flag."
            % (", ".join(required), ", ".join(required)))


_REQUESTS_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request"})

# `sys.stdlib_module_names` only exists from 3.10; this project targets
# 3.9+, so a small hardcoded list of the modules a generated file
# plausibly imports covers the fallback -- this only has to distinguish
# "a name that is almost certainly this project's own sibling file" from
# "a name that is almost certainly a real third-party/stdlib package,"
# not enumerate the whole standard library.
_STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    "os", "sys", "re", "json", "time", "math", "random", "typing",
    "pathlib", "collections", "itertools", "functools", "datetime",
    "argparse", "subprocess", "shutil", "tempfile", "uuid", "io",
    "csv", "sqlite3", "logging", "unittest", "dataclasses", "enum",
    "hashlib", "base64", "urllib", "http", "socket", "threading",
    "asyncio", "contextlib", "copy", "string", "textwrap", "traceback",
    "warnings", "abc", "queue", "shlex", "glob",
}


def _references_name_load(node: ast.AST, varname: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == varname
               and isinstance(n.ctx, ast.Load) for n in ast.walk(node))


def _is_absence_check(test: ast.AST, varname: str) -> bool:
    """`not VAR`, `VAR is None`/`None is VAR`, or either inside a `BoolOp`.

    Deliberately narrower than "any check involving VAR": a guard testing
    VAR against a specific expected *value* (`if status != 'draft':
    raise`) is an ordinary state-machine precondition and must be left
    alone -- this only matches a guard asking whether VAR exists at all,
    which is the shape that can never be satisfied for a field being set
    for the first time.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _references_name_load(test.operand, varname)
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(
            test.ops[0], ast.Is):
        left, right = test.left, test.comparators[0]
        if (isinstance(left, ast.Name) and left.id == varname
                and isinstance(right, ast.Constant) and right.value is None):
            return True
        if (isinstance(right, ast.Name) and right.id == varname
                and isinstance(left, ast.Constant) and left.value is None):
            return True
        return False
    if isinstance(test, ast.BoolOp):
        return any(_is_absence_check(v, varname) for v in test.values)
    return False


class _StaleMatch:
    def __init__(self, func, assign_node, guard_node, write_node,
                varname, field, written_as):
        self.func = func
        self.assign_node = assign_node
        self.guard_node = guard_node
        self.write_node = write_node
        self.varname = varname
        self.field = field
        self.written_as = written_as


def _find_stale_precondition_matches(tree: ast.AST) -> List["_StaleMatch"]:
    """Every instance of: a field's OLD value is read back from a GET
    response, required to be present, and then overwritten with a NEW,
    already-known value under a different name. Shared by the gate
    (which only needs to report it) and the autofix (which needs the
    actual nodes to remove).

    Scoped tightly to avoid the false positive that matters most: a
    guard comparing a fetched field to a specific expected *state*
    (`if status != 'draft': raise`) is an ordinary, legitimate
    state-machine precondition and is never touched -- only a guard
    that demands the field merely be *present*, checked via
    `_is_absence_check`, matches. And a write that re-sends the same
    variable it just read (a genuine read-modify-write, e.g. bumping a
    counter) is left alone too; this only fires when the outgoing value
    is demonstrably a different name than the one the guard checked.
    """
    matches: List[_StaleMatch] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # VAR = something.get("field") / something["field"]
        reads: Dict[str, str] = {}
        read_nodes: Dict[str, ast.Assign] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                continue
            value = node.value
            field = None
            if (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "get" and value.args
                    and isinstance(value.args[0], ast.Constant)
                    and isinstance(value.args[0].value, str)):
                field = value.args[0].value
            elif (isinstance(value, ast.Subscript)
                    and isinstance(value.slice, ast.Constant)
                    and isinstance(value.slice.value, str)):
                field = value.slice.value
            if field is not None:
                reads[node.targets[0].id] = field
                read_nodes[node.targets[0].id] = node
        if not reads:
            continue

        # a guard `if <absence check on VAR>: ... raise ...`
        guarded: Dict[str, ast.If] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.If)
                    and any(isinstance(n, ast.Raise)
                           for n in ast.walk(node))):
                continue
            for varname in reads:
                if _is_absence_check(node.test, varname):
                    guarded[varname] = node
        if not guarded:
            continue

        # A `json=` kwarg is usually a plain variable reference to a
        # dict built a few lines earlier (`payload = {...}; requests.
        # patch(url, json=payload)`), not an inline literal -- resolve
        # one level of that before giving up on a Name.
        dict_literals: Dict[str, ast.Dict] = {}
        for node in ast.walk(func):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Dict)):
                dict_literals[node.targets[0].id] = node.value

        # a later write whose json= body sets the SAME field name to a
        # DIFFERENT variable than the one the guard checked.
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("post", "put", "patch",
                                          "request")):
                continue
            for kw in node.keywords:
                if kw.arg != "json":
                    continue
                body = kw.value
                if isinstance(body, ast.Name):
                    body = dict_literals.get(body.id)
                if not isinstance(body, ast.Dict):
                    continue
                for key_node, val_node in zip(body.keys, body.values):
                    if not (isinstance(key_node, ast.Constant)
                            and isinstance(key_node.value, str)):
                        continue
                    field = key_node.value
                    for varname in guarded:
                        if reads[varname] != field:
                            continue
                        same_var = (isinstance(val_node, ast.Name)
                                   and val_node.id == varname)
                        if same_var:
                            continue
                        try:
                            written_as = ast.unparse(val_node)
                        except Exception:                # noqa: BLE001
                            written_as = "<expr>"
                        matches.append(_StaleMatch(
                            func, read_nodes[varname], guarded[varname],
                            node, varname, field, written_as))
    return matches


def gate_stale_precondition_before_overwrite(code: str) -> Optional[str]:
    """Reading a field's OLD value, requiring it to already exist, and
    then overwriting it with a NEW value you already had is backwards --
    it can only ever fail on the exact case the write exists to handle.

    Measured live: a script correctly read `project_id` and a freshly
    created `version_id` from two sibling `output.json` files -- it had
    everything it needed. Before using them, it also did
    `GET /api/projects/{project_id}`, read `applet_version_id` back out
    of that response, and raised if it was falsy, with the comment "we
    need the current applet_version_id to use it in the PATCH request."
    The PATCH body never actually used that fetched value -- it set
    `applet_version_id` to the already-known `version_id` instead. A
    brand-new project has no `applet_version_id` yet; that absence is
    not a timing issue or a malformed response, it is the exact, normal,
    permanent state of the one case this script is for (assigning a
    version for the first time), so the guard fails every single time.

    See `_find_stale_precondition_matches` for exactly what does and
    does not match; `autofix_stale_precondition_before_overwrite` below
    removes it mechanically rather than spending a repair attempt on
    something this decidable.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    matches = _find_stale_precondition_matches(tree)
    if not matches:
        return None
    m = matches[0]
    return (
        "%r is read back from a prior GET response and required to "
        "already be non-empty before raising, but the write on line %d "
        "sets that exact same field (%r) to a different, already-known "
        "value (%s) -- this checks the OLD value of the very field the "
        "write is about to replace. On the normal case this script "
        "exists to handle (setting %r for the first time), the old "
        "value does not exist yet and this raises every time. Drop the "
        "fetch-and-check; you already have the value to write, so write "
        "it directly."
        % (m.varname, m.write_node.lineno, m.field, m.written_as, m.field)
    )


def _enclosing_statement_map(root: ast.AST) -> Dict[int, Optional[ast.stmt]]:
    """node id -> nearest enclosing `ast.stmt` that *contains* it -- a
    statement node itself maps to its parent statement (or `None` at
    the function's own top level), never to itself. Getting this wrong
    (mapping a statement to itself) makes every deleted statement look
    like it is nested inside its own deletion, which silently empties
    the "outermost deletions only" filter every caller relies on."""
    mapping: Dict[int, Optional[ast.stmt]] = {}

    def visit(node: ast.AST, current: Optional[ast.stmt]) -> None:
        for child in ast.iter_child_nodes(node):
            mapping[id(child)] = current
            next_current = child if isinstance(child, ast.stmt) else current
            visit(child, next_current)

    visit(root, None)
    return mapping


def _is_safe_to_drop_rhs(value: ast.AST) -> bool:
    """Whether an assignment's right-hand side is provably free of any
    side effect worth preserving, so the statement may be removed once
    nothing reads its target. Deliberately a narrow whitelist rather
    than "any expression": a general `Call` might be
    `requests.post(...)` (a real write) or `logger.info(...)` (a real
    log line), and either one silently disappearing because its return
    value happened to go unused would be a correctness bug this autofix
    must never cause, on a component whose whole existence is talking
    to a real API. Only reads are ever whitelisted:

    - a dict/response access (`X.get(...)`, `X[...]`)
    - `.json()` (a parse, not a write)
    - `requests.get(...)` specifically -- the one call this whole
      pattern exists to remove, and the one place "cascade into
      deleting a network call" is exactly the intended fix rather than
      an accident of an unrelated variable going unread.
    - a bare Name, Constant, or f-string/JoinedStr built only from
      those (covers `project_url = f"{API_BASE_URL}/..."` -- a pure
      string, never a call).
    """
    if isinstance(value, (ast.Name, ast.Constant)):
        return True
    if isinstance(value, ast.JoinedStr):
        return all(_is_safe_to_drop_rhs(v) if not isinstance(
            v, ast.FormattedValue) else _is_safe_to_drop_rhs(v.value)
            for v in value.values)
    if isinstance(value, ast.Subscript):
        return True
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        if value.func.attr in ("get", "json"):
            return True
        if (value.func.attr == "get"
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "requests"):
            return True
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == "get"
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "requests"):
        return True
    return False


def _cascade_dead_statements(func: ast.AST,
                             seed: List[ast.stmt]
                             ) -> Optional[List[ast.stmt]]:
    """A *backward data-flow slice* from `seed`, not a general dead-code
    sweep: only a statement that something already being deleted is the
    SOLE consumer of, and whose own right-hand side is provably safe to
    drop (`_is_safe_to_drop_rhs`), is added to the deletion set.

    This distinction is load-bearing. An earlier version of this
    function instead asked, of every statement in the whole function,
    "does anything still read this?" -- which is unsound the moment a
    statement's value is legitimately never re-read for a reason that
    has nothing to do with the seed: a `resp2 = requests.patch(...)`
    whose result is never inspected afterward is not dead code, it is
    the actual write this script exists to perform, and a global
    unused-variable sweep deleted it outright in testing, alongside a
    same-fixture, wholly-unrelated placeholder variable that happened
    to never be read either. Walking backward from the seed's own
    dependencies instead of forward from "what's globally unused" means
    only the fetch-and-derive chain that *feeds* the pattern being
    removed is ever a candidate, regardless of what else in the
    function happens to be unused for unrelated reasons.

    A candidate is added only once two things are both true: (1) its
    RHS is in the safe-to-drop whitelist, and (2) every remaining Load
    of its target, correctly attributed to *this* binding rather than
    an unrelated reuse of the same name (see `_owning_binding_line`),
    sits inside a statement already in the deletion set.

    Also absorbs a compound statement (`try`/`if`/`for`/`while`/`with`)
    whose entire `body` ends up in the deletion set, but only when it
    carries no `orelse`/`finalbody` of its own -- removing a `try` whose
    body is now empty is safe exactly when there is nothing left in it
    that would otherwise run. A compound with real `orelse`/`finalbody`
    content is a case this function declines to guess about; returning
    `None` here means the whole autofix backs out rather than silently
    dropping code that might still matter.
    """
    enclosing = _enclosing_statement_map(func)
    to_delete = {id(s) for s in seed}
    all_stmts = [n for n in ast.walk(func) if isinstance(n, ast.stmt)]
    by_id = {id(s): s for s in all_stmts}

    # Every simple `NAME = ...` assignment's line, per name, sorted --
    # used below to attribute each Load to the *nearest preceding*
    # binding of that name rather than to every same-named assignment
    # anywhere in the function. Without this, a name reused for two
    # unrelated values (the live file rebinds both `project_data` and
    # `resp` for a second, unrelated request later in the same
    # function) reads as "still live" forever: a Load belonging to the
    # *later* rebinding looks, by name alone, like a live use of the
    # *earlier* one, and a Load belonging to the *earlier* rebinding
    # looks like a forward-looking use of a *later* one.
    assign_lines: Dict[str, List[int]] = {}
    assign_stmt_by_binding: Dict[Tuple[str, int], ast.stmt] = {}
    for s in all_stmts:
        if (isinstance(s, ast.Assign) and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name)):
            name = s.targets[0].id
            assign_lines.setdefault(name, []).append(s.lineno)
            assign_stmt_by_binding[(name, s.lineno)] = s
    for name in assign_lines:
        assign_lines[name].sort()

    def _owning_binding_line(name: str, load_lineno: int) -> Optional[int]:
        """The largest assignment lineno for `name` that is <= the
        load's own lineno -- the binding a linear reading of the
        function attributes this Load to."""
        candidates = [ln for ln in assign_lines.get(name, [])
                     if ln <= load_lineno]
        return max(candidates) if candidates else None

    def _all_uses_are_dead(target_name: str, own_binding: Optional[int],
                           self_stmt: ast.stmt) -> bool:
        for n in ast.walk(func):
            if not (isinstance(n, ast.Name) and n.id == target_name
                   and isinstance(n.ctx, ast.Load)):
                continue
            owner = enclosing.get(id(n))
            if owner is self_stmt:
                continue                  # the statement's own self-use
            if (isinstance(owner, ast.Expr)
                    and isinstance(owner.value, ast.Call)
                    and isinstance(owner.value.func, ast.Attribute)
                    and owner.value.func.attr == "raise_for_status"
                    and owner.value.func.value is n):
                continue    # handled by the dedicated raise_for_status
                            # rule below, never counted as a blocking use
            load_lineno = getattr(n, "lineno", self_stmt.lineno)
            if _owning_binding_line(target_name, load_lineno) != own_binding:
                continue    # belongs to a different binding of this name
            if owner is None or id(owner) not in to_delete:
                return False
        return True

    changed = True
    while changed:
        changed = False
        # Only names actually *referenced by something already in
        # to_delete* are candidates -- this is what makes the walk
        # backward from the seed rather than a sweep of the whole
        # function.
        referenced: Dict[str, List[int]] = {}
        for stmt_id in list(to_delete):
            stmt = by_id[stmt_id]
            for n in ast.walk(stmt):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    referenced.setdefault(n.id, []).append(
                        getattr(n, "lineno", stmt.lineno))

        for name, load_linenos in referenced.items():
            for load_lineno in load_linenos:
                binding_line = _owning_binding_line(name, load_lineno)
                if binding_line is None:
                    continue
                producer = assign_stmt_by_binding.get((name, binding_line))
                if producer is None or id(producer) in to_delete:
                    continue
                if not _is_safe_to_drop_rhs(producer.value):
                    continue
                if _all_uses_are_dead(name, binding_line, producer):
                    to_delete.add(id(producer))
                    changed = True

        # `raise_for_status()` on a receiver whose own producing
        # statement is already in the deletion set: the response being
        # validated is itself being discarded, so the validation call
        # has nothing left to protect. Restricted (unlike the general
        # rule above) to only fire once its receiver's producer is
        # already a confirmed deletion, never on its own say-so.
        for stmt in all_stmts:
            if id(stmt) in to_delete:
                continue
            if not (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "raise_for_status"
                    and isinstance(stmt.value.func.value, ast.Name)):
                continue
            receiver = stmt.value.func.value.id
            binding_line = _owning_binding_line(receiver, stmt.lineno)
            if binding_line is None:
                continue
            producer = assign_stmt_by_binding.get((receiver, binding_line))
            if producer is not None and id(producer) in to_delete:
                to_delete.add(id(stmt))
                changed = True

        for stmt in all_stmts:
            if id(stmt) in to_delete:
                continue
            if isinstance(stmt, (ast.Try, ast.If, ast.For, ast.While,
                                 ast.With)):
                body = getattr(stmt, "body", None) or []
                if body and all(id(s) in to_delete for s in body):
                    if getattr(stmt, "orelse", None) or getattr(
                            stmt, "finalbody", None):
                        return None
                    to_delete.add(id(stmt))
                    changed = True

    # Only the outermost deleted statements matter for text removal --
    # a statement nested inside an already-deleted compound is covered
    # by that compound's own line range.
    top_level = [s for s in all_stmts
                if id(s) in to_delete
                and id(enclosing.get(id(s))) not in to_delete]
    return top_level


def autofix_stale_precondition_before_overwrite(code: str
                                                 ) -> Optional[str]:
    """Mechanically deletes the fetch-and-check `gate_stale_precondition_
    before_overwrite` flags, and nothing else -- no repair attempt spent
    on a rewrite this decidable.

    Deliberately text-splicing rather than `ast.unparse`-ing the whole
    module, the same choice `autofix_dead_entry_point` already made:
    unparsing the entire file would silently strip every comment and
    reformat lines this change never touches, which is a far bigger
    footprint than "delete these lines" for a fix this narrow.

    Declines (returns `None`, leaving the ordinary blocking gate and the
    repair loop to handle it) whenever there is more than one match --
    ambiguity is not this function's call to make -- and whenever
    `_cascade_dead_statements` finds a compound statement it cannot
    safely absorb (real `orelse`/`finalbody` content that would be
    lost). Also re-parses and re-runs the gate on its own output before
    returning it, so a bug in this function fails closed rather than
    shipping code nobody checked.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    matches = _find_stale_precondition_matches(tree)
    if len(matches) != 1:
        return None
    m = matches[0]

    to_delete = _cascade_dead_statements(m.func, [m.assign_node,
                                                  m.guard_node])
    if not to_delete:
        return None

    lines = code.splitlines(keepends=True)
    ranges = []
    for stmt in to_delete:
        start = stmt.lineno - 1
        end = getattr(stmt, "end_lineno", stmt.lineno)
        # Absorb any pure-comment lines immediately above this
        # statement -- they almost always explain the code being
        # removed and are otherwise left as an orphaned, stale note.
        while start > 0 and lines[start - 1].strip().startswith("#"):
            start -= 1
        ranges.append((start, end))
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    for start, end in reversed(merged):
        del lines[start:end]
    fixed = "".join(lines)

    try:
        ast.parse(fixed)
    except SyntaxError:
        return None
    if gate_stale_precondition_before_overwrite(fixed) is not None:
        return None
    return fixed


def gate_dead_value_after_guard(code: str) -> Optional[str]:
    """Advisory: a value read out of a GET response, used only to gate
    execution (checked for truthiness/absence and raised on), and never
    referenced anywhere else in the function -- including never
    appearing in any later write. Broader than `gate_stale_precondition_
    before_overwrite` above (that one requires proof the exact same
    field is overwritten under a different name; this one only requires
    the value to be provably unused for anything else) and deliberately
    left advisory rather than blocking or autofixed, on purpose: unlike
    the narrower case, there is no textual proof here that the check is
    pointless -- a value fetched purely to gate on ("the resource must
    already be marked ready before you touch it") can be a real,
    load-bearing business rule this project has no way to tell apart
    from dead weight without knowing what the field means. Removing it
    automatically risks deleting a real precondition; saying nothing
    risks the same wasted repair attempts a blocking gate exists to
    avoid. Advisory -- reported through `notes`, never blocking -- is
    the version of this check this project's own stated tradeoff (a
    detector firing on correct code costs more than it saves) allows.

    Scoped to a GET response specifically, not any dict access: reading
    a value out of a sibling step's own `output.json` is this project's
    normal, encouraged way to pass an id between steps
    (`policy-output-json.md`), and flagging that would fire on the most
    common correct pattern this whole pipeline runs on.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # names assigned from `requests.get(...)`  or  `<that>.json()`
        get_response_vars = set()
        for node in ast.walk(func):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                continue
            value = node.value
            if (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "get"
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "requests"):
                get_response_vars.add(node.targets[0].id)
            elif (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "json"
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id in get_response_vars):
                get_response_vars.add(node.targets[0].id)

        reads: Dict[str, ast.Assign] = {}
        for node in ast.walk(func):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                continue
            value = node.value
            source = None
            if (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "get"):
                source = value.func.value
            elif isinstance(value, ast.Subscript):
                source = value.value
            is_get_response = (isinstance(source, ast.Name)
                               and source.id in get_response_vars)
            # A chained `resp.json().get("field")` with no intermediate
            # variable -- `source` is itself the `.json()` call rather
            # than a bare Name, and is just as much a GET-response read
            # as the two-statement form.
            if (not is_get_response and isinstance(source, ast.Call)
                    and isinstance(source.func, ast.Attribute)
                    and source.func.attr == "json"
                    and isinstance(source.func.value, ast.Name)
                    and source.func.value.id in get_response_vars):
                is_get_response = True
            if is_get_response:
                reads[node.targets[0].id] = node

        if not reads:
            continue

        for varname, assign_node in reads.items():
            guard = None
            for node in ast.walk(func):
                if (isinstance(node, ast.If)
                        and any(isinstance(n, ast.Raise)
                               for n in ast.walk(node))
                        and _is_absence_check(node.test, varname)):
                    guard = node
                    break
            if guard is None:
                continue
            excluded = ({id(n) for n in ast.walk(assign_node)}
                       | {id(n) for n in ast.walk(guard)})
            external_use = any(
                isinstance(n, ast.Name) and n.id == varname
                and isinstance(n.ctx, ast.Load) and id(n) not in excluded
                for n in ast.walk(func))
            if external_use:
                continue
            return (
                "%r (line %d) is read from a GET response and used only "
                "to check it is present before raising -- it is never "
                "referenced anywhere else in the function, including in "
                "any later write. If this value is meant to gate a real "
                "precondition, that is fine as-is; if the fetch only "
                "exists to validate something you can already tell from "
                "data you have without it, the fetch and the check can "
                "both be dropped."
                % (varname, assign_node.lineno)
            )
    return None


def gate_fabricated_client(code: str) -> Optional[str]:
    """A `--api` component that imports `requests` (even only to name its
    exception types) and never once calls `requests.get/post/put/patch/
    delete/request(...)` anywhere is not talking to any service at all --
    real or mocked.

    Measured live, under `--api maven`: a `create_task` step imported
    `requests`, defined its own `class MockClient` with hardcoded methods
    -- `print(f"MOCK: POST {url} with name='{name}'")` followed by
    `return {"id": "mock-task-id-456", ...}` -- and never called
    `requests.post` (or anything else on `requests`) once. Run for real,
    against the real host, it printed a real-looking URL next to the word
    "MOCK" and handed the next step a literal string, `"mock-task-id-456"`,
    which the real API then correctly rejected as not a UUID three steps
    later. This passed every gate at generation time for the reason this
    project has already found once for a missing-credential client: a
    component that never makes a real request also never appears in the
    mock's call log, and an empty call log is deliberately treated as
    legitimate (a client may have nothing to call from `main`) -- so
    inventing a fake response is indistinguishable, to every existing
    gate, from a component that correctly does no networking at all.

    Scoped to avoid the two honest ways a file can use `requests` and
    still be fine. A component split across files -- `client.py` making
    the real calls, `main.py` only importing it and catching the
    exceptions it raises -- imports something else local first; any
    `from X import ...` / `import X` naming a module this scan cannot
    identify as `requests` itself or a stdlib module is read as "the real
    work might be happening over there," and this gate stays silent. And
    a file that never mentions `requests` at all has made no claim this
    gate can catch it breaking.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    imports_requests = False
    imports_other_local_module = False
    calls_a_verb = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "requests":
                    imports_requests = True
                elif top not in _STDLIB_MODULES:
                    imports_other_local_module = True
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top == "requests":
                imports_requests = True
            elif top and top not in _STDLIB_MODULES:
                imports_other_local_module = True
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _REQUESTS_VERBS \
                    and isinstance(func.value, ast.Name) \
                    and func.value.id == "requests":
                calls_a_verb = True
    if not imports_requests or imports_other_local_module or calls_a_verb:
        return None
    return ("imports `requests` (or references its exception types) but "
           "never calls requests.get/post/put/patch/delete/request(...) "
           "anywhere in this file -- this looks like a self-invented fake "
           "client (a hand-written class returning hardcoded data) rather "
           "than a real call to the API. Make the actual HTTP request "
           "with `requests`, or import and call the shared client this "
           "component is supposed to depend on.")


_API_ERROR_NAMES = frozenset(
    {"HTTPError", "RequestException", "ConnectionError", "Timeout",
     "ConnectTimeout", "ReadTimeout", "ClientError"})


def _is_exit_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name) and f.id == "exit":
        return True
    if isinstance(f, ast.Attribute) and f.attr in ("exit", "_exit") \
            and isinstance(f.value, ast.Name) and f.value.id in ("sys", "os"):
        return True
    return False


def _exception_type_names(node: Optional[ast.AST]) -> Set[str]:
    if node is None:
        return set()
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    names = set()
    for p in parts:
        if isinstance(p, ast.Name):
            names.add(p.id)
        elif isinstance(p, ast.Attribute):
            names.add(p.attr)
    return names


def _is_main_guard(test: ast.AST) -> bool:
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == "__name__")


def _calls_main(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Name) and node.func.id == "main")
                 or (isinstance(node.func, ast.Attribute)
                     and node.func.attr == "main")))


def _main_result_consumed(guard: ast.AST) -> bool:
    """Whether `main()`'s return value is ever actually looked at.

    `sys.exit(main())` and `if not main(): sys.exit(1)` both call `main`
    somewhere other than as a bare, discarded statement -- `main()`'s
    result is the thing driving the exit. A bare `main()` call, the
    subject of nothing but its own `ast.Expr` statement, discards that
    result outright: whatever `sys.exit` sits nearby in the same guard can
    only ever fire from an exception escaping uncaught, never from
    `main()` having returned a value that says it failed. Distinguishing
    these is what keeps the escape valve below from pardoning a file whose
    only exit path is "an exception nothing caught", which is exactly the
    case an internally-swallowed API failure defeats by construction.
    """
    bare = set()
    for node in ast.walk(guard):
        if isinstance(node, ast.Expr) and _calls_main(node.value):
            bare.add(id(node.value))
    for node in ast.walk(guard):
        if _calls_main(node) and id(node) not in bare:
            return True
    return False


def gate_swallowed_api_failure(code: str) -> Optional[str]:
    """An `except` block around a real API/network error that prints and
    returns, with no `raise` and no `sys.exit`, hides a genuine failure
    behind a process exit code of 0 -- and this project's own generated
    orchestration boilerplate decides pass or fail from exactly that exit
    code (`if wait \"$PID\"; then echo succeeded`).

    Measured live: an `import_units` step's own error handling read
    `except requests.HTTPError as e: print(f\"...Unit Upload Failed...\");
    print(f\"Error details: {e}\")` with nothing after it -- no `raise`,
    no `sys.exit`. Run for real, the upload failed with a 422 from the
    real API, the script printed the failure exactly as designed, exited
    0, and the orchestrator's own `generate.sh` printed
    `03_upload_units succeeded` immediately below the failure it was
    reporting on. `gate_smoke` cannot catch this by running the code,
    because it only fails a file on an *unhandled* traceback
    (`_crashed`) -- a caught, printed, swallowed error is not one, by
    design (see the AGENTS.md discussion of why that gate is deliberately
    weak about exit codes). So this has to be decided from the source:
    an except handler naming a real API error type must itself contain a
    `raise` or a `sys.exit`/`os._exit` call, or the file can never report
    this failure to anything checking its exit code, no matter what
    happens at runtime.

    One escape valve: a `sys.exit`/`os._exit` call inside the
    `if __name__ == \"__main__\":` guard clears every handler in the file,
    but only when `main()`'s return value is actually consumed somewhere
    in that guard (`sys.exit(main())`, `if not main(): sys.exit(1)`,
    `rc = main(); sys.exit(rc)`) -- on the theory that a file wiring
    `main()`'s result to its exit code is propagating failure through the
    return value instead of through the handler itself, which is just as
    real a way to signal it.

    A bare `try: main()\n except Exception: sys.exit(1)`, discarding
    `main()`'s result entirely, does not qualify: that `sys.exit(1)` is
    reachable only if an exception escapes `main()` uncaught, and a
    handler this very check is examining -- one that catches an API
    error, prints it, and returns normally -- guarantees by construction
    that nothing escapes for that failure. Measured live: exactly this
    shape (`create_applet`'s content-update handler swallowing a 400 and
    printing "might be expected", inside a file whose `__main__` guard was
    nothing but `try: main() except Exception: sys.exit(1)`) let the whole
    file clear this gate, and the applet version shipped with placeholder
    content because nothing ever reported the PATCH had failed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    if "requests" not in code:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            has_exit = any(_is_exit_call(sub) for sub in ast.walk(node))
            if has_exit and _main_result_consumed(node):
                return None
    offending: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = _exception_type_names(node.type)
        if not (names & _API_ERROR_NAMES):
            continue
        signals_failure = any(
            isinstance(sub, ast.Raise) or _is_exit_call(sub)
            for sub in ast.walk(node) if sub is not node)
        if not signals_failure:
            offending |= names
    if not offending:
        return None
    return ("an `except` block catching %s prints or otherwise handles "
           "the error and then returns normally -- no `raise`, no "
           "`sys.exit`/`os._exit` -- so a real failure here still exits "
           "0. A caller checking this script's exit code (this "
           "project's own generate.sh does exactly that) will read a "
           "genuine failure as success. Call `sys.exit(1)` (or re-raise) "
           "in that handler, or wire main()'s return value to "
           "`sys.exit(...)` in the `if __name__ == \"__main__\":` guard."
           % ", ".join(sorted(offending)))


_SENSITIVE_REQUEST_KWARGS = frozenset({"headers", "auth", "cookies"})


def _is_trivial_empty_literal(node: Optional[ast.AST]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return len(node.elts) == 0
    if isinstance(node, ast.Constant):
        return node.value in ("", None)
    return False


def gate_placeholder_credential_guard(code: str) -> Optional[str]:
    """A bare `try: NAME` immediately followed by `except NameError: NAME =
    <empty>` does not guard anything when `NAME` is never assigned any
    other way in the file -- it is not defensive code, it is a
    deterministic no-op that always executes its `except` branch, because
    nothing before it could ever have defined `NAME` in the first place.
    When `NAME` is then passed as `headers=`/`auth=`/`cookies=` to a real
    `requests` call, this is not a style complaint: every request that
    guard protects goes out with no credentials at all.

    Measured live: a `create_task` step referenced a global `HEADERS` in
    two separate `requests.post(..., headers=HEADERS, ...)` calls, with a
    comment reading "Assume client setup and headers are available from
    the execution environment" -- nothing in the file ever built it from
    `API_KEY`. Whatever earlier attempt produced this hit the resulting
    `NameError`, and instead of building `HEADERS` for real (the way five
    of the six other components in the same run did, correctly, with
    `HEADERS = {"X-API-Key": os.environ["API_KEY"], ...}`), it silenced
    the crash with `try: HEADERS \\n except NameError: HEADERS = {}`
    inside the `if __name__ == "__main__":` guard. This passed every gate
    -- `gate_smoke` only fails a file on an unhandled traceback, and this
    file never raises one -- and shipped to `generate.sh`, which reported
    "06 generation succeeded". Run for real, every request carried an
    empty header dict and the real API answered `401 Unauthorized`,
    three steps into a real orchestration run, on a component that had
    already reported clean.

    Scoped to fire only when the guard is provably the *only* place `NAME`
    is ever assigned in the whole file: a real assignment anywhere else
    means the `except` branch is a legitimate fallback for a genuinely
    reachable edge case, not a mask over a bug this check can prove is
    always taken. And only when `NAME` reaches a `requests` call through
    one of the three kwargs that actually carry credentials -- a `HEADERS`
    guard feeding something else entirely (unrelated to auth) has made no
    claim about credentials for this check to catch it breaking.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    # Every place any name is ever assigned anywhere in the file, by
    # Assign-node identity, so "the guard is the only assignment" can be
    # checked as "this name's assignment list has exactly one entry, and
    # it is this exact node" rather than by re-deriving the same walk
    # twice and hoping the two derivations agree.
    assigns_by_name: Dict[str, List[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns_by_name.setdefault(target.id, []).append(node)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            assigns_by_name.setdefault(node.target.id, []).append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigns_by_name.setdefault(node.target.id, []).append(node)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            assigns_by_name.setdefault(node.target.id, []).append(node)
        elif isinstance(node, ast.withitem) \
                and isinstance(node.optional_vars, ast.Name):
            assigns_by_name.setdefault(node.optional_vars.id, []).append(node)
        elif isinstance(node, ast.FunctionDef):
            for arg in node.args.args + node.args.kwonlyargs:
                assigns_by_name.setdefault(arg.arg, []).append(node)

    guard_assign_by_name: Dict[str, ast.Assign] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or len(node.body) != 1:
            continue
        stmt = node.body[0]
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Name)):
            continue
        name = stmt.value.id
        for handler in node.handlers:
            if "NameError" not in _exception_type_names(handler.type):
                continue
            for h in handler.body:
                if (isinstance(h, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == name
                                for t in h.targets)
                        and _is_trivial_empty_literal(h.value)):
                    guard_assign_by_name[name] = h

    guarded_only = {
        name for name, guard_assign in guard_assign_by_name.items()
        if assigns_by_name.get(name) == [guard_assign]
    }
    if not guarded_only:
        return None

    offending: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _REQUESTS_VERBS
                and isinstance(func.value, ast.Name)
                and func.value.id == "requests"):
            continue
        for kw in node.keywords:
            if kw.arg in _SENSITIVE_REQUEST_KWARGS \
                    and isinstance(kw.value, ast.Name) \
                    and kw.value.id in guarded_only:
                offending.add(kw.value.id)
    if not offending:
        return None
    example = sorted(offending)[0]
    return ("`%s` is only ever assigned inside a `try: %s` / `except "
           "NameError: %s = {}`-shaped guard, which is not defensive -- "
           "nothing else in this file ever defines it, so that except "
           "branch always runs and %s is always empty. It is then passed "
           "as headers=/auth=/cookies= to a real requests call, so every "
           "request goes out with no credentials. Build it for real from "
           "the environment (e.g. `HEADERS = {\"X-API-Key\": "
           "os.environ[\"API_KEY\"], ...}`) at the point it is actually "
           "needed, rather than guarding against it being undefined."
           % (", ".join(sorted(offending)), example, example, example))


# A single Title-Case word or short phrase, immediately followed by a
# colon -- "Task ID:", "Project Link:" -- the shape an operator uses to
# demonstrate an exact print format by example.
_TITLE_PHRASE = r"[A-Z][A-Za-z]*(?:\s[A-Z][A-Za-z]*){0,3}"
_LABEL_RE = re.compile(r"(%s):" % _TITLE_PHRASE)
# Only text following "like" or "as" is scanned at all -- "print it like
# X: Y:", "format it as X: Y:" -- which is specifically how an operator
# demonstrates a format by example, and restricting to it is what keeps
# this from firing on an unrelated colon inside ordinary prose ("Note:
# this needs a key").
_TRIGGER_RE = re.compile(r"\b(?:like|as)\s+(.+)", re.I)
_CLAUSE_SPLIT_RE = re.compile(r"[.\n]|(?:\band\b(?!\s+%s:))" % _TITLE_PHRASE)


def output_labels(text: str) -> List[str]:
    """Literal print labels a request pins down by example.

    Measured live: "make sure it prints the task id like Project Link:
    Task ID: Project ID and saves them a json file" is an explicit,
    unambiguous statement of the output format the operator wants -- and
    the generated file printed four bare UUIDs with no labels at all,
    twice, because nothing anywhere carried that literal instruction
    forward to a mechanical check. Prompting for it is not new (the SPEC
    text already said it in English); what was missing is something that
    can tell the difference between "asked for" and "delivered" after the
    fact, since a model can be shown the exact wording and still not
    reproduce it -- the same finding this project already made once for
    an invented UI link and once for an entry-point rename.

    Deliberately narrow in two ways, both load-bearing. Only clauses
    introduced by "like"/"as" are read at all, and only a run of
    Title-Case-word-then-colon groups within that clause counts -- a
    detector that fired on any capitalised word before a colon anywhere in
    a long request would false-positive on ordinary prose ("Note: this
    needs a key") far more often than it would catch a real format
    example.

    The final label in a chain is recovered even without its own trailing
    colon ("...like Project Link: Task ID: Project ID" -- three labels,
    the operator dropped the last punctuation mark), because the shape of
    the first two makes the intent for the third unambiguous; a lone
    Title-Case phrase with nothing before it is not recovered, since there
    is no chain to disambiguate it from an ordinary proper noun.
    """
    labels: List[str] = []
    for trig in _TRIGGER_RE.finditer(text):
        clause = _CLAUSE_SPLIT_RE.split(trig.group(1), maxsplit=1)[0]
        chain_match = re.match(r"(?:\s*%s:\s*)+" % _TITLE_PHRASE, clause)
        if not chain_match:
            continue
        for lbl in _LABEL_RE.findall(chain_match.group(0)):
            text_ = lbl + ":"
            if text_ not in labels:
                labels.append(text_)
        rest = clause[chain_match.end():].strip()
        trailer = re.match(_TITLE_PHRASE, rest)
        if trailer:
            text_ = trailer.group(0) + ":"
            if text_ not in labels:
                labels.append(text_)
    return labels


def gate_import(path: str, cwd: str, timeout: int = 60,
                env_extra: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Import the file in a subprocess. Catches everything a parse cannot:
    bad names, bad indentation of real logic, import errors, decorators that
    blow up at definition time."""
    rel = os.path.relpath(path, cwd)
    ok, out = run_command(
        "%s -c \"import importlib.util,sys; "
        "s=importlib.util.spec_from_file_location('_probe', r'%s'); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\""
        % (sys.executable, path), cwd, timeout, env_extra=env_extra)
    if ok:
        return None
    detail = (out or "").strip()
    if not detail:
        # A failure that says nothing is the worst kind to hand back, to a
        # model or to a person: the repair prompt gets "importing main.py
        # failed:" and a blank line, which describes no defect to fix. The
        # usual cause is the import blocking until the timeout -- module-level
        # `input()` or a loop that starts on import.
        detail = ("it produced no output and did not finish within %ds. Code "
                  "that waits for input or runs a loop must not do so while "
                  "being imported; put it inside a function called from an "
                  "`if __name__ == \"__main__\":` block." % timeout)
    return "importing %s failed:\n%s" % (rel, detail[-2500:])


_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):$", re.M)


def _crashed(out: str) -> bool:
    """A real traceback, not a program that happens to print the word.

    Anchored to the header Python emits on its own line, because matching the
    bare word rejects any tool that mentions tracebacks in its help text.
    """
    return bool(_TRACEBACK.search(out or ""))


_EOF_AT_INPUT = re.compile(r"^EOFError\b", re.M)

# `local_urls` names its dead port deliberately: nothing listens on 1, so a
# request there fails immediately whether the code is right or wrong. A
# connection-refused traceback that names this exact address is not evidence
# about the file; it is evidence that no mock was configured, and the two
# must not be reported the same way, or the repair loop spends its whole
# budget rewriting code that could not have passed no matter what it said.
_NO_MOCK_SINK_RE = re.compile(
    r"127\.0\.0\.1['\"]?\s*,?\s*(?:port\s*[=:]\s*)?:?\s*1\b")
NO_MOCK_MARKER = "no mock is configured"


def _no_mock_sink_error(out: str) -> bool:
    """True when a crash's own traceback names the dead-port sink.

    Deliberately narrow: matching on "connection refused" alone would call
    every real network failure a missing mock, and matching on "127.0.0.1"
    alone would call every legitimate local test one. The port has to be
    exactly 1, because that is the one value `local_urls` ever writes there
    and no other code path in this project has a reason to use it.
    """
    return bool(_NO_MOCK_SINK_RE.search(out or ""))


# One layer further out than the dead-port sink above, and handled more
# strictly: `seed_parent_refs` mirrors a sibling step's real output file
# into the smoke sandbox *if it exists on real disk*, and a chain
# generated ahead of being run means it usually does not yet -- so
# `../01_create_project/output.json` is not there to find no matter how
# many times the writer is asked to try again. The no-mock case above is
# still reported as a failure, because `--api` is a flag the operator
# forgot and can add; this is not that. Nothing rewriting this file can
# make a sibling's real output exist before that sibling has actually run,
# so `gate_smoke` treats this as a skipped check, not a failed one --
# see the call site below. Measured live before that existed: a correct
# file reading `project_data["id"]` out of exactly this path, with its own
# try/except naming the path in the error, was rewritten across 12
# attempts and a full-loop restart because nothing told the loop the file
# could not have passed regardless of what it said.


def _missing_sibling_error(out: str, source: str, cwd: str) -> Optional[str]:
    """The one referenced `../sibling/path` this crash is actually about, if
    the reason `seed_parent_refs` could not seed it is that the real file
    genuinely does not exist yet -- not a wrong path, not a typo, just a
    dependency that has not been run.

    Matched by substring against the crash text rather than by re-deriving
    what failed: Python's own `FileNotFoundError` already names the exact
    path it could not open, and a file that wraps the read in its own
    try/except (as the rule above this asks writers to do) puts the same
    path string in its own message, so one check covers both shapes without
    guessing at either program's error-handling style.
    """
    for ref in referenced_parent_paths(source):
        real_path = os.path.normpath(os.path.join(cwd, ref))
        if os.path.isfile(real_path):
            continue  # this one really is seeded; a failure on it is real
        if ref in (out or ""):
            return ref
    return None


def _waiting_for_input(out: str) -> bool:
    """An interactive program reaching the end of an empty stdin is not a crash.

    The smoke run has no user, so `input()` returns EOF immediately and the
    program dies with EOFError -- a traceback, and therefore a failure, for the
    one thing a text adventure or a REPL is supposed to do. Measured: a correct
    `main.py` that imported all four of its modules and ran a command loop was
    rejected on exactly this, three attempts in a row, and the model had no way
    to satisfy the gate short of removing the game loop.

    Failing here is the expensive direction. This gate exists to catch code
    that crashes on its own terms, and reading a prompt nobody answered is not
    that.
    """
    return bool(_EOF_AT_INPUT.search(out or ""))


# Interpreter phrasings that no program produces on purpose. Deliberately
# narrow: each is the exact wording CPython uses, so a match is a real error
# that something caught and printed rather than a message anybody wrote.
_SWALLOWED = re.compile(
    r"^.*?((?:'[^']+' )?object has no attribute '[^']+'"
    r"|name '[^']+' is not defined"
    r"|object is not (?:callable|subscriptable|iterable)"
    r"|unsupported operand type\(s\) for"
    r"|got an unexpected keyword argument"
    r"|takes \d+ positional arguments? but \d+ (?:was|were) given"
    r"|missing \d+ required positional argument).*$", re.M)


def _swallowed_error(out: str) -> Optional[str]:
    """A real exception the program caught and printed rather than raised.

    `except Exception as e: print(e)` around a main loop turns every defect
    into ordinary-looking output: exit code 0, no traceback, gate satisfied.
    Measured on a generated text adventure, which reported `'GameState' object
    has no attribute 'current_or_room'` as game text and passed.

    Matching only the interpreter's own phrasings keeps this from firing on a
    program that merely prints the word error.
    """
    match = _SWALLOWED.search(out or "")
    return match.group(0).strip()[:200] if match else None


def placeholder_env(source: str,
                    env_extra: Optional[Dict[str, str]] = None
                    ) -> Dict[str, str]:
    """Stand-in values for credentials the file needs and nobody supplied.

    Only for names the file itself reads and that are genuinely absent, so a
    real value in the operator's environment is never shadowed -- the gate
    should not quietly test against different inputs than the one that is
    there. Names come from `usage.read`, which takes them off the syntax
    tree as literals.

    The shapes are chosen to survive the obvious first thing a client does
    with them: a `*_URL` points at whatever base the gates were given, so a
    request lands on the mock rather than a bad host, and an `*_ID` is a
    UUID because that is what this API returns and `uuid.UUID(...)` on
    `test-value` would raise.
    """
    import usage
    found = usage.read("probe.py", source)
    if found is None:
        return {}
    supplied = dict(os.environ)
    supplied.update(env_extra or {})
    base = supplied.get("API_BASE_URL", "http://127.0.0.1:1")

    filled: Dict[str, str] = {}
    for name in found.required_env:
        if supplied.get(name):
            continue
        upper = name.upper()
        if upper.endswith("_URL") or upper.endswith("_URI"):
            filled[name] = base
        elif upper.endswith("_ID") or upper.endswith("_UUID"):
            filled[name] = "00000000-0000-4000-8000-000000000000"
        elif "PORT" in upper:
            filled[name] = "8080"
        else:
            filled[name] = "placeholder-not-a-real-credential"
    return filled


# One package's worth of sources and templates, not a data directory. A
# smoke run that has to copy a corpus first is not cheap any more, and the
# files a generated program opens by name are the ones its siblings wrote.
SEED_FILE_LIMIT = 1 << 20
SEED_TOTAL_LIMIT = 8 << 20


def seed_scratch(src: str, dest: str) -> List[str]:
    """Copy the project's own files next to a program about to be run.

    Top level only, regular files only, skipping dotfiles -- the scratch
    directory exists so writes cannot land in the project, and copying a
    tree back in would defeat the size argument for having it. A `.env`
    is excluded along with the rest of the dotfiles, which is the right
    answer for the same reason `attach.py` refuses one.

    Best-effort: a file that cannot be read is skipped rather than failing
    the gate, since this only ever adds to what the run can see.
    """
    copied: List[str] = []
    total = 0
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return copied
    for name in names:
        if name.startswith("."):
            continue
        source = os.path.join(src, name)
        if not os.path.isfile(source):
            continue
        try:
            size = os.path.getsize(source)
            if size > SEED_FILE_LIMIT or total + size > SEED_TOTAL_LIMIT:
                continue
            shutil.copy2(source, os.path.join(dest, name))
        except OSError:
            continue
        total += size
        copied.append(name)
    return copied


_PARENT_REF_RE = re.compile(r'\.\.(?:/[\w.\-]+)+\.\w+')


def referenced_parent_paths(source: str) -> List[str]:
    """Relative parent-directory paths (`../01_create_project/output.json`)
    the source text itself references.

    Read off the generated source rather than the spec, because the source
    is what actually executes: whatever literal a writer used to satisfy
    "reads project_id from ../01_create_project/output.json" ends up
    somewhere in the file as a string, and that exact string is the one
    that has to resolve at smoke-run time for anything real to happen.
    """
    return sorted(set(_PARENT_REF_RE.findall(source)))


def seed_parent_refs(source: str, real_cwd: str, scratch: str) -> List[str]:
    """Mirror a real sibling-step file into the scratch sandbox, at the same
    relative path the generated code will actually open it at.

    `seed_scratch` only ever copies `real_cwd`'s own top-level files, because
    that used to be the whole story -- a generated file reading something a
    sibling *in the same directory* wrote. The multi-step chains this pool
    is now built around read a different directory entirely
    (`../01_create_project/output.json`), and nothing seeded that: the
    smoke run executed in a scratch directory whose parent has no
    `01_create_project` in it at all, so `open("../01_create_project/
    output.json")` raised, the program's own error handling caught it and
    exited zero, and the gate recorded a clean pass on a file that sent
    zero real traffic -- the identical vacuous-pass shape as the
    `open("applet.html")` case above, just one directory further out.

    Fixed by giving the scratch directory the same *name* as the real one
    (`gate_smoke` below), so `..` from inside it means the same tempdir
    `seed_parent_refs` also populates -- then copying each referenced real
    file into that tempdir at the matching relative path. Best-effort, like
    `seed_scratch`: a path that does not resolve to a real file is skipped
    rather than failing the gate, since this only ever adds to what the run
    can see.
    """
    copied: List[str] = []
    for ref in referenced_parent_paths(source):
        real_path = os.path.normpath(os.path.join(real_cwd, ref))
        if not os.path.isfile(real_path):
            if ref.endswith(".json"):
                _seed_placeholder_sibling_output(source, ref, scratch)
            continue
        dest_path = os.path.normpath(os.path.join(scratch, ref))
        try:
            if os.path.getsize(real_path) > SEED_FILE_LIMIT:
                continue
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(real_path, dest_path)
        except OSError:
            continue
        copied.append(ref)
    return copied


def _sibling_output_read_keys(source: str, ref: str) -> Set[str]:
    """Every key this source appears to read off the sibling JSON file at
    `ref`, by static analysis: the variable name(s) bound to the parsed
    dict (`json.load(f)`/`json.loads(...)`, where `f` -- or the argument
    to `loads` -- can be traced back to an `open(...)` call naming `ref`,
    or, when the file only ever opens one sibling JSON file, any
    `json.load`/`json.loads` result at all), and every `.get("key")` or
    `["key"]` access on that variable anywhere in the file.

    Deliberately falls back to "any parse call, if there is only one" --
    tracing `open(...)`'s argument back to the exact string literal that
    matched `ref` is precise but brittle the moment a path is built with
    an f-string or a variable (`output_file_path = "../01_create_project/
    output.json"` assigned one line above the `open()` call, which is
    the shape almost every generated file in this project actually
    uses). A file reading exactly one sibling JSON file has nothing else
    that ambiguity could be about; a file reading several is left alone
    rather than guessed at, the same "no evidence, no claim" rule every
    other static check in this module already follows.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    def _is_write_open(call: ast.Call) -> bool:
        # A file this source *writes* to (its own output.json, almost
        # always) is not a candidate for "the one sibling file this
        # source reads" -- counting it would make every generated file
        # that both reads one sibling and writes its own result look
        # like it opens two files and disqualify the read entirely,
        # which is close to every file this project generates.
        mode = None
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            mode = call.args[1].value
        for kw in call.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        return isinstance(mode, str) and any(c in mode for c in "wax")

    def _is_env_loader_open(call: ast.Call) -> bool:
        # `codegen.py` itself injects an `open(".../.env", ...)` call at
        # the very top of every entry point (see `ENV_LOADER_MARKER`),
        # which is not a candidate sibling read either -- left uncounted,
        # it made every generated file look like it opens two files
        # (its `.env` plus the one real sibling) and disqualified the
        # single-read heuristic on every single file this project's own
        # boilerplate touches.
        return bool(call.args) and isinstance(call.args[0], ast.Constant) \
            and isinstance(call.args[0].value, str) \
            and call.args[0].value.endswith(".env")

    def _open_path_matches_ref(call: ast.Call) -> bool:
        # An `open()` call whose literal first argument is exactly `ref`
        # is unambiguous regardless of how many other, unrelated opens
        # exist in the file -- unlike the single-open fallback below,
        # which is defeated the instant a file makes a second read-mode
        # open for any other reason (reopening a just-written dummy CSV
        # in "rb" mode for a multipart upload, for instance). This is
        # checked first and, when it matches, takes priority over that
        # fallback entirely.
        if not call.args:
            return False
        arg0 = call.args[0]
        return isinstance(arg0, ast.Constant) and arg0.value == ref

    read_opens = 0
    file_vars: Set[str] = set()
    exact_vars: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open" and not _is_write_open(node) \
                and not _is_env_loader_open(node):
            read_opens += 1

    single_open = read_opens == 1
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem):
            call = node.context_expr
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                    and call.func.id == "open" and not _is_write_open(call) \
                    and not _is_env_loader_open(call) \
                    and isinstance(node.optional_vars, ast.Name):
                if _open_path_matches_ref(call):
                    exact_vars.add(node.optional_vars.id)
                if single_open:
                    file_vars.add(node.optional_vars.id)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            call = node.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                    and call.func.id == "open" and not _is_write_open(call) \
                    and not _is_env_loader_open(call):
                if _open_path_matches_ref(call):
                    exact_vars.add(node.targets[0].id)
                if single_open:
                    file_vars.add(node.targets[0].id)

    if exact_vars:
        file_vars = exact_vars
    used_exact_match = bool(exact_vars)

    dict_vars: Set[str] = set()
    parse_calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("load", "loads")
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "json"):
            continue
        parse_calls += 1
        if not call.args:
            if not exact_vars:
                dict_vars.add(node.targets[0].id)  # still worth trying
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Name) and arg.id in file_vars:
            dict_vars.add(node.targets[0].id)
        elif isinstance(arg, ast.Name) and single_open and not exact_vars:
            dict_vars.add(node.targets[0].id)
        elif single_open and not exact_vars:
            # `json.loads(open(ref).read())` and similar -- no named file
            # variable to match against, but there is nothing else this
            # file's one open() call could be about.
            dict_vars.add(node.targets[0].id)

    # The "more than one parse call and no single open to blame them all
    # on" guard exists to refuse a guess when there is genuinely nothing
    # to disambiguate several parses by. An exact literal-path match does
    # not have that problem -- each `dict_vars` entry it produced is
    # already tied to the one open() call whose path is unambiguously
    # `ref` -- so a second, unrelated sibling-JSON read elsewhere in the
    # same file must not discard it.
    if not dict_vars or (parse_calls > 1 and not single_open
                         and not used_exact_match):
        return set()

    keys: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in dict_vars and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                keys.add(a0.value)
        elif isinstance(node, ast.Subscript) \
                and isinstance(node.value, ast.Name) \
                and node.value.id in dict_vars:
            sl = node.slice
            key_node = sl.value if isinstance(sl, ast.Index) else sl
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                keys.add(key_node.value)
    return keys


def _seed_placeholder_sibling_output(source: str, ref: str, scratch: str
                                     ) -> Optional[str]:
    """Stand in a placeholder JSON file where a real sibling step's
    `output.json` would land, for exactly the same reason `placeholder_env`
    stands in a fake credential: the file genuinely does not exist yet at
    generation time (the sibling chain step has not actually run), so
    without this the smoke run stops dead at `open(ref)` and the
    `FileNotFoundError` is correctly read by `_missing_sibling_error` as
    "not a real failure" -- but that same correctness is what leaves
    everything *after* the read completely unexercised. Measured live: a
    generated `main.py` referenced an undefined name, `HEADERS`, in the
    one `requests.post(...)` call that actually mattered, and it shipped
    -- the smoke run never got past the FileNotFoundError two lines
    earlier to ever evaluate that line, on every single attempt, because
    nothing had ever seeded a fake sibling output for it to read instead.

    Placeholders may only ever help, same rule as `placeholder_env`: this
    only ever adds a file where none existed, and a value guessed wrong
    is no worse than the FileNotFoundError this replaces -- if the file
    goes on to crash for a different, real reason, that crash is exactly
    as reportable as it always was. Best-effort and silent about what it
    could not infer: `_sibling_output_read_keys` finding no keys leaves
    this a no-op (no file written), which is correct whenever the source
    is too ambiguous to guess safely rather than evidence nothing needs
    seeding.
    """
    keys = _sibling_output_read_keys(source, ref)
    if not keys:
        return None
    placeholder: Dict[str, str] = {}
    for key in keys:
        upper = key.upper()
        if upper.endswith("_ID") or upper.endswith("ID") or upper == "ID":
            placeholder[key] = "00000000-0000-4000-8000-000000000000"
        elif upper.endswith("_URL") or upper.endswith("URL"):
            placeholder[key] = "http://127.0.0.1:1/placeholder"
        else:
            placeholder[key] = "placeholder-not-real-data"
    dest_path = os.path.normpath(os.path.join(scratch, ref))
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "w", encoding="utf-8") as fh:
            json.dump(placeholder, fh)
    except OSError:
        return None
    return ref


def gate_smoke(path: str, cwd: str, timeout: int = 60,
               env_extra: Optional[Dict[str, str]] = None,
               required_labels: Sequence[str] = ()) -> Optional[str]:
    """Run a command-line entry point and require it not to crash.

    The cheapest real gate there is, and the only automatic one that observes
    behaviour rather than shape. Importing a module proves it loads; running it
    proves `main` survives contact with an argument vector. The class of bug
    this catches is the one reading misses: on a probe carrying a dict mutated
    while being iterated -- which raises on the fourth insert -- both audit
    models failed to identify it from the source, and executing the code finds
    it in milliseconds.

    Deliberately weak about what counts as success. `--help` must exit 0
    because argparse guarantees that for a well-formed parser, but a bare
    invocation may legitimately exit non-zero for want of arguments, so only an
    unhandled traceback fails it. Guessing at correct exit codes for a program
    whose interface was invented by a model would reject working code.

    Skipped entirely for modules with no entry point, where it would prove
    nothing that `gate_import` has not already proven.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return None
    if "__main__" not in source:
        return None

    # A client whose first line is `os.environ["API_KEY"]` exits before it
    # reaches a single request, so the smoke run proves nothing and the mock
    # records nothing -- measured live, a Maven client passed 1/1 having sent
    # no traffic at all. Standing in a placeholder lets it get as far as the
    # calls, which is the part worth watching.
    #
    # Placeholders may only ever help. If the run fails while they are set,
    # it is retried without them and only a failure both ways is reported,
    # so a value this guessed wrong -- a key with a checked format, an id
    # that must resolve -- cannot fail a file that would otherwise pass.
    filled = placeholder_env(source, env_extra)
    probe_env = dict(env_extra or {})
    probe_env.update(filled)

    rel = os.path.relpath(path, cwd)
    # Display-only: a race/hedge candidate (see `team._race_scratch_path`)
    # is a daemon thread that keeps running, and printing, after its race
    # has already concluded -- so an advisory note below can fire against
    # `main.__swarmrace_race1__.py` well after `main.py` (the real winner)
    # already shipped. Every actual command below still runs against
    # `rel`/`path` (the real scratch file this candidate owns); only the
    # operator-facing text uses the recovered real name, so a straggler's
    # routine, harmless note reads as what it is -- about `main.py` -- and
    # not as a second, mysterious file that failed.
    display_rel = intent.race_scratch_display_name(rel)
    if "argparse" in source or "sys.argv" in source:
        ok, out = run_command("%s %s --help" % (sys.executable, rel), cwd,
                              timeout, env_extra=probe_env)
        if not ok and _crashed(out):
            return "running `%s --help` crashed:\n%s" % (rel, out[-2000:])

    # An interactive program given nothing to read exits on the first prompt,
    # having executed none of the code worth executing. Typing its own words at
    # it walks the same paths a user would, and costs one AST pass.
    typed = exercise_script(source) if "input(" in source else None

    # Bare run in a scratch directory, so a tool that writes where it is
    # pointed cannot touch the project -- but seeded with the project's own
    # files, or a program that reads one is being run somewhere it cannot
    # find it. Measured live: a client whose first act was `open("applet.html")`
    # exited on the resulting OSError, having sent nothing, and the mock
    # recorded no traffic while the gate reported a clean pass. That is the
    # same vacuous pass a missing credential used to produce, arriving
    # through the filesystem instead.
    def bare(env: Optional[Dict[str, str]], argv: Sequence[str] = ()) -> str:
        temp_root = tempfile.mkdtemp(prefix="smoke-")
        # Named after the real directory, not random, so a `../sibling`
        # reference inside the generated code resolves against `temp_root`
        # the same way it would resolve against the real project layout --
        # see `seed_parent_refs` above for why this has to match.
        scratch = os.path.join(
            temp_root, os.path.basename(os.path.normpath(cwd)) or "component")
        try:
            os.makedirs(scratch, exist_ok=True)
            seed_scratch(cwd, scratch)
            seed_parent_refs(source, cwd, scratch)
            command = " ".join([sys.executable, shlex.quote(
                os.path.abspath(path))] + [shlex.quote(a) for a in argv])
            _, text = run_command(command, scratch, timeout,
                                  stdin_text=typed, env_extra=env)
            return text
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    # Required arguments are filled the same way and under the same rule:
    # they may only ever help, since a failure here is retried plain.
    import usage
    argv = usage.placeholder_args(source)
    out = bare(probe_env or None, argv)
    if _crashed(out) and not _waiting_for_input(out):
        if filled or argv:
            # Might be the placeholder's fault rather than the file's. The
            # plain run is exactly what this gate did before any of this, so
            # deferring to it cannot make the gate stricter than it was.
            plain = bare(env_extra)
            if not _crashed(plain) or _waiting_for_input(plain):
                return None
            # Both crashed, so the file is at fault -- but the verdict comes
            # from the run with the placeholders, not from this one. The
            # plain run is an acquittal path, and a program with no
            # credentials stops at the first line that wants one: reporting
            # it said `AUTH_TOKEN environment variable is not set` after the
            # filled run had already driven the whole API chain and crashed
            # somewhere past it. That is not a defect, it is the file
            # behaving correctly, and it sent the repair loop after the one
            # thing that was not wrong.
        missing_ref = _missing_sibling_error(out, source, cwd)
        if missing_ref is not None:
            # Not a failure at all, unlike the no-mock case just below --
            # that one is reported as a (fast) failure on purpose, because
            # `--api` is a flag the operator forgot and can add on the next
            # run. This one is different in kind: nothing about rewriting
            # this file, adding a flag, or retrying can make a sibling
            # step's output exist before that step has actually been run,
            # so failing the component here is not a warning that
            # convinces anyone to fix anything -- it is 6+ wasted repair
            # attempts on a file with nothing wrong with it, every single
            # time a chain is written ahead of being run. The file already
            # proved it parses, imports, and satisfies every gate that
            # *can* run without its dependency's real output; asking for
            # more than that here is asking the impossible, not asking for
            # quality. So this attempt counts as passed -- the smoke run
            # is skipped, not failed -- and the orchestrator guidance
            # elsewhere in this project's docs is the actual fix: run each
            # step before generating whatever depends on it.
            sys.stderr.write(
                "  note: %s could not find %s -- the sibling step that "
                "creates it has not been run yet, so this smoke check is "
                "skipped rather than failed (see LOCAL.md's orchestration "
                "guidance: run each step before generating the one after "
                "it).\n" % (display_rel, missing_ref))
            return None
        if _no_mock_sink_error(out):
            # Measured live: three attempts on one file, and the same
            # `HTTPConnectionPool(host='127.0.0.1', port=1)` refusal every
            # time, because no `--api` was given so there was no mock to
            # point the client at -- `local_urls` sends every non-loopback
            # URL to a port nothing listens on by design. No rewrite of the
            # file changes that; the retry loop was spending its whole
            # budget on a file with nothing wrong with it.
            return ("running %s could not reach the API: %s (no --api was "
                    "given to `do`/`code`, so every non-loopback URL was "
                    "pointed at a dead port on purpose). This is not a "
                    "defect in the file -- rerun with --api <name> so a "
                    "mock exists to test against.\n%s"
                    % (rel, NO_MOCK_MARKER, out[-1000:]))
        return "running %s raised:\n%s" % (rel, out[-2000:])
    swallowed = _swallowed_error(out)
    if swallowed:
        return ("running %s hit a real error and printed it instead of "
                "raising:\n  %s\nA blanket `except Exception` around the main "
                "loop hides defects like this one; fix the cause."
                % (rel, swallowed))

    # `gate_platform_facts` reads the *source* text, which only ever
    # catches a bad literal sitting in one place. Measured live: a helper
    # correctly built `HOST/projects/{id}` and returned it, and a second
    # place -- `print(f"Project Link: {link}/projects/{project_id}")` --
    # appended the same suffix again onto the already-complete value. Every
    # individual string literal in the source is well-formed in isolation;
    # the host substring never sits next to the bad tail until the two
    # pieces are concatenated at runtime, which no static scan of the file
    # text can see. The printed output can, by the time this line runs.
    #
    # Advisory rather than a smoke failure: a malformed link in printed
    # output is a cosmetic defect in one string, not proof the program is
    # broken, and failing the whole gate over it spends a repair attempt
    # rewriting a file that otherwise runs correctly. Surfaced on stderr so
    # the operator sees it without the retry loop treating it as a crash.
    platform_issue = gate_platform_facts(out)
    if platform_issue:
        sys.stderr.write("  note: %s printed a malformed platform link: %s\n"
                         % (display_rel, platform_issue))

    # Blocking, unlike the advisory check above: a missing label is not a
    # cosmetic wart on one string, it is the request's own explicit format
    # simply not being there at all. Measured live: told by example to
    # print "Project Link: Task ID: Project ID", a generated file printed
    # four bare UUIDs instead, twice, and passed every other gate cleanly
    # because nothing crashed. Checked against the run with placeholders
    # filled in (`out`, not the plain run) since that is the one that
    # actually reaches the print statements at all.
    missing = [lbl for lbl in required_labels if lbl not in out]
    if missing:
        return ("running %s did not print %s. The request asked for the "
                "output to include exactly these labels before the values "
                "they introduce (e.g. `print(f\"Project Link: {link}\")`), "
                "not just the bare values on their own. Last part of what "
                "it actually printed:\n%s"
                % (rel, ", ".join(repr(m) for m in missing), out[-1000:]))
    return None


# --------------------------------------------------------------------------
# generation loop
# --------------------------------------------------------------------------


GROWTH_CEILING = 4

# One ceiling for a whole component rather than for each attempt at it. The
# per-request timeout only bounds a single call, so three attempts against a
# box that has stopped answering cost thirty minutes before anything is
# reported -- measured, on a laptop that went to sleep mid-run. The slowest
# legitimate generation seen on this pool is 1m26s, so a component that has
# spent ten minutes is not working slowly, it is not working.
TIME_BUDGET = 600.0

# Below this there is no point starting another generation. It has to cover a
# cold load before any tokens are produced at all -- 26-36s on these machines,
# and a load that is interrupted is abandoned rather than resumed -- plus the
# generation itself.
MIN_ATTEMPT_SECONDS = 120.0


def plan_context(spec: str, cfg: swarm.Config, node: swarm.Node, model: str,
                 answer_tokens: int, thinking: int,
                 state: Optional[swarm.State] = None) -> int:
    """One window for the whole loop, sized for the largest it may ever need.

    Ollama reloads the weights when num_ctx changes, which on this pool costs
    about 35 seconds. A repair loop that resized its window per attempt would
    pay that every iteration, so the window is fixed up front -- wide enough
    for a repair prompt carrying the spec, the full previous file, a traceback,
    and a generation grown to the ceiling.

    Sizing for the ceiling rather than the starting budget is what makes the
    budget free to adapt. The window costs VRAM whether or not it is used; the
    generation cap costs nothing until it is hit. So the window is generous and
    fixed, and the cap is tight and adjustable.

    That fixes reloads *within* one component's repair loop. It does nothing
    for the next `do`/`code` invocation, which is a separate process that
    remembers none of this -- and unlike `split`/`run`, which pins one
    context per node+model for the whole run via `plan_contexts`, `do`/`code`
    claims components onto workers live and has no run-wide plan to size
    against up front. Measured live: an `--api`-heavy run left `e4b-linux`
    loaded at a wide window, a small unrelated request right after it needed
    far less, and paid a full ~35s reload purely to get *smaller* -- and
    would have paid a second one the next time an API-sized request landed
    there. `state.resident_ctx` is the floor this call was last loaded at
    (if `state` is given), and the window here only ever grows against it,
    never shrinks: a smaller request rides in the existing window for free,
    and only a genuinely bigger one pays the reload, once, after which that
    becomes the new floor for everything that follows.
    """
    ceiling = answer_tokens * GROWTH_CEILING
    prompt_tokens = swarm.est_tokens(spec) + ceiling + answer_tokens + 900
    ctx = swarm.pick_num_ctx(cfg, node, model, prompt_tokens,
                             ceiling + thinking, reasoning=False)
    if state is not None:
        floor = state.resident_ctx(node.name, model)
        if floor is not None:
            ctx = max(ctx, min(floor, node.max_ctx(model)))
        state.mark_resident_ctx(node.name, model, ctx)
    return ctx


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    """The same advisory note can fire on several attempts in a row (a
    malformed link that never gets fixed, an output.json key that stays
    wrong) -- collapsed to one entry each, in first-seen order, before a
    caller prints them once at completion rather than once per attempt."""
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def generate_file(spec: str, out_path: str, node: swarm.Node, model: str,
                  cfg: swarm.Config, state: swarm.State,
                  check: Optional[str] = None,
                  attempts: int = 3, answer_tokens: int = 3000,
                  thinking: int = 1800, think: bool = False,
                  root: Optional[str] = None, smoke: bool = True,
                  extra: str = "", verbose: bool = True,
                  uses: Optional[Sequence[str]] = None,
                  time_budget: Optional[float] = TIME_BUDGET,
                  env_extra: Optional[Dict[str, str]] = None,
                  rejected: Optional[Callable[[int], List[str]]] = None,
                  sent_count: Optional[Callable[[], int]] = None,
                  count_check: Optional[Callable[[int], List[str]]] = None,
                  dep_paths: Optional[Sequence[str]] = None,
                  forbid_required_cli_args: bool = False,
                  env_names: Optional[Sequence[str]] = None,
                  original_request: str = ""
                  ) -> Dict[str, Any]:
    """Generate one file and repair it until the gates pass.

    `original_request`, when the caller has it, is the un-paraphrased
    request text for the whole `do`/`code` invocation this component
    belongs to -- not `spec`, which for anything routed through the
    ordinary planner is that planner's own summary of what this file
    should do, and can drop a literal detail (an exact promised
    output.json key, most notably) the real request stated outright.
    Passed straight through to `gate_output_json_key` and
    `autofix_output_json_key` as a second text to check the promise
    against, alongside `spec`; see `_promised_keys_from_texts` for why
    both are checked and in what order. Optional and inert when omitted
    -- both functions behave exactly as before against `spec` alone.

    Reasoning is off by default, and that is not a quality decision. num_predict
    caps thinking and answer together, so a model that reasons past the budget
    emits an empty `content` -- the answer is never started, Ollama reports
    `done_reason: length`, and the loop reads it as "the reply contained no
    code block" and spends a repair attempt on a file that was never written.
    Measured on gemma4:12b writing edit_script: reasoning off produced a
    complete 43-line implementation in 546 output tokens and 20 seconds, while
    reasoning on burned past 6800 tokens across repeated cap increases and
    never emitted a single line. Turn it on per call if a task actually needs
    it, and give it a budget that leaves room for the answer.
    """
    root = root or swarm.HERE
    # Standing policy reaches every other kind of call and never reached this
    # one, which is the call that writes the files. `swarm.chat` folds it into
    # a system prompt and `pack_context` excludes `always` cards on the
    # assumption it already has -- true everywhere except here, where there
    # was no system prompt at all. So a card marked as standing policy was
    # read by the triage model, the judge and `crosscheck`, and by nothing
    # that produced code. A rule like "never write a credential literal" has
    # to hold for generated files or it does not hold.
    policy = (swarm.standing_policy()
              if cfg.context.get("standing_policy", True) else "")
    # `extra` -- dependency source, the CONTRACT, attached reference
    # material, the maven boilerplate -- now rides along on every repair
    # attempt (see REPAIR_PROMPT below), not only the first one, so the
    # window has to be sized for it too or a repair attempt on a
    # context-heavy component would silently truncate the very material
    # that made the first attempt any good.
    num_ctx = plan_context(spec + policy + extra, cfg, node, model,
                           answer_tokens, thinking, state=state)
    target = "%s/%s" % (node.name, model)
    started = time.monotonic()

    backup = None
    if os.path.exists(out_path):
        backup = backup_path(out_path)
        try:
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copy2(out_path, backup)
        except OSError as exc:
            # Failing to archive is not a reason to refuse to work, but it
            # does remove the only undo, so it is said out loud rather than
            # swallowed.
            backup = None
            if verbose:
                sys.stderr.write("  could not back up %s: %s\n"
                                 % (out_path, exc))

    history: List[Dict[str, Any]] = []
    # Advisory notes that survive regardless of `verbose`. A parallel
    # component always runs with verbose=False (interleaving several
    # components' worth of per-attempt chatter into one stream would be
    # unreadable), which meant an advisory note -- correct, actionable,
    # and printed nowhere near the operator or the repair loop -- was
    # exactly as lost as if the check had never run at all. Measured
    # live: gate_narrowed_conflict_retry_status_codes had already found
    # run_8's exact defect and said so, and the note still never reached
    # anywhere, because it only ever reached stderr when verbose=True.
    # (That specific gate now also autofixes and no longer depends on
    # this list -- see autofix_narrowed_conflict_retry_status_codes --
    # but the same blind spot applies to every other advisory-only gate,
    # so this collects all of them, not just that one.) Appended
    # regardless of `verbose`; the caller (`run_components`) prints these
    # once, serialized, at the same point it already prints
    # `history_trail` -- not interleaved live, but not silently dropped
    # either.
    notes: List[str] = []
    language, fence = language_for(out_path)
    # The browser lines sit directly under the task. `extra` is where the
    # current file is pasted, and that file is the version that threw, so a
    # note beside it loses to the source the model is told to keep.
    page = page_failure_lines(root, out_path)
    shown_spec = spec
    if page:
        shown_spec = (spec + "\n\nThe browser console reported these lines. "
                      "They are the failure, quoted as the browser printed "
                      "them:\n" + page + "\nA name a line says is not defined "
                      "must not be called, and must not be used except on the "
                      "same line as typeof that name.\n")
    prompt = GENERATE_PROMPT.format(spec=shown_spec, extra=extra,
                                    language=language, fence=fence)
    code = ""
    # Start tight and grow only on proof. Ollama says why it stopped, so
    # running out of room is a fact rather than a guess: if generation was cut
    # off mid-file the cap was genuinely too small and doubling it is correct,
    # and if it stopped on its own the code is merely wrong, which more tokens
    # would not fix. Guessing instead of checking is what let one small module
    # climb from 630 to 7701 tokens without ever passing.
    budget = answer_tokens + thinking
    ceiling = answer_tokens * GROWTH_CEILING + thinking
    # Hold the node for the whole loop. A repair iteration is worthless if
    # another process swaps the model out between attempts.
    with swarm.node_claim(state, node, model):
        # A separate clock from `started` above, on purpose: `started` runs
        # from the moment this call began and is what the final "ok after
        # Nm Ys" summary reports, so it should include real queue wait --
        # that is genuinely how long the operator waited. TIME_BUDGET is a
        # different question ("has the node stopped answering"), and
        # `node_claim`'s own wait (now up to an hour, so several siblings
        # can legitimately queue ahead of this one on a max_parallel=1
        # node) used to count against that budget too, because `spent`
        # below was computed against `started`. Measured live, the exact
        # night that wait was raised: a component reported "gave up after
        # 8m08s; the node stopped answering" while the node in fact
        # answered every one of several queued siblings throughout that
        # window -- this call simply spent most of its 600s budget
        # waiting in line for a turn, then had almost none left once it
        # got one. `node_claim`'s own docstring already names this exact
        # failure shape as the reason `max_parallel` needed enforcing in
        # the first place; enforcing it fixed the GPU thrashing but left
        # this second half of the same bug in place, because queueing
        # correctly is still queueing, and it was still being charged to
        # the wrong clock.
        generation_started = time.monotonic()
        attempt = 0
        growths = 0
        # Latches off after one starvation and stays off: a model that filled
        # the budget with reasoning once will do it again, and the fallback is
        # only useful if it is not re-entered every iteration.
        think_now = think
        while attempt < attempts:
            # A fresh clock per iteration, on purpose: `spent`/`started`
            # above answer "how long has this call taken so far", which is
            # what a budget or a final summary needs. Bulk-debugging a slow
            # repair afterwards needs the other question -- which single
            # attempt, of however many this call made, was the slow one --
            # and until now that was only ever recoverable by re-reading a
            # live log's timestamps, if the log even had any (see
            # `interface_gate`'s repair calls, which had none). Recorded on
            # every `history` entry below so it survives into the saved plan.
            attempt_started = time.monotonic()
            spent = time.monotonic() - generation_started
            left = time_budget - spent if time_budget else None
            if left is not None and left < MIN_ATTEMPT_SECONDS:
                history.append({"attempt": attempt, "stage": "budget",
                                "seconds": round(
                                    time.monotonic() - attempt_started, 1),
                                "error": "gave up after %s; the node stopped "
                                         "answering rather than answering "
                                         "wrongly" % swarm.human(spent)})
                if verbose:
                    sys.stderr.write("  out of time after %s, giving up\n"
                                     % swarm.human(spent))
                break
            # A rough estimate is all `_node_load` (team.py) needs: it only
            # sums these to decide whether a node looks busy right now, not
            # to schedule against a deadline. `state.throughput` is the same
            # measured-decode source `team.rates` reads; falling back to the
            # model's configured hint keeps this working before any sample
            # exists, same as everywhere else that estimates a duration.
            _perf = state.throughput(node.name, model)
            _decode = (_perf or {}).get("decode_tps") or float(
                cfg.model_meta(model).get("decode_tps_hint", 20.0))
            lease = state.acquire_lease(
                node.name, model, budget / max(_decode, 1.0))
            try:
                messages = ([{"role": "system", "content": policy}]
                            if policy else [])
                messages.append({"role": "user", "content": prompt})
                result = swarm.chat_complete(
                    node, model, messages,
                    num_ctx=num_ctx, answer_tokens=budget, headroom=1.0,
                    retries=0,
                    think=None if think_now else False, temperature=0.1,
                    # Long enough for the slowest legitimate generation with
                    # room to spare -- the worst measured here is a 26b taking
                    # 1m26s on an LCS backtracker -- and short enough that a
                    # node which sleeps or drops off the LAN fails instead of
                    # holding a worker. At 2400 a dead box stalled a run for
                    # forty minutes without raising anything, which the worker
                    # death handling cannot help with because nothing is
                    # thrown. Same reasoning that took the planner to 300.
                    # Deliberately not shrunk to whatever the budget has left.
                    # Disconnecting mid-load makes Ollama abort the load, so a
                    # short timeout does not merely fail -- it destroys the
                    # work the next attempt depends on, and every retry starts
                    # loading from scratch and is cut off at the same point.
                    # Measured on e2b-2: 31.0s to load, 0.2s to answer, and a
                    # 20s probe made a healthy box look permanently wedged.
                    # The budget decides whether to start another attempt, not
                    # how long one may take; overrunning it by a single request
                    # is much cheaper than a livelock.
                    timeout=600)
                state.record_perf(node.name, model, result["sample"])
                state.mark_resident(node.name, model)
            except swarm.OllamaError as exc:
                attempt += 1
                history.append({"attempt": attempt, "stage": "request",
                                "seconds": round(
                                    time.monotonic() - attempt_started, 1),
                                "error": str(exc)})
                if verbose:
                    sys.stderr.write("  attempt %d: node error: %s\n"
                                     % (attempt, exc))
                continue
            finally:
                state.release_lease(lease)

            code = extract_code(result["text"], fence)

            # Cut off by the cap rather than finished. The file is incomplete
            # through no fault of the model, so this does not count as a failed
            # attempt -- give it room and ask again, instead of spending a
            # repair round on a syntax error that only exists because the text
            # stopped mid-statement. The window was already sized for the
            # ceiling, so raising the cap costs nothing.
            if (result.get("raw") or {}).get("done_reason") == "length" \
                    and budget < ceiling and growths < 2:
                grown = min(budget * 2, ceiling)
                growths += 1
                history.append({"attempt": attempt, "stage": "truncated",
                                "seconds": round(
                                    time.monotonic() - attempt_started, 1),
                                "error": "hit the %d token cap; raised to %d"
                                         % (budget, grown)})
                # Every returned attempt is kept, truncated ones included
                # -- a cut-off draft is still a real datapoint about what
                # this node/model produced under this budget, not nothing.
                save_failed_attempt(out_path, attempt, "truncated", code,
                                    "hit the %d token cap" % budget)
                if verbose:
                    sys.stderr.write("  truncated at %d tokens, raising cap "
                                     "to %d\n" % (budget, grown))
                budget = grown
                continue

            # Reasoning consumed the whole budget and the answer was never
            # begun. That is a budgeting failure, not a coding one, and the
            # difference matters: charging it as a repair attempt asks the
            # model to fix a file it never wrote, and hands it a longer prompt
            # to fail against next time. Growth above has already been tried,
            # so the remaining move is to stop reasoning and spend the whole
            # budget on the answer.
            if (think_now and not code.strip()
                    and (result.get("thinking") or "")
                    and (result.get("raw") or {}).get("done_reason") == "length"):
                think_now = False
                history.append(
                    {"attempt": attempt, "stage": "starved",
                     "seconds": round(time.monotonic() - attempt_started, 1),
                     "error": "reasoning used the entire %d token budget and "
                              "no answer was emitted; retrying with reasoning "
                              "off" % budget})
                # `code` is empty in this branch by construction (that is
                # the condition that reaches it) -- save_failed_attempt is
                # a no-op on empty code, so this is here purely so the
                # rule "every returned attempt is saved" has no silent
                # exception, not because there is a file to write yet.
                save_failed_attempt(out_path, attempt, "starved", code,
                                    "reasoning used the whole budget")
                if verbose:
                    sys.stderr.write("  reasoning starved the answer, "
                                     "retrying with reasoning off\n")
                continue

            attempt += 1
            python = is_python(out_path)
            if python:
                # After the truncation/starvation checks above (which need
                # to see exactly what the model emitted) and before every
                # gate below (which must see exactly what ships, or a gate
                # could pass a file the operator never actually gets).
                code = maybe_inject_env_loader(code, out_path, env_extra)
            # Where the shared call log stood before this attempt's gates, so
            # only what this attempt sent is quoted back at it.
            sent_before = sent_count() if sent_count else 0
            failure = gate_syntax(code, out_path)
            stage = "parse"
            # Node accepts require() and module.exports. The page does not,
            # and that failure names no call, so nothing else in this chain
            # sends it back. Checked before the file is called done.
            if failure is None and not python:
                failure = gate_undefined_binding(code, out_path, root)
                if failure is not None:
                    stage = "undefined_binding"
            # Advisory, not blocking: a malformed Maven UI link is worth
            # flagging so the operator can look, but it is a cosmetic wart
            # on one printed string, not a reason to burn a repair attempt
            # rewriting an otherwise-correct file. Printed once per attempt
            # rather than fed back into the retry loop as `failure`.
            if failure is None:
                platform_issue = gate_platform_facts(code)
                if platform_issue:
                    notes.append(platform_issue)
                    if verbose:
                        sys.stderr.write(
                            "  [%-18s] note: %s\n"
                            % (os.path.basename(out_path)[:18], platform_issue))
            # Blocking, unlike the advisory link-shape note just above: a
            # link built on the wrong shape is cosmetic on an otherwise
            # correct id; a link (or anything else) built on a fabricated
            # all-zero id is proof the code substituted a fake value for a
            # real one it failed to find, which reaches the real platform
            # exactly the same way whether or not anything happens to be
            # watching the printed string that day.
            if failure is None and python:
                failure = gate_placeholder_id_literal(code)
                if failure is not None:
                    stage = "placeholder_id_literal"
            # Same free, pre-subprocess stage, same blocking treatment,
            # for the credential-shaped and host-shaped sibling of the
            # zeroed UUID above: an `example.com` host or a "YOUR_API_
            # TOKEN"-shaped literal is a fabricated stand-in exactly the
            # same way, and neither `local_urls` (which only rewrites
            # `*_URL` environment variables, never a literal in the
            # source) nor the socket guard (bypassed the moment the code
            # catches and re-raises/exits on its own, which
            # `gate_swallowed_api_failure` already permits) can catch it.
            if failure is None and python:
                failure = gate_placeholder_host_literal(code)
                if failure is not None:
                    stage = "placeholder_host_literal"
            # Same free, pre-subprocess stage, same blocking treatment,
            # for a payload that claims to be base64 in the string right
            # next to it and provably is not. Measured live: a component
            # pushed real HTML content through `requests.utils.quote(...)`
            # into an f-string reading `data:text/html;base64,{...}`, with
            # its own comment admitting "Base64 encoding is complex, using
            # a placeholder structure" -- and the project was pointed at
            # exactly this version, so the applet showed "Loading
            # applet..." forever even though the real survey.html on disk
            # was correct the whole time. Nothing else in this pipeline
            # runs a data URI's payload through a decoder, so nothing else
            # would ever have caught this.
            if failure is None and python:
                failure = gate_mislabeled_base64_data_uri(code)
                if failure is not None:
                    stage = "mislabeled_base64_data_uri"
            # Blocking, unlike the advisory note above: there is no
            # legitimate reason for a step's own creation output to land
            # in a sibling directory's output.json, so unlike a malformed
            # printed link (a cosmetic wart on one string) this is a real
            # functional defect -- the exact next step in a chain fails to
            # find its own dependency's file. Decided from the source, at
            # the same free, pre-subprocess stage as the interface check
            # below, since a text/AST scan costs nothing and this class of
            # bug does not need the file on disk or a real run to catch.
            if failure is None and python:
                failure = gate_own_output_path(code)
                if failure is not None:
                    stage = "own_output_path"
            # Same free, pre-subprocess stage, one rung earlier than the
            # shape check below: a write that never goes through
            # json.dump/json.dumps at all is not merely mis-shaped JSON,
            # it is not JSON, and every consumer's json.load(...) raises
            # on it regardless of what the (non-existent) shape would have
            # been. Checked before gate_output_json_shape on purpose --
            # there is nothing for that gate to inspect the shape of when
            # this one already found no json call in the first place.
            if failure is None and python:
                failure = gate_output_json_not_json(code)
                if failure is not None:
                    stage = "output_json_not_json"
            # Same free, pre-subprocess stage, same blocking treatment: a
            # producer's output.json that is provably a bare scalar rather
            # than a JSON object is not a cosmetic wart, it is a defect
            # every consumer down the chain crashes on the moment it runs.
            if failure is None and python:
                failure = gate_output_json_shape(code)
                if failure is not None:
                    stage = "output_json_shape"
            # Same free, pre-subprocess stage, and the gap the two gates
            # just above both leave open: neither one fires when there is
            # no write to a local output.json at all, which reads
            # identically to "wrote it correctly" to both of them. A spec
            # asking this step to save its own result gets no write
            # whatsoever, silently, unless this checks for the write's
            # existence rather than only its placement/shape once found.
            if failure is None and python:
                failure = gate_missing_own_output_write(spec, code)
                if failure is not None:
                    stage = "missing_own_output_write"
            # Same free, pre-subprocess stage: the write exists, in the
            # right place, in the right shape, and still uses the wrong
            # key -- the one comparison none of the three gates above
            # ever makes, because each of them only ever looks at the
            # code, never at what the request actually promised.
            # Autofixed rather than left to a repair attempt when the
            # rename is unambiguous: the right value is already there
            # under the wrong name, so renaming one dict key is not a
            # guess. See `autofix_output_json_key`'s own docstring for
            # exactly when it declines instead.
            if failure is None and python:
                key_mismatch = gate_output_json_key(
                    spec, code, original_request)
                if key_mismatch is not None:
                    fixed = autofix_output_json_key(
                        spec, code, original_request)
                    if fixed is not None:
                        code = fixed
                        if verbose:
                            sys.stderr.write(
                                "  [%-18s] renamed the wrong output.json "
                                "key to match what the request promised "
                                "-- no repair attempt spent\n"
                                % os.path.basename(out_path)[:18])
                    else:
                        failure = key_mismatch
                        stage = "output_json_key"
            # Same free, pre-subprocess stage, and the comparison the check
            # above cannot make: the key can be exactly right and the value
            # under it still be the wrong resource's id, when this component
            # creates more than one thing along the way. Advisory, not
            # blocking -- see gate_output_json_wrong_variable's own
            # docstring for why an exact-string coincidence is not proof.
            if failure is None and python:
                wrong_var = gate_output_json_wrong_variable(spec, code)
                if wrong_var:
                    notes.append(wrong_var)
                    if verbose:
                        sys.stderr.write(
                            "  [%-18s] note: %s\n"
                            % (os.path.basename(out_path)[:18], wrong_var))
            # Same free, pre-subprocess stage, same advisory treatment:
            # a narrowed conflict-retry status set is a real design
            # choice a file might have a genuine reason for, so this
            # cannot block on it -- see gate_narrowed_conflict_retry_
            # status_codes's own docstring for the live 400 this exists
            # to flag.
            if failure is None and python:
                narrow_retry = gate_narrowed_conflict_retry_status_codes(code)
                if narrow_retry is not None:
                    widened = autofix_narrowed_conflict_retry_status_codes(code)
                    if widened is not None:
                        code = widened
                        if verbose:
                            sys.stderr.write(
                                "  [%-18s] widened a conflict-retry "
                                "status-code check to cover {400, 409, "
                                "422} -- no repair attempt spent\n"
                                % os.path.basename(out_path)[:18])
                    else:
                        notes.append(narrow_retry)
                        if verbose:
                            sys.stderr.write(
                                "  [%-18s] note: %s\n"
                                % (os.path.basename(out_path)[:18],
                                   narrow_retry))
            # Same free, pre-subprocess stage: a promised key can be
            # provably absent (the check above) or provably fine one
            # call at a time and still lost, because a second call to
            # the same writer overwrites the first -- a defect
            # `gate_output_json_key` cannot see precisely because the
            # dict is built through a variable, not a literal, which is
            # the shape that check already declines to guess about.
            if failure is None and python:
                failure = gate_output_json_overwritten(code)
                if failure is not None:
                    stage = "output_json_overwritten"
            # Same free, pre-subprocess stage: a write-only handle read
            # back through itself is not a maybe -- CPython raises
            # `io.UnsupportedOperation: not readable` unconditionally, so
            # this is decidable with certainty from the syntax tree alone
            # and does not need a live smoke run (or four of them, on
            # four different models) to find out. See
            # `gate_write_then_read_same_handle`'s own docstring for the
            # live failure this closes.
            if failure is None and python:
                failure = gate_write_then_read_same_handle(code)
                if failure is not None:
                    # No judgement call to make here -- the file mode is
                    # a fixed fact, so there is exactly one correct fix,
                    # and applying it costs nothing next to a whole
                    # repair round-trip through a model that cannot do
                    # any better (see the live failure in
                    # `autofix_write_then_read_same_handle`'s docstring:
                    # escalating models and replanning the CONTRACT both
                    # reproduced this identical error, because neither
                    # one touches a mode argument). Applied in place and
                    # re-checked before falling through to the ordinary
                    # repair path, so a successful autofix costs zero
                    # attempts and the file continues down the same gate
                    # chain as if it had been written correctly the
                    # first time.
                    fixed = autofix_write_then_read_same_handle(code)
                    if fixed is not None:
                        code = fixed
                        failure = gate_write_then_read_same_handle(code)
                        if failure is None and verbose:
                            sys.stderr.write(
                                "  attempt %d: mechanically reopened the "
                                "write-only handle for reading -- no "
                                "repair attempt spent\n" % attempt)
                    if failure is not None:
                        stage = "write_then_read_same_handle"
            # Same free, pre-subprocess stage: an entry point whose
            # `__main__` guard never calls anything it defines looks
            # identical to success everywhere else -- exit 0, no
            # traffic, nothing to smoke-run -- until a real orchestrated
            # chain finds out three steps later that this one never did
            # anything. Autofix covers only the one shape with no
            # judgement call (a `main()` already defined and simply
            # never invoked); everything else falls through to the
            # ordinary repair path with a message naming exactly what
            # was never called.
            if failure is None and python:
                failure = gate_dead_entry_point(code)
                if failure is not None:
                    fixed = autofix_dead_entry_point(code)
                    if fixed is not None:
                        code = fixed
                        failure = gate_dead_entry_point(code)
                        if failure is None and verbose:
                            sys.stderr.write(
                                "  attempt %d: mechanically called the "
                                "already-defined main() from the "
                                "__main__ guard -- no repair attempt "
                                "spent\n" % attempt)
                    if failure is not None:
                        stage = "dead_entry_point"
            # Same free, pre-subprocess stage: a business id read from
            # `os.environ` instead of a sibling output.json is a promise
            # nothing in the real orchestrated run will ever keep --
            # `placeholder_env` fills it during generation, so this only
            # ever crashes for real, several attempts and a restart
            # later. See `gate_invented_env_id`'s own docstring for the
            # live failure this closes.
            if failure is None and python:
                failure = gate_invented_env_id(code, env_names)
                if failure is not None:
                    stage = "invented_env_id"
            # Same free, pre-subprocess stage, and scoped to the one call
            # site that knows this is a chain-style entry point invoked
            # with zero CLI arguments every time: a required argparse
            # argument here can never be satisfied by the real
            # orchestrator, no matter how many times it is retried.
            if failure is None and python and forbid_required_cli_args:
                failure = gate_required_cli_args(code)
                if failure is not None:
                    stage = "required_cli_args"
            # Same free, pre-subprocess stage: a fabricated client (real
            # `requests` import, never one real call) and a swallowed API
            # failure (an except block that prints and returns with no
            # `raise`/`sys.exit`) are both decidable from the source alone,
            # and both were measured live to pass every other gate --
            # neither leaves a trace in the mock's call log the way a
            # rejected call does, so nothing downstream would ever catch
            # them otherwise.
            if failure is None and python:
                failure = gate_fabricated_client(code)
                if failure is not None:
                    stage = "fabricated_client"
            if failure is None and python:
                failure = gate_swallowed_api_failure(code)
                if failure is not None:
                    stage = "swallowed_api_failure"
            # Same free, pre-subprocess stage: a GET-then-check-then-
            # overwrite of the same field is decidable from the source
            # alone, and the mock's own call log gives no hint anything
            # is wrong -- the GET succeeds (200), and the guard is what
            # raises, entirely on the client side, before any write is
            # even attempted. Autofixed rather than left to a repair
            # attempt: the write already proves it has a replacement
            # value, so deleting the fetch-and-check is not a guess.
            if failure is None and python:
                stale_precondition = gate_stale_precondition_before_overwrite(
                    code)
                if stale_precondition is not None:
                    fixed = autofix_stale_precondition_before_overwrite(code)
                    if fixed is not None:
                        code = fixed
                        if verbose:
                            sys.stderr.write(
                                "  [%-18s] removed a fetch-and-check that "
                                "could only ever fail (it required the "
                                "field this call is about to set for the "
                                "first time to already be set) -- no "
                                "repair attempt spent\n"
                                % os.path.basename(out_path)[:18])
                    else:
                        failure = stale_precondition
                        stage = "stale_precondition_before_overwrite"
            # Same free, pre-subprocess stage, and deliberately advisory
            # rather than blocking: unlike the check just above, there is
            # no textual proof this fetch is pointless, only that its
            # result is never used for anything but the guard -- see
            # gate_dead_value_after_guard's own docstring for why that
            # difference means "flag it" rather than "fix it" or "fail
            # the file".
            if python:
                dead_value_note = gate_dead_value_after_guard(code)
                if dead_value_note:
                    notes.append(dead_value_note)
            # Same free, pre-subprocess stage: a `try: HEADERS / except
            # NameError: HEADERS = {}` guard around a name that is never
            # assigned any other way is not defensive code, it is a
            # deterministic no-op -- and unlike gate_fabricated_client and
            # gate_swallowed_api_failure just above, this shape leaves a
            # perfectly normal-looking entry in the mock's call log (a
            # real POST, just with empty headers), so nothing downstream
            # of generation would ever catch it either.
            if failure is None and python:
                failure = gate_placeholder_credential_guard(code)
                if failure is not None:
                    stage = "placeholder_credential_guard"
            if failure is None and uses and python:
                failure = gate_uses(code, uses) or gate_no_stubs(code, uses)
                stage = "uses"
            if failure is None:
                os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".",
                            exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(code)
                # Decidable from the syntax tree alone, against dependencies
                # that are already on disk, so it runs before either
                # subprocess gate below rather than after: a call into a
                # method a finished dependency's class does not have is never
                # going to survive gate_smoke, and finding that out the hard
                # way costs a mock round-trip and a process launch on every
                # attempt instead of one AST walk on this one. Measured live:
                # a main.py depending on an already-complete api_client.py
                # burned 6 attempts plus a full whole-loop restart -- 12
                # attempts, several minutes -- on what `interface_gate`
                # already knows how to name for free, just not until every
                # component's own budget was already spent.
                if python and dep_paths:
                    failure = gate_interfaces(out_path, root, dep_paths)
                    if failure is not None:
                        stage = "interfaces"
                # Importing and running are Python-shaped questions. A
                # stylesheet has no module level and no `main`, so asking is
                # not a weaker check, it is a meaningless one -- the `--check`
                # below is where a non-Python file gets judged.
                if failure is None and python:
                    failure = gate_import(out_path, root,
                                          env_extra=env_extra)
                    stage = "import"
                    # Free, so it runs whether or not a real check was
                    # supplied. When none was, this is the only gate that sees
                    # the code do anything at all.
                    if failure is None and smoke:
                        failure = gate_smoke(
                            out_path, root, env_extra=env_extra,
                            required_labels=output_labels(spec))
                        stage = "smoke"
                if failure is None and check:
                    ok, out = run_command(check, root, timeout=300,
                                          env_extra=env_extra)
                    if not ok:
                        failure, stage = out[-2500:] or "check failed", "check"

            # A traceback says `HTTP Error 422: Unprocessable Entity` and the
            # mock knows it was `missing required field 'version'`. The second
            # is the repair instruction; the first is a status line. Measured
            # live: three attempts on one file, each told only the status.
            if failure is not None and rejected is not None:
                refused = rejected(sent_before)
                if refused:
                    failure += ("\n\nThe API rejected these requests. The mock "
                                "is built from the real spec, so these are "
                                "what the real service would have said:\n  "
                                + "\n  ".join(refused))

            # A rejected request is not the same claim as no traffic at all.
            # `gate_smoke` only requires the process not to crash, so a call
            # to an endpoint that does not exist passes every structural gate
            # cleanly -- the file imports, `main` returns 0, and the only
            # trace of the defect is a line in the mock's own log that nothing
            # here was reading. Measured live: a generated `get_ui_url`
            # called `GET /api/projects/{id}/my-ui-url`, which is nowhere in
            # the real spec, and "3/3 passed every gate" ran right past it --
            # the 404 was printed at the end of the run, not fed back to the
            # writer, because nothing had failed by the gates' own definition.
            #
            # A client legitimately calling nothing from `main` is not this:
            # that is the reason rejections were never wired into pass/fail
            # in the first place. But a call the mock actually rejected is
            # never a legitimate outcome -- it means the code reached for an
            # endpoint, field, or type the real service does not have, which
            # is exactly the class of defect this whole mechanism exists to
            # catch before it reaches production.
            if failure is None and rejected is not None:
                refused = rejected(sent_before)
                if refused:
                    failure = ("the mock API rejected %d request%s this file "
                               "sent, which means it called something the "
                               "real service would not accept:\n  %s"
                               % (len(refused),
                                  "" if len(refused) == 1 else "s",
                                  "\n  ".join(refused)))
                    stage = "api"

            # An accepted call is not the same claim as a *correctly sized*
            # one. `rejected` catches a call the real service would refuse;
            # this catches the one it would happily accept, at the wrong
            # size. Measured live: "uploads 3 units to it" produced a task
            # with 2, and every gate -- including the rejection check above
            # -- passed clean, because a 200 says nothing about how many
            # rows were in the body that earned it.
            if failure is None and count_check is not None:
                wrong = count_check(sent_before)
                if wrong:
                    failure = ("the code ran and the API accepted its "
                               "request(s), but sent the wrong number of "
                               "items:\n  %s" % "\n  ".join(wrong))
                    stage = "count"

            # No rewrite of the code fixes a socket refused on purpose, so
            # this does not count as a repairable failure -- spending the
            # rest of the attempt budget rewriting a file with nothing
            # wrong with it is exactly the loop `--api`-less smoke testing
            # used to run into. Measured live: three identical
            # `HTTPConnectionPool(host='127.0.0.1', port=1)` failures on one
            # file, none of them the file's fault.
            if (failure is not None and stage == "smoke"
                    and NO_MOCK_MARKER in failure):
                history.append({"attempt": attempt, "stage": stage,
                                "seconds": round(
                                    time.monotonic() - attempt_started, 1),
                                "error": failure})
                # Not a defect in the code, but still a real generated
                # file worth keeping -- "every datapoint is valuable"
                # applies here too, not only to the attempts that were
                # actually wrong.
                save_failed_attempt(out_path, attempt, stage, code, failure)
                if verbose:
                    sys.stderr.write(
                        "  attempt %d: %s -- retrying will not help; pass "
                        "--api <name> so a mock exists to test against\n"
                        % (attempt, NO_MOCK_MARKER))
                break

            if failure is None:
                elapsed = time.monotonic() - started
                if verbose:
                    sys.stderr.write("  attempt %d: passed all gates (%s)\n"
                                     % (attempt, swarm.human(elapsed)))
                # The backup is deliberately kept on success too. Success
                # means the gates passed, which says the new file parses,
                # imports and satisfies whatever was asked of it -- not that
                # it kept everything the old one did. A whole-file rewrite can
                # pass every gate while having dropped a function nothing else
                # imports, and the operator who wants to check that has
                # nothing to compare against once the previous content is
                # gone.
                return {"ok": True, "path": out_path, "code": code,
                        "backup": backup,
                        "target": target, "attempts": attempt,
                        "seconds": round(elapsed, 1), "history": history,
                        "notes": _dedupe_preserve_order(notes)}

            history.append({"attempt": attempt, "stage": stage,
                            "seconds": round(
                                time.monotonic() - attempt_started, 1),
                            "error": failure})
            # The one thing this attempt has that will not survive past
            # this call returning: the actual source that failed. On a
            # real failure this is worth keeping regardless of whether
            # the file ultimately passes later -- a component that needed
            # several rungs to get there previously left no way to see
            # *what* the earlier, wrong attempts actually looked like, only
            # the condensed exception line above.
            save_failed_attempt(out_path, attempt, stage, code, failure)
            if verbose:
                first = failure.splitlines()[0] if failure else ""
                sys.stderr.write("  attempt %d: %s failed -- %s\n"
                                 % (attempt, stage, first[:90]))
            shown_error = failure
            if page:
                shown_error = ("The browser console reported:\n%s\n\n%s"
                               % (page, failure))
            prompt = REPAIR_PROMPT.format(
                stage=stage, error=shown_error, code=code, fence=fence,
                spec=shown_spec, extra=extra,
                avoid=avoid_repeats(history, failure))

    # Never leave a broken file behind pretending to be finished. Copied back
    # rather than moved, so the archive survives the restore: the same file
    # may be attempted again later, and a restore should not cost the history.
    if backup and os.path.exists(backup):
        shutil.copy2(backup, out_path)
    elif os.path.exists(out_path):
        os.remove(out_path)
    return {"ok": False, "path": out_path, "code": code, "target": target,
            "backup": backup,
            "attempts": attempts, "seconds": round(time.monotonic() - started, 1),
            "history": history, "notes": _dedupe_preserve_order(notes)}
