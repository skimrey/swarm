---
id: policy-routing
title: Routing policy and tiers
subject: policy
subcategory: routing
tags: [routing, tiers, escalation, dispatch, capability]
priority: 7
summary: Four tiers set a capability floor; among eligible nodes the router maximises quality per second.
updated: 2026-09-01
---

Routing happens in two stages. A deterministic classifier reads surface
features of the prompt — code fences, file paths, imperative verbs, prompt
length, numbered steps, requested output size — and assigns one of four tiers.
This costs nothing and takes no measurable time. Only when it reports low
confidence does the router spend roughly 200ms asking e2b for a second opinion.

The tiers and the minimum model capability each requires:

- **trivial** — yes/no, status checks, classification, extraction, one-liners.
  Floor: capability 1, so e2b is eligible.
- **simple** — a short factual answer or a small rewrite. Floor: 1.
- **moderate** — multi-step reasoning, planning, comparison, real summarisation.
  Floor: 2, so e4b and up.
- **hard** — code, debugging, architecture, proofs, long-form output. Floor: 3.

The tier sets a floor, not a target. Every model at or above the floor is a
candidate, and the router picks whichever maximises `quality^qw / seconds^sw`,
scaled by that node's bias. Expected seconds come from measured throughput plus
current queue depth, so a busy fast node loses to an idle slower one exactly
when it should.

Any model may reply with the single word `ESCALATE` if a task is beyond it. The
router then raises the floor by one and retries. Cheap self-triage after the
fact beats trying to predict difficulty perfectly beforehand.

Tune `speed_weight` up when latency matters more than polish, and `quality_weight`
up before a session where correctness dominates. Per-node `bias` above 1.0
attracts traffic to a node you want exercised, below 1.0 holds one in reserve.
