---
id: unique-slug
title: Human readable title
subject: infrastructure
subcategory: network
tags: [ollama, hosts, gpu]
priority: 5
summary: One single line. This is what gets sent when the token budget is tight.
updated: 2026-09-01
---

The body is the full version of this card. It is sent verbatim when there is
budget for it, and silently swapped for the one-line `summary` above when there
isn't. That two-tier design is the whole trick: you precompute the compression
once, at authoring time, instead of paying a model to summarise at query time.

Rules for this file format, because the parser is intentionally dumb:

- Frontmatter values are single-line only. No nested YAML, no multi-line strings.
- `tags` is a flat bracketed list. `priority` is 1-10 and breaks scoring ties.
- Files beginning with `_` are ignored, so this template is never retrieved.
- One topic per card. If a card needs two summaries, it should be two cards.
- `always: true` marks a card as standing policy. It skips relevance scoring
  and its `summary` goes into the system prompt on every call, so keep those
  summaries short and imperative. Use it sparingly — every always-card is a tax
  on every request.

Keep the body under roughly 400 words. Cards are selected whole, so a bloated
card crowds out three relevant small ones.
