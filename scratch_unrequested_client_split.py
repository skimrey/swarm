"""Validates `intent.unrequested_client_split`, the mechanical backstop for
the live failure: a request reading only "create an applet; save its id"
-- one action, zero mention of a project or a task -- came back as four
components: `api_client.py`, `applet_creator.py`, `project_api_client.py`,
`task_api_client.py`. The last two are named after, and call endpoints
for, a resource the request never asked about -- the planner read the
tag headings in the endpoint index ("## Applets", "## Projects", "##
Tasks") as a menu of clients to build rather than a place to look up an
id a real call needs.

`DECOMPOSE_PROMPT` now tells the planner this outright. This is the
mechanical check for when it does anyway, printed before any generation
time is spent building capability nobody requested.
"""
import intent


def _component(name, path, spec="stub", deps=None):
    return intent.Component(name, path, 2, deps or [], spec)


def test_flags_the_exact_live_shape():
    request = ("create an applet; save applet_id to output.json as "
               '{"applet_id": ...}')
    comps = [
        _component("api_client", "api_client.py"),
        _component("applet_creator", "applet_creator.py"),
        _component("project_api_client", "project_api_client.py"),
        _component("task_api_client", "task_api_client.py"),
    ]
    flagged = intent.unrequested_client_split(comps, request)
    names = {n for n, _ in flagged}
    assert names == {"project_api_client", "task_api_client"}, names
    # api_client strips to no domain at all (never flagged), and
    # applet_creator's domain word ("applet") IS in the request (never
    # flagged either) -- only the two genuinely unrequested ones survive.


def test_single_client_component_is_never_flagged():
    # Nothing to compare against -- one client file, however named, is
    # never itself evidence of an unnecessary split.
    request = "create an applet"
    comps = [_component("project_client", "project_client.py")]
    assert intent.unrequested_client_split(comps, request) == []


def test_a_legitimately_multi_resource_request_is_not_flagged():
    request = ("build a client that can create projects and assign "
               "tasks to them")
    comps = [
        _component("project_api_client", "project_api_client.py"),
        _component("task_api_client", "task_api_client.py"),
    ]
    assert intent.unrequested_client_split(comps, request) == []


def test_generic_api_client_name_claims_no_domain():
    request = "create an applet"
    comps = [
        _component("api_client", "api_client.py"),
        _component("service_client", "service_client.py"),
    ]
    # "api_client" strips to nothing; "service_client" strips to
    # "service", which is not in the request -- but with only ONE
    # domain-claiming file in the plan (api_client claims none), there
    # is nothing to compare it against, so nothing is flagged.
    assert intent.unrequested_client_split(comps, request) == []


def test_empty_request_is_never_flagged():
    comps = [
        _component("project_api_client", "project_api_client.py"),
        _component("task_api_client", "task_api_client.py"),
    ]
    assert intent.unrequested_client_split(comps, "") == []


def test_domain_word_present_via_plural_or_case_is_not_flagged():
    request = "Create a new PROJECT and link it to an applet"
    comps = [
        _component("project_api_client", "project_api_client.py"),
        _component("applet_client", "applet_client.py"),
    ]
    assert intent.unrequested_client_split(comps, request) == []


def test_sabotage_ignoring_the_plan_size_gate_would_overfire():
    # Prove the >= 2 client-shaped gate is load-bearing: without it, a
    # single, correctly-scoped client component named after a resource
    # the request happens to phrase differently would be flagged for no
    # reason -- exactly the false positive the gate exists to prevent.
    request = "write a script that does something with widgets"
    words = intent._client_domain_words("project_api_client.py")
    req_words = {w for w in __import__("re").findall(
        r"[a-z0-9]+", request.lower()) if len(w) > 2}
    assert not (words & req_words)  # would be "flagged" if checked alone
    comps = [_component("project_api_client", "project_api_client.py")]
    assert intent.unrequested_client_split(comps, request) == []


if __name__ == "__main__":
    test_flags_the_exact_live_shape()
    test_single_client_component_is_never_flagged()
    test_a_legitimately_multi_resource_request_is_not_flagged()
    test_generic_api_client_name_claims_no_domain()
    test_empty_request_is_never_flagged()
    test_domain_word_present_via_plural_or_case_is_not_flagged()
    test_sabotage_ignoring_the_plan_size_gate_would_overfire()
    print("ok")
