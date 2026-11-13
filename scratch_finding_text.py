"""A finding's message and its detail are one fact, so they read as one line.

`team.why_it_failed` and `team.why` were both built for tracebacks, where
the last non-boilerplate line is always the exception -- a correct
heuristic for that shape. `crossref.Finding.text()` used to put a finding's
message and its detail on two different lines (`"path:line  message\n
      detail"`), and both heuristics silently kept the detail line and
dropped the message -- the one line that names what was actually called
wrong.

Measured live, verbatim: a run's summary and `team.replan_component`'s own
prompt both read

    interfaces: it defines: Client, base64, hashlib, main, os, requests

with no way to tell what was being looked for, because the line that said
`main.py calls client.get_ui_url(...), but client.py has no such
attribute` was the one being thrown away. The repair loop itself was never
blind to this -- `REPAIR_PROMPT` is built from the raw, unstripped failure
text -- but the operator reading the summary, and `replan_component`
deciding whether the CONTRACT was the actual problem, both were.

Validated by sabotage: reverting `Finding.text()` to the two-line format
reproduces the exact garbled line above.
"""
import crossref
import team

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, ("  -- %s" % detail) if detail else ""))
        FAILURES.append(name)


MESSAGE = ("calls client.get_ui_url(...), but client.py has no such "
          "attribute")
DETAIL = "it defines: Client, base64, hashlib, main, os, requests"


def _gate_message() -> str:
    f = crossref.Finding("main.py", 23, MESSAGE, DETAIL)
    return ("this file does not fit the code it depends on -- these are "
           "facts read from the other files, not opinions:\n  " + f.text())


def test_finding_text_keeps_message_and_detail_together():
    f = crossref.Finding("main.py", 23, MESSAGE, DETAIL)
    text = f.text()
    check("message and detail are one line", "\n" not in text, text)
    check("the message survives", MESSAGE in text)
    check("the detail survives", DETAIL in text)


def test_why_it_failed_keeps_the_message():
    collapsed = team.why_it_failed(_gate_message())
    check("why_it_failed keeps the actual defect, not just its context",
         MESSAGE in collapsed, collapsed)


def test_summary_line_keeps_the_message():
    component_error = "interfaces: " + team.why_it_failed(_gate_message())
    row = team.why(component_error)
    check("the summary table cell still names the defect, even truncated",
         "calls client.get_ui_url" in row, row)


def _sabotage_two_line_finding():
    """Confirms these tests actually exercise the fix."""
    class OldFinding:
        def __init__(self, path, line, message, detail=""):
            self.path, self.line = path, line
            self.message, self.detail = message, detail

        def text(self):
            out = "%s:%d  %s" % (self.path, self.line, self.message)
            if self.detail:
                out += "\n      %s" % self.detail
            return out

    f = OldFinding("main.py", 23, MESSAGE, DETAIL)
    gate_message = ("this file does not fit the code it depends on -- "
                   "these are facts read from the other files, not "
                   "opinions:\n  " + f.text())
    collapsed = team.why_it_failed(gate_message)
    assert MESSAGE not in collapsed, (
        "sabotage failed to reproduce the bug -- the old two-line "
        "Finding.text() should have made why_it_failed drop the message")
    assert collapsed == DETAIL, collapsed
    print("  ok   sabotage confirmed: the two-line format reproduces the "
         "exact live failure (%r)" % collapsed)


if __name__ == "__main__":
    test_finding_text_keeps_message_and_detail_together()
    test_why_it_failed_keeps_the_message()
    test_summary_line_keeps_the_message()
    _sabotage_two_line_finding()
    if FAILURES:
        raise SystemExit("%d failure(s): %s" % (len(FAILURES), FAILURES))
    print("OK")
