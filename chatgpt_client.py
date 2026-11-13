#!/usr/bin/env python3
"""Minimal client for the hush ChatGPT browser server (POST /v1/chat/completions).

    python3 chatgpt_client.py "What is the capital of France?"   # one-shot
    python3 chatgpt_client.py                                    # interactive chat
    python3 chatgpt_client.py --url http://127.0.0.1:11436 "hi"

Stdlib only. The server drives a real Chrome window, so each call takes
5-120s (server gives up with 504 after 120s) and the client waits longer.
"""
import argparse
import http.client
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:%s" % os.environ.get("CHATGPT_SERVER_PORT", "11436")
CLIENT_TIMEOUT = 150  # server's own cap is 120s


def server_up(base_url):
    """The server only answers POST, so probe with a bare TCP connect."""
    u = urllib.parse.urlparse(base_url)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=3):
            return True
    except OSError:
        return False


def chat(base_url, messages, model="chatgpt"):
    body = json.dumps({"model": model, "messages": messages}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=CLIENT_TIMEOUT) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit("server returned HTTP %d: %s" % (e.code, e.read().decode(errors="replace")[:500]))
    except urllib.error.URLError as e:
        raise SystemExit("could not reach %s: %s" % (base_url, e.reason))
    except (http.client.HTTPException, ConnectionError, socket.timeout) as e:
        raise SystemExit("connection to %s dropped: %s" % (base_url, e))
    text = data["choices"][0]["message"]["content"]
    return text, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="*", help="prompt; omit for interactive mode")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default="chatgpt")
    ap.add_argument("--json", action="store_true", help="print the raw response JSON (one-shot only)")
    args = ap.parse_args()

    if not server_up(args.url):
        sys.exit("nothing listening at %s. Start it with: python3 chatgpt_browser_server.py" % args.url)

    if args.prompt:
        text, secs = chat(args.url, [{"role": "user", "content": " ".join(args.prompt)}], args.model)
        print(text)
        print("\n[%.1fs]" % secs, file=sys.stderr)
        return

    # Interactive. The server presumably types only a prompt into ChatGPT, so
    # we send the whole history each turn and let the server decide what to use.
    print("Talking to %s. Ctrl-D or 'quit' to exit." % args.url)
    history = []
    while True:
        try:
            line = input("\nyou> ").strip()
        except EOFError:
            print()
            return
        if line in ("quit", "exit"):
            return
        if not line:
            continue
        history.append({"role": "user", "content": line})
        print("(waiting on Chrome...)", flush=True)
        text, secs = chat(args.url, history, args.model)
        history.append({"role": "assistant", "content": text})
        print("chatgpt> %s\n[%.1fs]" % (text, secs))


if __name__ == "__main__":
    main()
