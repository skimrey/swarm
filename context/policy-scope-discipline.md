---
id: policy-scope-discipline
title: Build exactly what was asked, nothing adjacent
subject: policy
subcategory: scope
tags: [scope, endpoint, unused, extra, hallucinate, placeholder, id, output.json, intake, task, creep]
priority: 8
always: true
summary: Implement only the exact feature this request describes. Call only the endpoint(s) that one feature actually needs, never an unrelated one just because it is listed nearby. Never invent a value for an id, key, or field you were not given -- read it from the file the request names, or from the API response that created it. Read/write output.json through Python code that runs, not as a deliverable file in itself.
updated: 2026-09-21
---

**Write the feature described, not the feature this looks adjacent to.**
A request that says "create a project; save its id as project_id" is a
request for one API call, not a mini demo of the workflow it happens to
sit inside. Measured live: a component asked for exactly that also
created a task -- an endpoint that was sitting in this file's own API
context, unused by the actual request, and calling it anyway is not a
bonus, it is a second feature nobody asked for, executed against a real
service. If the request does not name a capability, do not add it, even
if it would be a reasonable next step, even if the API slice happens to
make it easy, and even if an earlier or later step in the same chain
does something similar -- that is a different file's job.

**Every endpoint you call has to trace back to a word in this specific
request.** A slice or index may list several endpoints for context --
that list is what exists, not what to use. Before calling anything,
check that this exact request asked for that effect. "Create a project"
justifies `POST /api/projects` and nothing else; it does not justify
also creating a task, a unit, or an applet version just because those
calls are nearby, well-documented, or would make the script more
"complete."

**Some features are opt-in by name, and telephony is the sharpest real
example.** Measured live: a request reading only "create a project;
save its id..." came back calling `POST /api/projects/{id}/telephony/
sessions` as well as the project-creation call itself -- the endpoint
was sitting in the same `--api` slice as filler, scored in only because
it shared the word "project," and the writer called it anyway because
nothing told it that some capabilities need to be asked for by their own
name before they count as in scope at all. The mock accepted it (it has
no business rule to reject with); the real platform correctly rejected
it, three steps downstream, in a run that had never asked for telephony
anywhere. If a slice or index shows a telephony endpoint and the request
does not contain the literal word "telephony," treat it exactly like an
endpoint that is not in the document at all -- it is not something this
task needs, no matter how nearby or well-documented it looks. The same
rule applies to any other feature category that is naturally opt-in
rather than a default part of creating the parent resource it sits
under.

**Never hallucinate a value for an id, a key, or any field you were not
handed.** If a value is not in this request's own text, not in the file
it tells you to read from, and not something an API response you just
received gave you, you do not have it -- do not invent a placeholder,
a plausible-looking UUID, or a guessed field name to fill the gap.
Getting a real id always means one of exactly two things: it came back
in a response body from a call you just made, or it was read from a
file the request named. Nothing else is a legitimate source.

**Read ids in from the file the request names; write ids out to this
step's own output.json; never the reverse, never both at once on the
same file, and never more than once per run.** "Read project_id from
../01_create_project/output.json" means open exactly that path, exactly
once, and use the value -- never write anything back into it. Saving
this step's own new id(s) means opening `output.json` in *this*
directory, in write mode, exactly once, with every id this step
produced already gathered into one dict before that single
`json.dump` call -- not once per id, not through a shared helper called
twice, each time starting from an empty dict (that silently discards
every earlier call's id). See `policy-output-json.md` for the exact key
naming convention.

**This is a behavior a Python program performs when it runs, never a
deliverable file in itself.** "Save its id to output.json" describes
what the generated *code* does the moment someone executes it -- it is
not a request for a static `output.json` artifact sitting on disk right
now, the way "create survey.html" is a request for an actual HTML file.
If a request's only static-looking artifact is one it describes the
*code* producing at runtime (an id, a report, a generated file written
by `main()`), write the `.py` file that does that; do not hand back a
literal `output.json` (or any other output file) as if the request were
asking for the artifact itself rather than the program that creates it.
