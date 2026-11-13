---
id: policy-output-json
title: Persisting an id for a later step
subject: policy
subcategory: orchestration
tags: [output.json, id, project_id, task_id, applet_id, version_id, unit_id, chain, sibling, step, orchestration]
priority: 8
always: true
summary: If this script creates something that has an id (a project, task, applet, applet version, unit, or similar) and the request does not say otherwise, write that id to output.json in the current directory as well as printing it, using a key named after what was created (project_id, task_id, applet_id, ...) -- a later step in the same chain almost always reads it from there, and stdout alone is gone the moment this process exits.
updated: 2026-09-21
---

Printing an id is not the same as saving one. `print(project_id)` puts the
value on this process's stdout, which a human watching a live run can read,
and which a later step run as a *separate* `python3 main.py` invocation in a
*different* directory can never see. If nothing writes the id anywhere on
disk, the only copy of it that ever existed is gone the instant this script
exits, and a later step that depends on it has to invent something -- an
empty-looking UUID, an environment variable nobody set -- instead of failing
loudly, which is worse.

So: if this file creates a resource that has its own id -- a project, a
task, an applet, an applet version, a unit, or anything similarly
identified -- and the request does not explicitly say the id is not needed
elsewhere, write it to a file named `output.json` in the current working
directory, in addition to printing it. Use `json.dump` with a plain dict, one
key per id this step produced, named after what was created:

```python
import json

with open("output.json", "w") as f:
    json.dump({"project_id": project_id}, f)
```

Name the key after the *thing*, not generically -- `project_id`, `task_id`,
`applet_id`, `applet_version_id`, `unit_id` -- because a later step's own
request will ask for it by that exact name (`"read project_id from
../01_create_project/output.json"`), and a mismatched key is as unusable as
no file at all.

If this step produced more than one id worth keeping (for example, an applet
id and the version id created from it), write all of them into the same
`output.json`, one key each, in one `json.dump` call -- do not write several
files or overwrite the same key twice.

If the request explicitly names a different file or format for the hand-off
(a CSV, a different filename, no hand-off at all because this is the last
step in its chain), follow the request instead. This is the default when
nothing else is said, not an override of anything the request does say.
