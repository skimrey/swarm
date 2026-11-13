---
id: policy-api-writes
title: Creating records against a real API
subject: policy
subcategory: api
tags: [api, client, http, posts, creates, applet, slug, project, unique, name, identifier, duplicate, conflict, endpoint, requests, uploads, pushes, assigns, tasks, units]
priority: 8
summary: Never hardcode a field the document calls unique -- suffix it with time.strftime("solomon-%Y%m%d-%H%M%S"), evaluated at run time, and retry once with random characters if it is still refused. Put the response body in the error and print each id as you create it.
updated: 2026-09-14
---

A gate runs against a mock that keeps no state. The real service does, and
that difference shows up in exactly one place: creating things.

**A field documented as unique is unique across the whole service and
forever, not per run.** A hardcoded literal works the first time and is
refused every time after, which reads as the script breaking when nothing
about it changed. Suffix it with the current time, prefixed `solomon-` so
it is recognizable as the operator's own regardless of which project
generated it:

```python
stamp = time.strftime("solomon-%Y%m%d-%H%M%S")
applet = client.create_applet(slug=f"survey-applet-{stamp}",
                              name=f"Survey Applet {stamp}")
```

A timestamp rather than a random suffix, and the difference is the whole
point: it is unique without anyone having to remember what was used
before. Nothing needs a record of every name ever created, the value says
when it was made, and a listing sorts into the order it happened. A uuid
buys the same uniqueness and tells the operator nothing.

**Ask the clock, never write a date.** You do not know today's date and
have no way to find out -- nothing runs you at the moment the request is
made, so there is no clock to consult. `time.strftime` is evaluated later,
when the generated script actually runs on the operator's machine, and
only then does the value become correct. A literal like
`"survey-applet-solomon-20240115"` is a guess that is wrong the moment it
is written and then stays wrong forever, which is the hardcoded case
again wearing a timestamp. A date in quotes in your output is a bug.

Do this on the first attempt, not as recovery. Look for the word "unique"
in the field's description, and treat `slug`, `code`, `key`, and a `name`
the document calls unique the same way.

**Suffix every human-readable name a script creates, documented as unique
or not.** A field's description saying nothing about uniqueness is not
the document promising there is none -- `ProjectCreate.name` in this spec
carries no description at all. The cost of a stamp on a field that turns
out not to need one is nothing; the cost of skipping it on one that did is
a script that only works once. A single `stamp` computed at the top of
`main` and reused across every create in the run keeps the applet, the
project, and the task readable together as one run instead of three
unrelated timestamps.

**If a create is refused anyway, append and retry once.** Two runs inside
the same second collide, and a rejection naming the identifier -- 400 or
409, with the field in the body -- means the value is taken rather than
the request being malformed. Retry once with a few random characters on
the end, then give up; a loop that keeps trying turns one bad assumption
into a hundred records.

**Never reuse an existing record to dodge a conflict** unless the request
asked for that. Quietly writing into whatever already had the name is
worse than failing, because nothing on screen says it happened.

**Put the response body in the error.** The status says a request was
refused; the body says which field and why. Losing it makes every failure
the same sentence and leaves the reason on the server:

```python
try:
    response.raise_for_status()
except requests.HTTPError as e:
    raise requests.HTTPError(
        f"{e}\n{method} {url}\nresponse body: {e.response.text[:800]}",
        response=e.response) from None
```

**Print every identifier you create**, on its own line, as it is created.
A script that creates an applet, a version, a project and a task and then
fails at the last step has made three real records the operator cannot
find. This is also the only way a follow-up run can target what the last
one made.

**A `PATCH` changes only the fields you send -- it is not a full-object
`PUT`, and it does not need the rest of the object fetched first.** If a
request asks you to update one field, send a body containing that one
field, not the whole schema. Measured live: asked to point a project at a
new applet version, a generated `PATCH /api/projects/{project_id}`
carried the one real field alongside a dozen others the request never
mentioned -- `"name": "Updated Project Name"`, each one marked in its own
comment as `# Placeholder`. That comment is the code admitting, in its
own words, that it invented a value with no basis, and sending it to a
real service overwrites whatever was actually there. Do not fetch the
resource "to be safe" and resend its other fields unchanged either --
that is the same mistake with an extra step, and it is also the one that
produces a nonsensical precondition (see `policy-applet-versioning.md`'s
note on this exact shape) when the field you are about to set has never
had a value at all. Send exactly the fields the request asked you to
change.
