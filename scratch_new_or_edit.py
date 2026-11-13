"""A new deliverable is a new file; a revision is the same path.

The existing-files block used to say "if the request is an edit, addition,
or fix to existing behaviour, name the SAME path". "Write me a script that
uploads a dummy unit" is an addition, so the planner reused main.py for it
-- and a component replaces its whole file, so the working script that
printed the project link was the thing being overwritten.
"""
import os
import tempfile

import session
import team


def a_project():
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "main.py"), "w") as fh:
        fh.write("from api_client import APIClient\n\n\n"
                 "def main():\n    print('project link')\n")
    with open(os.path.join(root, "api_client.py"), "w") as fh:
        fh.write("class APIClient:\n    def create_project(self):\n"
                 "        return {}\n")
    return root


def test_both_directions_are_stated_not_only_one():
    block = session.existing_files_block(a_project(), team.public_names)
    low = block.lower()
    assert "same path" in low, block
    assert "new path" in low, block
    # The word that caused it: an addition was grouped with an edit.
    assert "addition" not in low, block
    print("  the block says when to reuse and when not to")


def test_the_cost_of_reusing_a_path_is_named():
    """A rule without its reason is a rule a model weighs against others."""
    block = session.existing_files_block(a_project(), team.public_names)
    low = block.lower()
    assert "replaces its whole file" in low or "deletes" in low, block
    assert "main.py" in low, block
    print("  and says what reusing one costs")


def test_the_files_are_still_listed_with_what_they_define():
    block = session.existing_files_block(a_project(), team.public_names)
    assert "main.py" in block and "api_client.py" in block, block
    assert "APIClient" in block, block
    print("  and still lists the files and their names")


def test_the_entry_point_says_so():
    """`public_names` excludes `main` on purpose, which left the entry
    point as a bare filename -- no role beside the one file a planner
    should think hardest about before reusing its path."""
    root = a_project()
    block = session.existing_files_block(root, team.public_names)
    line = [ln for ln in block.splitlines() if ln.strip().startswith("main.py")]
    assert line and "entry point" in line[0], block
    other = [ln for ln in block.splitlines()
             if ln.strip().startswith("api_client.py")]
    assert other and "entry point" not in other[0], block
    print("  the entry point is labelled and a library is not")


def test_the_name_is_not_what_decides_it():
    """An entry point is not always called main.py, and vice versa."""
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "main.py"), "w") as fh:
        fh.write("CONSTANT = 1\n")          # named for it, is not one
    with open(os.path.join(root, "runner.py"), "w") as fh:
        fh.write("def main():\n    pass\n")  # is one, not named for it
    block = session.existing_files_block(root, team.public_names)
    by_file = {ln.split()[0]: ln for ln in
               (l.strip() for l in block.splitlines()) if ln.endswith(".py")
               or ".py " in ln}
    assert "entry point" not in by_file["main.py"], block
    assert "entry point" in by_file["runner.py"], block
    print("  decided by the syntax tree, not the filename")


def test_a_page_is_listed_but_not_as_a_module():
    """A planner that cannot see survey.html plans a second copy of it."""
    root = a_project()
    with open(os.path.join(root, "survey.html"), "w") as fh:
        fh.write("<html><body><form></form></body></html>")
    with open(os.path.join(root, ".DS_Store"), "w") as fh:
        fh.write("junk")
    block = session.existing_files_block(root, team.public_names)
    assert "survey.html" in block, block
    assert "run time" in block, block
    assert "DS_Store" not in block, block
    print("  a page is listed, dotfiles and unknown types are not")


def test_the_marker_reads_the_filesystem_and_nothing_else():
    """The one fact an operator may want to stop the run over.

    Deliberately not a judgement about whether replacing is wrong -- that
    is the planner's call. A detector guessing at intent would fire on
    correct plans, which costs more than it saves.
    """
    root = a_project()
    exists = os.path.isfile(os.path.join(root, "main.py"))
    fresh = os.path.isfile(os.path.join(root, "upload_unit.py"))
    assert exists and not fresh
    print("  replaces-existing is a filesystem fact, not an opinion")


def main():
    test_both_directions_are_stated_not_only_one()
    test_the_cost_of_reusing_a_path_is_named()
    test_the_files_are_still_listed_with_what_they_define()
    test_the_entry_point_says_so()
    test_the_name_is_not_what_decides_it()
    test_a_page_is_listed_but_not_as_a_module()
    test_the_marker_reads_the_filesystem_and_nothing_else()
    print("OK")


if __name__ == "__main__":
    main()
