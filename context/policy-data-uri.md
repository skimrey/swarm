---
id: policy-data-uri
title: A data: URI's label must match its actual encoding
subject: policy
subcategory: encoding
tags: [data uri, base64, artifact_url, applet, html, encode, hex, binascii, version, manifest, template]
priority: 8
summary: A "data:...;base64," URI must contain real base64 (base64.b64encode(x).decode()), never .hex()/binascii, even as a placeholder. Hex characters are valid base64 characters, so a wrong encoder produces something that decodes to different bytes instead of failing loudly.
updated: 2026-09-14
---

`data:text/html;base64,{content}` is not free-form text after the comma --
the label is a promise about which decoder reads it. Observed live:
`f"data:text/html;base64,{html.encode().hex()}"` -- the label says base64,
the bytes are hex.

This does not fail loudly, which is what makes it dangerous. `0-9a-f` are
all valid base64 characters, so a real base64 decoder accepts the string
without error and produces a different sequence of bytes than the ones
that went in. The result is a page that opens as unreadable noise instead
of a form, with nothing on screen saying why.

Use `base64.b64encode(content.encode("utf-8")).decode("ascii")`. Never
`.hex()`, `binascii.hexlify`, or any encoder whose name does not contain
"base64" for content going after `;base64,`. If unsure whether a field
truly wants base64 versus a raw string, that is a real question -- check
the field's description or ask, rather than picking an encoder and hoping
the label matches by coincidence.

The same rule applies in reverse: content that is not base64-labelled must
not be base64-encoded, since a plain string field decoded as raw bytes on
the other end would corrupt it identically.
