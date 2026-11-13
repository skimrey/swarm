"""codegen.gate_output_json_wrong_variable.

Measured live, in `05_create_applet/main.py`: the request read, verbatim,
"create an applet; save its id as applet_id in output.json". The applet
API's own create endpoint only takes a slug/name -- content has to be
pushed by creating the applet's first *version* -- so the generated file
reasonably created both an applet and its first version in one function.
That function computed the real applet id (`applet_id = applet_data["id"]`),
printed it, and then returned the version's id instead. The `__main__`
block saved the returned value under the promised key:

    applet_version_id = create_applet_with_content(...)
    output_data = {"applet_id": applet_version_id}

`gate_output_json_key` had nothing to say -- the promised key "applet_id"
is right there in the literal dict. The next step read a real UUID under
`applet_id`, and got a 404 from the real platform three steps later: the
UUID was real, just the wrong kind of resource (a version, not an applet).

Run directly: `python3 scratch_output_json_wrong_variable.py`.
"""
import codegen


LIVE_BUG_SPEC = ("create an applet; save its id as applet_id in this "
                "directory's own output.json")

LIVE_BUG_CODE = '''
import json
import requests

def create_applet_with_content(html_path):
    resp = requests.post("http://x/api/applets", json={"slug": "s"})
    applet_data = resp.json()
    applet_id = applet_data["id"]
    print(f"Successfully created applet. Applet ID: {applet_id}")

    resp2 = requests.post(f"http://x/api/applets/{applet_id}/versions",
                          json={"version": "1.0.0"})
    version_data = resp2.json()
    applet_version_id = version_data["id"]
    return applet_version_id

if __name__ == "__main__":
    applet_version_id = create_applet_with_content("x.html")
    output_data = {"applet_id": applet_version_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)
'''

CORRECT_CODE = '''
import json
import requests

def create_applet_with_content(html_path):
    resp = requests.post("http://x/api/applets", json={"slug": "s"})
    applet_data = resp.json()
    applet_id = applet_data["id"]

    resp2 = requests.post(f"http://x/api/applets/{applet_id}/versions",
                          json={"version": "1.0.0"})
    version_data = resp2.json()
    applet_version_id = version_data["id"]
    print(applet_version_id)
    return applet_id

if __name__ == "__main__":
    applet_id = create_applet_with_content("x.html")
    output_data = {"applet_id": applet_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)
'''

NO_SAME_NAMED_VAR_SPEC = ("create a task; save its id as task_id in this "
                          "directory's own output.json")

NO_SAME_NAMED_VAR_CODE = '''
import json
import requests

def create_task():
    resp = requests.post("http://x/api/tasks", json={"name": "t"})
    data = resp.json()
    return data["id"]

if __name__ == "__main__":
    new_id = create_task()
    output_data = {"task_id": new_id}
    with open("output.json", "w") as f:
        json.dump(output_data, f)
'''

WRITE_MODE_VARIANT_CODE = '''
import json
import requests

def create_applet_with_content(html_path):
    resp = requests.post("http://x/api/applets", json={"slug": "s"})
    applet_data = resp.json()
    applet_id = applet_data["id"]

    resp2 = requests.post(f"http://x/api/applets/{applet_id}/versions",
                          json={"version": "1.0.0"})
    version_data = resp2.json()
    applet_version_id = version_data["id"]
    return applet_version_id

if __name__ == "__main__":
    applet_version_id = create_applet_with_content("x.html")
    with open("output.json", "w") as f:
        f.write(json.dumps({"applet_id": applet_version_id}))
'''


def test_live_bug_is_caught():
    msg = codegen.gate_output_json_wrong_variable(LIVE_BUG_SPEC, LIVE_BUG_CODE)
    assert msg is not None, "the live wrong-resource-id-under-the-right-key bug was not caught"
    assert "applet_id" in msg, msg
    assert "applet_version_id" in msg, msg
    print("  the live applet_id/applet_version_id mix-up is caught")


def test_write_mode_variant_is_also_caught():
    msg = codegen.gate_output_json_wrong_variable(LIVE_BUG_SPEC, WRITE_MODE_VARIANT_CODE)
    assert msg is not None, "the f.write(json.dumps(...)) shape of the same bug was not caught"
    assert "applet_version_id" in msg, msg
    print("  the same bug via f.write(json.dumps(...)) instead of json.dump(...) is also caught")


def test_correct_code_is_left_alone():
    assert codegen.gate_output_json_wrong_variable(LIVE_BUG_SPEC, CORRECT_CODE) is None
    print("  code that saves the identically-named variable under its promised key passes clean")


def test_no_same_named_variable_means_nothing_to_compare():
    # `new_id` never happens to be spelled `task_id` anywhere in the file,
    # so there is nothing to compare it against -- this must not be
    # flagged on a guess about what `new_id` "should" have been named.
    assert codegen.gate_output_json_wrong_variable(
        NO_SAME_NAMED_VAR_SPEC, NO_SAME_NAMED_VAR_CODE) is None
    print("  a promised key with no identically-named variable anywhere "
         "in the file is left alone, not flagged on a guess")


def test_no_promise_means_nothing_to_check():
    assert codegen.gate_output_json_wrong_variable("print a greeting", LIVE_BUG_CODE) is None
    print("  a spec that promises no key at all is left alone")


def test_sabotage_no_existing_gate_would_have_caught_this():
    # gate_output_json_key only compares key *names*; the promised key
    # "applet_id" is right there in the literal dict, so it has nothing
    # to object to -- confirming this is genuinely new coverage.
    assert codegen.gate_output_json_key(LIVE_BUG_SPEC, LIVE_BUG_CODE) is None
    assert codegen.gate_output_json_shape(LIVE_BUG_CODE) is None
    print("  sabotage: confirmed gate_output_json_key/gate_output_json_shape "
         "both pass the live file clean -- the key name and the shape were "
         "both correct, which is exactly why neither one caught this")


def main():
    test_live_bug_is_caught()
    test_write_mode_variant_is_also_caught()
    test_correct_code_is_left_alone()
    test_no_same_named_variable_means_nothing_to_compare()
    test_no_promise_means_nothing_to_check()
    test_sabotage_no_existing_gate_would_have_caught_this()
    print("OK")


if __name__ == "__main__":
    main()
