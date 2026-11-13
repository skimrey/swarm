---
id: infra-ollama-ops
title: Ollama operational settings
subject: infrastructure
subcategory: ollama
tags: [ollama, env, systemd, windows, keepalive, vram, kvcache]
priority: 8
summary: Bind 0.0.0.0, keep models resident with OLLAMA_KEEP_ALIVE, halve KV cache with q8_0 plus flash attention.
updated: 2026-09-01
---

Every node needs `OLLAMA_HOST=0.0.0.0:11434` or it only listens on loopback and
the router cannot reach it. Port 11434 also has to be open inbound in the host
firewall.

The settings that matter for this pool:

- `OLLAMA_KEEP_ALIVE=-1` keeps the model resident indefinitely. Without it the
  model unloads after five idle minutes and the next request pays a cold load,
  which on the 26b box is tens of seconds. This single variable is the largest
  latency win available.
- `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` together roughly
  halve KV-cache memory. On the VRAM-constrained 26b box that memory goes back
  to the weights and directly reduces spill.
- `OLLAMA_NUM_PARALLEL=1` on the constrained box. Concurrent slots each get
  their own KV cache allocation, which is the opposite of what that machine
  needs. The faster boxes can run 2 or more.
- `OLLAMA_MAX_LOADED_MODELS=1` on single-purpose nodes, so a stray request for
  a different tag cannot evict the resident model.

Per-request `num_ctx` in the options block always overrides the server default
and is what the router uses. `OLLAMA_CONTEXT_LENGTH` only sets the default for
requests that do not specify one.

Useful checks: `ollama ps` shows what is resident and how much sits on CPU
versus GPU, and a non-zero CPU percentage there is the spill telling you it is
about to be slow.
