#!/usr/bin/env python3
"""Every .html component under `--api maven` gets the reference applet
baked into its prompt verbatim, deterministically -- not through the
scored-card system, which cannot guarantee a large literal template
survives the summary-vs-full cutoff or fires on every request.

Stubs `codegen.generate_file` to capture the `extra` kwarg each component
was actually handed, so this never contacts a node.
"""
import shutil
import tempfile
import threading

import codegen
import intent
import swarm
import team

TIMEOUT = 15.0


def run_plan(specs, api_name):
    components = []
    for name, path, floor, deps in specs:
        component = intent.Component(name, path, floor, list(deps), "spec")
        component.contract = "NONE"
        components.append(component)

    captured = {}

    def _instant(spec, out_path, node, model, cfg, state, **kw):
        name = out_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        captured[name] = kw.get("extra", "")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("<html></html>\n" if out_path.endswith((".html", ".htm"))
                     else "VALUE = 1\n")
        return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                "target": "%s/%s" % (node.name, model), "code": "x"}

    real_gen = codegen.generate_file
    codegen.generate_file = _instant
    root = tempfile.mkdtemp(prefix="plan-")
    box = {}
    try:
        thread = threading.Thread(
            target=lambda: box.update(r=team.run_components(
                components, swarm.Config.load(), swarm.State(), root=root,
                api_name=api_name)),
            daemon=True)
        thread.start()
        thread.join(TIMEOUT)
        if thread.is_alive():
            raise AssertionError("deadlocked")
        return captured
    finally:
        codegen.generate_file = real_gen
        shutil.rmtree(root, ignore_errors=True)


FAILURES = []


def check(label, condition, detail=""):
    print("  %-4s %s%s" % ("ok" if condition else "FAIL", label,
                           "" if condition else "  <- " + str(detail)[:200]))
    if not condition:
        FAILURES.append(label)


def main():
    boilerplate = team.maven_applet_boilerplate()
    check("the boilerplate file loads and is non-trivial",
          len(boilerplate) > 2000, len(boilerplate))
    check("it carries the real protocol field names",
          "prepopulate" in boilerplate and "'unit'" not in boilerplate
          and "event.data.unit" in boilerplate)

    specs = [("applet", "applet.html", 2, []),
             ("api_client", "api_client.py", 2, [])]

    print("\n--api maven: the .html component gets it, the .py one does not")
    captured = run_plan(specs, "maven")
    check("applet.html's prompt carries the reference applet",
          "APPLET_READY" in captured["applet"]
          and "event.data.unit" in captured["applet"], captured["applet"][:200])
    check("the instruction says what to keep and what to replace",
          "buildCurrentOutput" in captured["applet"]
          and "Do not rename the message types" in captured["applet"])
    check("api_client.py's prompt does not carry HTML boilerplate",
          "APPLET_READY" not in captured["api_client"],
          captured["api_client"][:200])

    print("\nno --api, or a different --api: no injection into anything")
    captured_none = run_plan(specs, None)
    check("no api_name -> no boilerplate in the html file either",
          "APPLET_READY" not in captured_none["applet"])

    captured_other = run_plan(specs, "some-other-service")
    check("a different --api name -> no boilerplate",
          "APPLET_READY" not in captured_other["applet"])

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all maven-applet-boilerplate checks passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
