"""An interactive program must be typed at, not handed an empty stdin."""
import os
import shutil
import tempfile

import codegen

GAME = '''
class State:
    def __init__(self):
        self.room = "hall"
    def move(self, d):
        return self.{attr}[d] if d in {{"north": 1}} else None

def main():
    s = State()
    while True:
        line = input("> ").strip()
        verb = line.split()[0] if line.split() else ""
        noun = line.split()[1] if len(line.split()) > 1 else ""
        if verb in ("quit", "exit"):
            print("bye"); break
        elif verb == "look":
            print("You are in the", s.room)
        elif verb == "go":
            print(s.move(noun))
        elif verb == "help":
            print("look, go [north], quit")
        else:
            print("I don't understand that.")

if __name__ == "__main__":
    main()
'''

SWALLOWING = GAME.replace("        line = input",
                          "      try:\n        line = input").replace(
    'if __name__ == "__main__":',
    '      except Exception as e:\n        print("An error occurred:", e); break\n\n'
    'if __name__ == "__main__":')


def write(src):
    root = tempfile.mkdtemp(prefix="exercise-")
    path = os.path.join(root, "main.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    return root, path


def main() -> None:
    words = codegen.vocabulary(GAME.format(attr="room"))
    assert "look" in words and "go" in words and "quit" in words, words
    print("vocabulary read off the source:", words)

    script = codegen.exercise_script(GAME.format(attr="room"))
    assert script.strip().splitlines()[-1] in ("quit", "exit"), script
    print("script ends with a way out:", script.split()[:6], "...")

    # A correct program must pass, and quickly.
    root, path = write(GAME.format(attr="room"))
    try:
        assert codegen.gate_smoke(path, root) is None, "failed correct code"
        print("correct interactive program: PASS")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # The real bug: self.rooom is never assigned, reached only by `go north`.
    root, path = write(GAME.format(attr="rooom"))
    try:
        got = codegen.gate_smoke(path, root)
        assert got and "rooom" in got, got
        print("broken program caught:", got.splitlines()[-1][:70])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Swallowed by a blanket except: no traceback, exit 0, still caught.
    root, path = write(SWALLOWING.format(attr="rooom"))
    try:
        got = codegen.gate_smoke(path, root)
        assert got and "rooom" in got, "swallowed error slipped through: %s" % got
        print("swallowed error caught: ", got.splitlines()[1].strip()[:70])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # A program that merely prints the word error is not a failure.
    root, path = write('def main():\n    print("error: bad input, try again")\n'
                       'if __name__ == "__main__":\n    main()\n')
    try:
        assert codegen.gate_smoke(path, root) is None
        print("program printing the word 'error': PASS")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
