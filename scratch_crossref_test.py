"""Every cross-file bug from tonight's two runs, rebuilt as a fixture.

Each case is a real defect that passed parse, import and the smoke run, and
that a model reviewer missed. The last case is the one that matters most: a
correct package must stay silent, because a checker that fires on working code
sends the repair loop to rewrite it.
"""
import os
import tempfile

import crossref


def project(files):
    root = tempfile.mkdtemp()
    for name, text in files.items():
        full = os.path.join(root, name)
        os.makedirs(os.path.dirname(full) or root, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    return root, sorted(files)


def case(title, files, expect):
    root, names = project(files)
    found = crossref.scan(names, root)
    texts = " | ".join(f.message for f in found)
    if expect is None:
        assert not found, "%s: expected silence, got %s" % (title, texts)
        print("  %-34s silent" % title)
        return found
    assert found, "%s: nothing detected" % title
    assert any(expect in f.message for f in found), (
        "%s: wanted %r, got %s" % (title, expect, texts))
    print("  %-34s %s" % (title, found[0].message))
    return found


def main():
    print("catches the real failures:")

    # space_invader_swarm/main.py imported three names collision.py never had.
    found = case("import of a name that isn't there", {
        "collision.py": "def check_collision(a, b):\n    return a.colliderect(b)\n",
        "main.py": "from collision import check_collision, PlayerShip, GameState\n",
    }, "does not define it")
    assert "check_collision" in found[0].detail, found[0].detail

    # enemy.py ended with `Enemy = EnemyWave(...)`.
    case("class rebound to an instance", {
        "enemy.py": ("class Enemy:\n    pass\n\n\nclass EnemyWave:\n"
                     "    def __init__(self, n):\n        self.n = n\n\n\n"
                     "Enemy = EnemyWave(5)\n"),
    }, "rebinds the class")

    # main.py called player.get_rect(); Player has move/draw/fire_projectile.
    case("method that does not exist", {
        "player.py": ("class Player:\n    def __init__(self, x):\n"
                      "        self.rect = x\n    def move(self, d):\n"
                      "        pass\n    def draw(self, s):\n        pass\n"),
        "main.py": ("from player import Player\n\n"
                    "p = Player(1)\np.get_rect()\n"),
    }, "no such attribute")

    # The same mistake reached through self, which is how main.py held it.
    case("missing method via self", {
        "enemy.py": ("class EnemyWave:\n    def __init__(self):\n"
                     "        self.enemies = []\n    def update(self):\n"
                     "        pass\n"),
        "game.py": ("from enemy import EnemyWave\n\n"
                    "class Game:\n    def __init__(self):\n"
                    "        self.wave = EnemyWave()\n"
                    "    def step(self):\n"
                    "        self.wave.get_invader_units()\n"),
    }, "no such attribute")

    # A component that declares dependencies and then reimplements them
    # inline: the quietest failure of all, because the program runs.
    root, names = project({
        "shapes.py": "class Shape:\n    def draw(self, s):\n        pass\n",
        "main.py": ("import pygame\n\n\nclass Shape:\n"
                    "    def update(self):\n        pass\n\n\n"
                    "def main():\n    pass\n"),
    })
    found = crossref.scan(names, root, {"main": ["shapes"], "shapes": []},
                          {"main": "main.py", "shapes": "shapes.py"})
    assert any("imports nothing from it" in f.message for f in found), found
    print("  %-34s %s" % ("declared a dep, imported nothing", found[0].message))

    print("\nstays quiet when it should:")

    # The corrected package must produce nothing at all.
    case("a correct package", {
        "player.py": ("class Player:\n    def __init__(self, x):\n"
                      "        self.rect = x\n    def move(self, d):\n"
                      "        pass\n"),
        "main.py": ("from player import Player\n\n"
                    "p = Player(1)\np.move(2)\nprint(p.rect)\n"),
    }, None)

    # Inheriting from outside the project means absence proves nothing.
    case("subclass of a third-party class", {
        "sprite.py": ("import pygame\n\n\n"
                      "class Ship(pygame.sprite.Sprite):\n"
                      "    def __init__(self):\n        pass\n"),
        "main.py": ("from sprite import Ship\n\n"
                    "s = Ship()\ns.kill()\ns.add(None)\n"),
    }, None)

    # A class answering through __getattr__ can have any attribute.
    case("class with __getattr__", {
        "bag.py": ("class Bag:\n    def __getattr__(self, k):\n"
                   "        return 42\n"),
        "main.py": "from bag import Bag\n\nb = Bag()\nprint(b.whatever)\n",
    }, None)

    # Imports from outside the project are none of our business.
    case("third-party imports", {
        "main.py": ("import pygame\nfrom pygame.locals import QUIT\n"
                    "from typing import List\n"),
    }, None)

    # Packages: `from .thing import x` must resolve to pkg/thing.py.
    case("relative import inside a package", {
        "pkg/__init__.py": "",
        "pkg/thing.py": "def helper():\n    return 1\n",
        "pkg/main.py": "from .thing import helper\n\nprint(helper())\n",
    }, None)

    case("relative import of a missing name", {
        "pkg/__init__.py": "",
        "pkg/thing.py": "def helper():\n    return 1\n",
        "pkg/main.py": "from .thing import helper, missing_one\n",
    }, "does not define it")

    root, names = project({
        "lib.py": "def helper():\n    return 1\n",
        "app.py": "from lib import helper\n\nprint(helper())\n",
    })
    quiet = crossref.scan(names, root, {"app": ["lib"], "lib": []},
                          {"app": "app.py", "lib": "lib.py"})
    assert quiet == [], quiet
    print("  %-34s silent" % "a dependency that is used")

    # A Python dependency that is genuinely never imported. Asserted right
    # next to the HTML case below, because the fix for one is exactly what
    # would make the other vacuous.
    root, names = project({
        "helper.py": "def useful():\n    return 42\n",
        "standalone.py": ("def useful():\n    return 41\n\n\n"
                          "def main():\n    print(useful())\n"),
    })
    loud = crossref.scan(names, root, {"s": ["h"]},
                         {"s": "standalone.py", "h": "helper.py"})
    assert any("imports nothing from it" in f.message for f in loud), loud
    print("  %-34s %s" % ("a python dependency ignored", loud[0].message))

    # A page is a dependency you read at runtime, not one you import. The
    # detector demanded `import applet_template` from a main.py that opened
    # the file correctly, spent both repair rounds rewriting working code,
    # and then reported the plan unresolved.
    root, names = project({
        "applet_template.html": "<html><body><form></form></body></html>\n",
        "main.py": ("def main():\n"
                    "    with open('applet_template.html') as fh:\n"
                    "        print(len(fh.read()))\n"),
    })
    quiet = crossref.scan(names, root, {"m": ["p"]},
                          {"m": "main.py", "p": "applet_template.html"})
    assert quiet == [], quiet
    print("  %-34s silent" % "a non-python dependency")

    print("\nOK")


if __name__ == "__main__":
    main()
