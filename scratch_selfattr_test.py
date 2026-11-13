"""self.X read but never assigned: real, and never fired on correct code."""
import os
import shutil
import tempfile

import crossref


def findings_for(**files):
    root = tempfile.mkdtemp(prefix="selfattr-")
    try:
        for name, src in files.items():
            with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
                fh.write(src)
        return crossref.check_self_attributes(
            crossref.Index(list(files), root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


REAL = '''
class GameState:
    def __init__(self, room):
        self.current_room = room
    def move_to(self, name):
        if name in self.current_room.exits:
            self.current_room = self.current_or_room.exits[name]
'''

CLEAN = '''
class GameState:
    def __init__(self, room):
        self.current_room = room
        self.inventory = []
    def move_to(self, name):
        self.current_room = self.current_room.exits[name]
        return self.describe()
    def describe(self):
        return self.current_room.name
'''

# Every way of putting a name on an instance without a plain assignment.
UNUSUAL = '''
class Odd:
    LIMIT = 10
    def __init__(self, rows, f):
        self.a, self.b = 1, 2
        for self.row in rows:
            pass
        with f as self.handle:
            pass
        self.count = 0
        self.count += 1
    def use(self):
        return (self.a, self.b, self.row, self.handle, self.count, self.LIMIT)
'''

# Surfaces that cannot be read off the file must be left alone.
INHERITED = '''
import threading
class Sub(threading.Thread):
    def go(self):
        return self.daemon
'''
GETATTR = '''
class Proxy:
    def __getattr__(self, name):
        return 42
    def go(self):
        return self.anything
'''
SETATTR = '''
class Dyn:
    def __init__(self, cfg):
        for k, v in cfg.items():
            setattr(self, k, v)
    def go(self):
        return self.whatever
'''


def main() -> None:
    got = findings_for(**{"engine.py": REAL})
    assert len(got) == 1, got
    assert "current_or_room" in got[0].message
    print("caught:", got[0].text().replace("\n", " "))

    for label, src in (("clean", CLEAN), ("unusual bindings", UNUSUAL),
                       ("inherited", INHERITED), ("__getattr__", GETATTR),
                       ("setattr", SETATTR)):
        got = findings_for(**{"m.py": src})
        assert not got, "%s produced a false finding: %s" % (label, got)
        print("%-18s clean" % label)
    print("OK")


if __name__ == "__main__":
    main()
