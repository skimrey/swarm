#!/usr/bin/env python3
"""Opt-in route: send every model call to the ChatGPT browser server when it fits.

This is a parallel path. Nothing in swarm.py/team.py/codegen.py is edited;
`install()` replaces `swarm.chat` (the one function every model call goes
through) with a wrapper. Without `install()`, nothing changes.

Per call:
  * prompt under the size limit  -> ChatGPT (POST /v1/chat/completions)
  * prompt over the limit        -> the original Ollama path, unchanged
  * ChatGPT fails (no reply, dropped connection, copy failed, empty reply):
    retried, up to CHATGPT_ROUTE_ATTEMPTS attempts in total (default 3), waiting
    between attempts for the server to settle. A usage-limit reply is not retried.
  * every attempt failed / rate-limited -> the original Ollama path, and ChatGPT
    is skipped for a while (circuit breaker) so a dead server costs one round of
    attempts, not one per call.

Why it is built this way (all measured on this server, see chatgpt_limit_test.py):
  * The server drives one Chrome window and has no lock. Two requests at once
    paste into the same chat box and both come back wrong. Every call here
    takes a cross-process file lock, so requests go one at a time, with a short
    idle gap after each.
  * It passed 96k characters (~24k tokens) and failed at 128k. The limit is
    enforced on both tokens and characters, because characters are what was
    measured and code tokenises worse than the 4-chars/token estimate.
  * A request that fails may still be running in Chrome (a dropped connection
    does not stop it). Sending the next one immediately overlaps them, so a
    failure pauses ChatGPT for a while instead of retrying at once.
  * Warm-up pings (num_predict=1) stay on Ollama; each ChatGPT call opens a
    browser window and takes over the machine's keyboard.

Config (environment):
  CHATGPT_ROUTE_URL         default http://10.0.0.178:11436
  CHATGPT_ROUTE_MAX_TOKENS  default 24000
  CHATGPT_ROUTE_MAX_CHARS   default 96000
  CHATGPT_ROUTE_TIMEOUT     default 600  (seconds to wait for one reply)
  CHATGPT_ROUTE_ATTEMPTS    default 3    (total tries per call, first one included)
  CHATGPT_ROUTE_DIR         lock/breaker/log directory (default: next to state.db)

Decisions are appended to <dir>/chatgpt-route.jsonl.
"""
import fcntl
import functools
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

import swarm

DEFAULT_URL = "http://10.0.0.178:11436"
IDLE_GAP_S = 4.0            # server idle time after each request, held under the lock
SETTLE_AFTER_NO_REPLY_S = 45.0   # before a retry when no reply came back (Chrome may still be running)
SETTLE_AFTER_UNREACHABLE_S = 10.0  # before a retry when the server was not reachable
FAIL_PAUSE_S = 90.0         # skip ChatGPT this long after every attempt failed
RATE_LIMIT_PAUSE_S = 900.0  # ...and this long after a usage-limit reply
LOCK_WAIT_S = 3600.0

RATE_LIMIT_RE = re.compile(
    r"(reached (our|the|your)\b.{0,30}\blimit|usage (limit|cap)|too many requests|"
    r"try again (later|in)|you've hit|you have hit)", re.I)

_thread_lock = threading.Lock()
_installed = False


def _cfg() -> Dict[str, Any]:
    d = os.environ.get("CHATGPT_ROUTE_DIR") or os.path.dirname(swarm.STATE_PATH)
    return {
        "url": os.environ.get("CHATGPT_ROUTE_URL", DEFAULT_URL).rstrip("/"),
        "max_tokens": int(os.environ.get("CHATGPT_ROUTE_MAX_TOKENS", "24000")),
        "max_chars": int(os.environ.get("CHATGPT_ROUTE_MAX_CHARS", "96000")),
        "timeout": float(os.environ.get("CHATGPT_ROUTE_TIMEOUT", "600")),
        "attempts": max(1, int(os.environ.get("CHATGPT_ROUTE_ATTEMPTS", "3"))),
        "dir": d,
    }


# --------------------------------------------------------------------------
# sizing and prompt building
# --------------------------------------------------------------------------

def _content(m: Dict[str, Any]) -> str:
    c = m.get("content", "")
    return c if isinstance(c, str) else str(c)


def prompt_size(messages: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    text = [_content(m) for m in messages]
    chars = sum(len(t) for t in text)
    tokens = sum(swarm.est_tokens(t) for t in text) + 4 * len(text)
    return {"chars": chars, "tokens": tokens}


def flatten(messages: Sequence[Dict[str, Any]]) -> str:
    """One prompt string. The server only types the last user message, so a
    system prompt or earlier turns have to be folded into it."""
    if len(messages) == 1 and messages[0].get("role", "user") == "user":
        return _content(messages[0])
    parts: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        label = {"system": "SYSTEM INSTRUCTIONS", "assistant": "EARLIER ASSISTANT REPLY"}.get(role, "USER")
        parts.append("%s:\n%s" % (label, _content(m)))
    parts.append("Follow the system instructions above and answer the final USER message.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# lock, breaker, log (all files, so they hold across processes)
# --------------------------------------------------------------------------

class _Lock:
    """Exclusive across threads and processes; waits up to LOCK_WAIT_S."""

    def __init__(self, directory: str):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "chatgpt.lock")
        self.fh = None

    def __enter__(self):
        _thread_lock.acquire()
        try:
            self.fh = open(self.path, "a+")
            deadline = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("chatgpt lock held for over %ds" % LOCK_WAIT_S)
                    time.sleep(0.5)
        except BaseException:
            if self.fh:
                self.fh.close()
            _thread_lock.release()
            raise

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        finally:
            _thread_lock.release()


def _breaker_path(d: str) -> str:
    return os.path.join(d, "chatgpt-breaker.json")


def _paused_for(d: str) -> float:
    try:
        with open(_breaker_path(d)) as f:
            until = float(json.load(f).get("until", 0))
    except (OSError, ValueError):
        return 0.0
    return max(0.0, until - time.time())


def _trip(d: str, seconds: float, reason: str) -> None:
    os.makedirs(d, exist_ok=True)
    with open(_breaker_path(d), "w") as f:
        json.dump({"until": time.time() + seconds, "reason": reason}, f)


def _log(d: str, rec: Dict[str, Any]) -> None:
    try:
        os.makedirs(d, exist_ok=True)
        rec["ts"] = time.strftime("%F %T")
        with open(os.path.join(d, "chatgpt-route.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

class ChatGPTFailure(RuntimeError):
    """`settle` is how long to leave the server alone before a retry: long when
    the request may still be running in Chrome (no reply came back), short
    when the server itself reported the failure (its handler has finished).
    `retry=False` for failures a retry cannot fix (a usage limit)."""

    def __init__(self, msg: str, pause: float = FAIL_PAUSE_S,
                 settle: Optional[float] = None, retry: bool = True):
        super().__init__(msg)
        self.pause = pause
        self.settle = IDLE_GAP_S if settle is None else settle
        self.retry = retry


def _post(url: str, prompt: str, timeout: float) -> str:
    body = json.dumps({"model": "chatgpt", "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url + "/v1/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # 504 and friends: the Chrome run may still be going.
        raise ChatGPTFailure("HTTP %d from chatgpt server" % e.code, settle=SETTLE_AFTER_NO_REPLY_S)
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ChatGPTFailure("chatgpt server timed out", settle=SETTLE_AFTER_NO_REPLY_S)
        # Refused / unreachable: nothing was started, so nothing is running.
        raise ChatGPTFailure("cannot reach chatgpt server: %s" % e.reason, settle=SETTLE_AFTER_UNREACHABLE_S)
    except (http.client.HTTPException, ConnectionError, socket.timeout, OSError, ValueError) as e:
        raise ChatGPTFailure("chatgpt server dropped the connection: %s" % e,
                             settle=SETTLE_AFTER_NO_REPLY_S)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ChatGPTFailure("malformed reply from chatgpt server")


def _ask(cfg: Dict[str, Any], prompt: str) -> str:
    reply = _post(cfg["url"], prompt, cfg["timeout"]).strip()
    # The server reports its own exceptions as a normal 200 reply.
    if reply.startswith("Error:"):
        raise ChatGPTFailure("server error: %s" % reply[:160])
    if not reply:
        raise ChatGPTFailure("empty reply")
    if len(reply) < 400 and RATE_LIMIT_RE.search(reply):
        raise ChatGPTFailure("rate limited: %s" % reply[:160], pause=RATE_LIMIT_PAUSE_S, retry=False)
    return reply


def _result(text: str, prompt_tokens: int, total_s: float) -> Dict[str, Any]:
    # No decode/prefill rates: state.record_perf would otherwise file a browser
    # round trip under whichever Ollama node the scheduler happened to pick.
    return {
        "text": text, "thinking": "",
        "sample": {"prompt_tokens": prompt_tokens, "output_tokens": swarm.est_tokens(text),
                   "prefill_tps": None, "decode_tps": None, "load_s": 0.0,
                   "total_s": total_s, "ttft_s": None},
        "raw": {"done_reason": "stop", "chatgpt": True},
        "thinking_starved": False,
    }


def make_router(original):
    """Wrap `original` (the real swarm.chat). Pure function of the environment."""

    @functools.wraps(original)
    def routed(node, model, messages, num_ctx, *args, **kw):
        cfg = _cfg()
        d = cfg["dir"]
        num_predict = kw.get("num_predict", args[0] if args else -1)
        size = prompt_size(messages)
        who = "%s/%s" % (getattr(node, "name", "?"), model)

        def standard(why: str):
            _log(d, {"route": "standard", "why": why, "node": who, **size})
            return original(node, model, messages, num_ctx, *args, **kw)

        if num_predict == 1:
            return standard("warm-up ping")
        if size["tokens"] >= cfg["max_tokens"] or size["chars"] > cfg["max_chars"]:
            return standard("over limit (%d tokens, %d chars)" % (size["tokens"], size["chars"]))
        wait = _paused_for(d)
        if wait > 0:
            return standard("chatgpt paused %.0fs more after a failure" % wait)

        prompt = flatten(messages)
        try:
            with _Lock(d):
                if _paused_for(d) > 0:  # another process failed while we queued
                    return standard("chatgpt paused while queued")
                t0 = time.monotonic()
                attempts = cfg["attempts"]
                reply = None
                for n in range(1, attempts + 1):
                    try:
                        reply = _ask(cfg, prompt)
                        break
                    except ChatGPTFailure as exc:
                        if n >= attempts or not exc.retry:
                            _trip(d, exc.pause, str(exc))
                            time.sleep(IDLE_GAP_S)
                            return standard("chatgpt failed after %d attempt%s: %s"
                                            % (n, "" if n == 1 else "s", exc))
                        # Hold the lock and let the server settle: a retry sent
                        # while the failed run is still in Chrome overlaps it.
                        _log(d, {"route": "retry", "attempt": n, "of": attempts,
                                 "why": str(exc), "wait_s": exc.settle, "node": who, **size})
                        time.sleep(exc.settle)
                secs = time.monotonic() - t0
                time.sleep(IDLE_GAP_S)
        except TimeoutError as exc:
            return standard(str(exc))

        _log(d, {"route": "chatgpt", "node": who, "secs": round(secs, 1),
                 "reply_chars": len(reply), **size})
        on_token = kw.get("on_token")
        if on_token:
            on_token(reply)
        return _result(reply, size["tokens"], secs)

    routed._chatgpt_original = original
    return routed


def install() -> None:
    """Route swarm.chat through ChatGPT. Idempotent."""
    global _installed
    if _installed:
        return
    swarm.chat = make_router(swarm.chat)
    _installed = True
    cfg = _cfg()
    sys.stderr.write("ChatGPT mode ON: prompts under %d tokens / %d chars -> %s; "
                     "larger ones use the normal nodes.\n"
                     % (cfg["max_tokens"], cfg["max_chars"], cfg["url"]))
