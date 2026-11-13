"""Is the assembling component recognised, so it gets no invented oracle?"""
import checks
import intent


def comp(name, deps, floor=2):
    c = intent.Component.__new__(intent.Component)
    c.name = name
    c.path = name + ".py"
    c.spec = ""
    c.floor = floor
    c.deps = deps
    return c


def main():
    # Tonight's plan: four utilities and a package export that pulls them in.
    plan = [comp("slugify", []), comp("wrap", []), comp("ngrams", []),
            comp("edit_script", [], floor=4),
            comp("package_export", ["slugify", "wrap", "ngrams",
                                    "edit_script"])]
    found = checks.joins(plan)
    assert found == {"package_export"}, "expected the export module, got %r" % found
    print("textkit plan: join identified as %s" % ", ".join(found))

    # A component two others build on is not a join, even with dependencies.
    chain = [comp("base", []), comp("mid", ["base", "other"]),
             comp("other", []), comp("top", ["mid"])]
    found = checks.joins(chain)
    assert "mid" not in found, "a depended-on component was called a join"
    assert found == set(), "unexpected joins in a chain: %r" % found
    print("chain: nothing misread as a join")

    # One dependency is a chain link, not an assembly point.
    single = [comp("a", []), comp("b", ["a"])]
    assert checks.joins(single) == set(), "single-dep component called a join"
    print("single dependency: not a join")

    # Flat plans have no join and every component keeps its oracle.
    flat = [comp("a", []), comp("b", []), comp("c", [])]
    assert checks.joins(flat) == set(), "flat plan produced a join"
    print("flat plan: every component keeps its oracle")
    print("OK")


if __name__ == "__main__":
    main()
