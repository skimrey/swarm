---
id: policy-applet-versioning
title: An applet has no content of its own -- only a version does
subject: policy
subcategory: maven
tags: [applet, version, versions, content, update, push, publish, artifact_url, create_applet_version, update-content, html, project, precondition]
priority: 8
summary: PATCH /api/applets/{applet_id} only changes metadata (name/slug/status) -- it cannot carry content, and there is no endpoint that attaches content to an applet directly. Content lives on a version. "Update/push/publish the applet's content" always means POST /api/applets/{applet_id}/versions (required: version, artifact_url) to create a new one -- never a search for an existing version, and never a PATCH straight onto the applet. A freshly created applet has zero versions; finding an empty versions[] is the normal case, not an error to raise on. Pointing a project at a new version is a one-way PATCH /api/projects/{project_id} with the version_id you already have -- never GET the project first to require its current applet_version_id already be set; a project with no version yet has none, which is the normal case this write exists to handle, not an error.
updated: 2026-09-24
---

**A newly created applet has no version, and that is expected, not broken.**
`POST /api/applets` creates the applet record and nothing else. Its
`versions` list starts empty and stays empty until something explicitly
creates one. Code that does `GET /api/applets/{applet_id}`, reads
`versions[]`, finds it empty, and raises `"No versions found"` has
diagnosed a real fact and drawn the wrong conclusion from it -- the fix
for an empty list is to create the thing that belongs in it, not to
report failure and stop.

**"Update the applet with this content" is an instruction to create a
version, not to look for one.** There is no endpoint that patches content
onto the applet resource itself -- `PATCH /api/applets/{applet_id}` only
ever touches metadata such as name, slug, or status; it has no content
field, and passing one is silently ignored or rejected depending on the
route, never applied. Every request shaped like "push/update/publish the
applet with X" -- regardless of whether the applet already has versions or
none at all -- means:

```
POST /api/applets/{applet_id}/versions
{
  "version": "1.0.0",              # any string; bump it if one already exists
  "artifact_url": "data:text/html;base64,...",   # see policy-data-uri.md for the encoder
  "integrity_sha256": "<sha256 hex of the raw bytes>"   # optional but cheap, include it
}
```

The response's `id` field is the new `applet_version_id`. That is what a
later step needs to attach this content to a project -- save it to
`output.json` (see `policy-output-json.md`), the same as any other created
id.

**`PATCH /api/applet-versions/{version_id}/update-content` is for revising
a version that already exists**, not for creating the first one -- it
takes a `version_id` as a path parameter, which by definition means a
version record is already there. If the applet has no version yet, this
endpoint has nothing to operate on and cannot be reached honestly; create
the version first with the endpoint above. Once a version exists, a
*second* content push to that same applet is a judgment call the request
should settle: create another version (most APIs treat versions as
immutable history) unless the request specifically says to revise this
exact version in place.

**Never fetch the applet and read `versions[-1]` as though version order
in that array is guaranteed or as though its presence is something to wait
for.** If the array is empty, that is not a timing issue to retry past --
nothing else is going to populate it. The only way a version comes to
exist is this file calling the endpoint above.

**Pointing a project at a new version needs no read first.** Once a
version exists (you just created it above, or a sibling step already did
and saved `version_id` to its own `output.json`), attaching it is a
one-way write: `PATCH /api/projects/{project_id}` with
`{"applet_version_id": version_id}`. Do not `GET` the project first to
read its *current* `applet_version_id` and require that value to already
be set before proceeding -- a project that has never had a version
assigned has no `applet_version_id` yet, which is not a malformed
response or a timing issue, it is the exact, permanent state of the one
case this script exists to handle. A guard reading `GET
/api/projects/{project_id}`, checking `applet_version_id` is present, and
raising if it is not, is checking the *old* value of the very field you
already have the *new* value for and are about to overwrite -- it fails
every time on a first assignment and serves no purpose the rest of the
time either, since the PATCH already carries the value that matters.
