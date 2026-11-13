---
id: platform-maven-urls
title: Maven admin UI links are on a different host from the API
subject: platform
subcategory: maven
tags: [maven, project, task, link, url, admin, portal, applet, share, cloudfront, browser, review]
priority: 7
summary: The admin UI is https://d30ca7kumywvjf.cloudfront.net -- a different host from the API. A project link is that host plus /projects/{project_id}; a task link is that host plus /tasks/{task_id}. Never build a UI link from API_BASE_URL. Print/return the link, not the bare id, whenever one of these two is derivable.
updated: 2026-09-20
---

The Maven API and the Maven admin UI are two different hosts. Building a
link for a human to open out of the API's base URL produces an address that
looks right and goes nowhere.

    API   https://dztfdhkmj6wqi.cloudfront.net      (API_BASE_URL, paths start /api/)
    UI    https://d30ca7kumywvjf.cloudfront.net      (what a person opens)

A project link is:

    https://d30ca7kumywvjf.cloudfront.net/projects/{project_id}

A task link is the same host, same shape, different collection:

    https://d30ca7kumywvjf.cloudfront.net/tasks/{task_id}

Neither is in the OpenAPI document. Nothing there describes the UI, and no
response field returns a link to it, so there is no way to derive either
one and neither must be guessed. A script that builds
`f"{API_BASE_URL}/projects/{project_id}"` is wrong even though it runs,
because the reader only finds out when they click it. There is no third
shape -- an applet, a unit, a submission -- confirmed yet; do not invent
one by analogy. Only `/projects/{id}` and `/tasks/{id}` are real.

**Prefer the link over the bare id in anything meant for a human to
read.** Once a project or task id is in hand, the thing worth printing,
returning, or writing to a result file for a person is the clickable link
built from it, not the id alone -- an id is only useful for another API
call or another script, a link is useful to the person who asked for the
work. This does not replace saving the raw id to `output.json` for a
downstream step to consume (that step wants the id, not a URL to parse);
it is about what a script prints to the console or puts in a
human-facing summary. When both a project and a task exist, print both
links.

Read the UI host from `MAVEN_UI_URL` when it is set, and fall back to the
literal above. It is a public address rather than a credential, so a
default is appropriate here -- the rule against inventing values is about
secrets and about facts you cannot check, and this one is recorded.

Keep it separate from `API_BASE_URL`. During gates that variable points at
a local mock, and a UI link derived from it would come out as
`http://127.0.0.1:PORT/projects/...`.
