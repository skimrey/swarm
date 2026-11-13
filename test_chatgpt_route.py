#!/usr/bin/env python3
"""Offline tests for chatgpt_route. Runs against a fake server; never touches
the real ChatGPT box or any Ollama node.

    python3 test_chatgpt_route.py
"""
import json
import os
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chatgpt_route as cr
import swarm


class Fake:
    """Fake browser server. Behaviour is switched per test."""
    reply = "fake answer"
    sequence: list = []  # per-request replies, consumed first; "__DROP__" closes with no response
    delay = 0.0
    prompts: list = []
    active = 0
    max_active = 0
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with Fake.lock:
            Fake.active += 1
            Fake.max_active = max(Fake.max_active, Fake.active)
            Fake.prompts.append(body["messages"][-1]["content"])
        time.sleep(Fake.delay)
        with Fake.lock:
            Fake.active -= 1
            reply = Fake.sequence.pop(0) if Fake.sequence else Fake.reply
        if reply == "__DROP__":
            self.close_connection = True
            return
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


NODE = types.SimpleNamespace(name="n1")


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["CHATGPT_ROUTE_DIR"] = self.dir
        os.environ["CHATGPT_ROUTE_URL"] = self.url
        os.environ["CHATGPT_ROUTE_MAX_TOKENS"] = "24000"
        os.environ["CHATGPT_ROUTE_MAX_CHARS"] = "96000"
        os.environ["CHATGPT_ROUTE_TIMEOUT"] = "10"
        os.environ["CHATGPT_ROUTE_ATTEMPTS"] = "3"
        cr.IDLE_GAP_S = 0.0
        cr.SETTLE_AFTER_NO_REPLY_S = 0.0
        cr.SETTLE_AFTER_UNREACHABLE_S = 0.0
        Fake.reply, Fake.delay, Fake.prompts, Fake.sequence = "fake answer", 0.0, [], []
        Fake.active = Fake.max_active = 0
        self.orig_calls = []

        def original(node, model, messages, num_ctx, **kw):
            self.orig_calls.append(messages)
            return {"text": "from ollama", "thinking": "", "sample": {}, "raw": {}}

        self.route = cr.make_router(original)

    def call(self, content="hello", **kw):
        return self.route(NODE, "m", [{"role": "user", "content": content}], 4096, **kw)

    def log(self):
        p = os.path.join(self.dir, "chatgpt-route.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def test_under_limit_goes_to_chatgpt(self):
        r = self.call("what is 2+2")
        self.assertEqual(r["text"], "fake answer")
        self.assertEqual(Fake.prompts, ["what is 2+2"])
        self.assertEqual(self.orig_calls, [])
        # no rates, so record_perf never files this under an Ollama node
        self.assertIsNone(r["sample"]["decode_tps"])
        self.assertIsNone(r["sample"]["prefill_tps"])
        self.assertTrue(r["raw"]["chatgpt"])

    def test_over_token_limit_uses_standard(self):
        r = self.call("x" * 100000)
        self.assertEqual(r["text"], "from ollama")
        self.assertEqual(Fake.prompts, [])

    def test_over_char_limit_uses_standard_even_if_tokens_look_small(self):
        os.environ["CHATGPT_ROUTE_MAX_TOKENS"] = "10000000"
        r = self.call("x" * 96001)
        self.assertEqual(r["text"], "from ollama")

    def test_just_under_limit_goes_to_chatgpt(self):
        r = self.call("x" * 90000)
        self.assertEqual(r["text"], "fake answer")

    def test_warmup_ping_stays_on_ollama(self):
        r = self.call("hi", num_predict=1)
        self.assertEqual(r["text"], "from ollama")
        self.assertEqual(Fake.prompts, [])

    def test_system_and_history_are_folded_into_one_prompt(self):
        self.route(NODE, "m", [{"role": "system", "content": "BE TERSE"},
                               {"role": "user", "content": "first"},
                               {"role": "assistant", "content": "ok"},
                               {"role": "user", "content": "second"}], 4096)
        sent = Fake.prompts[0]
        for needle in ("BE TERSE", "first", "ok", "second"):
            self.assertIn(needle, sent)
        self.assertLess(sent.index("BE TERSE"), sent.index("second"))

    def test_server_error_reply_falls_back_and_pauses(self):
        Fake.reply = "Error: [Errno 2] No such file or directory: 'google-chrome'"
        r = self.call()
        self.assertEqual(r["text"], "from ollama")
        self.assertEqual(len(Fake.prompts), 3)  # all three attempts used
        # breaker is open: the next call does not touch the server at all
        r2 = self.call()
        self.assertEqual(r2["text"], "from ollama")
        self.assertEqual(len(Fake.prompts), 3)
        self.assertIn("paused", self.log()[-1]["why"])

    def test_rate_limit_reply_falls_back_and_pauses_long(self):
        Fake.reply = "You've reached our limit of messages. Please try again later."
        self.assertEqual(self.call()["text"], "from ollama")
        self.assertEqual(len(Fake.prompts), 1)  # a usage limit is not retried
        self.assertGreater(cr._paused_for(self.dir), 600)

    def test_empty_reply_falls_back(self):
        Fake.reply = "   "
        self.assertEqual(self.call()["text"], "from ollama")

    def test_unreachable_server_falls_back(self):
        os.environ["CHATGPT_ROUTE_URL"] = "http://127.0.0.1:1"
        self.assertEqual(self.call()["text"], "from ollama")

    def test_requests_never_overlap_on_the_server(self):
        Fake.delay = 0.3
        out = []
        ts = [threading.Thread(target=lambda: out.append(self.call()["text"])) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(out, ["fake answer"] * 4)
        self.assertEqual(Fake.max_active, 1)

    def test_copy_failure_then_success_is_retried(self):
        Fake.sequence = ["Error: Clipboard was empty after clicking the copy icon", "good answer"]
        r = self.call()
        self.assertEqual(r["text"], "good answer")
        self.assertEqual(len(Fake.prompts), 2)
        self.assertEqual(self.orig_calls, [])
        self.assertEqual(cr._paused_for(self.dir), 0)  # breaker stays closed
        self.assertEqual([x["route"] for x in self.log()], ["retry", "chatgpt"])

    def test_dropped_connection_is_retried(self):
        Fake.sequence = ["__DROP__", "good answer"]
        self.assertEqual(self.call()["text"], "good answer")
        self.assertEqual(len(Fake.prompts), 2)

    def test_two_failures_then_success_on_the_third(self):
        Fake.sequence = ["Error: x", "", "third time"]
        self.assertEqual(self.call()["text"], "third time")
        self.assertEqual(len(Fake.prompts), 3)

    def test_three_failures_fall_back_and_pause(self):
        Fake.sequence = ["__DROP__", "Error: x", "__DROP__", "never used"]
        self.assertEqual(self.call()["text"], "from ollama")
        self.assertEqual(len(Fake.prompts), 3)
        self.assertEqual(Fake.sequence, ["never used"])
        self.assertGreater(cr._paused_for(self.dir), 0)
        self.assertIn("3 attempts", self.log()[-1]["why"])

    def test_attempts_env_is_respected(self):
        os.environ["CHATGPT_ROUTE_ATTEMPTS"] = "1"
        Fake.reply = "Error: x"
        self.call()
        self.assertEqual(len(Fake.prompts), 1)

    def test_retry_waits_for_the_server_to_settle(self):
        cr.SETTLE_AFTER_NO_REPLY_S = 0.4
        Fake.sequence = ["__DROP__", "ok"]
        t0 = time.monotonic()
        self.call()
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)

    def test_retries_never_overlap_on_the_server(self):
        Fake.delay = 0.1
        Fake.sequence = ["Error: x", "Error: x", "ok", "Error: x", "ok"]
        out = []
        ts = [threading.Thread(target=lambda: out.append(self.call()["text"])) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(Fake.max_active, 1)

    def test_on_token_gets_the_reply(self):
        got = []
        self.call(on_token=got.append)
        self.assertEqual(got, ["fake answer"])

    def test_install_replaces_swarm_chat_once_and_keeps_original(self):
        saved = swarm.chat
        try:
            cr._installed = False
            cr.install()
            cr.install()
            self.assertIs(swarm.chat._chatgpt_original, saved)
        finally:
            swarm.chat = saved
            cr._installed = False


if __name__ == "__main__":
    unittest.main(verbosity=2)
