"""A planner reply that bolds one of its field labels corrupts the value,
not just the key -- and a repeated COMPONENT block silently costs a plan
its single-component `main.py` default.

Measured live: a plan for what should have been one file ("upload units to
a task") came back with two COMPONENT blocks for the same job, one of
which had its `FILE:` line bolded (`**FILE:** unit_uploader.py` in effect).
`_fields()` already strips markdown noise off the *front* of a whole line
(`line.lstrip("*-# ")`, so a bolded key like "**FILE:**" still matches) but
never touched what `_FIELD`'s own regex captures as the *value* -- so the
leftover "**" right after the colon rode straight through, producing a
component whose path was the literal string "** unit_uploader.py".
Verbatim from the live run:

    [** unit_uploader  ] ** unit_uploader.py on e4b-linux/...
    [unit_uploader     ] unit_uploader.py on mac/...

Two things are checked here:
  1. `_fields`/`parse_components` strip a leading/trailing run of `*` off
     every captured value, so a bolded FILE/COMPONENT line normalizes to
     the same clean string the un-bolded sibling already produced.
  2. Once both blocks normalize to the identical name+path+spec, that is
     the model repeating itself, not two real files -- `parse_components`
     collapses it to one component rather than keeping both (which would
     have still cost the request its `main.py` entry-point default, since
     that only fires for exactly one component).
"""
import sys

import intent


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("ok:", msg)


# The exact live shape: one block plain, one with its FILE (and COMPONENT)
# line bolded markdown-style, both describing the identical job.
PLAN_TEXT = """\
CONTRACT: NONE

COMPONENT: unit_uploader
FILE: unit_uploader.py
FLOOR: 4
DEPS: NONE
SPEC: upload three units to the task whose task_id is read from \
../02_create_task/output.json

**COMPONENT:** unit_uploader
**FILE:** unit_uploader.py
FLOOR: 4
DEPS: NONE
SPEC: upload three units to the task whose task_id is read from \
../02_create_task/output.json
"""

components = intent.parse_components(PLAN_TEXT)

check(len(components) == 1,
     "two verbatim-identical COMPONENT blocks (one markdown-bolded) "
     "collapse to a single component, not two (got %d: %r)"
     % (len(components), [(c.name, c.path) for c in components]))

only = components[0]
check(only.path == "unit_uploader.py",
     "the surviving component's path has no markdown noise in it "
     "(got %r)" % only.path)
check(only.name == "unit_uploader",
     "the surviving component's name has no markdown noise in it "
     "(got %r)" % only.name)
check("*" not in only.path and "*" not in only.name,
     "no asterisk survives anywhere in the parsed name or path")


# A bolded line that is NOT a duplicate of anything else must still have
# its own noise stripped, rather than only being fixed by the collapse
# above -- proves the per-field stripping is doing real work on its own,
# not merely riding on the dedup.
SOLO_BOLDED = """\
CONTRACT: NONE

**COMPONENT:** main
**FILE:** main.py
FLOOR: 2
DEPS: NONE
SPEC: a single bolded component with nothing to collapse against
"""
solo = intent.parse_components(SOLO_BOLDED)
check(len(solo) == 1, "a lone bolded block still parses to one component")
check(solo[0].path == "main.py",
     "a lone bolded FILE line strips clean to 'main.py' (got %r)"
     % solo[0].path)
check(solo[0].name == "main",
     "a lone bolded COMPONENT line strips clean to 'main' (got %r)"
     % solo[0].name)


# Two blocks that share a name/path but genuinely differ in SPEC must NOT
# be silently collapsed -- that would throw away real, different work.
# The existing rename-on-duplicate-name logic still owns this case.
REAL_DUPES = """\
CONTRACT: NONE

COMPONENT: main
FILE: main.py
FLOOR: 2
DEPS: NONE
SPEC: the real entry point

COMPONENT: main
FILE: cli.py
FLOOR: 2
DEPS: NONE
SPEC: a completely different CLI wrapper
"""
different = intent.parse_components(REAL_DUPES)
check(len(different) == 2,
     "two blocks with different SPECs are never collapsed, only exact "
     "repeats are (got %d)" % len(different))


def sabotage():
    """Confirms the fixture is real: with the old (unstripped) `_fields`
    behaviour, the bolded FILE/COMPONENT line reproduces the exact
    mangled path from the live run, and the two blocks are treated as
    different because their raw strings differ by the markdown noise.
    """
    def old_fields(text):
        out = []
        for line in (text or "").splitlines():
            m = intent._FIELD.match(line.lstrip("*-# "))
            if m:
                out.append((m.group(1).lower(), m.group(2).strip()))
        return out

    real_fields = intent._fields
    intent._fields = old_fields
    try:
        old_components = intent.parse_components(PLAN_TEXT)
    finally:
        intent._fields = real_fields

    paths = [c.path for c in old_components]
    check(len(old_components) == 2,
         "sabotage: without the fix, the two blocks are NOT collapsed -- "
         "the markdown noise makes their raw values differ, reproducing "
         "the live duplicate (got %d: %r)" % (len(old_components), paths))
    check(any(p.strip().startswith("*") for p in paths),
         "sabotage: without the fix, one path still carries the literal "
         "markdown noise, reproducing '** unit_uploader.py' exactly "
         "(got %r)" % paths)


sabotage()
print("all checks passed")
