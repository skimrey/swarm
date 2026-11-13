"""A component editing an existing file must be shown that file's real content.

Session history tells the *planner* a file exists and what it defines, so a
follow-up request can name it -- but the planner's spec only describes the
change. Without this, the model actually asked to write that path never sees
what is already there and would replace it blind, silently dropping whatever
the spec did not happen to mention. Exercised with generation stubbed out, so
no model is involved -- only whether the real content reaches the prompt.
"""
import os
import shutil
import tempfile
import threading

import codegen
import intent
import swarm
import team

TIMEOUT = 20.0


def test_existing_content_reaches_the_prompt() -> None:
    root = tempfile.mkdtemp(prefix="edit-")
    seen_prompts = {}
    try:
        # A file that already exists, with real content the writer must see.
        with open(os.path.join(root, "shapes.py"), "w", encoding="utf-8") as fh:
            fh.write("class Shape:\n    def __init__(self, x):\n        self.x = x\n")

        component = intent.Component(
            "shapes", "shapes.py", 4, [],
            "add a bounce() method to the existing Shape class")
        component.contract = ""

        def fake_generate(spec, out_path, node, model, cfg, state, **kw):
            # `extra` -- not `spec` -- is where the worker puts contract notes,
            # dependency context, and (with this fix) prior file content; the
            # real prompt sent to the model is spec-plus-extra concatenated
            # inside generate_file itself.
            seen_prompts[out_path] = spec + "\n" + kw.get("extra", "")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("class Shape:\n    def bounce(self): pass\n")
            return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                    "target": "%s/%s" % (node.name, model), "code": ""}

        existing = {"shapes.py": open(os.path.join(root, "shapes.py"),
                                      encoding="utf-8").read()}

        real = codegen.generate_file
        codegen.generate_file = fake_generate
        try:
            thread = threading.Thread(
                target=lambda: team.run_components(
                    [component], swarm.Config.load(), swarm.State(),
                    root=root, existing=existing),
                daemon=True)
            thread.start()
            thread.join(TIMEOUT)
            assert not thread.is_alive(), "run_components hung"
        finally:
            codegen.generate_file = real

        prompt = seen_prompts.get(os.path.join(root, "shapes.py"))
        assert prompt is not None, "the component was never generated at all"
        assert "This file already exists" in prompt, \
            "no notice that this is an edit, not a fresh file:\n%s" % prompt
        assert "def __init__(self, x):" in prompt, \
            "the real prior content never reached the prompt:\n%s" % prompt
        assert "CHANGE to make" in prompt
        print("edit-in-place     real prior content reached the writer's prompt")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_brand_new_file_gets_no_stale_notice() -> None:
    """A component whose path was never on disk must not be told it exists."""
    root = tempfile.mkdtemp(prefix="new-")
    seen_prompts = {}
    try:
        component = intent.Component("engine", "engine.py", 4, [],
                                     "the main game loop")
        component.contract = ""

        def fake_generate(spec, out_path, node, model, cfg, state, **kw):
            seen_prompts[out_path] = spec + "\n" + kw.get("extra", "")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("def main(): pass\n")
            return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                    "target": "%s/%s" % (node.name, model), "code": ""}

        real = codegen.generate_file
        codegen.generate_file = fake_generate
        try:
            thread = threading.Thread(
                target=lambda: team.run_components(
                    [component], swarm.Config.load(), swarm.State(),
                    root=root, existing={}),
                daemon=True)
            thread.start()
            thread.join(TIMEOUT)
            assert not thread.is_alive(), "run_components hung"
        finally:
            codegen.generate_file = real

        prompt = seen_prompts.get(os.path.join(root, "engine.py"))
        assert prompt is not None
        assert "This file already exists" not in prompt, \
            "a brand-new file was falsely told it already existed:\n%s" % prompt
        print("brand-new file    no false 'already exists' notice")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_existing_argument_does_not_crash() -> None:
    """The default (existing=None) must behave exactly like before this change."""
    root = tempfile.mkdtemp(prefix="none-")
    try:
        component = intent.Component("util", "util.py", 2, [], "a helper")
        component.contract = ""

        def fake_generate(spec, out_path, node, model, cfg, state, **kw):
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("VALUE = 1\n")
            return {"ok": True, "attempts": 1, "seconds": 0.0, "history": [],
                    "target": "%s/%s" % (node.name, model), "code": ""}

        real = codegen.generate_file
        codegen.generate_file = fake_generate
        try:
            thread = threading.Thread(
                target=lambda: team.run_components(
                    [component], swarm.Config.load(), swarm.State(), root=root),
                daemon=True)
            thread.start()
            thread.join(TIMEOUT)
            assert not thread.is_alive(), "run_components hung with no existing= arg"
        finally:
            codegen.generate_file = real
        print("no existing= arg  old call signature still works, no crash")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_existing_content_reaches_the_prompt()
    test_brand_new_file_gets_no_stale_notice()
    test_no_existing_argument_does_not_crash()
    print("OK")
