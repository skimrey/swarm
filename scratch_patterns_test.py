"""The detectors must find the real thing and stay silent on correct code."""
import os
import shutil
import tempfile

import patterns

CASES = {
    # The bug both audit models missed by reading.
    "del_while_iterating": ("""
def evict(entries, count):
    dropped = 0
    for key in entries:
        if dropped >= count:
            break
        del entries[key]
        dropped += 1
""", True),

    "pop_while_iterating": ("""
def drain(items):
    for x in items:
        items.pop()
""", True),

    "append_while_iterating": ("""
def grow(xs):
    for x in xs:
        xs.append(x)
""", True),

    "mutate_via_keys_view": ("""
def f(d):
    for k in d.keys():
        del d[k]
""", True),

    # The standard fixes. These must NOT fire, or the detector punishes the
    # correct version of the same code.
    "iterate_over_list_copy": ("""
def f(d):
    for k in list(d):
        del d[k]
""", False),

    "iterate_over_copy_method": ("""
def f(d):
    for k in d.copy():
        del d[k]
""", False),

    "collect_then_delete": ("""
def f(d):
    doomed = [k for k in d if k]
    for k in doomed:
        del d[k]
""", False),

    "mutate_a_different_container": ("""
def f(src, dst):
    for k in src:
        dst.append(k)
""", False),

    "rebind_element_is_fine": ("""
def f(d):
    for k in d:
        d[k] = 0
""", False),

    "mutable_default": ("""
def f(acc=[]):
    return acc
""", True),

    "none_default_is_fine": ("""
def f(acc=None):
    return acc or []
""", False),

    "is_literal": ("""
def f(x):
    return x is 256
""", True),

    "is_none_is_fine": ("""
def f(x):
    return x is None
""", False),

    "is_true_is_fine": ("""
def f(x):
    return x is True
""", False),
}


def main():
    root = tempfile.mkdtemp(prefix="patterns-")
    failures = []
    try:
        for name, (source, should_fire) in CASES.items():
            path = os.path.join(root, name + ".py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            found = patterns.scan(path, root)
            fired = bool(found)
            status = "ok " if fired == should_fire else "BAD"
            if fired != should_fire:
                failures.append(name)
            print("  %s %-28s %s" % (
                status, name,
                (found[0]["claim"][:60] if found else "(silent)")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    assert not failures, "wrong on: %s" % ", ".join(failures)
    print("\n%d/%d cases correct" % (len(CASES), len(CASES)))

    # And against the real probe, where ground truth is known by execution.
    if os.path.exists("probe/cache.py"):
        found = patterns.scan("probe/cache.py", ".")
        claims = [f["claim"] for f in found]
        assert any("iterated" in c for c in claims), (
            "missed the probe's iteration bug: %r" % claims)
        print("probe/cache.py: caught the bug both audit models missed")
        for f in found:
            print("  %s:%s  %s" % (f["file"], f["lines"], f["claim"][:70]))
    print("OK")


if __name__ == "__main__":
    main()
