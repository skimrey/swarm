"""Does the smoke gate catch runtime breakage without rejecting good code?"""
import os
import shutil
import tempfile

import codegen

# Crashes only when run: the dict is mutated while being iterated. This is the
# bug both audit models failed to find by reading the source.
CRASHER = '''
def evict(entries, count):
    dropped = 0
    for key in entries:
        if dropped >= count:
            break
        del entries[key]
        dropped += 1
    return dropped


def main():
    evict({"a": 1, "b": 2, "c": 3}, 2)


if __name__ == "__main__":
    main()
'''

# Imports fine, runs fine, exits non-zero for want of arguments. Must pass:
# a tool that demands arguments is not broken.
PICKY = '''
import argparse
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path")
    args = p.parse_args()
    print(args.path)


if __name__ == "__main__":
    main()
'''

CLEAN = '''
def add(a, b):
    return a + b


def main():
    print(add(1, 2))


if __name__ == "__main__":
    main()
'''

# No entry point at all: the gate must decline rather than invent an opinion.
LIBRARY = '''
def helper(x):
    return x * 2
'''

# Writes into the working directory. Must not land in the project.
LITTERER = '''
def main():
    with open("litter.txt", "w") as fh:
        fh.write("x")


if __name__ == "__main__":
    main()
'''


def run(name, source):
    root = tempfile.mkdtemp(prefix="smokecase-")
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    try:
        return codegen.gate_smoke(path, root), root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    err, _ = run("crasher.py", CRASHER)
    assert err and "raised" in err, "runtime crash slipped through: %r" % err
    print("mutation-during-iteration: caught by running it")

    err, _ = run("picky.py", PICKY)
    assert err is None, "a tool needing arguments was rejected: %r" % err
    print("argument-requiring CLI: correctly accepted")

    err, _ = run("clean.py", CLEAN)
    assert err is None, "working code was rejected: %r" % err
    print("clean script: accepted")

    err, _ = run("library.py", LIBRARY)
    assert err is None, "module with no entry point was judged: %r" % err
    print("no entry point: gate declines, as intended")

    before = set(os.listdir("."))
    err, _ = run("litterer.py", LITTERER)
    after = set(os.listdir("."))
    assert err is None, "a writing tool was rejected: %r" % err
    assert before == after, "smoke run wrote into the project: %r" % (
        after - before)
    print("file-writing tool: ran in scratch, project untouched")

    # The gate must not be fooled by the word appearing in output.
    err, _ = run("mentions.py",
                 'print("Traceback")\nif __name__ == "__main__":\n    pass\n')
    assert err is None, "a program printing the word was rejected: %r" % err
    print("prints the word Traceback: correctly accepted")

    # ...but a real traceback still fails, even amid other output.
    err, _ = run("late.py",
                 'print("working")\n\n\ndef main():\n    raise ValueError("x")'
                 '\n\n\nif __name__ == "__main__":\n    main()\n')
    assert err and "raised" in err, "a real traceback was missed: %r" % err
    print("real traceback after output: caught")

    # A file that stops for want of a credential, then fails further in once
    # the placeholder supplies one. Both runs crash, so the file is at fault
    # -- but only the second failure is a defect. Measured on a live run: the
    # filled run drove the whole API chain and crashed past it, and the gate
    # reported `AUTH_TOKEN environment variable is not set` from the plain
    # acquittal run, which is the file working as designed.
    err, _ = run("credentialed.py", '''
import os


def main():
    token = os.environ["AUTH_TOKEN"]
    raise ValueError("real defect, reached only with %s" % token)


if __name__ == "__main__":
    main()
''')
    assert err, "a crashing file passed the gate"
    assert "real defect" in err, (
        "the gate reported the acquittal run, not the run it gated: %r" % err)
    assert "KeyError" not in err, (
        "the missing credential was reported as the defect: %r" % err)
    print("credential placeholder: the defect past it is what gets reported")

    # A crash is not always evidence about the file. `local_urls` sends
    # every non-loopback URL to port 1 on purpose when there is no mock, so
    # a refusal naming exactly that address means "no --api was given",
    # not "this code is broken" -- and it has to be labelled differently
    # from the ordinary crash above, or the repair loop spends its whole
    # budget rewriting a file that could not have passed no matter what it
    # said. Measured live: three identical attempts, one file, no defect.
    err, _ = run("nomock.py", '''
def main():
    raise ConnectionError(
        "HTTPConnectionPool(host=\\'127.0.0.1\\', port=1): Max retries "
        "exceeded with url: /api/tasks (Caused by NewConnectionError("
        "\\'Failed to establish a new connection: [Errno 61] Connection "
        "refused\\'))")


if __name__ == "__main__":
    main()
''')
    assert err and codegen.NO_MOCK_MARKER in err, (
        "a refusal to the dead-port sink was not recognised: %r" % err)
    print("dead-port refusal: labelled as 'no mock configured', not a "
          "code defect")

    # Only the exact sink earns that pass. A refusal naming a real-looking
    # host must still read as an ordinary crash, or a genuine connectivity
    # bug -- a typo'd hostname, a service that is actually down -- would be
    # waved through as harmless right alongside it.
    err, _ = run("realhost.py", '''
def main():
    raise ConnectionError(
        "HTTPConnectionPool(host=\\'api.example.com\\', port=443): Max "
        "retries exceeded (Caused by NewConnectionError("
        "\\'Connection refused\\'))")


if __name__ == "__main__":
    main()
''')
    assert err and codegen.NO_MOCK_MARKER not in err, (
        "a refusal to a real host was mislabelled as no-mock: %r" % err)
    print("real-host refusal: still reported as an ordinary crash")

    # A program that reads a file its siblings wrote must find it. The
    # scratch directory stops writes reaching the project; running there
    # with none of the project's files present means a program whose first
    # act is `open("applet.html")` exits on OSError having done nothing,
    # and the gate passes on a run that executed no part of the work.
    _seed_files_are_visible()
    _the_project_is_still_not_written_to()
    print("OK")


def _seed_files_are_visible():
    root = tempfile.mkdtemp(prefix="seeded-")
    try:
        with open(os.path.join(root, "template.html"), "w") as fh:
            fh.write("<p>hi</p>")
        target = os.path.join(root, "reader.py")
        with open(target, "w") as fh:
            fh.write('''
def main():
    with open("template.html") as fh:
        assert "hi" in fh.read()
    print("read it")


if __name__ == "__main__":
    main()
''')
        err = codegen.gate_smoke(target, root)
        assert err is None, "a program reading a sibling file failed: %r" % err

        # And the reverse: the gate must still see a genuine crash that
        # happens after the file is read, which is the case the seeding
        # exists to make reachable at all.
        with open(target, "w") as fh:
            fh.write('''
def main():
    with open("template.html") as fh:
        fh.read()
    raise ValueError("past the read")


if __name__ == "__main__":
    main()
''')
        err = codegen.gate_smoke(target, root)
        assert err and "past the read" in err, (
            "the crash after the read was missed: %r" % err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("sibling files: visible to the smoke run, crash past them caught")


def _the_project_is_still_not_written_to():
    root = tempfile.mkdtemp(prefix="seeded-")
    try:
        target = os.path.join(root, "writer.py")
        with open(target, "w") as fh:
            fh.write('''
def main():
    with open("output.txt", "w") as fh:
        fh.write("x")


if __name__ == "__main__":
    main()
''')
        before = set(os.listdir(root))
        err = codegen.gate_smoke(target, root)
        assert err is None, err
        assert set(os.listdir(root)) == before, (
            "seeding let the smoke run write into the project: %r"
            % (set(os.listdir(root)) - before))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("seeding copies in, and nothing copies back out")


if __name__ == "__main__":
    main()
