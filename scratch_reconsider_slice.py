"""Validates `openapi.reconsider_slice`, the re-slicing step a failed
planning attempt can trigger before retrying (see `team.build_components`'s
`reconsider_api` callback).

Two shapes, both offline (no node contacted -- `propose_multi` is stubbed
for the STARVED case, and the BLOATED case never calls it at all):

  STARVED: nothing in `chosen` scored as a real match for the request.
  `reconsider_slice` should ask `propose_multi` again and, if it finds a
  real hint this time, return a broadened slice that includes it.

  BLOATED: `chosen` is large and almost entirely filler relative to one
  strong match. `reconsider_slice` should narrow to the real match and
  drop the filler via `avoid`.

  NEITHER: a small, focused, already-correct slice must return None --
  the common case, where a failed decompose had nothing to do with the
  slice at all.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openapi


def op(path, method, op_id, summary, tags):
    return {method: {"operationId": op_id, "summary": summary, "tags": tags,
                     "responses": {"200": {"description": "ok"}}}}


def make_spec():
    paths = {}
    paths.update(op("/api/projects", "post", "create_project",
                    "Create a new project", ["Projects"]))
    paths.update(op("/api/widgets", "get", "list_widgets",
                    "List widgets", ["Widgets"]))
    paths.update(op("/api/gizmos", "get", "list_gizmos",
                    "List gizmos", ["Gizmos"]))
    paths.update(op("/api/gadgets", "get", "list_gadgets",
                    "List gadgets", ["Gadgets"]))
    paths.update(op("/api/doohickeys", "get", "list_doohickeys",
                    "List doohickeys", ["Doohickeys"]))
    paths.update(op("/api/thingamajigs", "get", "list_thingamajigs",
                    "List thingamajigs", ["Thingamajigs"]))
    return {"openapi": "3.1.0", "info": {"title": "T", "version": "1"},
            "paths": paths, "components": {"schemas": {}}}


def entry(spec, method, path):
    for e in openapi.operations(spec):
        if e["method"] == method and e["path"] == path:
            return e
    raise KeyError((method, path))


def test_starved_broadens_after_a_fresh_hint():
    spec = make_spec()
    # A request about creating a project, but `chosen` (as if slice_for
    # already ran and found nothing better) holds only unrelated filler.
    request = "create a brand new project record"
    chosen = [entry(spec, "GET", "/api/widgets")]
    fake_hints = [("POST", "/api/projects")]
    with mock.patch.object(openapi, "propose_multi",
                           return_value=(fake_hints, [])):
        result = openapi.reconsider_slice(
            spec, request, chosen, cfg=None, state=None, limit=6,
            hints=[], avoid=[], budget=None, exclude_tags=())
    assert result is not None, "expected a broadened slice"
    new_text, new_chosen, reason = result
    keys = {(e["method"], e["path"]) for e in new_chosen}
    assert ("POST", "/api/projects") in keys, (
        "broadened slice should include the freshly-hinted real match")
    assert "broadening" in reason


def test_starved_with_no_new_hint_changes_nothing():
    spec = make_spec()
    request = "create a brand new project record"
    chosen = [entry(spec, "GET", "/api/widgets")]
    with mock.patch.object(openapi, "propose_multi", return_value=([], [])):
        result = openapi.reconsider_slice(
            spec, request, chosen, cfg=None, state=None, limit=6,
            hints=[], avoid=[], budget=None, exclude_tags=())
    assert result is None, (
        "no fresh hint was found -- nothing should change")


def test_bloated_narrows_to_the_real_match():
    spec = make_spec()
    request = "create a brand new project record"
    # One real match plus five unrelated filler entries -- as if `limit`
    # was generous and the scorer's weak tail padded out the rest.
    chosen = [entry(spec, "POST", "/api/projects"),
             entry(spec, "GET", "/api/widgets"),
             entry(spec, "GET", "/api/gizmos"),
             entry(spec, "GET", "/api/gadgets"),
             entry(spec, "GET", "/api/doohickeys"),
             entry(spec, "GET", "/api/thingamajigs")]
    result = openapi.reconsider_slice(
        spec, request, chosen, cfg=None, state=None, limit=6,
        hints=[], avoid=[], budget=None, exclude_tags=())
    assert result is not None, "expected a narrowed slice"
    new_text, new_chosen, reason = result
    assert len(new_chosen) < len(chosen)
    keys = {(e["method"], e["path"]) for e in new_chosen}
    assert ("POST", "/api/projects") in keys, (
        "the real match must survive narrowing")
    assert "narrowing" in reason


def test_small_focused_slice_is_left_alone():
    spec = make_spec()
    request = "create a brand new project record"
    chosen = [entry(spec, "POST", "/api/projects")]
    result = openapi.reconsider_slice(
        spec, request, chosen, cfg=None, state=None, limit=6,
        hints=[], avoid=[], budget=None, exclude_tags=())
    assert result is None, (
        "a slice that is already just the real match must not be touched")


def test_sabotage_sabotaged_core_floor_never_fires():
    # If the real/filler distinction were disabled (core_floor forced to
    # 0, so everything counts as "core"), the bloated case above must stop
    # triggering a narrow -- proving the floor computation is what makes
    # the real function fire at all, not something incidental.
    spec = make_spec()
    request = "create a brand new project record"
    chosen = [entry(spec, "POST", "/api/projects"),
             entry(spec, "GET", "/api/widgets"),
             entry(spec, "GET", "/api/gizmos"),
             entry(spec, "GET", "/api/gadgets"),
             entry(spec, "GET", "/api/doohickeys"),
             entry(spec, "GET", "/api/thingamajigs")]

    original_score = openapi.score

    def all_equal_score(e, wanted, weight=None):
        return 1.0  # every entry "matches" identically -- no real/filler line

    with mock.patch.object(openapi, "score", side_effect=all_equal_score):
        sabotaged = openapi.reconsider_slice(
            spec, request, chosen, cfg=None, state=None, limit=6,
            hints=[], avoid=[], budget=None, exclude_tags=())
    assert sabotaged is None, (
        "with no real/filler distinction, nothing should look bloated")
    # And the real function, unsabotaged, on the identical input, does fire.
    real = openapi.reconsider_slice(
        spec, request, chosen, cfg=None, state=None, limit=6,
        hints=[], avoid=[], budget=None, exclude_tags=())
    assert real is not None


def run():
    test_starved_broadens_after_a_fresh_hint()
    test_starved_with_no_new_hint_changes_nothing()
    test_bloated_narrows_to_the_real_match()
    test_small_focused_slice_is_left_alone()
    test_sabotage_sabotaged_core_floor_never_fires()
    print("scratch_reconsider_slice: all checks passed")


if __name__ == "__main__":
    run()
