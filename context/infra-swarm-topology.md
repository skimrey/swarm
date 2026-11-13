---
id: infra-swarm-topology
title: Swarm node topology
subject: infrastructure
subcategory: network
tags: [ollama, hosts, nodes, gpu, vram, topology]
priority: 9
summary: Four ollama nodes; the 26b box is a 12GB 4070 that spills to system RAM and is the slow one.
updated: 2026-09-01
---

The pool is four ollama servers on the LAN, all speaking the standard HTTP API
on port 11434.

- **26b box** — Windows, 10.0.0.190, RTX 4070, 12GB VRAM. Runs `gemma4-fixed:latest`,
  the user's own 26b MoE with a custom Modelfile, not the stock `gemma4:26b` tag
  (25.2B params, Q4_K_M, 16.9GB on disk). Measured 17.1 tok/s: it spills past
  the card's VRAM and decodes at roughly typing speed. Highest quality in the
  pool, and by a wide margin the slowest of its two models on this box. Route
  to it only when the task genuinely needs it.

  This box also has `gemma4:12b` pulled, and here it is *not* deprioritized: at
  7.6GB it fits the 4070's 12GB comfortably and measured 43.3 tok/s, faster
  than the 26b on this same card. That is the opposite relationship from the
  Mac and the e4b-linux box, where 12b is the one that chokes. Don't assume a
  model's behavior transfers between machines — the same tag can be the fast
  option on one GPU and the one to avoid on another. Trust `swarm.py bench`
  per node, every time, over pattern-matching from a different box.
- **e4b box** — Linux, 10.0.0.130. Runs `gemma4:e4b` at a measured 33.5 tok/s,
  the workhorse: good enough for most reasoning and planning, fast enough not
  to hurt. Also has `gemma4:12b` pulled, but measured at 4.4 tok/s it does not
  fit this GPU either (same dense-vs-effective-parameter story as the Mac) and
  is pinned to a 0.05 model bias rather than removed outright.
- **e2b box** — runs `gemma4:e2b`. Fastest by a lot. Handles classification,
  extraction, status checks, sentiment, and the router's own second opinion on
  ambiguous prompts.
- **mac** — the controller, an M5 MacBook Pro with 16GB unified memory at
  10.0.0.13. Serves `gemma4:e2b` and `gemma4:e4b` as overflow with a low bias.
  The 26b (18GB) does not fit at all. `gemma4:12b` is registered but pinned to
  a 0.05 model bias, because it is dense: all 12B parameters stay resident and
  the whole machine becomes unusable while it runs.

Do not size E-series models by their download size. `gemma4:e4b` is a 9.6GB
artifact against 12b's 7.6GB, but most of that is per-layer embedding tables
which do not need to occupy fast memory during inference. Resident footprint
tracks the *effective* parameter count — 2.3B for e2b, 4.5B for e4b — so both
run comfortably in 16GB while the smaller-on-disk 12b does not. Measured on
this machine: e2b 68 tok/s, 12b 13 tok/s.

Gemma 4 native context limits are 128K on the E-series and 256K on the 26b MoE,
so the practical ceiling is KV-cache VRAM rather than the model. The router
sizes `num_ctx` per request for exactly this reason: an oversized KV cache on
the 4070 steals memory the weights need and makes the spill worse.

Note that `gemma4:26b` is a Mixture-of-Experts model, 26B total parameters with
only about 3.8B active per token. On hardware that fits it, it runs near 4B
speeds. The 4070 does not fit it, which is why its measured throughput is far
below what its architecture would suggest. Always trust `swarm.py bench` output
over reasoning from parameter counts.
