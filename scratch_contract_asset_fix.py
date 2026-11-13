#!/usr/bin/env python3
"""A stale CONTRACT clause naming a .py module must not survive to a writer.

Measured live: the plan built `applet.html` for "the applet should be html",
which is the right call -- but the broadcast CONTRACT line still read
`applet.py owns generate_applet_html() -> str`, unchanged from a draft that
predated that choice. `dependency_context` correctly told `main.py` to read
`applet.html` from disk; the CONTRACT broadcast, sent to every writer with
"use these exact names" framing, said the opposite about the same file under
a name that was never built. `main.py` followed the CONTRACT: it hit an
ImportError against a module that could not exist, "fixed" it with a
hardcoded fallback, and printed "Warning: Using mocked generate_applet_html
function." while passing every gate -- the real, reviewed `applet.html` was
never read.

`intent.reconcile_contract_assets` rewrites the stale clause before any
writer's prompt is built. This is offline: no node, no generation.
"""
import sys

import intent

FAILURES = []


def check(label, condition, detail=""):
    print("  %-4s %s%s" % ("ok" if condition else "FAIL", label,
                           "" if condition else "  <- " + str(detail)[:300]))
    if not condition:
        FAILURES.append(label)


# The exact shape from the live run.
LIVE = ("CONTRACT: api_client.py owns MavenClient(api_base_url: str, "
        "api_key: str) with create_applet(slug: str, name: str) -> "
        "AppletResponse; applet.py owns generate_applet_html() -> str; "
        "survey_data.json owns SURVEY_UNITS, a list of 3 dicts\n\n"
        "COMPONENT: applet\nFILE: applet.html\nFLOOR: 2\nDEPS: NONE\n"
        "SPEC: the survey page\n\n"
        "COMPONENT: survey_data\nFILE: survey_data.json\nFLOOR: 2\n"
        "DEPS: NONE\nSPEC: the unit payloads\n\n"
        "COMPONENT: api_client\nFILE: api_client.py\nFLOOR: 4\nDEPS: NONE\n"
        "SPEC: the client\n\n"
        "COMPONENT: main\nFILE: main.py\nFLOOR: 2\n"
        "DEPS: api_client, applet, survey_data\nSPEC: entry point\n")


def main():
    print("the stale applet.py clause is corrected")
    comps = intent.parse_components(LIVE)
    contract = comps[0].contract
    fixed = intent.reconcile_contract_assets(contract, comps)
    check("the stale .py clause is gone",
          "applet.py owns generate_applet_html" not in fixed, fixed)
    check("the real file is named instead",
          "applet.html" in fixed and "there is no applet.py" in fixed, fixed)
    check("the invented signature is disclaimed",
          "generate_applet_html() -> str" in fixed
          and "does not exist" in fixed, fixed)
    check("the untouched clause survives verbatim",
          "api_client.py owns MavenClient" in fixed, fixed)
    check("survey_data.json's own clause is untouched (it was built)",
          "survey_data.json owns SURVEY_UNITS" in fixed, fixed)

    print("\nno false positives on a correct plan")
    correct = ("CONTRACT: shapes.py owns Shape(x, y) with draw(surface)\n\n"
               "COMPONENT: s\nFILE: shapes.py\nFLOOR: 2\nDEPS: NONE\n"
               "SPEC: x\n\nCOMPONENT: m\nFILE: main.py\nFLOOR: 2\nDEPS: s\n"
               "SPEC: y\n")
    ccomps = intent.parse_components(correct)
    check("a .py clause matching a real .py component is untouched",
          intent.reconcile_contract_assets(ccomps[0].contract, ccomps)
          == ccomps[0].contract)

    print("\nno false positives when nothing shares the stem")
    lonely = ("CONTRACT: helpers.py owns util(x)\n\nCOMPONENT: m\n"
              "FILE: main.py\nFLOOR: 2\nDEPS: NONE\nSPEC: y\n")
    lcomps = intent.parse_components(lonely)
    check("an orphan with no same-stem sibling is left for the operator",
          intent.reconcile_contract_assets(lcomps[0].contract, lcomps)
          == lcomps[0].contract)

    print("\nCONTRACT: NONE and an empty contract are both quiet")
    check("NONE contract",
          intent.reconcile_contract_assets("NONE", []) == "NONE")
    check("empty contract",
          intent.reconcile_contract_assets("", []) == "")

    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        return 1
    print("all contract-asset checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
