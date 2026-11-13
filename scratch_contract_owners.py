"""The contract has to name an owner, or it reads as an order to write.

It is broadcast to every writer so unrelated files agree on a shared type
without waiting for each other. Unqualified, a complete class signature reads
as a specification of something to write: a `constants.py` asked for a colour
list came back with `Shape`, `main()` and a whole pygame loop, because the
contract handed it `Shape(x, y, size, ...)` and said "use these exact names".

Naming the owner turns that sentence into a prohibition. Which means the
parsing has to be exact -- a symbol wrongly claimed as owned is forbidden
everywhere else in the package, so a false entry costs more than a missing one.

No models are consulted here; this is all text and syntax.
"""
import intent
import team

# The contract from the run that failed, verbatim.
LIVE = ("shapes.py owns Shape(x: float, y: float, size: float, color: tuple, "
        "velocity_x: float, velocity_y: float, hue: float, shape_type: str) "
        "with update(dt: float) and draw(surface: pygame.Surface); "
        "constants.py owns COLORS: list[tuple[int, int, int]]")

OWNERS = {
    LIVE: {"Shape": "shapes.py", "COLORS": "constants.py"},
    "a.py owns Foo; b.py owns Bar": {"Foo": "a.py", "Bar": "b.py"},
    # `int` is a type inside an annotation, not a symbol anyone owns.
    "constants.py owns MAX_SPEED: float, COLORS: list[tuple[int, int, int]]":
        {"MAX_SPEED": "constants.py", "COLORS": "constants.py"},
    "util.py owns slugify(text: str) -> str and wrap(text: str, width: int)":
        {"slugify": "util.py", "wrap": "util.py"},
    "the files should agree on the Shape type": {},
    "": {},
}


def test_owners() -> None:
    for contract, expected in OWNERS.items():
        got = intent.contract_owners(contract)
        assert got == expected, "%r\n  got      %s\n  expected %s" % (
            contract[:60], got, expected)
    print("contract_owners  %d contracts parsed exactly" % len(OWNERS))


def test_note() -> None:
    """Each file is told what it owns, and forbidden the rest by name."""
    constants = intent.ownership_note(LIVE, "constants.py")
    assert "You define COLORS" in constants, constants
    assert "Do NOT define Shape" in constants, constants
    # Methods of an owned class are not top-level names, and forbidding them
    # would ban two ordinary method names across the whole package.
    for method in ("update", "draw"):
        assert method not in constants, "forbade the method %r:\n%s" % (
            method, constants)
    # A type mentioned in an annotation is not owned by anybody.
    assert "int" not in constants.split(), constants

    shapes = intent.ownership_note(LIVE, "shapes.py")
    assert "You define Shape" in shapes, shapes
    assert "Do NOT define COLORS" in shapes, shapes

    # A file the contract never mentions is told nothing it must write, and
    # everything it must not.
    other = intent.ownership_note(LIVE, "main.py")
    assert "You define" not in other, other
    assert "Do NOT define Shape" in other and "Do NOT define COLORS" in other

    assert intent.ownership_note("", "constants.py") == ""
    print("ownership_note   owner named, methods and types left alone")


def test_repair_direction() -> None:
    """Only a file that was handed the owner's source can be told to import it.

    `constants.py` depends on nothing, so it never saw `shapes.py` and cannot
    import from it -- and the arrow may point the other way, making the import
    circular. Told to import anyway, it rewrote the duplicated class on both
    attempts and the loop gave up.
    """
    shapes = intent.Component("shapes", "shapes.py", 4, [], "s")
    constants = intent.Component("constants", "constants.py", 2, [], "s")
    main = intent.Component("main", "main.py", 2, ["shapes", "constants"], "s")
    by_path = {c.path: c for c in (shapes, constants, main)}

    assert not team.depends_on(constants, "shapes.py", by_path), \
        "constants.py does not depend on shapes.py; it cannot import from it"
    assert team.depends_on(main, "shapes.py", by_path), \
        "main.py depends on shapes.py and was handed its source"
    assert not team.depends_on(main, "nosuch.py", by_path)
    print("repair direction non-dependents are told to delete, not import")


if __name__ == "__main__":
    test_owners()
    test_note()
    test_repair_direction()
    print("OK")
