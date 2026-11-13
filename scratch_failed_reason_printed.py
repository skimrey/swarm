"""A FAILED component's summary line must say why, not just that.

Measured live: a one-endpoint component failed 6 attempts, restarted the
whole loop, failed 6 more, and the entire generate.log for it read
"FAILED after 6 attempt(s), 10m33s" -- nothing about the cause, even
though `component.error` was computed the whole time (it is the same
string a retry or `replan_component` would have been shown). This
exercises the real `run_components` with generation stubbed to always
fail the same way, and checks the final printed line actually contains
the reason rather than just the pass/fail verdict and the clock.
"""
import io
import sys

import codegen
import intent
import swarm
import team


def comp(name, deps, floor=2):
    c = intent.Component.__new__(intent.Component)
    c.name = name
    c.path = "scratchpkg/%s.py" % name
    c.spec = "irrelevant, generation is stubbed"
    c.floor = floor
    c.deps = deps
    c.ok = False
    c.code = ""
    c.error = ""
    c.target = ""
    c.attempts = 0
    c.seconds = 0.0
    c.contract = ""
    c.history_trail = []
    return c


def main():
    plan = [comp("project_creator", [])]

    real = codegen.generate_file

    def always_fails(spec, out_path, node, model, cfg, state, **kw):
        return {"ok": False, "path": out_path, "code": "",
                "target": "stub", "attempts": 6, "seconds": 5.0,
                "history": [{"attempt": i, "stage": "smoke",
                            "error": "running main.py raised:\n"
                                     "requests.exceptions.HTTPError: 401 "
                                     "Client Error: Unauthorized"}
                           for i in range(1, 7)]}

    codegen.generate_file = always_fails
    captured = io.StringIO()
    real_stderr = sys.stderr
    try:
        cfg = swarm.Config.load()
        state = swarm.State()
        sys.stderr = captured
        try:
            team.run_components(plan, cfg, state, repair=1,
                                answer_tokens=100, check=None,
                                root="/tmp", tests=None)
        finally:
            sys.stderr = real_stderr
        state.close()
    finally:
        codegen.generate_file = real

    output = captured.getvalue()
    print(output)

    failed_lines = [ln for ln in output.splitlines() if "FAILED after" in ln]
    assert failed_lines, "no FAILED summary line printed at all:\n%s" % output
    line = failed_lines[-1]
    assert "401" in line or "Unauthorized" in line, (
        "FAILED line printed with no reason attached: %r" % line)

    print("OK: %r" % line)


if __name__ == "__main__":
    main()
