"""Offline tests for judge.py: the model call is stubbed, nothing touches the network."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.calls = []
        judge._node = lambda name: ("http://stub", "stub-model", 0)
        self.reply = json.dumps({"broken": True, "quote": "Hey dude", "fix": "Use a formal greeting."})
        self.confirm = json.dumps({"violates": True})
        def ask(url, model, prompt, timeout=90.0, num_ctx=0):
            if prompt.startswith("A rule and a short quotation"):
                self.calls.append("confirm")
                return self.confirm
            self.calls.append(prompt)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        judge._ask = ask

    def test_broken_with_real_quote_fails(self):
        out = judge.failures("Hey dude, welcome.", ["The tone is formal."], self.root)
        self.assertEqual(len(out), 1)
        self.assertIn("Hey dude", out[0])
        self.assertIn("formal greeting", out[0])

    def test_quote_not_in_text_is_dropped(self):
        self.reply = json.dumps({"broken": True, "quote": "yo wassup", "fix": "x"})
        self.assertEqual(judge.failures("Good day to you.", ["The tone is formal."], self.root), [])

    def test_unconfirmed_finding_passes(self):
        self.confirm = json.dumps({"violates": False})
        self.assertEqual(judge.failures("Hey dude, welcome.", ["The tone is formal."], self.root), [])

    def test_rule_holds(self):
        self.reply = json.dumps({"broken": False, "quote": "", "fix": ""})
        self.assertEqual(judge.failures("Good day.", ["The tone is formal."], self.root), [])

    def test_verdict_is_cached_and_survives_a_new_process(self):
        judge.failures("Hey dude, welcome.", ["The tone is formal."], self.root)
        judge.failures("Hey dude, welcome.", ["The tone is formal."], self.root)
        self.assertEqual(len(self.calls), 2)  # the question and its confirmation, once
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".ralph", "judge-cache.jsonl")))

    def test_new_rule_wording_or_text_asks_again(self):
        judge.failures("Hey dude, welcome.", ["The tone is formal."], self.root)
        judge.failures("Hey dude, welcome.", ["The tone is very formal."], self.root)
        judge.failures("Hey dude, welcome!", ["The tone is formal."], self.root)
        self.assertEqual(len([c for c in self.calls if c != "confirm"]), 3)

    def test_unreachable_judge_passes_and_caches_nothing(self):
        self.reply = OSError("down")
        self.assertEqual(judge.failures("Hey dude.", ["The tone is formal."], self.root), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, ".ralph", "judge-cache.jsonl")))
        self.reply = json.dumps({"broken": True, "quote": "Hey dude", "fix": "f"})
        self.assertEqual(len(judge.failures("Hey dude.", ["The tone is formal."], self.root)), 1)

    def test_garbage_reply_passes(self):
        self.reply = "I think it is fine"
        self.assertEqual(judge.failures("Hey dude.", ["The tone is formal."], self.root), [])

    def test_quote_matches_across_whitespace_and_case(self):
        self.reply = json.dumps({"broken": True, "quote": "hey   DUDE", "fix": "f"})
        self.assertEqual(len(judge.failures("Hey\ndude, hi.", ["The tone is formal."], self.root)), 1)


if __name__ == "__main__":
    unittest.main()
