"""ground.py: reading existing files locally to disambiguate a vague request.

Every model call is stubbed -- no network, no real Ollama -- and asserts on
the mechanics: chunking never splits a function in half, an unverifiable
claim (a quoted line that is not actually in the file) is refused rather than
trusted, num_ctx is computed once and held fixed across every call in a pass
(the reload-avoidance property the docstring promises), and a project with
nothing to ground, or no local node configured, returns "" rather than
raising.
"""
import ast

import ground
import swarm


def _cfg_with_mac():
    return swarm.Config({"nodes": [
        {"name": "mac", "url": "http://127.0.0.1:11434", "enabled": False,
         "models": ["gemma4:e2b", "gemma4:e4b"]},
    ]}, "test-config.json")


def _cfg_without_mac():
    return swarm.Config({"nodes": [
        {"name": "g26b", "url": "http://10.0.0.190:11434",
         "models": ["gemma4:12b"]},
    ]}, "test-config.json")


def test_no_existing_files_makes_no_call() -> None:
    calls = []
    real = swarm.chat_complete
    swarm.chat_complete = lambda *a, **k: calls.append(1) or {}
    try:
        out = ground.ground("go faster", {}, _cfg_with_mac(), swarm.State(),
                            verbose=False)
    finally:
        swarm.chat_complete = real
    assert out == ""
    assert not calls, "a call was made with nothing to ground"
    print("empty project     no existing files -> no call, no output")


def test_disabled_mac_still_reachable_because_enabled_only_gates_the_pool() -> None:
    """`enabled: false` excludes a node from cfg.enabled_nodes() (the pool's
    own candidate list), not from cfg.node(name) -- ground.py must use the
    latter, or the one machine this feature is meant to run on would never
    be found."""
    cfg = _cfg_with_mac()
    assert cfg.node("mac") is not None
    assert cfg.node("mac") not in cfg.enabled_nodes()
    print("disabled node      still resolvable directly by name")


def test_no_mac_node_returns_empty_without_crashing() -> None:
    out = ground.ground("go faster", {"a.py": "x = 1\n"}, _cfg_without_mac(),
                        swarm.State(), verbose=False)
    assert out == ""
    print("no local node      returns empty string, does not raise")


def test_enclosing_symbol_overrides_a_model_that_names_the_file_instead() -> None:
    """Measured live against a real dancing-shapes project: e2b answered
    'RELEVANT: engine.py | def run_loop(shapes):' -- a real, verifiable line,
    but the wrong idea of what a symbol name is. The AST walk must recover
    the actual enclosing function/class regardless of what the model wrote."""
    content = ("class Shape:\n"
              "    def __init__(self, x):\n"
              "        self.speed = 2.0\n"
              "\n"
              "def run_loop(shapes):\n"
              "    pass\n")
    assert ground._enclosing_symbol(content, "self.speed = 2.0") == "Shape.__init__"
    assert ground._enclosing_symbol(content, "def run_loop(shapes):") == "run_loop"
    assert ground._enclosing_symbol(content, "not a real line") == "", \
        "a line that cannot be found should not claim an enclosing symbol"
    print("enclosing symbol   AST recovers real symbol regardless of the "
          "model's own label")


def test_wrapped_citation_is_unwrapped_not_rejected() -> None:
    """Measured live, in two different runs: e4b sometimes wraps its
    citation in an extra quote or backtick pair on top of the real line
    ('"self.pc = target"', then later '`self.pc = target`'). Both must be
    accepted once unwrapped, since the underlying text really is in the
    file -- but a line that was not actually in the file must still be
    rejected even after unwrapping, since the point is verifying real
    content, not being lenient about formatting."""
    content = "class VM:\n    def run(self):\n        self.pc = target\n"

    def double_quoted(node, model, messages, **kw):
        return {"text": 'RELEVANT: run | "self.pc = target"',
                "sample": {"input_tokens": 5, "output_tokens": 5,
                          "decode_tps": 50}}

    def backtick_quoted(node, model, messages, **kw):
        return {"text": "RELEVANT: run | `self.pc = target`",
                "sample": {"input_tokens": 5, "output_tokens": 5,
                          "decode_tps": 50}}

    def fabricated_but_wrapped(node, model, messages, **kw):
        return {"text": 'RELEVANT: run | "self._dispatch[opcode]()"',
                "sample": {"input_tokens": 5, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    try:
        for fake, should_pass in ((double_quoted, True), (backtick_quoted, True),
                                  (fabricated_but_wrapped, False)):
            swarm.chat_complete = fake
            out = ground.ground("check jump handling", {"vm.py": content},
                                _cfg_with_mac(), swarm.State(), verbose=False)
            if should_pass:
                assert "self.pc = target" in out, \
                    "a real line, merely wrapped, was rejected: %r" % out
            else:
                assert out == "", \
                    "fabricated content survived just by being quote-wrapped: %r" % out
    finally:
        swarm.chat_complete = real
    print("wrapped citation   quote/backtick wrapping unwrapped and accepted; "
          "fabricated content still rejected even when wrapped the same way")


def test_relevant_finding_reaches_the_summary() -> None:
    content = "class Shape:\n    def __init__(self):\n        self.speed = 2\n"

    def fake_chat_complete(node, model, messages, **kw):
        return {"text": "RELEVANT: speed | self.speed = 2",
                "sample": {"input_tokens": 10, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = fake_chat_complete
    try:
        out = ground.ground("make it faster", {"shapes.py": content},
                            _cfg_with_mac(), swarm.State(), verbose=False)
    finally:
        swarm.chat_complete = real
    assert "shapes.py" in out and "speed" in out and "self.speed = 2" in out, out
    print("relevant finding   real quoted line reached the grounding summary")


def test_unverifiable_claim_is_dropped() -> None:
    """A quoted line that is not actually in the file must never reach the
    planner as though it were verified -- this is the whole point of asking
    for a citation instead of a paraphrase."""
    content = "class Shape:\n    def __init__(self):\n        self.speed = 2\n"

    def fake_chat_complete(node, model, messages, **kw):
        return {"text": "RELEVANT: speed | self.velocity = 99  # invented",
                "sample": {"input_tokens": 10, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = fake_chat_complete
    try:
        out = ground.ground("make it faster", {"shapes.py": content},
                            _cfg_with_mac(), swarm.State(), verbose=False)
    finally:
        swarm.chat_complete = real
    assert out == "", "a hallucinated line was trusted: %r" % out
    print("unverifiable claim caught and dropped, not passed to the planner")


def test_low_yield_warns_the_operator_to_be_more_specific() -> None:
    """Measured live: 'make it run faster' against a real 3-file project
    returned mostly rejected fabrications, 3 findings out of far more chunks
    scanned. That shape should be surfaced to the operator -- the fix is
    wording the request more concretely, which grounding cannot do on
    anyone's behalf, only flag."""
    import io

    def mostly_not_relevant(node, model, messages, **kw):
        return {"text": "NOT RELEVANT",
                "sample": {"input_tokens": 5, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = mostly_not_relevant
    stderr, sys_module = io.StringIO(), __import__("sys")
    old_stderr = sys_module.stderr
    sys_module.stderr = stderr
    try:
        ground.ground("make it better", {"a.py": "x=1\n", "b.py": "y=2\n",
                                         "c.py": "z=3\n"},
                      _cfg_with_mac(), swarm.State(), verbose=True)
    finally:
        swarm.chat_complete = real
        sys_module.stderr = old_stderr
    assert "consider wording it more concretely" in stderr.getvalue(), \
        stderr.getvalue()
    print("low yield          operator warned when almost nothing was found")


def test_good_yield_does_not_warn() -> None:
    """The same check must stay quiet when grounding actually found
    something substantial, or the warning becomes noise on every run."""
    import io

    def all_relevant(node, model, messages, **kw):
        return {"text": "RELEVANT: x | x=1",
                "sample": {"input_tokens": 5, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = all_relevant
    stderr, sys_module = io.StringIO(), __import__("sys")
    old_stderr = sys_module.stderr
    sys_module.stderr = stderr
    try:
        ground.ground("make it faster", {"a.py": "x=1\n"},
                      _cfg_with_mac(), swarm.State(), verbose=True)
    finally:
        swarm.chat_complete = real
        sys_module.stderr = old_stderr
    assert "consider wording it more concretely" not in stderr.getvalue()
    print("good yield         no warning when grounding actually found "
          "something")


def test_not_relevant_contributes_nothing() -> None:
    def fake_chat_complete(node, model, messages, **kw):
        return {"text": "NOT RELEVANT",
                "sample": {"input_tokens": 10, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = fake_chat_complete
    try:
        out = ground.ground("make it faster", {"unrelated.py": "x = 1\n"},
                            _cfg_with_mac(), swarm.State(), verbose=False)
    finally:
        swarm.chat_complete = real
    assert out == ""
    print("not relevant       contributes nothing to the summary")


def test_large_file_chunks_do_not_split_a_function() -> None:
    """A chunk boundary landing inside a function body would hide the one
    line that matched. Every function in a large file must appear whole
    inside exactly one chunk."""
    funcs = ["def f%d():\n    x = %d\n    y = %d\n    return x + y\n" % (i, i, i)
             for i in range(400)]
    content = "\n".join(funcs)
    assert len(content) > ground.CHUNK_CHARS, "test file is not actually large"

    chunks = ground._chunks("big.py", content)
    assert len(chunks) > 1, "a large file produced only one chunk"
    for label, text in chunks:
        ast.parse(text)  # every chunk must itself be syntactically complete
    rejoined = "".join(text for _, text in chunks)
    for i in range(400):
        needle = "def f%d():" % i
        assert needle in rejoined, "%s missing entirely after chunking" % needle
    print("large file          %d chunks, every function whole in exactly one"
          % len(chunks))


def test_num_ctx_is_fixed_across_every_call_in_a_pass() -> None:
    """Ollama reloads the model whenever num_ctx changes, so a per-file loop
    that recomputed it per call would reload every iteration. One value,
    computed once, must be reused for every call regardless of how much each
    individual chunk's content actually needs."""
    seen_ctx = []

    def fake_chat_complete(node, model, messages, num_ctx, **kw):
        seen_ctx.append(num_ctx)
        return {"text": "NOT RELEVANT",
                "sample": {"input_tokens": 10, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = fake_chat_complete
    try:
        ground.ground("make it faster",
                      {"a.py": "x = 1\n", "b.py": "y = 2\n" * 500},
                      _cfg_with_mac(), swarm.State(), verbose=False)
    finally:
        swarm.chat_complete = real
    assert len(seen_ctx) >= 2, "expected at least one call per file"
    assert len(set(seen_ctx)) == 1, \
        "num_ctx changed between calls in the same pass: %r" % seen_ctx
    print("num_ctx stability   identical across %d calls in one pass"
          % len(seen_ctx))


def test_ollama_error_on_one_file_does_not_abort_the_pass() -> None:
    calls = {"n": 0}

    def flaky_chat_complete(node, model, messages, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise swarm.OllamaError("node busy")
        return {"text": "RELEVANT: y | y = 2",
                "sample": {"input_tokens": 5, "output_tokens": 5,
                          "decode_tps": 50}}

    real = swarm.chat_complete
    swarm.chat_complete = flaky_chat_complete
    try:
        out = ground.ground("go faster", {"a.py": "x = 1\n", "b.py": "y = 2\n"},
                            _cfg_with_mac(), swarm.State(), verbose=False)
    finally:
        swarm.chat_complete = real
    assert "b.py" in out, "the file after the failure was never reached"
    print("node error on one file   pass continues, later files still ground")


if __name__ == "__main__":
    test_no_existing_files_makes_no_call()
    test_disabled_mac_still_reachable_because_enabled_only_gates_the_pool()
    test_no_mac_node_returns_empty_without_crashing()
    test_enclosing_symbol_overrides_a_model_that_names_the_file_instead()
    test_wrapped_citation_is_unwrapped_not_rejected()
    test_low_yield_warns_the_operator_to_be_more_specific()
    test_good_yield_does_not_warn()
    test_relevant_finding_reaches_the_summary()
    test_unverifiable_claim_is_dropped()
    test_not_relevant_contributes_nothing()
    test_large_file_chunks_do_not_split_a_function()
    test_num_ctx_is_fixed_across_every_call_in_a_pass()
    test_ollama_error_on_one_file_does_not_abort_the_pass()
    print("OK")
