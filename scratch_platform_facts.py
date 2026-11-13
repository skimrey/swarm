"""`gate_platform_facts` catches a hallucinated Maven UI host or an invented
path on the real one, mechanically rather than by asking harder in prose.

Measured live, with `context/platform-maven-urls.md` -- stating the exact
correct host and URL shape verbatim -- already sitting in the writer's own
prompt: a generated `get_ui_url` still produced
`https://dztfdhkmj6wqi.cloudfront.net/projects/{id}/ui`, a fabricated host
with an invented path suffix on top. Prose was not enough, so this is
decided from the source text instead.

Also catches the shape a source scan cannot: a helper that correctly
returns `HOST/projects/{id}`, concatenated with a caller that appends
`/projects/{id}` onto it a second time. Neither literal is wrong in
isolation, so this only becomes visible in what the program actually
prints -- which is why `gate_smoke` runs this same function again against
captured stdout, not just `generate_file` against the source.

The real host has changed more than once during this project (the UI is
hosted on CloudFront, which rotates). Every test below reads
`codegen.MAVEN_UI_HOST` rather than hardcoding a literal, so a future host
change is a one-line edit in `codegen.py` and nothing here goes stale
silently.
"""
import codegen

HOST = codegen.MAVEN_UI_HOST


def test_the_real_host_and_project_shape_pass_clean():
    code = (
        'def get_ui_url(base_url, project_id):\n'
        '    return f"https://%s/projects/{project_id}"\n' % HOST
    )
    assert codegen.gate_platform_facts(code) is None


def test_the_real_host_and_task_shape_pass_clean():
    code = (
        'def get_task_url(base_url, task_id):\n'
        '    return f"https://%s/tasks/{task_id}"\n' % HOST
    )
    assert codegen.gate_platform_facts(code) is None


def test_a_fabricated_cloudfront_host_is_caught():
    fake = "dztfdhkmj6wqi.cloudfront.net"
    assert fake != HOST
    code = (
        'def get_ui_url(base_url, project_id):\n'
        '    return f"https://%s/projects/{project_id}"\n' % fake
    )
    failure = codegen.gate_platform_facts(code)
    assert failure is not None
    assert fake in failure
    assert HOST in failure


def test_the_real_host_with_an_invented_path_suffix_is_caught():
    code = (
        'def get_ui_url(base_url, project_id):\n'
        '    return f"https://%s/projects/{project_id}/ui"\n' % HOST
    )
    failure = codegen.gate_platform_facts(code)
    assert failure is not None
    assert "/ui" in failure


def test_the_real_host_with_an_invented_collection_is_caught():
    # Only /projects/ and /tasks/ are confirmed shapes -- an applet or unit
    # link built by analogy is exactly as unverified as any other guess.
    code = (
        'def get_applet_url(base_url, applet_id):\n'
        '    return f"https://%s/applets/{applet_id}"\n' % HOST
    )
    failure = codegen.gate_platform_facts(code)
    assert failure is not None


def test_code_that_never_mentions_the_platform_is_untouched():
    code = "def add(a, b):\n    return a + b\n"
    assert codegen.gate_platform_facts(code) is None


def test_reading_the_host_from_an_env_var_is_untouched():
    # A correct alternative design -- `MAVEN_UI_URL` from the environment,
    # per context/platform-maven-urls.md -- never puts the literal host in
    # the source at all, so there is nothing here for a text scan to catch,
    # and nothing here should invent a reason to flag it.
    code = (
        'import os\n'
        'def get_ui_url(project_id):\n'
        '    host = os.environ.get("MAVEN_UI_URL", DEFAULT_HOST)\n'
        '    return f"{host}/projects/{project_id}"\n'
    )
    assert codegen.gate_platform_facts(code) is None


def test_a_plain_variable_tail_is_still_the_right_shape():
    # Not every caller interpolates an f-string placeholder -- string
    # concatenation with a bare variable name is just as correct a shape.
    code = (
        'url = "https://%s/projects/" + project_id\n' % HOST
    )
    assert codegen.gate_platform_facts(code) is None


def test_a_plain_variable_tail_is_still_the_right_shape_for_tasks():
    code = (
        'url = "https://%s/tasks/" + task_id\n' % HOST
    )
    assert codegen.gate_platform_facts(code) is None


def test_a_doubled_suffix_is_caught_even_in_plain_runtime_output():
    # Seen live: a helper correctly returns the full link, and a separate
    # print statement appends /projects/{id} onto it a second time. Neither
    # string literal in the source is individually wrong -- this can only
    # be seen once the pieces are concatenated, which is exactly what a
    # program's actual printed output has already done by the time
    # `gate_smoke` captures it and runs this same check against it.
    out = ("Project Link: https://%s/projects/"
          "40539b72-11c5-475b-8125-c16e164d6b84/projects/"
          "40539b72-11c5-475b-8125-c16e164d6b84\n" % HOST)
    failure = codegen.gate_platform_facts(out)
    assert failure is not None
    assert "nothing else builds one on top" in failure


def test_a_correct_link_followed_by_ordinary_prose_is_not_a_false_positive():
    # The exact shape the operator asked main.py to print: the link, then
    # more fields on the same line, separated by punctuation rather than
    # whitespace immediately after the id. A checker that stops only at
    # whitespace would read the trailing period as part of the id and
    # reject a perfectly correct program.
    out = ("Project Link: https://%s/projects/"
          "40539b72-11c5-475b-8125-c16e164d6b84. Task Link: "
          "https://%s/tasks/9c1e5c2a-0000-0000-0000-000000000000. "
          "Project ID: 40539b72-11c5-475b-8125-c16e164d6b84\n"
          % (HOST, HOST))
    assert codegen.gate_platform_facts(out) is None

    # And the comma/pipe/semicolon-separated variants some models prefer.
    for sep in (",", " |", ";"):
        out2 = ("Link: https://%s/projects/"
               "abc123%s more stuff after it\n" % (HOST, sep))
        assert codegen.gate_platform_facts(out2) is None, sep


def test_a_bare_host_constant_definition_is_untouched():
    code = 'MAVEN_UI_HOST = "%s"\n' % HOST
    assert codegen.gate_platform_facts(code) is None


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok   %s" % name)
            except AssertionError as exc:
                print("  FAIL %s -- %s" % (name, exc))
                failures.append(name)
    if failures:
        raise SystemExit("%d failure(s): %s" % (len(failures), failures))
    print("all platform-facts gate checks passed")
