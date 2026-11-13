---
id: policy-answer-style
title: How local models should answer
subject: policy
subcategory: style
tags: [style, output, prompting, format]
priority: 6
always: true
summary: Answer directly with no preamble and no closing summary; match the requested length exactly; for classification or extraction reply with the bare value and nothing else.
updated: 2026-09-01
---

Small models pad. Left alone they open with "Certainly! Here's a breakdown of
your question about..." and close with a summary of what they just said. Both
habits burn tokens the pool does not have and slow every response.

The house style for anything generated locally:

- Lead with the answer. No preamble, no restating the question back.
- No closing summary unless the answer was genuinely long.
- Code blocks contain code only. Explanations go outside them.
- When something is unknown, say so in one sentence rather than guessing at
  length. A short honest answer routes better than a confident wrong one.
- Match the requested output size. "One line" means one line.

For extraction and classification work, reply with the bare value and nothing
else — no quotes, no label, no trailing period. Downstream code parses these
directly and any decoration has to be stripped.
